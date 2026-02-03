from celery import chain, chord, shared_task
import logging
import requests
from django.contrib.auth import get_user_model
from users.services import ensure_spotify_token
from users.models import Track,Album,Artist
from django.db import transaction, IntegrityError
from datetime import date

User = get_user_model()
logger=logging.getLogger(__name__)

@shared_task
def cold_start_refresh_all():
    """Orchestrator: fetch all playlists in parallel, then finalize."""
    chord([
        cold_start_fetch_spotify_global.s(),
        cold_start_fetch_spotify_viral.s(),
        cold_start_fetch_lastfm_global.s(),
    ])(cold_start_finalize.s())

logger = logging.getLogger(__name__)
User = get_user_model()


@shared_task(bind=True, autoretry_for=(requests.RequestException,), retry_backoff=10, max_retries=3)
def cold_start_fetch_spotify_global(self):
    logger.info("Cold start Spotify global – started")

    user = User.objects.get(email="adam@onet.pl")
    token = ensure_spotify_token(user)

    headers = {"Authorization": f"Bearer {token.access_token}"}

    playlist_id = "5ABHKGoOzxkaa28ttQV9sE"
    url = f"https://api.spotify.com/v1/playlists/{playlist_id}/tracks"

    resp = requests.get(
        url,
        headers=headers,
        params={"limit": 50, "market": "US"},
        timeout=10,
    )
    resp.raise_for_status()

    items = resp.json().get("items", [])
    logger.info("Fetched %s playlist items", len(items))

    for rank, item in enumerate(items, start=1):
        track = item.get("track")
        if not track or track.get("is_local"):
            continue

        track_id = track.get("id")
        if track_id:
            cold_start_process_track.delay(track_id, rank)

    logger.info("Cold start Spotify global – fanout done")


@shared_task(bind=True, autoretry_for=(requests.RequestException,), retry_backoff=5, max_retries=3)
def cold_start_process_track(self, track_id, rank):
    """Orchestrator: coordinates artist → album → track ingestion."""

    # Fetch track data once
    user = User.objects.get(email="adam@onet.pl")
    token = ensure_spotify_token(user)
    headers = {"Authorization": f"Bearer {token.access_token}"}

    resp = requests.get(
        f"https://api.spotify.com/v1/tracks/{track_id}",
        headers=headers,
        params={"market": "US"},
        timeout=10,
    )
    resp.raise_for_status()
    track_data = resp.json()

    # Process in order: artists → album → track
    artist_ids = [a["id"] for a in track_data["artists"]]
    artists = ingest_artists(artist_ids, headers)
    album = ingest_album(track_data["album"], artists[0])
    track = ingest_track(track_data, album, artists)

    logger.info(
        "#%s %s – %s (%s artists, preview=%s)",
        rank, track.name, artists[0].name, len(artists), bool(track.preview_url)
    )


def cold_start_fetch_spotify_viral():
    pass

def cold_start_fetch_lastfm_global():
    pass

def cold_start_finalize(*args, **kwargs):
    pass

# ============================================================
# INGEST TASKS FOR SPOTIFY
# ============================================================
def ingest_artists(artist_ids, headers):
    artists = []

    for artist_id in artist_ids:
        try:
            artist, created = Artist.objects.get_or_create(
                spotify_id=artist_id,
                defaults={"name": "Unknown"},
            )
        except IntegrityError:
            # ktoś inny stworzył równolegle
            artist = Artist.objects.get(spotify_id=artist_id)
            created = False

        # fetch danych POZA transakcją
        if created or not artist.image_url:
            resp = requests.get(
                f"https://api.spotify.com/v1/artists/{artist_id}",
                headers=headers,
                timeout=10,
            )
            resp.raise_for_status()
            data = resp.json()

            update_fields = []
            if artist.name != data["name"]:
                artist.name = data["name"]
                update_fields.append("name")

            if data.get("images"):
                artist.image_url = data["images"][0]["url"]
                update_fields.append("image_url")

            if update_fields:
                artist.save(update_fields=update_fields)

        artists.append(artist)

    return artists

def ingest_album(album_data: dict, primary_artist: Artist) -> Album:
    """
    Create or fetch album and attach primary artist.
    Celery-safe, idempotent.
    """
    try:
        with transaction.atomic():
            album, created = Album.objects.get_or_create(
                spotify_id=album_data["id"],
                defaults={
                    "name": album_data["name"],
                    "album_type": album_data.get(
                        "album_type", Album.AlbumTypes.ALBUM
                    ),
                    "release_date": album_data.get("release_date"),
                    "image_url": (
                        album_data["images"][0]["url"]
                        if album_data.get("images")
                        else None
                    ),
                },
            )
    except IntegrityError:
        # someone else created it concurrently
        album = Album.objects.get(spotify_id=album_data["id"])
        created = False

    # attach artist outside critical section
    album.artists.add(primary_artist)

    return album

def ingest_track(track_data: dict, album: Album, artists: list[Artist]) -> Track:
    """
    Create or fetch track and attach all artists.
    Celery-safe, idempotent.
    """
    try:
        with transaction.atomic():
            track, created = Track.objects.get_or_create(
                spotify_id=track_data["id"],
                defaults={
                    "name": track_data["name"],
                    "album": album,
                    "duration_ms": track_data["duration_ms"],
                    "popularity": track_data.get("popularity"),
                    "preview_url": track_data.get("preview_url"),
                    "preview_type": (
                        "audio" if track_data.get("preview_url") else "embed"
                    ),
                    "image_url": album.image_url,
                },
            )
    except IntegrityError:
        # someone else created it concurrently
        track = Track.objects.get(spotify_id=track_data["id"])
        created = False

    # Attach artists outside transaction
    track.artists.add(*artists)

    return track

