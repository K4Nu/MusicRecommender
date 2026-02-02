from django.db import models
from users.models import Track


class ColdStartTrack(models.Model):
    """
    Stores globally recommended tracks for cold-start users
    (users without enough listening history).
    """

    class Source(models.TextChoices):
        SPOTIFY_GLOBAL = "SPOTIFY_GLOBAL", "Spotify Global Top"
        SPOTIFY_VIRAL = "SPOTIFY_VIRAL", "Spotify Viral"
        LASTFM_GLOBAL = "LASTFM_GLOBAL", "Last.fm Global Top"

    track = models.ForeignKey(
        Track,
        on_delete=models.CASCADE,
        related_name="cold_start_entries",
    )

    source = models.CharField(
        max_length=32,
        choices=Source.choices,
        db_index=True,
    )

    # Position in the chart (1..N)
    rank = models.PositiveIntegerField()

    # Optional normalized score (for hybrid / ranking logic)
    # None = raw order from provider
    score = models.FloatField(
        null=True,
        blank=True,
        help_text="Optional normalized score used for ranking",
    )

    # Updated on each refresh
    fetched_at = models.DateTimeField(
        auto_now=True,
        help_text="Last time this entry was refreshed from the source",
    )

    class Meta:
        unique_together = ("track", "source")
        ordering = ["source", "rank"]
        indexes = [
            models.Index(fields=["source", "rank"]),
            models.Index(fields=["track"]),
        ]

    def __str__(self) -> str:
        return f"[{self.source}] #{self.rank} – {self.track.name}"
