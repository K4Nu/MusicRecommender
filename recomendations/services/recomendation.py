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