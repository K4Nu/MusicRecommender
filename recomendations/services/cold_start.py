from celery import chain, chord, shared_task
import logging
import requests
from django.contrib.auth import get_user_model
from users.services import ensure_spotify_token
from users.models import Track,Album,Artist
from django.db import transaction, IntegrityError
from datetime import date
from recomendations.models import Recommendation, RecommendationItem, ColdStartTrack
from utils.locks import ResourceLock, ResourceLockedException
from users.tasks.spotify_tasks import save_tracks_bulk
import os

User = get_user_model()
logger=logging.getLogger(__name__)

@shared_task
def cold_start_refresh_all():
    """Orchestrator: fetch all playlists in parallel, then finalize."""
    chord([
        cold_start_fetch_spotify_global.s(),
        # cold_start_fetch_lastfm_global.s(),  # later
    ])(cold_start_finalize.s())

logger = logging.getLogger(__name__)
User = get_user_model()

@shared_task(
    bind=True,
    autoretry_for=(requests.RequestException,),
    retry_backoff=10,
    max_retries=3,
)
def cold_start_fetch_spotify_global(self):
    lock = ResourceLock("cold_start_source", "spotify_global", timeout=600)

    try:
        with lock:
            logger.info("Cold start Spotify GLOBAL – started")

            user = User.objects.get(email="adam@onet.pl")
            token = ensure_spotify_token(user)
            headers = {"Authorization": f"Bearer {token.access_token}"}

            playlist_id = "5ABHKGoOzxkaa28ttQV9sE"  # Global Top 50
            url = f"https://api.spotify.com/v1/playlists/{playlist_id}/tracks"

            resp = requests.get(
                url,
                headers=headers,
                params={"limit": 50, "market": "US"},
                timeout=15,
            )
            resp.raise_for_status()

            items = resp.json().get("items", [])
            tracks_data = []

            for item in items:
                track = item.get("track")
                if not track or track.get("is_local"):
                    continue
                tracks_data.append(track)

            if not tracks_data:
                logger.warning("Spotify GLOBAL returned no tracks")
                return

            tracks_cache = save_tracks_bulk(tracks_data)

            for rank, track_data in enumerate(tracks_data, start=1):
                track = tracks_cache.get(track_data["id"])
                if not track:
                    continue

                ColdStartTrack.objects.update_or_create(
                    track=track,
                    source=ColdStartTrack.Source.SPOTIFY_GLOBAL,
                    defaults={
                        "rank": rank,
                        "score": 1.0 - (rank - 1) / 50,
                    },
                )

            logger.info(
                "Cold start Spotify GLOBAL – finished",
                extra={"tracks": len(tracks_data)},
            )

    except ResourceLockedException:
        logger.info("Spotify GLOBAL cold start already running – skipped")


def cold_start_fetch_lastfm_global():
    pass

def cold_start_finalize(*args, **kwargs):
    pass

"""
LastFM helpers
"""
def fetch_lastfm_top_artists(limit=100):
    resp = requests.get(
        "https://ws.audioscrobbler.com/2.0/",
        params={
            "method": "chart.gettopartists",
            "api_key": os.environ.get("LASTFM_API_KEY"),
            "format": "json",
            "limit": limit,
        },
        timeout=10,
    )
    resp.raise_for_status()
    return resp.json()["artists"]["artist"]