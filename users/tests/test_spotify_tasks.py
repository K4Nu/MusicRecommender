import pytest
import requests
from datetime import date
from unittest.mock import patch, MagicMock

from music.models import Artist, Track, Album, Genre
from users.models import UserTopItem
from users.tasks.spotify_tasks import (
    parse_spotify_release_date,
    save_artists_bulk,
    save_track,
    fetch_top_items,
    fetch_saved_tracks,
)


# ─────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────

def make_artist(spotify_id="art1", name="Artist One", genres=None, images=None):
    return {
        "id": spotify_id,
        "name": name,
        "popularity": 80,
        "genres": genres or [],
        "images": images or [{"url": "http://img.test/art.jpg"}],
    }


def make_track(spotify_id="trk1", name="Track One", artist_id="art1"):
    return {
        "id": spotify_id,
        "name": name,
        "duration_ms": 200000,
        "popularity": 70,
        "preview_url": "http://preview.test/trk.mp3",
        "artists": [{"id": artist_id, "name": "Artist One"}],
        "album": {
            "id": "alb1",
            "name": "Album One",
            "album_type": "album",
            "release_date": "2022-06-15",
            "images": [{"url": "http://img.test/alb.jpg"}],
            "artists": [{"id": artist_id, "name": "Artist One"}],
        },
    }


def make_api_response(items, next_url=None):
    mock = MagicMock()
    mock.json.return_value = {"items": items, "next": next_url}
    return mock


# ─────────────────────────────────────────────
# parse_spotify_release_date
# ─────────────────────────────────────────────

@pytest.mark.parametrize("value,expected", [
    ("2020",       date(2020, 1, 1)),    # year only
    ("2021-02",    date(2021, 2, 1)),    # year-month
    ("2025-05-15", date(2025, 5, 15)),   # full date
    ("",           None),               # empty string
    (None,         None),               # None input
    ("not-a-date", None),               # garbage string — 10 chars but invalid
    ("2020-99",    None),               # invalid month
])
def test_parse_spotify_release_date(value, expected):
    assert parse_spotify_release_date(value) == expected


# ─────────────────────────────────────────────
# save_artists_bulk
# ─────────────────────────────────────────────

@pytest.mark.django_db
def test_save_artists_bulk_empty_input_does_nothing():
    save_artists_bulk([])
    assert Artist.objects.count() == 0


@pytest.mark.django_db
def test_save_artists_bulk_creates_new_artists():
    save_artists_bulk([make_artist("art1", "Artist One"), make_artist("art2", "Artist Two")])
    assert Artist.objects.count() == 2
    assert Artist.objects.filter(spotify_id="art1").exists()


@pytest.mark.django_db
def test_save_artists_bulk_no_duplicate_on_second_call():
    data = [make_artist("art1", "Artist One")]
    save_artists_bulk(data)
    save_artists_bulk(data)
    assert Artist.objects.filter(spotify_id="art1").count() == 1


@pytest.mark.django_db
def test_save_artists_bulk_creates_genres():
    save_artists_bulk([make_artist("art1", genres=["rock", "indie"])])
    assert Genre.objects.filter(name="rock").exists()
    assert Genre.objects.filter(name="indie").exists()


@pytest.mark.django_db
def test_save_artists_bulk_links_genres_to_artist():
    save_artists_bulk([make_artist("art1", genres=["pop"])])
    artist = Artist.objects.get(spotify_id="art1")
    assert artist.genres.filter(name="pop").exists()


@pytest.mark.django_db
def test_save_artists_bulk_skips_genre_m2m_when_no_genres():
    save_artists_bulk([make_artist("art1", genres=[])])
    assert Genre.objects.count() == 0


@pytest.mark.django_db
def test_save_artists_bulk_updates_existing_artist_with_full_data():
    Artist.objects.create(spotify_id="art1", name="Old Name", popularity=10)
    save_artists_bulk([make_artist("art1", name="New Name", genres=["jazz"])])
    assert Artist.objects.get(spotify_id="art1").name == "New Name"


# ─────────────────────────────────────────────
# save_track
# ─────────────────────────────────────────────

@pytest.mark.django_db
def test_save_track_returns_none_for_empty_input():
    assert save_track(None) is None
    assert save_track({}) is None


@pytest.mark.django_db
def test_save_track_creates_track():
    track = save_track(make_track())
    assert track is not None
    assert Track.objects.filter(spotify_id="trk1").exists()


@pytest.mark.django_db
def test_save_track_creates_album():
    save_track(make_track())
    assert Album.objects.filter(spotify_id="alb1").exists()


@pytest.mark.django_db
def test_save_track_creates_artist():
    save_track(make_track(artist_id="art1"))
    assert Artist.objects.filter(spotify_id="art1").exists()


@pytest.mark.django_db
def test_save_track_no_duplicate_on_second_call():
    save_track(make_track())
    save_track(make_track())
    assert Track.objects.filter(spotify_id="trk1").count() == 1


@pytest.mark.django_db
def test_save_track_sets_image_from_album():
    track = save_track(make_track())
    assert track.image_url == "http://img.test/alb.jpg"


@pytest.mark.django_db
def test_save_track_links_artists_to_track():
    track = save_track(make_track(artist_id="art1"))
    assert track.artists.filter(spotify_id="art1").exists()


# ─────────────────────────────────────────────
# fetch_top_items
# ─────────────────────────────────────────────

@pytest.mark.django_db
def test_fetch_top_items_skips_on_request_error(user):
    with patch("users.tasks.spotify_tasks.requests.get") as mock_get:
        mock_get.side_effect = requests.exceptions.RequestException("timeout")
        fetch_top_items({"Authorization": "Bearer x"}, "artists", "short_term", user_id=user.id)
    assert UserTopItem.objects.count() == 0


@pytest.mark.django_db
def test_fetch_top_items_creates_artist_top_items(user):
    with patch("users.tasks.spotify_tasks.requests.get") as mock_get:
        mock_get.return_value = make_api_response([make_artist("art1", genres=["rock"])])
        fetch_top_items({"Authorization": "Bearer x"}, "artists", "short_term", user_id=user.id)
    assert UserTopItem.objects.filter(user=user, item_type="artist").count() == 1


@pytest.mark.django_db
def test_fetch_top_items_creates_track_top_items(user):
    with patch("users.tasks.spotify_tasks.requests.get") as mock_get:
        mock_get.return_value = make_api_response([make_track()])
        fetch_top_items({"Authorization": "Bearer x"}, "tracks", "short_term", user_id=user.id)
    assert UserTopItem.objects.filter(user=user, item_type="track").count() == 1


@pytest.mark.django_db
def test_fetch_top_items_empty_response_creates_nothing(user):
    with patch("users.tasks.spotify_tasks.requests.get") as mock_get:
        mock_get.return_value = make_api_response([])
        fetch_top_items({"Authorization": "Bearer x"}, "artists", "short_term", user_id=user.id)
    assert UserTopItem.objects.count() == 0


# ─────────────────────────────────────────────
# fetch_saved_tracks
# ─────────────────────────────────────────────

@pytest.mark.django_db
def test_fetch_saved_tracks_skips_on_request_error(user):
    with patch("users.tasks.spotify_tasks.requests.get") as mock_get:
        mock_get.side_effect = requests.exceptions.RequestException("timeout")
        fetch_saved_tracks({"Authorization": "Bearer x"}, user_id=user.id)
    assert Track.objects.count() == 0


@pytest.mark.django_db
def test_fetch_saved_tracks_saves_tracks(user):
    with patch("users.tasks.spotify_tasks.requests.get") as mock_get:
        mock_get.return_value = make_api_response([{"track": make_track()}])
        fetch_saved_tracks({"Authorization": "Bearer x"}, user_id=user.id)
    assert Track.objects.filter(spotify_id="trk1").exists()


@pytest.mark.django_db
def test_fetch_saved_tracks_follows_pagination(user):
    page1 = MagicMock()
    page1.json.return_value = {
        "items": [{"track": make_track("trk1")}],
        "next": "http://next-page",
    }
    page2 = MagicMock()
    page2.json.return_value = {
        "items": [{"track": make_track("trk2")}],
        "next": None,
    }
    with patch("users.tasks.spotify_tasks.requests.get") as mock_get:
        mock_get.side_effect = [page1, page2]
        fetch_saved_tracks({"Authorization": "Bearer x"}, user_id=user.id)
    assert Track.objects.count() == 2
