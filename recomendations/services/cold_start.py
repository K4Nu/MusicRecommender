from celery import chain, chord, shared_task
import logging
import requests
from django.contrib.auth import get_user_model
from users.services import ensure_spotify_token
from music.models import Track,Album,Artist
from django.db import transaction, IntegrityError
from datetime import date
from recomendations.models import Recommendation, RecommendationItem, ColdStartTrack
from utils.locks import ResourceLock, ResourceLockedException
from users.tasks.spotify_tasks import save_tracks_bulk
import os

User = get_user_model()
logger=logging.getLogger(__name__)
LASTFM_TOP_ARTISTS = 40
LASTFM_TRACKS_PER_ARTIST = 2
SPOTIFY_MATCH_LIMIT = 60

@shared_task
def cold_start_refresh_all():
    """Orchestrator: fetch all playlists in parallel, then finalize."""
    chord([
        cold_start_fetch_spotify_global.s(),
         cold_start_fetch_lastfm_global.s(),
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


@shared_task(
    bind=True,
    autoretry_for=(requests.RequestException,),
    retry_backoff=15,
    max_retries=3,
)
def cold_start_fetch_lastfm_global(self):
    lock = ResourceLock("cold_start_source", "lastfm_global", timeout=900)

    try:
        with lock:
            logger.info("Cold start LastFM GLOBAL v2 – started")

            user = User.objects.get(email="adam@onet.pl")
            token = ensure_spotify_token(user)
            headers = {"Authorization": f"Bearer {token.access_token}"}

            artists = fetch_lastfm_top_artists(limit=LASTFM_TOP_ARTISTS)

            # 1️⃣ Build seed pool (deduped)
            seeds = []
            seen = set()

            for artist in artists:
                tracks = fetch_lastfm_top_tracks(
                    artist["name"],
                    limit=LASTFM_TRACKS_PER_ARTIST
                )

                for t in tracks:
                    key = (
                        artist["name"].lower().strip(),
                        t["name"].lower().strip(),
                    )
                    if key in seen:
                        continue
                    seen.add(key)

                    seeds.append({
                        "artist": artist["name"],
                        "track": t["name"],
                    })

            # 2️⃣ Spotify canonicalization
            spotify_tracks = spotify_search_tracks(seeds, headers)
            spotify_tracks = spotify_tracks[:SPOTIFY_MATCH_LIMIT]

            if not spotify_tracks:
                logger.warning("LastFM GLOBAL v2 – no Spotify matches")
                return

            tracks_cache = save_tracks_bulk(spotify_tracks)

            total = len(spotify_tracks)

            # 3️⃣ Save cold start tracks
            for idx, track_data in enumerate(spotify_tracks):
                track = tracks_cache.get(track_data["id"])
                if not track:
                    continue

                # nonlinear decay (top-heavy)
                score = 1.0 - (idx / total) ** 0.5

                ColdStartTrack.objects.update_or_create(
                    track=track,
                    source=ColdStartTrack.Source.LASTFM_GLOBAL,
                    defaults={
                        "rank": idx + 1,
                        "score": round(score, 4),
                    },
                )

            logger.info(
                "Cold start LastFM GLOBAL v2 – finished",
                extra={"tracks": total},
            )

    except ResourceLockedException:
        logger.info("LastFM GLOBAL v2 already running – skipped")

@shared_task
def cold_start_finalize(*args, **kwargs):
    logger.info("Cold Start data finished")
    return

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

def fetch_lastfm_top_tracks(artist_name, limit=3):
    resp = requests.get(
        "https://ws.audioscrobbler.com/2.0/",
        params={
            "method": "artist.gettoptracks",
            "artist": artist_name,
            "api_key": os.environ.get("LASTFM_API_KEY"),
            "format": "json",
            "limit": limit,
        },
        timeout=10,
    )
    resp.raise_for_status()
    return resp.json()["toptracks"]["track"]

def spotify_search_tracks(seeds, headers):
    found = []

    for seed in seeds:
        q = f'track:"{seed["track"]}" artist:"{seed["artist"]}"'
        resp = requests.get(
            "https://api.spotify.com/v1/search",
            headers=headers,
            params={
                "q": q,
                "type": "track",
                "limit": 1,
                "market": "US",
            },
            timeout=10,
        )
        resp.raise_for_status()

        items = resp.json()["tracks"]["items"]
        if items:
            found.append(items[0])

    return found