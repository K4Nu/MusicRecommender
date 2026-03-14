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
    RecommendationFeedback,
)
from music.models import Track, TrackSimilarity, TrackTag
from users.models import ListeningHistory, UserTopItem, SpotifyAccount
from recomendations.services.tag_filter import MIN_TAG_USAGE_COUNT, BLOCKED_TAG_NAMES

logger = logging.getLogger(__name__)


# =========================================================
# STRATEGY DETECTION
# =========================================================

def detect_strategy(user) -> str:
    """
    Auto-detect which recommendation strategy to use based on
    how much data we have about the user.

    COLD_START   → only onboarding data (3 likes, no Spotify)
    WARM_START   → has Spotify but limited history (<20 tracks)
    HYBRID_START → has good Spotify history (≥20 tracks)
    """
    has_spotify = SpotifyAccount.objects.filter(user=user).exists()

    if not has_spotify:
        return Recommendation.RecommendationStrategy.COLD_START

    history_count = ListeningHistory.objects.filter(user=user).count()
    top_items_count = UserTopItem.objects.filter(user=user).count()
    total_signals = history_count + top_items_count

    if total_signals >= 20:
        return Recommendation.RecommendationStrategy.HYBRID_START

    return Recommendation.RecommendationStrategy.WARM_START


# =========================================================
# USER TAG PROFILE
# =========================================================

def get_user_tag_profile(user, source=None) -> dict:
    """
    Returns {tag_id: weighted_score} for the user.
    Uses computed aggregate if available, otherwise raw sources.
    """
    computed = UserTag.objects.for_user(user, source="computed")
    if computed.exists():
        return {ut.tag_id: ut.weight * ut.confidence for ut in computed}

    qs = UserTag.objects.for_user(user, source=source)
    profile = defaultdict(float)
    counts = defaultdict(int)

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
    if not user_tag_profile or not track_ids:
        return {}

    track_tags = (
        TrackTag.objects
        .filter(
            track_id__in=track_ids,
            is_active=True,
            tag__total_usage_count__gte=MIN_TAG_USAGE_COUNT,
        )
        .exclude(tag__normalized_name__in=BLOCKED_TAG_NAMES)
        .values("track_id", "tag_id", "weight")
    )

    scores = defaultdict(float)
    for tt in track_tags:
        user_weight = user_tag_profile.get(tt["tag_id"], 0)
        if user_weight > 0:
            scores[tt["track_id"]] += user_weight * tt["weight"]  # ✅ key by track_id

    if not scores:
        return {}

    max_score = max(scores.values())
    if max_score > 0:
        scores = {tid: s / max_score for tid, s in scores.items()}

    return dict(scores)


def score_tracks_by_similarity(seed_track_ids: list, candidate_ids: list) -> dict:
    """
    Score candidate tracks by similarity to seed tracks.
    Returns {track_id: score}
    """
    if not seed_track_ids or not candidate_ids:
        return {}

    similarities = (
        TrackSimilarity.objects
        .filter(
            from_track_id__in=seed_track_ids,
            to_track_id__in=candidate_ids,
        )
        .values("to_track_id", "score")
    )

    scores = defaultdict(float)
    counts = defaultdict(int)

    for sim in similarities:
        scores[sim["to_track_id"]] += sim["score"]
        counts[sim["to_track_id"]] += 1

    return {tid: scores[tid] / counts[tid] for tid in scores}

# =========================================================
# CANDIDATE POOLS
# =========================================================
def get_already_rated_track_ids(user) -> set:
    return set(
        RecommendationFeedback.objects
        .filter(user=user)
        .values_list("recommendation_item__track_id", flat=True)
    )

def get_previously_recommended_track_ids(user, strategy) -> set:
    """
    Returns all track_ids that were ever recommended
    to this user for given strategy.
    """
    return set(
        RecommendationItem.objects
        .filter(
            recommendation__user=user,
            recommendation__strategy=strategy,
        )
        .values_list("track_id", flat=True)
    )

def get_cold_start_candidates() -> dict:
    """Returns {track_id: best_score} deduplicated across sources."""
    return dict(
        ColdStartTrack.objects
        .values("track_id")
        .annotate(best_score=Max("score"))
        .values_list("track_id", "best_score")
    )


def get_seed_tracks(user, limit=20) -> list:
    """
    Seed tracks = liked onboarding + top items + recent listening
    Onboarding likes are most reliable so they come first.
    """
    onboarding_ids = list(
        OnboardingEvent.objects
        .filter(user=user, action=OnboardingEvent.Action.LIKE)
        .values_list("cold_start_track__track_id", flat=True)
    )

    history_ids = list(
        ListeningHistory.objects
        .filter(user=user)
        .order_by("-played_at")
        .values_list("track_id", flat=True)[:limit]
    )

    top_track_ids = list(
        UserTopItem.objects
        .filter(user=user, item_type="tracks")
        .order_by("rank")
        .values_list("track_id", flat=True)[:limit]
    )

    # dict.fromkeys preserves order and deduplicates
    all_ids = list(dict.fromkeys(onboarding_ids + top_track_ids + history_ids))
    return all_ids[:limit]


def apply_artist_diversity(scored_tracks: list, max_per_artist: int = 2) -> list:
    """
    Limit tracks per artist to avoid recommending the same artist too much.
    scored_tracks = [(track_id, score, reason), ...]
    """
    track_ids = [t[0] for t in scored_tracks]

    tracks = (
        Track.objects
        .filter(id__in=track_ids)
        .prefetch_related("artists")
    )
    track_artists = {t.id: [a.id for a in t.artists.all()] for t in tracks}

    result = []
    artist_counts = defaultdict(int)

    for track_id, score, reason in scored_tracks:
        artists = track_artists.get(track_id, [])
        main_artist = artists[0] if artists else None

        if main_artist and artist_counts[main_artist] >= max_per_artist:
            continue

        if main_artist:
            artist_counts[main_artist] += 1

        result.append((track_id, score, reason))

    return result


# =========================================================
# RECOMMENDATION BUILDERS
# =========================================================
def _precompute_reason_data(candidate_ids, seed_ids, user_tags):
    if not candidate_ids:
        return {}, {}

    matched_tags_map = defaultdict(list)

    track_tags_qs = (
        TrackTag.objects
        .filter(
            track_id__in=candidate_ids,
            is_active=True,
            tag__total_usage_count__gte=MIN_TAG_USAGE_COUNT,
        )
        .exclude(tag__normalized_name__in=BLOCKED_TAG_NAMES)
        .select_related("tag")
        .order_by("-weight")
    )

    for tt in track_tags_qs:
        if user_tags.get(tt.tag_id, 0) > 0:
            if len(matched_tags_map[tt.track_id]) < 3:
                matched_tags_map[tt.track_id].append(tt.tag.name)

    similar_to_map = defaultdict(list)

    if seed_ids:
        seed_track_names = dict(
            Track.objects.filter(id__in=seed_ids)
            .values_list("id", "name")
        )

        sims_qs = (
            TrackSimilarity.objects
            .filter(from_track_id__in=seed_ids, to_track_id__in=candidate_ids)
            .order_by("-score")
            .values("from_track_id", "to_track_id", "score")
        )

        for sim in sims_qs:
            if len(similar_to_map[sim["to_track_id"]]) < 2:
                name = seed_track_names.get(sim["from_track_id"])
                if name:
                    similar_to_map[sim["to_track_id"]].append({
                        "track_name": name,
                        "score": round(sim["score"], 4),
                    })

    return matched_tags_map, similar_to_map

def build_cold_start_recommendation(user, limit=20) -> Recommendation:
    user_tags = get_user_tag_profile(user)
    candidates = get_cold_start_candidates()
    seed_ids = get_seed_tracks(user)

    strategy = Recommendation.RecommendationStrategy.COLD_START

    # 🔹 onboarding already seen
    seen_ids = set(
        OnboardingEvent.objects
        .filter(user=user)
        .values_list("cold_start_track__track_id", flat=True)
    )

    # 🔹 already rated
    rated_ids = get_already_rated_track_ids(user)

    # 🔹 previously recommended (NEW)
    previous_ids = get_previously_recommended_track_ids(user, strategy)

    excluded_ids = seen_ids | rated_ids | previous_ids

    candidate_ids = [
        tid for tid in candidates.keys()
        if tid not in excluded_ids
    ]

    # Fallback if pool exhausted
    if not candidate_ids:
        logger.warning(f"Cold start pool exhausted for user={user.id}. Resetting previous_ids.")
        excluded_ids = seen_ids | rated_ids
        candidate_ids = [
            tid for tid in candidates.keys()
            if tid not in excluded_ids
        ]

    if not candidate_ids:
        return _save_recommendation(
            user=user,
            strategy=strategy,
            scored_tracks=[],
            context={"reason": "no_candidates_after_filtering"},
        )

    tag_scores = score_tracks_by_tags(candidate_ids, user_tags)
    sim_scores = score_tracks_by_similarity(seed_ids, candidate_ids)

    matched_tags_map, similar_to_map = _precompute_reason_data(
        candidate_ids, seed_ids, user_tags
    )

    scored = []

    for track_id in candidate_ids:
        cs_score = candidates.get(track_id, 0)
        tag_score = tag_scores.get(track_id, 0)
        sim_score = sim_scores.get(track_id, 0)

        final_score = (
            (cs_score * 0.3) +
            (tag_score * 0.5) +
            (sim_score * 0.2)
        ) if user_tags else cs_score

        reason = {
            "strategy": "cold_start",
            "scores": {
                "cold_start": round(cs_score, 4),
                "tag": round(tag_score, 4),
                "similarity": round(sim_score, 4),
                "final": round(final_score, 4),
            },
            "signals": {
                "matched_tags": matched_tags_map.get(track_id, []),
                "similar_to": similar_to_map.get(track_id, []),
            }
        }

        scored.append((track_id, final_score, reason))

    scored.sort(key=lambda x: x[1], reverse=True)
    scored = apply_artist_diversity(scored, max_per_artist=2)
    scored = scored[:limit]

    return _save_recommendation(
        user=user,
        strategy=strategy,
        scored_tracks=scored,
        context={
            "seed_track_ids": seed_ids,
            "user_tag_count": len(user_tags),
            "candidate_count": len(candidate_ids),
            "excluded_count": len(excluded_ids),
        },
    )

def build_hybrid_recommendation(user, limit=20) -> Recommendation:
    user_tags = get_user_tag_profile(user)
    candidates = get_cold_start_candidates()
    seed_ids = get_seed_tracks(user, limit=50)

    strategy = Recommendation.RecommendationStrategy.HYBRID_START

    similar_track_ids = list(
        TrackSimilarity.objects
        .filter(from_track_id__in=seed_ids)
        .order_by("-score")
        .values_list("to_track_id", flat=True)[:500]
    )

    all_candidate_ids = set(candidates.keys()) | set(similar_track_ids)

    # 🔹 already heard
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

    # 🔹 already rated
    rated_ids = get_already_rated_track_ids(user)

    # 🔹 previously recommended (NEW)
    previous_ids = get_previously_recommended_track_ids(user, strategy)

    excluded_ids = heard_ids | rated_ids | previous_ids

    candidate_ids = [
        tid for tid in all_candidate_ids
        if tid not in excluded_ids
    ]

    # Fallback if pool exhausted
    if not candidate_ids:
        logger.warning(f"Hybrid pool exhausted for user={user.id}. Resetting previous_ids.")
        excluded_ids = heard_ids | rated_ids
        candidate_ids = [
            tid for tid in all_candidate_ids
            if tid not in excluded_ids
        ]

    if not candidate_ids:
        return _save_recommendation(
            user=user,
            strategy=strategy,
            scored_tracks=[],
            context={"reason": "no_candidates_after_filtering"},
        )

    tag_scores = score_tracks_by_tags(candidate_ids, user_tags)
    sim_scores = score_tracks_by_similarity(seed_ids, candidate_ids)

    matched_tags_map, similar_to_map = _precompute_reason_data(
        candidate_ids, seed_ids, user_tags
    )

    scored = []

    for track_id in candidate_ids:
        cs_score = candidates.get(track_id, 0)
        tag_score = tag_scores.get(track_id, 0)
        sim_score = sim_scores.get(track_id, 0)

        final_score = (
            (cs_score * 0.2) +
            (tag_score * 0.4) +
            (sim_score * 0.4)
        )

        reason = {
            "strategy": "hybrid",
            "scores": {
                "cold_start": round(cs_score, 4),
                "tag": round(tag_score, 4),
                "similarity": round(sim_score, 4),
                "final": round(final_score, 4),
            },
            "signals": {
                "matched_tags": matched_tags_map.get(track_id, []),
                "similar_to": similar_to_map.get(track_id, []),
            }
        }

        scored.append((track_id, final_score, reason))

    scored.sort(key=lambda x: x[1], reverse=True)
    scored = apply_artist_diversity(scored, max_per_artist=2)
    scored = scored[:limit]

    return _save_recommendation(
        user=user,
        strategy=strategy,
        scored_tracks=scored,
        context={
            "seed_track_ids": seed_ids[:10],
            "user_tag_count": len(user_tags),
            "candidate_count": len(candidate_ids),
            "excluded_count": len(excluded_ids),
        },
    )
# =========================================================
# SAVE TO DB
# =========================================================

def _save_recommendation(
    user,
    strategy: str,
    scored_tracks: list,
    context: dict = None,
) -> Recommendation:
    """
    Saves Recommendation + RecommendationItems to DB.
    scored_tracks = [(track_id, score, reason), ...]
    """
    with transaction.atomic():
        # Deactivate previous recommendations of same strategy
        Recommendation.objects.filter(
            user=user,
            strategy=strategy,
        ).update(is_active=False)

        rec = Recommendation.objects.create(
            user=user,
            type=Recommendation.RecommendationTypes.TRACK,
            strategy=strategy,
            status=Recommendation.RecommendationStatus.DRAFT,
            is_active=False,
            context=context,
        )

        items = [
            RecommendationItem(
                recommendation=rec,
                type=RecommendationItem.ItemTypes.TRACK,
                track_id=track_id,
                score=round(score, 4),
                rank=rank,
                reason=reason,
            )
            for rank, (track_id, score, reason) in enumerate(scored_tracks)
        ]

        RecommendationItem.objects.bulk_create(items, ignore_conflicts=True)

        rec.status = Recommendation.RecommendationStatus.READY
        rec.is_active = True  # ✅ activate after items saved
        rec.finished_at = timezone.now()
        rec.save(update_fields=["status", "is_active", "finished_at"])

    logger.info(
        f"Recommendation built: user={user.id} strategy={strategy} "
        f"items={len(items)}"
    )
    return rec


# =========================================================
# MAIN ENTRY POINT
# =========================================================

PREBUILD_THRESHOLD = 0.8


def get_or_build_recommendation(user, limit=20, force_rebuild=False) -> Recommendation:
    """
    Main entry point for recommendation retrieval.

    Behaviour:
    - If no active → build new
    - If ≥80% consumed → prebuild next in background
    - If 100% consumed → switch to prebuilt instantly
    - force_rebuild=True → always build fresh active
    """

    strategy = detect_strategy(user)

    # FORCE REBUILD
    if force_rebuild:
        logger.info(f"Force rebuilding recommendation: user={user.id}")
        if strategy == Recommendation.RecommendationStrategy.COLD_START:
            return build_cold_start_recommendation(user=user, limit=limit)
        return build_hybrid_recommendation(user=user, limit=limit)

    active = Recommendation.objects.active_for_user(
        user=user,
        strategy=strategy,
    )

    # No active → build fresh
    if not active:
        logger.info(f"No active recommendation. Building new for user={user.id}")
        if strategy == Recommendation.RecommendationStrategy.COLD_START:
            return build_cold_start_recommendation(user=user, limit=limit)
        return build_hybrid_recommendation(user=user, limit=limit)

    # Count usage
    total_items = RecommendationItem.objects.filter(
        recommendation=active
    ).count()

    if total_items == 0:
        return active

    rated_items = RecommendationFeedback.objects.filter(
        recommendation=active,
        user=user,
    ).count()

    ratio = rated_items / total_items

    # 100% consumed → SWITCH TO PREBUILT

    if rated_items >= total_items:
        logger.info(f"Recommendation fully consumed: user={user.id}")

        next_rec = (
            Recommendation.objects
            .filter(
                user=user,
                strategy=strategy,
                is_active=False,
                status=Recommendation.RecommendationStatus.READY,
            )
            .order_by("-created_at")
            .first()
        )

        if next_rec:
            with transaction.atomic():
                active.is_active = False
                active.save(update_fields=["is_active"])

                next_rec.is_active = True
                next_rec.save(update_fields=["is_active"])

            logger.info(
                f"Switched to prebuilt recommendation id={next_rec.id} user={user.id}"
            )
            return next_rec

        # fallback — no prebuilt ready
        logger.info(f"No prebuilt found. Building new for user={user.id}")

        active.is_active = False
        active.save(update_fields=["is_active"])

        if strategy == Recommendation.RecommendationStrategy.COLD_START:
            return build_cold_start_recommendation(user=user, limit=limit)

        return build_hybrid_recommendation(user=user, limit=limit)

    # ≥80% consumed → TRIGGER PREBUILD (async)
    if ratio >= PREBUILD_THRESHOLD:
        logger.info(
            f"Triggering async prebuild: user={user.id} ratio={ratio:.2f}"
        )

        from recomendations.tasks.recommendation_tasks import build_recommendation_task

        build_recommendation_task.delay(
            user.id,
            prebuild=True,
        )
    # still using current active
    logger.info(
        f"Returning active recommendation id={active.id} user={user.id}"
    )
    return active