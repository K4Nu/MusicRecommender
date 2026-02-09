from django.contrib.auth import get_user_model
from django.db import models
from django.db.models import Avg
from django.core.validators import MinValueValidator, MaxValueValidator
from django.contrib.auth import get_user_model
from music.models import Tag

User=get_user_model()

class UserTagManager(models.Manager):
    def active(self):
        return self.filter(is_active=True)

    def for_user(self, user, source=None):
        qs = self.active().filter(user=user)
        if source:
            qs = qs.filter(source=source)
        return qs.select_related("tag")

    def top_tags(self, user, limit=20, source="computed"):
        return (
            self.for_user(user, source=source)
            .order_by("-weight")[:limit]
        )

    def recompute_computed(self, user):
        """
        Buduje FINALNY profil gustu usera
        na podstawie wszystkich źródeł ≠ computed
        """
        raw = (
            self.filter(user=user, is_active=True)
            .exclude(source="computed")
            .values("tag")
            .annotate(
                weight=Avg("weight"),
                confidence=Avg("confidence"),
            )
        )

        computed = []

        for row in raw:
            obj, _ = self.update_or_create(
                user=user,
                tag_id=row["tag"],
                source="computed",
                defaults={
                    "weight": row["weight"],
                    "confidence": row["confidence"],
                    "is_active": True,
                },
            )
            computed.append(obj)

        return computed

class UserTag(models.Model):
    """
    User musical taste representation.
    """

    SOURCE_CHOICES = [
        ("onboarding", "Onboarding"),
        ("spotify", "Spotify top items"),
        ("listening", "Listening history"),
        ("lastfm", "Last.fm"),
        ("computed", "Computed aggregate"),
    ]

    user = models.ForeignKey(
        User,
        on_delete=models.CASCADE,
        related_name="user_tags"
    )

    tag = models.ForeignKey(
        Tag,
        on_delete=models.CASCADE,
        related_name="tagged_users"
    )

    # Siła preferencji (0–1)
    weight = models.FloatField(
        validators=[MinValueValidator(0.0), MaxValueValidator(1.0)]
    )

    # Jak bardzo ufamy temu sygnałowi
    confidence = models.FloatField(
        default=1.0,
        validators=[MinValueValidator(0.0), MaxValueValidator(1.0)]
    )

    source = models.CharField(
        max_length=20,
        choices=SOURCE_CHOICES,
        db_index=True
    )

    is_active = models.BooleanField(default=True)

    updated_at = models.DateTimeField(auto_now=True)
    created_at = models.DateTimeField(auto_now_add=True)

    objects = UserTagManager()

    class Meta:
        unique_together = ("user", "tag", "source")
        ordering = ["-weight"]
        indexes = [
            models.Index(fields=["user", "source", "-weight"]),
            models.Index(fields=["tag", "-weight"]),
        ]

    def __str__(self):
        return f"{self.user.email} · {self.tag.name} · {self.source} ({self.weight:.2f})"