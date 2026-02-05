from django.db import models
from django.contrib.auth import get_user_model
from .cold_start_source import ColdStartTrack
from django.db.models import Count, Q

User = get_user_model()


class OnboardingEvent(models.Model):
    """
    Immutable event log of onboarding interactions.
    One row = one user reaction to one cold start track.
    """

    class Action(models.TextChoices):
        LIKE = "LIKE", "Like"
        SKIP = "SKIP", "Skip"
        NOT_MY_STYLE = "NOT_MY_STYLE", "Not My Style"

    user = models.ForeignKey(
        User,
        on_delete=models.CASCADE,
        related_name="onboarding_events",
    )

    cold_start_track = models.ForeignKey(
        ColdStartTrack,
        on_delete=models.CASCADE,
        related_name="onboarding_events",
    )

    action = models.CharField(
        max_length=20,
        choices=Action.choices,
    )

    # Order shown to user (UX + analytics)
    position = models.PositiveSmallIntegerField(
        null=True,
        blank=True,
        help_text="Order in onboarding flow (1..N)",
    )

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["user", "cold_start_track"],
                name="unique_onboarding_event_per_track",
            )
        ]
        indexes = [
            models.Index(fields=["user", "created_at"]),
            models.Index(fields=["user", "action"]),
        ]
        ordering = ["created_at"]

    @classmethod
    def get_user_stats(cls, user):
        """
        Single source of truth for onboarding progress.
        """
        return cls.objects.filter(user=user).aggregate(
            likes_count=Count("id", filter=Q(action=cls.Action.LIKE)),
            skips_count=Count("id", filter=Q(action=cls.Action.SKIP)),
            not_my_style_count=Count(
                "id", filter=Q(action=cls.Action.NOT_MY_STYLE)
            ),
            total_count=Count("id"),
        )

    def save(self, *args, **kwargs):
        """
        Auto-assign position if not provided.
        Safe enough for V1 (no bulk_create here).
        """
        if self.position is None:
            last_pos = (
                OnboardingEvent.objects
                .filter(user=self.user)
                .aggregate(models.Max("position"))
                .get("position__max")
            )
            self.position = (last_pos or 0) + 1

        super().save(*args, **kwargs)

    # ---------- helpers ----------
    @property
    def is_positive(self) -> bool:
        return self.action == self.Action.LIKE

    @property
    def is_negative(self) -> bool:
        return self.action in {self.Action.SKIP, self.Action.NOT_MY_STYLE}

    @property
    def track(self):
        return self.cold_start_track.track