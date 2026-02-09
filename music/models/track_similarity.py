from .base_similarity import BaseSimiliarityManager, BaseSimilarity
from django.db import models

class TrackSimilarityManager(BaseSimiliarityManager):
    from_field = "from_track"
    to_field = "to_track"

class TrackSimilarity(BaseSimilarity):
    from_track = models.ForeignKey(
        "Track",
        on_delete=models.CASCADE,
        related_name="similar_to"
    )
    to_track = models.ForeignKey(
        "Track",
        on_delete=models.CASCADE,
        related_name="similar_from"
    )

    objects = TrackSimilarityManager()

    class Meta:
        unique_together = ("from_track", "to_track", "source")
        ordering = ["-score"]
        indexes = [
            models.Index(fields=["from_track", "source", "-score"]),
            models.Index(fields=["to_track", "-score"]),
        ]
        verbose_name = "Track Similarity"
        verbose_name_plural = "Track Similarities"

    def get_from(self):
        return self.from_track.name

    def get_to(self):
        return self.to_track.name

