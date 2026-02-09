from django.db import models
from .track import Track
from django.core.validators import MinValueValidator, MaxValueValidator
from datetime import timedelta
from django.utils import timezone

class TrackLastFMData(models.Model):
    """Raw cache of Last.fm API responses"""
    track = models.OneToOneField(
        Track,
        on_delete=models.CASCADE,
        related_name="lastfm_cache"
    )
    lastfm_name = models.CharField(max_length=255)
    lastfm_artist_name = models.CharField(max_length=255)
    lastfm_url = models.URLField(null=True, blank=True)
    mbid = models.CharField(max_length=64, null=True, blank=True, db_index=True)

    listeners = models.BigIntegerField(null=True, blank=True)
    playcount = models.BigIntegerField(null=True, blank=True)

    raw_tags = models.JSONField(default=list, blank=True)

    match_confidence = models.FloatField(
        default=1.0,
        validators=[MinValueValidator(0.0), MaxValueValidator(1.0)]
    )

    fetched_at = models.DateTimeField(auto_now=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'lastfm_track_cache'
        indexes = [
            models.Index(fields=['fetched_at']),
            models.Index(fields=['playcount']),
        ]

    def needs_refresh(self, days=30):
        return self.fetched_at < timezone.now() - timedelta(days=days)
