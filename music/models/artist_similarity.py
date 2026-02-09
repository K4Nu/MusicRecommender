from .base_similarity import BaseSimiliarityManager,BaseSimilarity
from django.db import models

class ArtistSimilarityManager(BaseSimiliarityManager):
    from_field = "from_artist"
    to_field = "to_artist"

class ArtistSimilarity(BaseSimilarity):
    from_artist = models.ForeignKey(
        "Artist",
        on_delete=models.CASCADE,
        related_name="similar_to"
    )
    to_artist = models.ForeignKey(
        "Artist",
        on_delete=models.CASCADE,
        related_name="similar_from"
    )

    objects = ArtistSimilarityManager()

    class Meta:
        unique_together = ("from_artist", "to_artist", "source")
        ordering=['-score']
        indexes = [
            models.Index(fields=["from_artist", "source", "-score"]),
            models.Index(fields=["to_artist", "-score"]),
        ]
        verbose_name = "Artist Similarity"
        verbose_name_plural = "Artist Similarities"

    def get_from(self):
        return self.from_artist.name

    def get_to(self):
        return self.to_artist.name