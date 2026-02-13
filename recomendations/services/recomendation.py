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
from music.models import Track, TrackSimilarity
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

