"""
Testy jednostkowe systemu rekomendacyjnego MusicRecommender.

Uruchomienie:
    pytest recomendations/tests/test_recommendation_engine.py -v

Wymagane:
    pip install pytest-django
"""

import pytest
from django.utils import timezone
from datetime import timedelta

from users.models import User, SpotifyAccount, ListeningHistory
from music.models import Artist, Album, Track, Tag, TrackTag
from recomendations.models import (
    Recommendation,
    RecommendationItem,
    RecommendationFeedback,
    UserTag,
    ColdStartTrack,
)
from recomendations.services.recomendation import detect_strategy
from recomendations.services.feedback_service import apply_feedback_to_tags


# =========================================================
# FIXTURES
# =========================================================

@pytest.fixture
def user(db):
    u = User.objects.create_user(
        email="test@example.com",
        password="testpass123",
    )
    u.profile.onboarding_completed = True
    u.profile.save()
    return u


@pytest.fixture
def user_2(db):
    return User.objects.create_user(
        email="test2@example.com",
        password="testpass123",
    )


@pytest.fixture
def artist(db):
    return Artist.objects.create(name="Test Artist", spotify_id="artist_1")


@pytest.fixture
def artist_2(db):
    return Artist.objects.create(name="Test Artist 2", spotify_id="artist_2")


@pytest.fixture
def album(db, artist):
    a = Album.objects.create(
        name="Test Album",
        spotify_id="album_1",
    )
    a.artists.add(artist)
    return a


@pytest.fixture
def track(db, artist, album):
    t = Track.objects.create(
        name="Test Track",
        spotify_id="track_1",
        album=album,
        duration_ms=200000,
        popularity=50,
    )
    t.artists.add(artist)
    return t


@pytest.fixture
def track_2(db, artist_2, album):
    t = Track.objects.create(
        name="Test Track 2",
        spotify_id="track_2",
        album=album,
        duration_ms=180000,
        popularity=40,
    )
    t.artists.add(artist_2)
    return t


@pytest.fixture
def tags(db):
    rock = Tag.objects.create(
        name="rock",
        normalized_name="rock",
        category="genre",
        total_usage_count=5000,
    )
    pop = Tag.objects.create(
        name="pop",
        normalized_name="pop",
        category="genre",
        total_usage_count=8000,
    )
    jazz = Tag.objects.create(
        name="jazz",
        normalized_name="jazz",
        category="genre",
        total_usage_count=3000,
    )
    return {"rock": rock, "pop": pop, "jazz": jazz}


@pytest.fixture
def track_with_tags(track, tags):
    TrackTag.objects.create(track=track, tag=tags["rock"], weight=0.9, source="lastfm", is_active=True)
    TrackTag.objects.create(track=track, tag=tags["pop"], weight=0.5, source="lastfm", is_active=True)
    return track


def _make_spotify(user):
    return SpotifyAccount.objects.create(
        user=user,
        spotify_id=f"sp_{user.id}",
        access_token="token",
        refresh_token="refresh",
        expires_at=timezone.now() + timedelta(hours=1),
    )


def _make_recommendation(user, track):
    rec = Recommendation.objects.create(
        user=user,
        type=Recommendation.RecommendationTypes.TRACK,
        strategy=Recommendation.RecommendationStrategy.COLD_START,
        status=Recommendation.RecommendationStatus.READY,
        is_active=True,
    )
    item = RecommendationItem.objects.create(
        recommendation=rec,
        type=RecommendationItem.ItemTypes.TRACK,
        track=track,
        score=0.8,
        rank=0,
        reason={},
    )
    return rec, item


# =========================================================
# 1. DETEKCJA STRATEGII
# =========================================================

class TestDetectStrategy:

    def test_cold_start_no_spotify(self, user):
        assert detect_strategy(user) == Recommendation.RecommendationStrategy.COLD_START

    def test_warm_start_low_signals(self, user, track):
        _make_spotify(user)
        for i in range(5):
            ListeningHistory.objects.create(
                user=user,
                track=track,
                played_at=timezone.now() - timedelta(hours=i),
            )
        assert detect_strategy(user) == Recommendation.RecommendationStrategy.WARM_START

    def test_hybrid_start_enough_signals(self, user, track):
        _make_spotify(user)
        for i in range(25):
            ListeningHistory.objects.create(
                user=user,
                track=track,
                played_at=timezone.now() - timedelta(hours=i),
            )
        assert detect_strategy(user) == Recommendation.RecommendationStrategy.HYBRID_START

    def test_warm_start_zero_history(self, user):
        _make_spotify(user)
        assert detect_strategy(user) == Recommendation.RecommendationStrategy.WARM_START


# =========================================================
# 2. PETLA ZWROTNA
# =========================================================

class TestFeedbackLoop:

    def test_like_creates_feedback_tag(self, user, track_with_tags):
        _, item = _make_recommendation(user, track_with_tags)

        apply_feedback_to_tags(user=user, item=item,
                               action=RecommendationFeedback.Action.LIKE)

        assert UserTag.objects.filter(user=user, source="feedback").exists()

    def test_like_weight_higher_than_dislike(self, user, user_2, track_with_tags, tags):
        _, item = _make_recommendation(user, track_with_tags)
        _, item2 = _make_recommendation(user_2, track_with_tags)

        apply_feedback_to_tags(user=user, item=item,
                               action=RecommendationFeedback.Action.LIKE)
        apply_feedback_to_tags(user=user_2, item=item2,
                               action=RecommendationFeedback.Action.DISLIKE)

        like_tag = UserTag.objects.get(user=user, tag=tags["rock"], source="feedback")
        dislike_tag = UserTag.objects.get(user=user_2, tag=tags["rock"], source="feedback")

        assert like_tag.weight > dislike_tag.weight

    def test_skip_no_feedback_tags(self, user, track_with_tags):
        _, item = _make_recommendation(user, track_with_tags)

        apply_feedback_to_tags(user=user, item=item,
                               action=RecommendationFeedback.Action.SKIP)

        assert UserTag.objects.filter(user=user, source="feedback").count() == 0

    def test_feedback_updates_existing_tag(self, user, track_with_tags, tags):
        UserTag.objects.create(
            user=user,
            tag=tags["rock"],
            weight=0.5,
            confidence=0.5,
            source="feedback",
        )

        _, item = _make_recommendation(user, track_with_tags)

        apply_feedback_to_tags(user=user, item=item,
                               action=RecommendationFeedback.Action.LIKE)

        tag = UserTag.objects.get(user=user, tag=tags["rock"], source="feedback")
        assert tag.weight > 0.5


# =========================================================
# 3. DYWERSYFIKACJA ARTYSTOW
# =========================================================

class TestArtistDiversity:

    def test_limits_tracks_per_artist(self, artist, album):
        from recomendations.services.recomendation import apply_artist_diversity

        tracks = []
        for i in range(4):
            t = Track.objects.create(
                name=f"Same Artist Track {i}",
                spotify_id=f"sat_{i}",
                album=album,
                duration_ms=200000,
            )
            t.artists.add(artist)
            tracks.append(t)

        scored = [(t.id, 0.9 - i * 0.1, {}) for i, t in enumerate(tracks)]
        result = apply_artist_diversity(scored, max_per_artist=2)

        assert len(result) == 2

    def test_different_artists_pass_through(self, track, track_2):
        from recomendations.services.recomendation import apply_artist_diversity

        scored = [(track.id, 0.9, {}), (track_2.id, 0.7, {})]
        result = apply_artist_diversity(scored, max_per_artist=2)

        assert len(result) == 2

    def test_preserves_score_order(self, track, track_2):
        from recomendations.services.recomendation import apply_artist_diversity

        scored = [(track.id, 0.9, {}), (track_2.id, 0.7, {})]
        result = apply_artist_diversity(scored, max_per_artist=2)

        assert result[0][1] >= result[1][1]


# =========================================================
# 4. SELEKCJA COLD START
# =========================================================

class TestColdStartSelection:

    def test_unique_artists_in_batch(self, db, album):
        from recomendations.views import InitialSetupView

        for i in range(5):
            a = Artist.objects.create(name=f"CS Artist {i}", spotify_id=f"csa_{i}")
            t = Track.objects.create(
                name=f"CS Track {i}",
                spotify_id=f"cst_{i}",
                album=album,
                duration_ms=200000,
            )
            t.artists.add(a)

            ColdStartTrack.objects.create(
                track=t,
                score=0.5,
                source="test",
                rank=i,
            )

        view = InitialSetupView()
        selected = view._get_coldstart_tracks(limit=5)

        artist_ids = {cst.track.artists.first().id for cst in selected}
        assert len(artist_ids) == len(selected)


# =========================================================
# 5. METRYKI EWALUACYJNE
# =========================================================
@pytest.mark.django_db
class TestEvaluationMetrics:

    def test_precision_at_k(self, user, album, artist):
        from evaluation_metrics import precision_at_k

        rec = Recommendation.objects.create(
            user=user,
            type=Recommendation.RecommendationTypes.TRACK,
            strategy=Recommendation.RecommendationStrategy.COLD_START,
            status=Recommendation.RecommendationStatus.READY,
            is_active=True,
        )

        items = []
        for i in range(5):
            t = Track.objects.create(
                name=f"Precision Track {i}",
                spotify_id=f"pt_{i}",
                album=album,
                duration_ms=200000,
            )
            t.artists.add(artist)

            item = RecommendationItem.objects.create(
                recommendation=rec,
                type=RecommendationItem.ItemTypes.TRACK,
                track=t,
                score=1.0 - i * 0.1,
                rank=i,
                reason={},
            )
            items.append(item)

        actions = ["LIKE", "LIKE", "DISLIKE", "LIKE", "DISLIKE"]
        for i, action in enumerate(actions):
            RecommendationFeedback.objects.create(
                user=user,
                recommendation=rec,
                recommendation_item=items[i],
                action=action,
            )

        p = precision_at_k(user, k=5)
        assert p == pytest.approx(0.6, abs=0.01)

    def test_precision_zero_when_no_feedback(self, user):
        from evaluation_metrics import precision_at_k
        result = precision_at_k(user, k=5)
        assert result in (0, None)

    def test_catalog_coverage(self, user, track, track_2):
        from evaluation_metrics import catalog_coverage

        rec = Recommendation.objects.create(
            user=user,
            type=Recommendation.RecommendationTypes.TRACK,
            strategy=Recommendation.RecommendationStrategy.COLD_START,
            status=Recommendation.RecommendationStatus.READY,
            is_active=True,
        )

        RecommendationItem.objects.create(
            recommendation=rec,
            type=RecommendationItem.ItemTypes.TRACK,
            track=track,
            score=0.9,
            rank=0,
            reason={},
        )

        cov = catalog_coverage()
        assert cov["recommended_unique"] == 1
        assert cov["catalog_size"] >= 2

    def test_catalog_coverage_empty(self):
        from evaluation_metrics import catalog_coverage

        cov = catalog_coverage()

        if cov is None:
            assert cov is None
        else:
            assert cov["recommended_unique"] == 0