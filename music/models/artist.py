from django.db import models
from django.db.models import Q
from .genre import Genre

class Artist(models.Model):
    spotify_id = models.CharField(max_length=255,blank=True,null=True)
    name=models.CharField(max_length=255)
    genres=models.ManyToManyField(Genre, related_name='artists')
    popularity=models.IntegerField(null=True)
    image_url=models.URLField(null=True,blank=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["spotify_id"],
                condition=Q(spotify_id__isnull=False),
                name="unique_spotify_id_not_null"
            )
        ]

    def __str__(self):
        return self.name