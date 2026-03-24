import pytest
from django.contrib.auth import get_user_model
from rest_framework.test import APIClient
from music.models import Album, Artist, Track
from recomendations.models import ColdStartTrack, Recommendation, RecommendationItem

User = get_user_model()


@pytest.fixture
def user(db):
    return User.objects.create_user(email="test@test.com", password="test123")


@pytest.fixture
def auth_client(user):
    client = APIClient()
    client.force_authenticate(user=user)
    return client


def _make_track(title, spotify_id, index=0):
    artist = Artist.objects.create(name=f"Artist {index}", spotify_id=f"art{index}")
    album = Album.objects.create(name=f"Album {index}", spotify_id=f"alb{index}")
    track = Track.objects.create(
        name=title, spotify_id=spotify_id, duration_ms=0, album=album
    )
    track.artists.set([artist])
    return track


@pytest.fixture
def recommendation(user, db):
    user.profile.onboarding_completed = True
    user.profile.save()
    return Recommendation.objects.create(
        user=user,
        strategy=Recommendation.RecommendationStrategy.COLD_START,
        status=Recommendation.RecommendationStatus.READY,
        is_active=True,
    )


@pytest.fixture
def recommendation_item(recommendation, cold_start_track, db):
    return RecommendationItem.objects.create(
        recommendation=recommendation,
        track=cold_start_track.track,
        type=RecommendationItem.ItemTypes.TRACK,
        score=0.8,
        rank=0,
    )


@pytest.fixture
def cold_start_track(db):
    track = _make_track("Test Track", "trk1")
    return ColdStartTrack.objects.create(track=track, rank=1)


@pytest.fixture
def cold_start_tracks(db):
    result = []
    for i in range(5):
        track = _make_track(f"Track {i}", f"trk{i}", index=i)
        result.append(ColdStartTrack.objects.create(track=track, rank=i))
    return result
