from django.contrib.auth import get_user_model
from django.db import models

User = get_user_model()

class RecommendationManager(models.Manager):
    def ready(self):
        return self.filter(
            status=self.model.RecommendationStatus.READY
        )
    def for_user(self, user):
        return self.filter(user=user)
    def active_for_user(self, user, strategy=None):
        qs = self.ready().filter(user=user, is_active=True)
        if strategy:
            qs = qs.filter(strategy=strategy)
        return qs.first()

class Recommendation(models.Model):
    class RecommendationTypes(models.TextChoices):
        TRACK = "TRACK", "track"
        ARTIST = "ARTIST", "artist"

    class RecommendationStrategy(models.TextChoices):
        COLD_START = "COLD_START", "cold-start"
        WARM_START = "WARM_START", "warm-start"
        HYBRID_START = "HYBRID_START", "hybrid-start"

    class RecommendationStatus(models.TextChoices):
        DRAFT = "DRAFT", "draft"
        READY = "READY", "ready"

    user = models.ForeignKey(
        User,
        on_delete=models.CASCADE,
        related_name="recommendations",
    )

    type = models.CharField(
        max_length=10,
        choices=RecommendationTypes.choices,
        default=RecommendationTypes.TRACK,
    )

    strategy = models.CharField(
        max_length=30,
        choices=RecommendationStrategy.choices,
    )

    status = models.CharField(
        max_length=10,
        choices=RecommendationStatus.choices,
        default=RecommendationStatus.DRAFT,
    )

    context = models.JSONField(
        null=True,
        blank=True,
        help_text="Context used to generate recommendation (seed items, params, etc.)",
    )
    is_active = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)
    finished_at = models.DateTimeField(null=True, blank=True)

    objects = RecommendationManager()

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(
                fields=["user", "status", "is_active", "strategy"],
                name="rec_user_stat_act_str_idx",
            )
        ]

    def __str__(self) -> str:
        return (
            f"Recommendation(user={self.user_id}, "
            f"type={self.type}, strategy={self.strategy}, status={self.status})"
        )
