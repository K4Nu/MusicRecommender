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
