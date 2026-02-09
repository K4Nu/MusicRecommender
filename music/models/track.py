from django.db import models
from .album import Album
from .artist import Artist

class Track(models.Model):
    spotify_id = models.CharField(max_length=255,null=True,blank=True)
    name=models.CharField(max_length=255)
    artists=models.ManyToManyField(Artist, related_name='tracks')
    album=models.ForeignKey(Album, on_delete=models.CASCADE, related_name='tracks')
    duration_ms=models.IntegerField()
    popularity=models.IntegerField(null=True)
    preview_type = models.CharField(
        max_length=10,
        choices=[("audio", "Audio"), ("embed", "Embed")],
        default="embed"
    )
    preview_url=models.URLField(null=True,blank=True)
    image_url=models.URLField(null=True,blank=True)

    def __str__(self):
        return self.name