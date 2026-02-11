import logging
import os
import requests
from celery import shared_task, group, chord
from django.contrib.auth import get_user_model
from music.models import Track, Artist
from recomendations.models import ColdStartTrack
from users.services import ensure_spotify_token
from users.tasks.spotify_tasks import save_tracks_bulk
from users.tasks.lastfm_tasks import (
    get_similar_artists_task,
    get_similar_track_task,
    get_artist_info,
    get_track_info,
)
from utils.locks import ResourceLock, ResourceLockedException

logger = logging.getLogger(__name__)
User = get_user_model()

LASTFM_TOP_ARTISTS = 40
LASTFM_TRACKS_PER_ARTIST = 2
SPOTIFY_MATCH_LIMIT = 60


# =========================================================
# ORCHESTRATOR
# =========================================================

@shared_task
def cold_start_refresh_all():
    """
    #1 Fetch Spotify + LastFM (parallel)
    #2 After fetch → run full enrichment chord
    """

    workflow = chord(
        [
            cold_start_fetch_spotify_global.s(),
            cold_start_fetch_lastfm_global.s(),
        ],
        cold_start_after_fetch.s(),
    )

    workflow.delay()

    logger.info("Cold Start workflow initiated")
    return "Cold start workflow initiated"


# =========================================================
# AFTER FETCH → FULL ENRICHMENT
# =========================================================

@shared_task
def cold_start_after_fetch(*args, **kwargs):
    track_ids = list(
        Track.objects
        .filter(cold_start_entries__isnull=False)
        .values_list("id", flat=True)
        .distinct()
    )

    if not track_ids:
        logger.warning("Cold start after_fetch: no tracks found")
        return

    artist_ids = list(
        Artist.objects
        .filter(tracks__id__in=track_ids)
        .values_list("id", flat=True)
        .distinct()
    )

    tasks = []

    # Track info (creates TrackLastFMData)
    for track_id in track_ids:
        tasks.append(get_track_info.si(track_id))

    # Artist info + similar
    for artist_id in artist_ids:
        tasks.append(get_artist_info.si(artist_id))
        tasks.append(get_similar_artists_task.si(artist_id))

    # Track similar
    for track_id in track_ids:
        tasks.append(get_similar_track_task.si(track_id))

    if not tasks:
        logger.warning("Cold start after_fetch: no enrichment tasks created")
        return

    workflow = chord(
        group(*tasks),
        cold_start_finalize.s(),
    )

    workflow.delay()

    logger.info(f" Cold Start enrichment workflow initiated with {len(tasks)} tasks")
    return f"Enrichment workflow initiated with {len(tasks)} tasks"


@shared_task
def cold_start_finalize(*args, **kwargs):
    logger.info("Cold Start FULL pipeline finished")
    return


# =========================================================
# FETCH TASKS
# =========================================================

@shared_task(bind=True, autoretry_for=(requests.RequestException,), retry_backoff=10, max_retries=3)
def cold_start_fetch_spotify_global(self):
    lock = ResourceLock("cold_start_source", "spotify_global", timeout=600)

    try:
        with lock:
            logger.info("Cold start Spotify GLOBAL – started")

            user = User.objects.get(email="adam@onet.pl")
            token = ensure_spotify_token(user)
            headers = {"Authorization": f"Bearer {token.access_token}"}

            playlist_id = "5ABHKGoOzxkaa28ttQV9sE"
            url = f"https://api.spotify.com/v1/playlists/{playlist_id}/tracks"

            resp = requests.get(url, headers=headers, params={"limit": 50, "market": "US"}, timeout=15)
            resp.raise_for_status()

            items = resp.json().get("items", [])
            tracks_data = [
                item["track"]
                for item in items
                if item.get("track") and not item["track"].get("is_local")
            ]

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

            logger.info(f"Cold start Spotify GLOBAL – finished ({len(tracks_data)} tracks)")
            return f"Spotify: {len(tracks_data)} tracks"

    except ResourceLockedException:
        logger.info("Spotify GLOBAL cold start already running – skipped")
        return "Spotify: skipped (locked)"


@shared_task(bind=True, autoretry_for=(requests.RequestException,), retry_backoff=15, max_retries=3)
def cold_start_fetch_lastfm_global(self):
    lock = ResourceLock("cold_start_source", "lastfm_global", timeout=900)

    try:
        with lock:
            logger.info("Cold start LastFM GLOBAL – started")

            user = User.objects.get(email="adam@onet.pl")
            token = ensure_spotify_token(user)
            headers = {"Authorization": f"Bearer {token.access_token}"}

            artists = fetch_lastfm_top_artists(limit=LASTFM_TOP_ARTISTS)

            seeds = []
            seen = set()

            for artist in artists:
                tracks = fetch_lastfm_top_tracks(
                    artist["name"],
                    limit=LASTFM_TRACKS_PER_ARTIST
                )

                for t in tracks:
                    key = (artist["name"].lower().strip(), t["name"].lower().strip())
                    if key in seen:
                        continue
                    seen.add(key)

                    seeds.append({
                        "artist": artist["name"],
                        "track": t["name"],
                    })

            spotify_tracks = spotify_search_tracks(seeds, headers)[:SPOTIFY_MATCH_LIMIT]

            tracks_cache = save_tracks_bulk(spotify_tracks)
            total = len(spotify_tracks)

            for idx, track_data in enumerate(spotify_tracks):
                track = tracks_cache.get(track_data["id"])
                if not track:
                    continue

                score = 1.0 - (idx / total) ** 0.5

                ColdStartTrack.objects.update_or_create(
                    track=track,
                    source=ColdStartTrack.Source.LASTFM_GLOBAL,
                    defaults={
                        "rank": idx + 1,
                        "score": round(score, 4),
                    },
                )

            logger.info(f"Cold start LastFM GLOBAL – finished ({len(spotify_tracks)} tracks)")
            return f"LastFM: {len(spotify_tracks)} tracks"

    except ResourceLockedException:
        logger.info("LastFM GLOBAL cold start already running – skipped")
        return "LastFM: skipped (locked)"


# =========================================================
# LASTFM HELPERS
# =========================================================

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