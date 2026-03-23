from celery import shared_task
from django.contrib.auth import get_user_model

from recomendations.models import Recommendation
from recomendations.services.recomendation import (
    build_cold_start_recommendation,
    build_hybrid_recommendation,
    detect_strategy,
    get_or_build_recommendation,
)

User = get_user_model()


@shared_task
def build_recommendation_task(
    user_id: int,
    force_rebuild: bool = False,
    prebuild: bool = False,
):
    try:
        user = User.objects.get(id=user_id)
    except User.DoesNotExist:
        return

    if not prebuild:
        get_or_build_recommendation(user, force_rebuild=force_rebuild)
        return

    # PREBUILD MODE
    strategy = detect_strategy(user)

    exists = Recommendation.objects.filter(
        user=user,
        strategy=strategy,
        is_active=False,
        status=Recommendation.RecommendationStatus.READY,
    ).exists()

    if exists:
        return

    if strategy == Recommendation.RecommendationStrategy.COLD_START:
        rec = build_cold_start_recommendation(user=user)
    else:
        rec = build_hybrid_recommendation(user=user)

    rec.is_active = False
    rec.save(update_fields=["is_active"])
