from django.db import models
from django.contrib.auth import get_user_model
from .recommendation import Recommendation
from .recommendation_item import RecommendationItem

User = get_user_model()

class RecommendationFeedback(models.Model):
    class Action(models.TextChoices):
        LIKE = "LIKE", "like"
        DISLIKE = "DISLIKE", "dislike"
        SKIP = "SKIP", "skip"
    user = models.ForeignKey(
        User,
        on_delete=models.CASCADE,
        related_name="recommendation_feedback",
    )
    recommendation = models.ForeignKey(
        Recommendation,
        on_delete=models.CASCADE,
        related_name="feedback",
    )
    recommendation_item = models.ForeignKey(
        RecommendationItem,
        on_delete=models.CASCADE,
        related_name="feedback",
    )
    action = models.CharField(
        max_length=20,
        choices=Action.choices,
    )
    created_at = models.DateTimeField(auto_now_add=True)
    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["user", "recommendation_item"],
                name="uniq_user_rec_item_fb",
            )
        ]
        indexes = [
            models.Index(
                fields=["user", "created_at"],
                name="recfb_user_created_idx",
            ),
            models.Index(
                fields=["recommendation", "action"],
                name="recfb_rec_action_idx",
            ),
            models.Index(
                fields=["recommendation_item"],
                name="recfb_item_idx",
            ),
        ]
    def __str__(self):
        return (
            f"Feedback(user={self.user_id}, "
            f"item={self.recommendation_item_id}, "
            f"action={self.action})"
        )