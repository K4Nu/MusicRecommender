from celery import chain, chord, shared_task
import logging
import requests
from django.contrib.auth import get_user_model
from users.services import ensure_spotify_token
from users.models import Track,Album,Artist

User = get_user_model()
logger=logging.getLogger(__name__)

def cold_start_refresh_all():
    cold_start_fetch_spotify_global()
    cold_start_fetch_spotify_viral()
    cold_start_fetch_lastfm_global()
    cold_start_finalize()


from celery import shared_task
import requests
import logging
from django.contrib.auth import get_user_model
from users.services import ensure_spotify_token

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


def cold_start_process_track(track_id, rank):
    pass


def cold_start_fetch_spotify_viral():
    pass

def cold_start_fetch_lastfm_global():
    pass

def cold_start_finalize(*args, **kwargs):
    pass