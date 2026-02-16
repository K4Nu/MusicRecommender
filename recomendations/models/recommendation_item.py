from django.db import models
from django.db.models import Q
from music.models import Track, Artist  # ✅ fixed import
from .recommendation import Recommendation


class RecommendationItemManager(models.Manager):

    def for_recommendation(self, recommendation):
        return (
            self.filter(recommendation=recommendation)
            .select_related("track__album", "artist")
            .prefetch_related(
                "track__artists",
                "track__track_tags__tag",
            )
        )

    def top_n(self, recommendation, limit=3):
        return (
            self.for_recommendation(recommendation)
            .order_by("rank")[:limit]
        )

    def lighter(self, recommendation, start_rank=5, limit=3):
        return (
            self.for_recommendation(recommendation)
            .filter(rank__gte=start_rank)
            .order_by("rank")[:limit]
        )


class RecommendationItem(models.Model):
    class ItemTypes(models.TextChoices):
        TRACK = "TRACK", "track"
        ARTIST = "ARTIST", "artist"

    recommendation = models.ForeignKey(
        Recommendation,
        on_delete=models.CASCADE,
        related_name="items",
    )

    type = models.CharField(
        max_length=10,
        choices=ItemTypes.choices,
    )

    track = models.ForeignKey(
        Track,
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="recommended_items",
    )

    artist = models.ForeignKey(
        Artist,
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="recommended_items",
    )

    score = models.FloatField(
        help_text="Final recommendation score (0–1)",
    )

    rank = models.PositiveIntegerField(
        help_text="Position in recommendation list (0 = best)",
    )

    reason = models.JSONField(
        null=True,
        blank=True,
        help_text="Why this item was recommended (signals, similarities, etc.)",
    )

    created_at = models.DateTimeField(auto_now_add=True)

    objects = RecommendationItemManager()

    class Meta:
        ordering = ["rank"]
        constraints = [
            models.UniqueConstraint(
                fields=["recommendation", "track"],
                condition=Q(track__isnull=False),
                name="unique_recommendation_track",
            ),
            models.UniqueConstraint(
                fields=["recommendation", "artist"],
                condition=Q(artist__isnull=False),
                name="unique_recommendation_artist",
            ),
        ]
        indexes = [
            models.Index(
                fields=["recommendation", "rank"],
                name="rec_item_rec_rank_idx",
            ),
        ]

    def clean(self):
        if self.type == self.ItemTypes.TRACK and not self.track:
            raise ValueError("TRACK recommendation must have track set")
        if self.type == self.ItemTypes.ARTIST and not self.artist:
            raise ValueError("ARTIST recommendation must have artist set")

    def __str__(self) -> str:
        target = self.track or self.artist
        return f"RecommendationItem({self.type}, {target}, score={self.score:.2f})"