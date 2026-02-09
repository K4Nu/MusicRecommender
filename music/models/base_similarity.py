from django.db import models
from django.db.models import Q
from django.core.validators import MinValueValidator, MaxValueValidator

class BaseSimiliarityManager(models.Manager):
    """
        Base manager for similarity models.
        Subclasses MUST define:
        - from_field
        - to_field
        """
    from_field:str|None = None
    to_field:str|None = None

    def _validate_fields(self):
        if not self.from_field or not self.to_field:
            raise NotImplementedError(
                "from_field and to_field must be defined in subclass"
            )

    def top_similiar(self, obj, source=None,limit=20):
        """
                Get top N similar items for a single object.
                """
        self._validate_fields()
        qs = self.filter(**{self.from_field: obj})
        if source:
            qs=qs.filter(source=source)

        return (qs.select_related(self.to_field).order_by("-score")[:limit]
                )

    def batch_similiar(self,objects,source=None):
        """
        Get similarities for MANY objects.

        IMPORTANT:
        - This does NOT apply per-object limits.
        - Limit per object MUST be applied in Python or SQL window functions.
        """
        self._validate_fields()

        filter_key=f'{self.from_field}__in'
        qs=self.filter(**{filter_key: objects})
        if source:
            qs.filter(source=source)

        return (
            qs.select_related(self.to_field,self.to_field).order_by(self.from_field,"-score")
        )

    def for_object(self, obj):
        """
        Get all similarity records where obj appears
        either as 'from' or 'to'.
        Intended for debugging / introspection.
        """
        self._validate_fields()
        from_q=Q(**{self.from_field:obj})
        to_q=Q(**{self.to_field:obj})
        return self.filter(from_q|to_q)

class BaseSimilarity(models.Model):
    """
    Abstract base model for similarity records.
    """
    score=models.FloatField(
        validators=[MinValueValidator(0.0), MaxValueValidator(1.0)],
        db_index=True
    )

    source = models.CharField(
        max_length=20,
        choices=[
            ("lastfm", "Last.fm"),
            ("tags", "Tag-based"),
            ("audio", "Audio-based"),
            ("hybrid", "Hybrid"),
        ],
        default="tags"
    )

    score_breakdown=models.JSONField(
        null=True,
        blank=True,
        help_text='Explainability data, e.g. {"tag_sim": 0.7, "audio_sim": 0.5}'
    )

    computed_at=models.DateTimeField(auto_now=True)
    created_at=models.DateTimeField(auto_now_add=True)

    class Meta:
        abstract = True
        ordering=['-score']

    def __str__(self):
        return f"{self.get_from()} → {self.get_to()} ({self.score:.2f})"

    def get_from(self):
        raise NotImplementedError

    def get_to(self):
        raise NotImplementedError