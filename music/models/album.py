from django.db import models
from .artist import Artist

class Album(models.Model):
    class AlbumTypes(models.TextChoices):
        ALBUM = "album", "Album"
        SINGLE = "single", "Single"
        COMPILATION = "compilation", "Compilation"

    spotify_id = models.CharField(max_length=255,blank=True,null=True)
    name = models.CharField(max_length=255)

    album_type = models.CharField(
        max_length=20,
        choices=AlbumTypes.choices,
        default=AlbumTypes.ALBUM
    )

    release_date = release_date = models.DateField(null=True, blank=True)

    artists = models.ManyToManyField(
        Artist,
        related_name="albums"
    )

    image_url = models.URLField(null=True, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return self.name