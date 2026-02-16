import logging
from recomendations.models import UserTag, RecommendationFeedback
from music.models import TrackTag
from recomendations.services.tag_filter import MIN_TAG_USAGE_COUNT, BLOCKED_TAG_NAMES

logger = logging.getLogger(__name__)

# Weight deltas per action
WEIGHT_DELTAS = {
    RecommendationFeedback.Action.LIKE:    +0.2,
    RecommendationFeedback.Action.SKIP:    -0.05,
    RecommendationFeedback.Action.DISLIKE: -0.3,
}

CONFIDENCE_DELTAS = {
    RecommendationFeedback.Action.LIKE:    +0.1,
    RecommendationFeedback.Action.SKIP:     0.0,
    RecommendationFeedback.Action.DISLIKE: -0.1,
}


def apply_feedback_to_tags(user, item, action: str):
    """
    Update UserTag weights based on feedback action.
    Uses track tags of the interacted item as signal.
    Clamps weight and confidence to [0.0, 1.0].
    """
    weight_delta = WEIGHT_DELTAS.get(action, 0)
    confidence_delta = CONFIDENCE_DELTAS.get(action, 0)

    if weight_delta == 0:
        return

    track_tags = (
        TrackTag.objects
        .filter(
            track=item.track,
            is_active=True,
            tag__total_usage_count__gte=MIN_TAG_USAGE_COUNT,
        )
        .exclude(tag__normalized_name__in=BLOCKED_TAG_NAMES)
        .select_related("tag")
    )

    if not track_tags.exists():
        logger.warning(f"No tags for track={item.track_id}, skipping feedback")
        return

    for tt in track_tags:
        user_tag, created = UserTag.objects.get_or_create(
            user=user,
            tag=tt.tag,
            source="feedback",
            defaults={
                "weight": max(0.0, min(1.0, tt.weight + weight_delta)),
                "confidence": max(0.0, min(1.0, 0.5 + confidence_delta)),
                "is_active": True,
            },
        )

        if not created:
            user_tag.weight = max(0.0, min(1.0, user_tag.weight + weight_delta))
            user_tag.confidence = max(0.0, min(1.0, user_tag.confidence + confidence_delta))
            user_tag.save(update_fields=["weight", "confidence"])

    # Recompute computed aggregate from all sources
    UserTag.objects.recompute_computed(user)

    logger.info(
        f"Feedback applied: user={user.id} action={action} "
        f"track={item.track_id} tags={track_tags.count()}"
    )