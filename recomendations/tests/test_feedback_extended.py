"""
Extended tests for recomendations/services/feedback_service.py

Covers: clamp() edge cases, apply_feedback_to_tags no-tags path,
        weight/confidence clamping stays within [0, 1] bounds.
"""
import pytest
from recomendations.services.feedback_service import clamp, apply_feedback_to_tags
from recomendations.models import (
    RecommendationFeedback,
    UserTag,
    Recommendation,
    RecommendationItem,
)
from users.models import User
from music.models import Artist, Album, Track, Tag, TrackTag


# =========================================================
# FIXTURES
# =========================================================

@pytest.fixture
def user(db):
    u = User.objects.create_user(email="fb_ext@example.com", password="testpass")
    u.profile.onboarding_completed = True
    u.profile.save()
    return u


@pytest.fixture
def artist(db):
    return Artist.objects.create(name="FB Ext Artist", spotify_id="fb_ext_artist")


@pytest.fixture
def album(db, artist):
    a = Album.objects.create(name="FB Ext Album", spotify_id="fb_ext_album")
    a.artists.add(artist)
    return a


@pytest.fixture
def track(db, artist, album):
    t = Track.objects.create(
        name="FB Ext Track",
        spotify_id="fb_ext_track",
        album=album,
        duration_ms=200000,
    )
    t.artists.add(artist)
    return t


def _make_item(user, track):
    rec = Recommendation.objects.create(
        user=user,
        type=Recommendation.RecommendationTypes.TRACK,
        strategy=Recommendation.RecommendationStrategy.COLD_START,
        status=Recommendation.RecommendationStatus.READY,
        is_active=True,
    )
    return RecommendationItem.objects.create(
        recommendation=rec,
        type=RecommendationItem.ItemTypes.TRACK,
        track=track,
        score=0.8,
        rank=0,
        reason={},
    )


# =========================================================
# 1. clamp()
# =========================================================

class TestClamp:

    def test_value_in_range_is_unchanged(self):
        assert clamp(0.5) == 0.5

    def test_value_below_zero_clamped_to_zero(self):
        assert clamp(-0.1) == 0.0

    def test_value_above_one_clamped_to_one(self):
        assert clamp(1.5) == 1.0

    def test_exactly_zero_is_unchanged(self):
        assert clamp(0.0) == 0.0

    def test_exactly_one_is_unchanged(self):
        assert clamp(1.0) == 1.0

    def test_large_negative_clamped(self):
        assert clamp(-999) == 0.0

    def test_large_positive_clamped(self):
        assert clamp(999) == 1.0

    def test_custom_min_bound(self):
        assert clamp(0, min_value=1, max_value=10) == 1

    def test_custom_max_bound(self):
        assert clamp(15, min_value=1, max_value=10) == 10

    def test_custom_in_range(self):
        assert clamp(5, min_value=1, max_value=10) == 5


# =========================================================
# 2. apply_feedback_to_tags — edge cases
# =========================================================

class TestApplyFeedbackEdgeCases:

    def test_no_tags_returns_early_no_user_tags_created(self, user, track):
        """Track has no active tags → no UserTags should be created."""
        item = _make_item(user, track)
        apply_feedback_to_tags(
            user=user, item=item, action=RecommendationFeedback.Action.LIKE
        )
        assert UserTag.objects.filter(user=user, source="feedback").count() == 0

    def test_inactive_tags_only_returns_early(self, db, user, track):
        """Track has tags but all inactive → no UserTags created."""
        tag = Tag.objects.create(
            name="jazz ext", category="genre", total_usage_count=5000
        )
        TrackTag.objects.create(
            track=track, tag=tag, weight=0.8, source="lastfm", is_active=False
        )
        item = _make_item(user, track)
        apply_feedback_to_tags(
            user=user, item=item, action=RecommendationFeedback.Action.LIKE
        )
        assert UserTag.objects.filter(user=user, source="feedback").count() == 0

    def test_weight_never_exceeds_one_after_many_likes(self, db, user, track):
        """Repeated LIKEs on max-weight tag should never push weight above 1.0."""
        tag = Tag.objects.create(
            name="metal ext", category="genre", total_usage_count=5000
        )
        TrackTag.objects.create(
            track=track, tag=tag, weight=1.0, source="lastfm", is_active=True
        )
        item = _make_item(user, track)

        for _ in range(30):
            apply_feedback_to_tags(
                user=user, item=item, action=RecommendationFeedback.Action.LIKE
            )

        user_tag = UserTag.objects.get(user=user, tag=tag, source="feedback")
        assert user_tag.weight <= 1.0
        assert user_tag.confidence <= 1.0

    def test_weight_never_below_zero_after_many_dislikes(self, db, user, track):
        """Repeated DISLIKEs should never push weight below 0.0."""
        tag = Tag.objects.create(
            name="classical ext", category="genre", total_usage_count=5000
        )
        TrackTag.objects.create(
            track=track, tag=tag, weight=1.0, source="lastfm", is_active=True
        )
        item = _make_item(user, track)

        for _ in range(30):
            apply_feedback_to_tags(
                user=user, item=item, action=RecommendationFeedback.Action.DISLIKE
            )

        user_tag = UserTag.objects.get(user=user, tag=tag, source="feedback")
        assert user_tag.weight >= 0.0
        assert user_tag.confidence >= 0.0

    def test_skip_creates_no_user_tags(self, db, user, track):
        """SKIP action (delta=0) should exit early without touching UserTags."""
        tag = Tag.objects.create(
            name="pop ext", category="genre", total_usage_count=5000
        )
        TrackTag.objects.create(
            track=track, tag=tag, weight=0.8, source="lastfm", is_active=True
        )
        item = _make_item(user, track)
        apply_feedback_to_tags(
            user=user, item=item, action=RecommendationFeedback.Action.SKIP
        )
        assert UserTag.objects.filter(user=user, source="feedback").count() == 0

    def test_like_creates_user_tag_with_positive_weight(self, db, user, track):
        """LIKE on a tagged track → UserTag created with weight > 0."""
        tag = Tag.objects.create(
            name="blues ext", category="genre", total_usage_count=5000
        )
        TrackTag.objects.create(
            track=track, tag=tag, weight=0.8, source="lastfm", is_active=True
        )
        item = _make_item(user, track)
        apply_feedback_to_tags(
            user=user, item=item, action=RecommendationFeedback.Action.LIKE
        )
        user_tag = UserTag.objects.get(user=user, tag=tag, source="feedback")
        assert user_tag.weight > 0

    def test_dislike_creates_user_tag_with_lower_weight_than_like(self, db, track):
        """DISLIKE weight < LIKE weight for the same track tag."""
        user_like = User.objects.create_user(
            email="fb_like@example.com", password="pass"
        )
        user_dislike = User.objects.create_user(
            email="fb_dislike@example.com", password="pass"
        )
        tag = Tag.objects.create(
            name="hip hop ext", category="genre", total_usage_count=5000
        )
        TrackTag.objects.create(
            track=track, tag=tag, weight=0.8, source="lastfm", is_active=True
        )
        item_like = _make_item(user_like, track)
        item_dislike = _make_item(user_dislike, track)

        apply_feedback_to_tags(
            user=user_like, item=item_like, action=RecommendationFeedback.Action.LIKE
        )
        apply_feedback_to_tags(
            user=user_dislike,
            item=item_dislike,
            action=RecommendationFeedback.Action.DISLIKE,
        )

        like_tag = UserTag.objects.get(user=user_like, tag=tag, source="feedback")
        dislike_tag = UserTag.objects.get(user=user_dislike, tag=tag, source="feedback")
        assert like_tag.weight > dislike_tag.weight
