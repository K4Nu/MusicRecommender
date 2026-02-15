import logging
from collections import defaultdict
from django.utils import timezone
from django.db import transaction
from django.db.models import Max

from recomendations.models import (
    Recommendation,
    RecommendationItem,
    ColdStartTrack,
    UserTag,
    OnboardingEvent,
)
from music.models import Track, TrackSimilarity,TrackTag
from users.models import ListeningHistory, UserTopItem, SpotifyAccount

logger = logging.getLogger(__name__)


# =========================================================
# STRATEGY DETECTION
# =========================================================

def detect_strategy(user)->str:
    """
       Auto-detect which recommendation strategy to use based on
       how much data we have about the user.

       COLD_START   → only onboarding data (3 likes, no Spotify)
       WARM_START   → has Spotify but limited history (<20 tracks)
       HYBRID_START → has good Spotify history (≥20 tracks)
       """
    has_spotify=SpotifyAccount.objects.filter(user=user).exists()

    if not(has_spotify):
        return Recommendation.RecommendationStrategy.COLD_START

    history_count=ListeningHistory.objects.filter(user=user).count()
    top_items_count=UserTopItem.objects.filter(user=user).count()
    total_signals=history_count+top_items_count

    if total_signals >=20:
        return Recommendation.RecommendationStrategy.HYBRID_START

    return Recommendation.RecommendationStrategy.WARM_START

# =========================================================
# USER TAG PROFILE
# =========================================================
def get_user_tag_profile(user,source=None)->dict:
    """
        Returns {tag_id: weighted_score} for the user.
        Uses computed aggregate if available, otherwise raw sources.
        """
    computed=UserTag.objects.for_user(user, source="computed")
    if computed.exists():
        return {ut.tag_id: ut.weight * ut.confidence for ut in computed}

    qs=UserTag.objects.for_user(user, source=source)
    profile=defaultdict(float)
    counts=defaultdict(int)

    for ut in qs:
        profile[ut.tag_id] += ut.weight * ut.confidence
        counts[ut.tag_id] += 1

    return {tag_id: score / counts[tag_id] for tag_id, score in profile.items()}

# =========================================================
# TRACK SCORING
# =========================================================

def score_tracks_by_tags(track_ids: list, user_tag_profile: dict) -> dict:
    """
        Score tracks by tag overlap with user profile.
        Returns {track_id: score}

        Score = sum(user_tag_weight * track_tag_weight) for matching tags
        Normalized to 0-1 range.
        """
    if not user_tag_profile:
        return {}

    tracks_tag=(
        TrackTag.objects.filter(track_id__in=track_ids,is_active=True).values("track_id","tag_id","weight")
    )
    scores=defaultdict(float)
    for tt in tracks_tag:
        user_weight=user_tag_profile.get(tt["tag_id"],0)
        if user_weight>0:
            scores[tt["tag_id"]] += user_weight * tt["weight"]

    if not scores:
        return {}

    max_score=max(scores.values())
    if max_score>0:
        scores = {tid: s / max_score for tid, s in scores.items()}

    return dict(scores)

def score_tracks_by_similarity(seed_tracks_ids:list, candidate_ids:list)->dict:
    """
        Score candidate tracks by similarity to seed tracks.
        Returns {track_id: score}
        """
    if not seed_tracks_ids or not candidate_ids:
        return {}

    similarities=(TrackSimilarity.objects.filter(from_track_id__in=seed_tracks_ids,to_track_id__in=candidate_ids).values("to_track_id","score"))

    scores=defaultdict(float)
    counts=defaultdict(int)

    for sim in similarities:
        scores[sim["to_track_id"]] += sim["score"]
        counts[sim["to_track_id"]] += 1

    return {
        tid:scores[tid]/counts[tid] for tid in scores
    }

# =========================================================
# CANDIDATE POOLS
# =========================================================
def get_cold_start_candidates()->dict:
    """
    Returns {track_id: cold_start_score} deduplicated by track.
    Takes the best score when a track appears in multiple sources.
    """

    return dict(
        ColdStartTrack.objects.values("track_id").annotate(best_score=Max("score")).order_by("track_id","best_score")
    )

def get_seed_tracks(user,limit=20)->list:
    """
    Seed tracks = liked onboarding + recent listening + top items
    """
    onboarding_ids=list(OnboardingEvent.objects.filter(user=user, action=OnboardingEvent.Action.LIKE).values("cold_start_track__track_id", flat=True))
    history_ids=list(UserTopItem.objects.filter(user=user).order_by("-played_at").values("track_id", flat=True)[:limit])
    top_tracks_ids=list(UserTopItem.objects.filter(user=user).order_by("rank").values_list("track_id", flat=True)[:limit])

    all_ids=list(dict.fromkeys(onboarding_ids+history_ids+top_tracks_ids))
    return all_ids[:limit]

def apply_artist_diversity(scored_tracks: list, max_per_artist: int = 2) -> list:
    """
    Limit tracks per artist to avoid recommending the same artist too much.
    scored_tracks = [(track_id, score, reason), ...]
    """

    tracks_ids=[t[0] for t in scored_tracks]
    artist_map=defaultdict(list)

    tracks=(
        Track.objects.filter(id__in=tracks_ids).prefetch_related("artist")
    )
    track_artists={t.id:[a.id for a in t.arists.all()]for t in tracks}
    result=[]
    artist_counts=defaultdict(int)

    for track_id, score,reason in scored_tracks:
        artists=track_artists.get(track_id,[])
        main_artist=artists[0] if artists else None

        if main_artist and artist_counts.get(main_artist,0)>=max_per_artist:
            continue

        if main_artist:
            artist_counts[main_artist]+=1

        result.append((track_id, score, reason))

    return result

# =========================================================
# RECOMMENDATION BUILDERS
# =========================================================
def build_cold_start_recommendation(user, limit=20) -> Recommendation:
    """
    Pure cold start - uses cold start pool scored by user tag profile.
    For users who have completed onboarding but have no Spotify.
    """
    user_tags=get_user_tag_profile(user)
    candidates=get_cold_start_candidates()
    seed_ids=get_seed_tracks(user)

    seen_ids=set(
        OnboardingEvent.objects.filter(
            user=user).values_list("cold_start_track__track_id", flat=True)
        )

    candidate_ids=[tid for tid in candidates if tid not in seen_ids]

    tag_scores=score_tracks_by_tags(candidate_ids, user_tags)

    sim_scores=score_tracks_by_similarity(seed_ids, candidate_ids)

    #Combine scores
    # cold_start_score: 30% (popularity signal)
    # tag_score:        50% (taste match)
    # sim_score:        20% (collaborative signal)

    scored=[]
    for track_id in candidate_ids:
        cs_score=candidates.get(track_id,0)
        tag_score=tag_scores.get(track_id,0)
        sim_score=sim_scores.get(track_id,0)

        if user_tags:
            final_score=(cs_score*0.3)+(tag_score*0.5)+(sim_score*0.2)
        else:
            # No tags yet - just use cold start score
            final_score=cs_score

        reason = {
            "cold_start_score": round(cs_score, 4),
            "tag_score": round(tag_score, 4),
            "sim_score": round(sim_score, 4),
            "strategy": "cold_start",
        }
        scored.append((track_id, final_score, reason))

    scored.sort(key=lambda x: x[1], reverse=True)
    scored=apply_artist_diversity(scored,max_per_artist=2)

    scored=scored[:limit]

    return _save_recommendation(
        user=user,
        strategy=Recommendation.RecommendationStrategy.COLD_START,
        scored_tracks=scored,
        context={
            "seed_track_ids": seed_ids,
            "user_tag_count": len(user_tags),
            "candidate_count": len(candidate_ids),
        },
    )

def build_hybrid_recommendation(user, limit=20) -> Recommendation:
    """
    Hybrid - uses cold start pool + Spotify history scored by user tags.
    For users with Spotify connected.
    """
    user_tags = get_user_tag_profile(user)
    candidates = get_cold_start_candidates()
    seed_ids = get_seed_tracks(user, limit=50)

    similar_track_ids = list(
        TrackSimilarity.objects
        .filter(from_track_id__in=seed_ids)
        .order_by("-score")
        .values_list("to_track_id", flat=True)[:200]
    )

    all_candidate_ids=list(set(list(candidates.keys())+similar_track_ids))

    heard_ids = set(
        ListeningHistory.objects
        .filter(user=user)
        .values_list("track_id", flat=True)
    )
    heard_ids |= set(
        UserTopItem.objects
        .filter(user=user, item_type="tracks")
        .values_list("track_id", flat=True)
    )

    candidate_ids = [tid for tid in all_candidate_ids if tid not in heard_ids]

    # Score
    tag_scores = score_tracks_by_tags(candidate_ids, user_tags)
    sim_scores = score_tracks_by_similarity(seed_ids, candidate_ids)

    candidate_ids = [tid for tid in all_candidate_ids if tid not in heard_ids]

    # Score
    tag_scores = score_tracks_by_tags(candidate_ids, user_tags)
    sim_scores = score_tracks_by_similarity(seed_ids, candidate_ids)

    scored = []
    for track_id in candidate_ids:
        cs_score = candidates.get(track_id, 0)
        tag_score = tag_scores.get(track_id, 0)
        sim_score = sim_scores.get(track_id, 0)

    final_score = (cs_score * 0.2) + (tag_score * 0.4) + (sim_score * 0.4)

    reason = {
        "cold_start_score": round(cs_score, 4),
        "tag_score": round(tag_score, 4),
        "sim_score": round(sim_score, 4),
        "strategy": "hybrid",
    }
    scored.append((track_id, final_score, reason))


    scored.sort(key=lambda x: x[1], reverse=True)
    scored = apply_artist_diversity(scored, max_per_artist=2)
    scored = scored[:limit]

    return _save_recommendation(
        user=user,
        strategy=Recommendation.RecommendationStrategy.HYBRID_START,
        scored_tracks=scored,
        context={
            "seed_track_ids": seed_ids[:10],
            "user_tag_count": len(user_tags),
            "candidate_count": len(candidate_ids),
            "heard_excluded": len(heard_ids),
        },
    )



