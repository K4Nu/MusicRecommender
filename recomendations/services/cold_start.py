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
# MAIN ORCHESTRATOR
# =========================================================

@shared_task
def cold_start_refresh_all():
    """
    Kicks off two independent pipelines in parallel.
    Does NOT use a chord - cold_start_fetch_lastfm_global returns
    immediately after dispatching subtasks, so a chord would fire
    cold_start_after_fetch before LastFM data is saved.

    Flow:
        cold_start_fetch_spotify_global  → saves tracks (~15s)
        cold_start_fetch_lastfm_global   → dispatches 40 parallel tasks (~2s)
            └── 40x lastfm_fetch_artist_tracks (~15s total)
                └── cold_start_lastfm_save → saves tracks → triggers enrichment
    """
    cold_start_fetch_spotify_global.delay()
    cold_start_fetch_lastfm_global.delay()

    logger.info("Cold Start workflow initiated")
    return "Cold start workflow initiated"


# =========================================================
# SPOTIFY PIPELINE
# Single task, ~15s - fast enough as-is
# =========================================================

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
            logger.info("Spotify GLOBAL – started")

            try:
                user = User.objects.get(email="adam@onet.pl")
            except User.DoesNotExist:
                logger.error("Cold start user adam@onet.pl not found")
                return "Spotify: user not found"

            token = ensure_spotify_token(user)
            if not token:
                logger.error("No Spotify token for cold start user")
                return "Spotify: no token"

            headers = {"Authorization": f"Bearer {token.access_token}"}

            resp = requests.get(
                "https://api.spotify.com/v1/playlists/5ABHKGoOzxkaa28ttQV9sE/tracks",
                headers=headers,
                params={"limit": 50, "market": "US"},
                timeout=15,
            )
            resp.raise_for_status()

            tracks_data = [
                item["track"]
                for item in resp.json().get("items", [])
                if item.get("track") and not item["track"].get("is_local")
            ]

            if not tracks_data:
                logger.warning("No tracks found in Spotify playlist")
                return "Spotify: no tracks"

            tracks_cache = save_tracks_bulk(tracks_data)

            track_scores = {}
            for rank, td in enumerate(tracks_data, start=1):
                track = tracks_cache.get(td["id"])
                if track:
                    track_scores[track.id] = {
                        "rank": rank,
                        "score": round(1.0 - (rank - 1) / 50, 4),
                    }

            _bulk_upsert_cold_start(track_scores, ColdStartTrack.Source.SPOTIFY_GLOBAL)

            logger.info(f"Spotify GLOBAL – finished ({len(tracks_data)} tracks)")
            return f"Spotify: {len(tracks_data)} tracks"

    except ResourceLockedException:
        logger.info("Spotify GLOBAL already running – skipped")
        return "Spotify: skipped (locked)"


# =========================================================
# LASTFM PIPELINE
#
# Problem: sequential loops (40 LastFM + 80 Spotify calls)
#          block eventlet's mainloop → crash
#
# Fix: each artist = one small Celery task
#      40 tasks run in parallel via eventlet
#
# Step 1: cold_start_fetch_lastfm_global
#   - 1 API call to get top 40 artists
#   - dispatches 40x lastfm_fetch_artist_tracks
#   - returns immediately (no blocking)
#
# Step 2: lastfm_fetch_artist_tracks (x40 in parallel)
#   - 1 LastFM call: get top N tracks for artist
#   - N Spotify searches: find each track
#   - returns list of Spotify track dicts
#
# Step 3: cold_start_lastfm_save (chord callback)
#   - receives all results from 40 tasks
#   - deduplicates and saves ColdStartTrack entries
#   - triggers cold_start_after_fetch (enrichment)
#
# Performance:
#   Before: ~9 min (sequential + eventlet crash)
#   After:  ~15s  (40 parallel tasks)
# =========================================================

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
            logger.info("LastFM GLOBAL – started")

            try:
                user = User.objects.get(email="adam@onet.pl")
            except User.DoesNotExist:
                logger.error("Cold start user adam@onet.pl not found")
                return "LastFM: user not found"

            token = ensure_spotify_token(user)
            if not token:
                logger.error("No Spotify token for cold start user")
                return "LastFM: no token"

            # ONE fast API call - get top artists list
            resp = requests.get(
                "https://ws.audioscrobbler.com/2.0/",
                params={
                    "method": "chart.gettopartists",
                    "api_key": os.environ.get("LASTFM_API_KEY"),
                    "format": "json",
                    "limit": LASTFM_TOP_ARTISTS,
                },
                timeout=10,
            )
            resp.raise_for_status()
            artists = resp.json()["artists"]["artist"]

            logger.info(f"Got {len(artists)} artists, dispatching parallel tasks")

            # Dispatch one task per artist - all run in parallel
            # Each task: 1 LastFM call + 2 Spotify searches = ~3 API calls
            chord(
                group(*[
                    lastfm_fetch_artist_tracks.s(
                        artist["name"],
                        token.access_token,
                        LASTFM_TRACKS_PER_ARTIST,
                    )
                    for artist in artists
                ]),
                cold_start_lastfm_save.s(),
            ).delay()

            logger.info(f"Dispatched {len(artists)} parallel artist tasks")
            return f"LastFM: {len(artists)} tasks dispatched"

    except ResourceLockedException:
        logger.info("LastFM GLOBAL already running – skipped")
        return "LastFM: skipped (locked)"


@shared_task(
    autoretry_for=(requests.RequestException,),
    max_retries=2,
    retry_backoff=5,
)
def lastfm_fetch_artist_tracks(artist_name: str, access_token: str, limit: int):
    """
    Fetches top tracks for ONE artist from LastFM, then searches
    each on Spotify. Small task - only 1 + N API calls total.
    Runs in parallel with all other artist tasks.
    """
    # 1. Fetch top tracks from LastFM
    try:
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
        tracks = resp.json()["toptracks"]["track"]
    except requests.RequestException as e:
        logger.warning(f"LastFM fetch failed for '{artist_name}': {e}")
        return []

    if not tracks:
        return []

    # 2. Search each track on Spotify
    headers = {"Authorization": f"Bearer {access_token}"}
    found = []

    for track in tracks:
        try:
            resp = requests.get(
                "https://api.spotify.com/v1/search",
                headers=headers,
                params={
                    "q": f'track:"{track["name"]}" artist:"{artist_name}"',
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
        except requests.RequestException as e:
            logger.warning(f"Spotify search failed for '{artist_name}' – '{track['name']}': {e}")
            continue

    logger.debug(f"'{artist_name}': {len(found)}/{len(tracks)} tracks found on Spotify")
    return found


@shared_task
def cold_start_lastfm_save(all_results):
    """
    Chord callback - receives list of lists from all 40 artist tasks.
    Deduplicates, saves ColdStartTrack entries, then triggers enrichment.
    """
    # Flatten + deduplicate across all artist results
    spotify_tracks = []
    seen_ids = set()

    for artist_results in all_results:
        if not artist_results:
            continue
        for track_data in artist_results:
            tid = track_data.get("id")
            if tid and tid not in seen_ids:
                seen_ids.add(tid)
                spotify_tracks.append(track_data)

    spotify_tracks = spotify_tracks[:SPOTIFY_MATCH_LIMIT]
    logger.info(f"LastFM save: {len(spotify_tracks)} unique tracks from {len(all_results)} artists")

    if not spotify_tracks:
        logger.warning("No tracks found from LastFM – skipping enrichment")
        return "LastFM: no matches"

    tracks_cache = save_tracks_bulk(spotify_tracks)
    total = len(spotify_tracks)

    track_scores = {}
    for idx, td in enumerate(spotify_tracks):
        track = tracks_cache.get(td["id"])
        if track:
            track_scores[track.id] = {
                "rank": idx + 1,
                "score": round(1.0 - (idx / total) ** 0.5, 4),
            }

    _bulk_upsert_cold_start(track_scores, ColdStartTrack.Source.LASTFM_GLOBAL)
    logger.info(f"✅ LastFM GLOBAL saved: {len(track_scores)} tracks")

    # Trigger enrichment now that both pipelines have saved their data.
    # Spotify (~15s) finishes before LastFM pipeline completes (~15s),
    # so by the time we reach here all tracks are in the DB.
    cold_start_after_fetch.delay()

    return f"LastFM: {len(spotify_tracks)} tracks saved"


# =========================================================
# ENRICHMENT PIPELINE
# Triggered by cold_start_lastfm_save after all data is saved
# =========================================================

@shared_task
def cold_start_after_fetch():
    """
    Runs enrichment for all cold start tracks + artists.
    Triggered by cold_start_lastfm_save (not a chord callback anymore).
    """
    track_ids = list(
        Track.objects
        .filter(cold_start_entries__isnull=False)
        .values_list("id", flat=True)
        .distinct()
    )

    if not track_ids:
        logger.warning("cold_start_after_fetch: no tracks found")
        return "No tracks"

    artist_ids = list(
        Artist.objects
        .filter(tracks__id__in=track_ids)
        .values_list("id", flat=True)
        .distinct()
    )

    logger.info(f"Enriching {len(track_ids)} tracks, {len(artist_ids)} artists")

    tasks = []
    for track_id in track_ids:
        tasks.append(get_track_info.si(track_id))
    for artist_id in artist_ids:
        tasks.append(get_artist_info.si(artist_id))
        tasks.append(get_similar_artists_task.si(artist_id))
        tasks.append(fetch_full_artist_data.si(artist_id))  # ← fetch genres
    for track_id in track_ids:
        tasks.append(get_similar_track_task.si(track_id))

    if not tasks:
        logger.warning("cold_start_after_fetch: no enrichment tasks created")
        return "No tasks"

    chord(group(*tasks), cold_start_finalize.s()).delay()

    logger.info(f"Enrichment started: {len(tasks)} tasks")
    return f"Enrichment: {len(tasks)} tasks"


@shared_task
def cold_start_finalize(*args, **kwargs):
    logger.info("✅ Cold Start full pipeline finished")
    return "Done"



# =========================================================
# ARTIST GENRE ENRICHMENT
# =========================================================

@shared_task(
    autoretry_for=(requests.RequestException,),
    max_retries=3,
    retry_backoff=5,
)
def fetch_full_artist_data(artist_id: int):
    """
    Fetch full artist data from Spotify including genres.
    Called during enrichment for every cold start artist.
    Skips artists that already have genres.
    """
    from music.models import Genre

    try:
        artist = Artist.objects.get(id=artist_id)
    except Artist.DoesNotExist:
        return

    if not artist.spotify_id:
        return

    if artist.genres.exists():
        return  # Already enriched

    user=User.objects.filter(spotifyaccount__isnull=False).first()
    if not user:
        logger.warning("No user with Spotify account found for genre enrichment")
        return

    token = ensure_spotify_token(user)
    if not token:
        return

    resp = requests.get(
        f"https://api.spotify.com/v1/artists/{artist.spotify_id}",
        headers={"Authorization": f"Bearer {token.access_token}"},
        timeout=10,
    )
    resp.raise_for_status()
    data = resp.json()

    genre_names = data.get("genres", [])
    if not genre_names:
        logger.debug(f"No genres on Spotify for '{artist.name}'")
        return

    # Update artist popularity/image while we have the data
    update_fields = []
    if data.get("popularity") is not None:
        artist.popularity = data["popularity"]
        update_fields.append("popularity")
    if data.get("images"):
        artist.image_url = data["images"][0]["url"]
        update_fields.append("image_url")
    if update_fields:
        artist.save(update_fields=update_fields)

    # Bulk create genres
    existing_genre_names = set(
        Genre.objects.filter(name__in=genre_names).values_list("name", flat=True)
    )
    new_genres = [Genre(name=g) for g in genre_names if g not in existing_genre_names]
    if new_genres:
        Genre.objects.bulk_create(new_genres, ignore_conflicts=True)

    genres = {g.name: g for g in Genre.objects.filter(name__in=genre_names)}

    # Bulk create M2M relations
    existing_relations = set(
        Artist.genres.through.objects
        .filter(artist_id=artist.id)
        .values_list("genre_id", flat=True)
    )
    new_relations = [
        Artist.genres.through(artist_id=artist.id, genre_id=genre.id)
        for genre in genres.values()
        if genre.id not in existing_relations
    ]
    if new_relations:
        Artist.genres.through.objects.bulk_create(new_relations, ignore_conflicts=True)

    logger.info(f"Genres saved for '{artist.name}': {genre_names}")


# =========================================================
# HELPERS
# =========================================================

def _bulk_upsert_cold_start(track_scores: dict, source):
    """
    Bulk upsert ColdStartTrack records.
    Avoids N individual update_or_create calls.
    """
    if not track_scores:
        return

    existing = {
        cs.track_id: cs
        for cs in ColdStartTrack.objects.filter(
            track_id__in=track_scores.keys(),
            source=source,
        )
    }

    to_update, to_create = [], []

    for track_id, data in track_scores.items():
        if track_id in existing:
            cs = existing[track_id]
            cs.rank = data["rank"]
            cs.score = data["score"]
            to_update.append(cs)
        else:
            to_create.append(ColdStartTrack(
                track_id=track_id,
                source=source,
                rank=data["rank"],
                score=data["score"],
            ))

    if to_update:
        ColdStartTrack.objects.bulk_update(to_update, ['rank', 'score'])
    if to_create:
        ColdStartTrack.objects.bulk_create(to_create, ignore_conflicts=True)

    logger.info(f"Upserted [{source}]: {len(to_create)} created, {len(to_update)} updated")