from django.db import models
from users.models import Track, Artist
from .recommendation import Recommendation


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

    class Meta:
        ordering = ["rank"]
        unique_together = (
            ("recommendation", "track"),
            ("recommendation", "artist"),
        )

    def clean(self):
        """
        Ensure exactly one target is set depending on type.
        """
        if self.type == self.ItemTypes.TRACK and not self.track:
            raise ValueError("TRACK recommendation must have track set")

        if self.type == self.ItemTypes.ARTIST and not self.artist:
            raise ValueError("ARTIST recommendation must have artist set")

    def __str__(self) -> str:
        target = self.track or self.artist
        return f"RecommendationItem({self.type}, {target}, score={self.score:.2f})"
