from django.db import models
from django.db.models import Q
from .genre import Genre

class Artist(models.Model):
    spotify_id = models.CharField(max_length=255, blank=True, null=True, db_index=True)  # ← Added db_index
    name = models.CharField(max_length=255, db_index=True)  # ← Added db_index
    genres = models.ManyToManyField(Genre, related_name='artists')
    popularity = models.IntegerField(null=True, db_index=True)  # ← Added db_index (for sorting/filtering)
    image_url = models.URLField(null=True, blank=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["spotify_id"],
                condition=Q(spotify_id__isnull=False),
                name="unique_spotify_id_not_null"
            )
        ]
        indexes = [
            models.Index(fields=['spotify_id'], name='artist_spotify_idx'),  # ← Explicit index
            models.Index(fields=['name'], name='artist_name_idx'),
            models.Index(fields=['-popularity'], name='artist_pop_idx'),  # Descending for ORDER BY
        ]

    def __str__(self):
        return self.name