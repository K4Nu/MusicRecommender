import logging
from django.db import transaction
from recomendations.models import UserTag, RecommendationFeedback
from music.models import TrackTag
from recomendations.services.tag_filter import (
    MIN_TAG_USAGE_COUNT,
    BLOCKED_TAG_NAMES,
)

logger = logging.getLogger(__name__)


WEIGHT_DELTAS = {
    RecommendationFeedback.Action.LIKE:    +0.10,
    RecommendationFeedback.Action.SKIP:     0.00,
    RecommendationFeedback.Action.DISLIKE: -0.15,
}

CONFIDENCE_DELTAS = {
    RecommendationFeedback.Action.LIKE:    +0.05,
    RecommendationFeedback.Action.SKIP:     0.00,
    RecommendationFeedback.Action.DISLIKE: -0.05,
}


def clamp(value, min_value=0.0, max_value=1.0):
    return max(min_value, min(max_value, value))


@transaction.atomic
def apply_feedback_to_tags(user, item, action: str):
    """
    Stabilna adaptacja gustu użytkownika.

    - Wpływ ważony przez wagę tagu utworu
    - Łagodny start dla nowych tagów
    - Brak recompute w request cycle
    """

    weight_delta = WEIGHT_DELTAS.get(action, 0.0)
    confidence_delta = CONFIDENCE_DELTAS.get(action, 0.0)

    if weight_delta == 0.0 and confidence_delta == 0.0:
        return

    track_tags_qs = (
        TrackTag.objects
        .filter(
            track=item.track,
            is_active=True,
            tag__total_usage_count__gte=MIN_TAG_USAGE_COUNT,
        )
        .exclude(tag__normalized_name__in=BLOCKED_TAG_NAMES)
        .select_related("tag")
    )

    # unikamy podwójnego zapytania .exists()
    track_tags = list(track_tags_qs)
    if not track_tags:
        logger.warning(
            f"No tags for track={item.track_id}, skipping feedback"
        )
        return

    updated_count = 0

    for tt in track_tags:
        influence = weight_delta * tt.weight
        confidence_influence = confidence_delta * tt.weight

        user_tag, created = UserTag.objects.get_or_create(
            user=user,
            tag=tt.tag,
            source="feedback",
            defaults={
                # 🔹 łagodny start (nie 0.6 jak wcześniej)
                "weight": clamp(0.3 * tt.weight + influence),
                "confidence": clamp(0.4 + confidence_influence),
                "is_active": True,
            },
        )

        if not created:
            user_tag.weight = clamp(user_tag.weight + influence)
            user_tag.confidence = clamp(
                user_tag.confidence + confidence_influence
            )
            user_tag.save(update_fields=["weight", "confidence"])

        updated_count += 1

    logger.info(
        f"Feedback applied (stable v2): "
        f"user={user.id} action={action} "
        f"track={item.track_id} tags_updated={updated_count}"
    )
