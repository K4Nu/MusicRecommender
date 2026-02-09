from django.db import models
from django.core.validators import MinValueValidator, MaxValueValidator
from .track import Track
from .tag import Tag

class TrackTagManager(models.Manager):
    def top_tags(self, track, limit=10, source="computed"):
        return (
            self.filter(
                track=track,
                source=source,
                tag__is_active=True
            )
            .select_related("tag")
            .order_by("-weight")[:limit]
        )

    def by_category(self, track, category, source="computed"):
        return (
            self.filter(
                track=track,
                tag__category=category,
                source=source,
                tag__is_active=True
            )
            .select_related("tag")
            .order_by("-weight")
        )


class TrackTag(models.Model):
    track = models.ForeignKey(
        Track,
        on_delete=models.CASCADE,
        related_name="track_tags"
    )

    tag = models.ForeignKey(
        Tag,
        on_delete=models.CASCADE,
        related_name="tagged_tracks"
    )

    weight = models.FloatField(
        validators=[MinValueValidator(0.0), MaxValueValidator(1.0)]
    )

    source = models.CharField(
        max_length=20,
        choices=[
            ("lastfm", "Last.fm raw"),
            ("audio", "Audio features"),
            ("artist", "Inherited from artist"),
            ("computed", "Computed canonical"),
            ("manual", "Manual"),
        ],
        default="lastfm"
    )

    raw_count = models.IntegerField(null=True, blank=True)
    is_active = models.BooleanField(default=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    objects = TrackTagManager()

    class Meta:
        unique_together = ("track", "tag", "source")
        ordering = ["-weight"]
        indexes = [
            models.Index(fields=["track", "-weight"]),
            models.Index(fields=["tag", "-weight"]),
            models.Index(fields=["source"]),
        ]

    def __str__(self):
        return f"{self.track.name} · {self.tag.name} ({self.weight:.2f})"
