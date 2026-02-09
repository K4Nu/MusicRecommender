from django.db import models
from .artist import Artist
from django.core.validators import MinValueValidator, MaxValueValidator
from datetime import timedelta
from django.utils import timezone

class ArtistLastFMData(models.Model):
    """Raw cache of Last.fm API responses"""
    artist = models.OneToOneField(
        Artist,
        on_delete=models.CASCADE,
        related_name="lastfm_cache"
    )
    lastfm_name = models.CharField(max_length=255)
    lastfm_url = models.URLField(null=True, blank=True)
    mbid = models.CharField(max_length=64, null=True, blank=True, db_index=True)

    listeners = models.BigIntegerField(null=True, blank=True)
    playcount = models.BigIntegerField(null=True, blank=True)

    # Raw tags from Last.fm (not normalized yet)
    raw_tags = models.JSONField(default=list, blank=True)
    # [{"name": "indie rock", "count": 100}, ...]

    bio_summary = models.TextField(null=True, blank=True)
    image_url = models.URLField(null=True, blank=True)

    match_confidence = models.FloatField(
        default=1.0,
        validators=[MinValueValidator(0.0), MaxValueValidator(1.0)]
    )

    fetched_at = models.DateTimeField(auto_now=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'lastfm_artist_cache'
        indexes = [
            models.Index(fields=['fetched_at']),
            models.Index(fields=['playcount']),
        ]

    def needs_refresh(self, days=30):
        return self.fetched_at < timezone.now() - timedelta(days=days)

    def __str__(self):
        return self.artist.name