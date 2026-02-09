from django.db import models
from .artist import Artist
from .tag import Tag
from django.core.validators import MinValueValidator, MaxValueValidator

class ArtistTagManager(models.Manager):
    def top_tags(self, artist, limit=10, source="computed"):
        return (
            self.filter(
                artist=artist,
                source=source,
                tag__is_active=True
            )
            .select_related("tag")
            .order_by("-weight")[:limit]
        )

    def by_category(self, artist, category, source="computed"):
        return (
            self.filter(
                artist=artist,
                tag__category=category,
                source=source,
                tag__is_active=True
            )
            .select_related("tag")
            .order_by("-weight")
        )

class ArtistTag(models.Model):
    """
    Normalized tag assignments for artists.
    Each artist has MANY tags with weights → vector representation.
    """
    artist = models.ForeignKey(
        Artist,
        on_delete=models.CASCADE,
        related_name="artist_tags"
    )

    tag = models.ForeignKey(
        Tag,
        on_delete=models.CASCADE,
        related_name="tagged_artists"
    )

    # Final normalized weight used in similarity (0–1)
    weight = models.FloatField(
        validators=[MinValueValidator(0.0), MaxValueValidator(1.0)]
    )

    source = models.CharField(
        max_length=20,
        choices=[
            ("lastfm", "Last.fm raw"),
            ("spotify", "Spotify genres"),
            ("computed", "Computed canonical"),
            ("manual", "Manual"),
        ],
        default="lastfm"
    )

    # Raw value from source (e.g. Last.fm tag count)
    raw_count = models.IntegerField(null=True, blank=True)

    is_active = models.BooleanField(default=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    objects = ArtistTagManager()

    class Meta:
        unique_together = ("artist", "tag", "source")
        ordering = ['-weight']
        indexes = [
            models.Index(fields=['artist', '-weight']),
            models.Index(fields=['tag', '-weight']),
            models.Index(fields=['source']),
        ]

    def __str__(self):
        return f"{self.artist.name} · {self.tag.name} ({self.weight:.2f})"