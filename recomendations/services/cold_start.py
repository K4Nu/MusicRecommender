import logging
import os
import re
import time
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

LASTFM_TOP_ARTISTS = 100
LASTFM_TRACKS_PER_ARTIST = 5
SPOTIFY_MATCH_LIMIT = 500

# Catalog expansion via Spotify Related Artists
EXPAND_SEED_LIMIT = 300       # Top artists by popularity to seed from
EXPAND_BATCH_SIZE = 10        # Artists per parallel task
EXPAND_REQUEST_DELAY = 0.05   # 50ms between Spotify API calls

SPOTIFY_RECOMMENDATION_GENRES = [
    "pop", "hip-hop", "rock", "latin", "indie", "r-n-b",
    "country", "edm", "dance", "alternative", "jazz", "classical",
    "metal", "soul", "reggaeton", "k-pop",
]


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
                user=User.objects.filter(spotifyaccount__isnull=False).first()
            except User.DoesNotExist:
                logger.error("Cold start user not found")
                return "Spotify: user not found"

            token = ensure_spotify_token(user)
            if not token:
                logger.error("No Spotify token for cold start user")
                return "Spotify: no token"

            headers = {"Authorization": f"Bearer {token.access_token}"}

            all_tracks_data = []
            seen_spotify_ids = set()

            # 1. Top 50 Global playlist (reliable, always works)
            try:
                resp = requests.get(
                    "https://api.spotify.com/v1/playlists/5ABHKGoOzxkaa28ttQV9sE/items",
                    headers=headers,
                    params={"limit": 50},
                    timeout=15,
                )
                resp.raise_for_status()

                for item in resp.json().get("items", []):
                    track = item.get("track")
                    if not track or track.get("is_local"):
                        continue
                    sid = track.get("id")
                    if sid and sid not in seen_spotify_ids:
                        seen_spotify_ids.add(sid)
                        all_tracks_data.append(track)

                logger.info(f"Top 50 Global: {len(all_tracks_data)} tracks")
            except requests.RequestException as e:
                logger.warning(f"Top 50 Global playlist failed: {e}")

            # 2. Recommendations API - 100 tracks per genre seed
            for genre in SPOTIFY_RECOMMENDATION_GENRES:
                try:
                    resp = requests.get(
                        "https://api.spotify.com/v1/recommendations",
                        headers=headers,
                        params={
                            "seed_genres": genre,
                            "limit": 100,
                            "min_popularity": 40,
                        },
                        timeout=15,
                    )
                    resp.raise_for_status()

                    count = 0
                    for track in resp.json().get("tracks", []):
                        sid = track.get("id")
                        if sid and sid not in seen_spotify_ids:
                            seen_spotify_ids.add(sid)
                            all_tracks_data.append(track)
                            count += 1

                    logger.debug(f"Recommendations '{genre}': {count} new tracks")
                except requests.RequestException as e:
                    logger.warning(f"Recommendations for genre '{genre}' failed: {e}")
                    continue

            if not all_tracks_data:
                logger.warning("No tracks found from Spotify")
                return "Spotify: no tracks"

            tracks_cache = save_tracks_bulk(all_tracks_data)
            total = len(all_tracks_data)

            track_scores = {}
            for rank, td in enumerate(all_tracks_data, start=1):
                track = tracks_cache.get(td["id"])
                if track:
                    track_scores[track.id] = {
                        "rank": rank,
                        "score": round(1.0 - (rank - 1) / total, 4),
                    }

            _bulk_upsert_cold_start(track_scores, ColdStartTrack.Source.SPOTIFY_GLOBAL)

            logger.info(f"Spotify GLOBAL – finished ({total} unique tracks from {len(SPOTIFY_RECOMMENDATION_GENRES)} genres)")
            return f"Spotify: {total} tracks"

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
                user=User.objects.filter(spotifyaccount__isnull=False).first()
            except User.DoesNotExist:
                logger.error("Cold start user not found")
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

def normalize_track_name(name: str) -> str:
    if not name:
        return name

    name = re.sub(r"\(feat.*?\)", "", name, flags=re.IGNORECASE)

    name = re.sub(r"\[feat.*?\]", "", name, flags=re.IGNORECASE)

    name = name.replace('"', '').replace('*', '')

    name = re.sub(r"\s+", " ", name)

    return name.strip()

@shared_task(
    bind=True,
    autoretry_for=(requests.RequestException,),
    retry_backoff=5,
    max_retries=3,
)
def enrich_missing_spotify_ids(self):

    lock = ResourceLock("enrich_missing_spotify_ids", "spotify_global", timeout=600)

    try:
        with lock:
            logger.info(" Enrich missing Spotify IDs – started")

            tracks = (
                Track.objects
                .filter(spotify_id__isnull=True)
                .prefetch_related("artists")
                .order_by("id")[:300]  # LIMIT BATCH
            )

            if not tracks:
                logger.info("No tracks to enrich")
                return "No tracks"

            user = User.objects.filter(spotifyaccount__isnull=False).first()
            if not user:
                logger.error("No Spotify user found")
                return

            token = ensure_spotify_token(user)
            if not token:
                logger.error("No Spotify token")
                return

            headers = {"Authorization": f"Bearer {token.access_token}"}

            updated = 0

            for track in tracks:
                clean_name = normalize_track_name(track.name)

                query = f'track:"{clean_name}"'
                first_artist = track.artists.first()
                if first_artist:
                    query += f' artist:"{first_artist.name}"'

                try:
                    resp = requests.get(
                        "https://api.spotify.com/v1/search",
                        headers=headers,
                        params={
                            "q": query,
                            "type": "track",
                            "limit": 1,
                            "market": "US",
                        },
                        timeout=10,
                    )
                    resp.raise_for_status()
                except requests.RequestException as e:
                    logger.warning(f"Search failed for {track.name}: {e}")
                    continue

                items = resp.json()["tracks"]["items"]
                if not items:
                    continue

                data = items[0]

                # UPDATE TRACK
                track.spotify_id = data["id"]
                track.preview_url = data.get("preview_url")
                track.duration_ms = data.get("duration_ms")

                album_data = data.get("album", {})
                images = album_data.get("images")
                if images:
                    track.image_url = images[0]["url"]

                track.save(update_fields=[
                    "spotify_id",
                    "preview_url",
                    "duration_ms",
                    "image_url",
                ])

                updated += 1

            logger.info(f"✅ Enriched {updated} tracks with Spotify IDs")
            return f"Updated {updated} tracks"

    except ResourceLockedException:
        logger.info("enrich_missing_spotify_ids already running")
        return "Locked"


@shared_task(bind=True)
def expand_track_catalog(self):
    """
    Orchestrator: fetches top 300 artists from DB, splits into
    batches of 10, dispatches parallel tasks to fetch related
    artists and their top tracks from Spotify.
    """
    lock = ResourceLock("expand_catalog", "global", timeout=3600)
    try:
        with lock:
            logger.info("Expand catalog – started")

            user = User.objects.filter(spotifyaccount__isnull=False).first()
            if not user:
                logger.error("No Spotify user found")
                return "No user"

            token = ensure_spotify_token(user)
            if not token:
                logger.error("No Spotify token")
                return "No token"

            # Seed artists: top by popularity, must have spotify_id
            seed_artists = list(
                Artist.objects
                .filter(spotify_id__isnull=False)
                .exclude(spotify_id="")
                .order_by("-popularity")
                .values_list("spotify_id", flat=True)
                [:EXPAND_SEED_LIMIT]
            )

            if not seed_artists:
                logger.warning("No seed artists found")
                return "No seeds"

            # Known artist IDs to skip (avoid re-fetching top tracks)
            known_artist_ids = list(
                Artist.objects
                .filter(spotify_id__isnull=False)
                .exclude(spotify_id="")
                .values_list("spotify_id", flat=True)
            )

            # Split into batches
            batches = [
                seed_artists[i:i + EXPAND_BATCH_SIZE]
                for i in range(0, len(seed_artists), EXPAND_BATCH_SIZE)
            ]

            logger.info(
                f"Dispatching {len(batches)} batches "
                f"({len(seed_artists)} seed artists)"
            )

            chord(
                group(*[
                    fetch_related_artists_batch.s(
                        batch,
                        token.access_token,
                        known_artist_ids,
                    )
                    for batch in batches
                ]),
                expand_catalog_save.s(),
            ).delay()

            return f"Dispatched {len(batches)} batches"

    except ResourceLockedException:
        logger.info("Expand catalog already running – skipped")
        return "Locked"


@shared_task(
    autoretry_for=(requests.RequestException,),
    max_retries=3,
    retry_backoff=10,
)
def fetch_related_artists_batch(
    artist_spotify_ids: list,
    access_token: str,
    known_artist_ids: list,
):
    """
    Worker task: for each seed artist, fetch related artists from
    Spotify, then fetch top tracks for each NEW related artist.
    Returns list of Spotify track data dicts.
    """
    headers = {"Authorization": f"Bearer {access_token}"}
    known_set = set(known_artist_ids)
    all_tracks = []
    seen_track_ids = set()

    def _refresh_headers():
        """Re-fetch token if expired (401)."""
        nonlocal headers
        u = User.objects.filter(spotifyaccount__isnull=False).first()
        if u:
            t = ensure_spotify_token(u)
            if t:
                headers = {"Authorization": f"Bearer {t.access_token}"}

    def _get(url, params=None):
        """GET with auto-retry on 401."""
        resp = requests.get(url, headers=headers, params=params, timeout=15)
        if resp.status_code == 401:
            _refresh_headers()
            resp = requests.get(url, headers=headers, params=params, timeout=15)
        resp.raise_for_status()
        return resp.json()

    for seed_id in artist_spotify_ids:
        # 1. Related artists
        try:
            data = _get(
                f"https://api.spotify.com/v1/artists/{seed_id}/related-artists"
            )
        except requests.RequestException as e:
            logger.warning(f"Related artists failed for {seed_id}: {e}")
            continue

        related = data.get("artists", [])
        time.sleep(EXPAND_REQUEST_DELAY)

        # 2. Top tracks for each NEW related artist
        for rel_artist in related:
            rel_id = rel_artist.get("id")
            if not rel_id or rel_id in known_set:
                continue

            # Mark as known so we don't fetch again in this batch
            known_set.add(rel_id)

            try:
                top_data = _get(
                    f"https://api.spotify.com/v1/artists/{rel_id}/top-tracks",
                    params={"market": "US"},
                )
            except requests.RequestException as e:
                logger.warning(f"Top tracks failed for {rel_id}: {e}")
                continue

            for track in top_data.get("tracks", []):
                tid = track.get("id")
                if tid and tid not in seen_track_ids:
                    seen_track_ids.add(tid)
                    all_tracks.append(track)

            time.sleep(EXPAND_REQUEST_DELAY)

    logger.info(
        f"Batch done: {len(artist_spotify_ids)} seeds → "
        f"{len(all_tracks)} new tracks"
    )
    return all_tracks


@shared_task
def expand_catalog_save(all_results):
    """
    Chord callback: receives track lists from all batch tasks.
    Deduplicates, saves via save_tracks_bulk, creates ColdStartTrack entries.
    """
    # Flatten + deduplicate
    unique_tracks = []
    seen_ids = set()

    for batch_tracks in all_results:
        if not batch_tracks:
            continue
        for track_data in batch_tracks:
            tid = track_data.get("id")
            if tid and tid not in seen_ids:
                seen_ids.add(tid)
                unique_tracks.append(track_data)

    logger.info(
        f"Expand catalog save: {len(unique_tracks)} unique tracks "
        f"from {len(all_results)} batches"
    )

    if not unique_tracks:
        logger.warning("No tracks to save from catalog expansion")
        return "No tracks"

    # Save to DB
    tracks_cache = save_tracks_bulk(unique_tracks)

    # Sort by popularity descending for ranking
    scored_tracks = []
    for td in unique_tracks:
        track = tracks_cache.get(td["id"])
        if track:
            popularity = td.get("popularity") or 0
            scored_tracks.append((track.id, popularity))

    scored_tracks.sort(key=lambda x: x[1], reverse=True)

    # Build cold start scores
    track_scores = {}
    total = len(scored_tracks)
    for rank, (track_id, popularity) in enumerate(scored_tracks, start=1):
        track_scores[track_id] = {
            "rank": rank,
            "score": round(popularity / 100.0, 4),
        }

    _bulk_upsert_cold_start(track_scores, ColdStartTrack.Source.SPOTIFY_RELATED)

    logger.info(
        f"✅ Catalog expansion complete: {len(track_scores)} tracks saved "
        f"as SPOTIFY_RELATED"
    )
    return f"Expanded: {len(track_scores)} tracks"

