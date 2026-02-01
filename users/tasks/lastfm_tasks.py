from collections import defaultdict
import hashlib
from celery import shared_task, chain
from django.utils import timezone
from django.conf import settings
from datetime import timedelta
import logging
import requests

from utils.locks import ResourceLock, ResourceLockedException
from users.models import (
    Artist,
    ArtistLastFMData,
    ArtistTag,
    Tag,
    UserTopItem,
    ArtistSimilarity,
    TrackTag,
    TrackSimilarity,
    Track,
    TrackLastFMData,
    Album
)
from django.db import transaction

logger = logging.getLogger(__name__)

LASTFM_URL = "https://ws.audioscrobbler.com/2.0/"
LASTFM_DAYS_TTL = 30


# ============================================================
# HELPERS
# ============================================================

def get_lastfm_api_key() -> str | None:
    return getattr(settings, "LAST_FM_API_KEY", None)


def lastfm_get(params: dict) -> dict | None:
    """
    Wykonuje zapytanie do Last.fm API z obsługą błędów.

    Returns:
        dict | None: JSON response lub None jeśli zasób nie istnieje (404)

    Raises:
        requests.RequestException: dla błędów sieci/timeoutu (będzie retry)
        requests.HTTPError: dla błędów serwera 5xx (będzie retry)
    """
    api_key = get_lastfm_api_key()
    if not api_key:
        logger.error("LAST_FM_API_KEY not set")
        return None

    try:
        response = requests.get(
            LASTFM_URL,
            params={**params, "api_key": api_key, "format": "json"},
            timeout=(3, 20),
        )
        response.raise_for_status()
        return response.json()

    except requests.HTTPError as e:
        status_code = e.response.status_code

        # Błędy serwera (5xx) - Last.fm ma problem, warto spróbować ponownie
        if status_code >= 500:
            logger.warning(
                f"Last.fm server error {status_code} - will retry",
                extra={
                    "params": params,
                    "status": status_code,
                    "url": e.response.url
                }
            )
            # Propaguj wyjątek - Celery spróbuje ponownie
            raise

        # 404 - zasób nie istnieje, nie ma sensu retry
        elif status_code == 404:
            logger.info(
                "Last.fm resource not found (404)",
                extra={"params": params}
            )
            return None

        # 429 - rate limit
        elif status_code == 429:
            logger.warning(
                "Last.fm rate limit exceeded",
                extra={"params": params}
            )
            # Propaguj - Celery może spróbować z dłuższym opóźnieniem
            raise

        # 400, 403 - błąd klienta, nie ma sensu retry
        elif status_code in (400, 403):
            logger.error(
                f"Last.fm client error {status_code}",
                extra={"params": params, "response": e.response.text[:200]}
            )
            return None

        # Inne błędy HTTP
        else:
            logger.error(
                f"Last.fm HTTP error {status_code}",
                extra={"params": params}
            )
            raise

    except requests.Timeout as e:
        logger.warning(
            "Last.fm request timeout - will retry",
            extra={"params": params}
        )
        raise

    except requests.ConnectionError as e:
        logger.warning(
            "Last.fm connection error - will retry",
            extra={"params": params}
        )
        raise

    except requests.RequestException as e:
        logger.error(
            "Last.fm request failed",
            extra={"params": params, "error": str(e)}
        )
        raise


def safe_cache_key(value: str) -> str:
    """
    Produces memcached-safe cache key.
    """
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


# ============================================================
# TAG CACHE (per worker, soft-limited)
# ============================================================

TAG_CACHE: dict[str, Tag] = {}
MAX_TAG_CACHE_SIZE = 5_000


def get_cached_tag(normalized: str, name: str) -> Tag:
    tag = TAG_CACHE.get(normalized)
    if tag:
        return tag

    tag, _ = Tag.objects.get_or_create(
        normalized_name=normalized,
        defaults={"name": name},
    )

    if len(TAG_CACHE) >= MAX_TAG_CACHE_SIZE:
        TAG_CACHE.clear()

    TAG_CACHE[normalized] = tag
    return tag


@shared_task
def lastfm_initial_sync(user_id: int) -> None:
    chain(
        sync_user_top_artists.si(user_id),
        sync_user_top_tracks.si(user_id),
        sync_end.si(),
    ).delay()


# ============================================================
# ORCHESTRATION – USER TOP ARTISTS
# ============================================================
@shared_task
def sync_end():
    logger.info("Sync finished")
    return


@shared_task
def sync_user_top_artists(user_id: int) -> None:
    artist_ids = set(
        UserTopItem.objects
        .filter(user_id=user_id, item_type="artist")
        .values_list("artist_id", flat=True)
    )

    for artist_id in artist_ids:
        get_artist_info.delay(artist_id)
        get_similar_artists_task.delay(artist_id)


# ============================================================
# TASK – FETCH ARTIST INFO
# ============================================================

@shared_task(
    bind=True,
    autoretry_for=(requests.RequestException,),
    retry_kwargs={"max_retries": 3, "countdown": 10},
    retry_backoff=True,  # Exponential backoff
    retry_backoff_max=600,  # Max 10 minut
    retry_jitter=True,  # Losowe opóźnienie
)
def get_artist_info(self, artist_id: int) -> None:
    try:
        with ResourceLock("artist-info", artist_id, timeout=600):
            _fetch_artist_info(artist_id)
    except ResourceLockedException:
        logger.info("Artist info already being processed", extra={"artist_id": artist_id})


def _fetch_artist_info(artist_id: int) -> None:
    artist = (
        Artist.objects
        .select_related('lastfm_cache')
        .filter(id=artist_id)
        .first()
    )

    if not artist:
        return

    if hasattr(artist, 'lastfm_cache'):
        lastfm = artist.lastfm_cache
        if timezone.now() - lastfm.fetched_at < timedelta(days=LASTFM_DAYS_TTL):
            return

    data = lastfm_get({
        "method": "artist.getInfo",
        "artist": artist.name,
        "autocorrect": 1,
    })

    if not data or "artist" not in data:
        logger.warning("No artist data from Last.fm", extra={"artist": artist.name})
        return

    artist_data = data["artist"]
    stats = artist_data.get("stats") or {}

    ArtistLastFMData.objects.update_or_create(
        artist=artist,
        defaults={
            "lastfm_name": artist_data.get("name", artist.name),
            "lastfm_url": artist_data.get("url"),
            "mbid": artist_data.get("mbid"),
            "listeners": int(stats.get("listeners", 0) or 0),
            "playcount": int(stats.get("playcount", 0) or 0),
            "raw_tags": artist_data.get("tags", {}).get("tag", []),
            "fetched_at": timezone.now(),
        },
    )

    get_artist_tags.delay(artist.id)
    logger.info("Fetched Last.fm artist info", extra={"artist": artist.name})


# ============================================================
# ORCHESTRATION – TAG PROCESSING
# ============================================================

def sync_all_artist_tags() -> None:
    artist_ids = ArtistLastFMData.objects.values_list("artist_id", flat=True)
    for artist_id in set(artist_ids):
        get_artist_tags.delay(artist_id)


# ============================================================
# TASK – PROCESS ARTIST TAGS
# ============================================================

@shared_task
def get_artist_tags(artist_id: int) -> None:
    try:
        with ResourceLock("artist-tags", artist_id, timeout=600):
            _process_artist_tags(artist_id)
    except ResourceLockedException:
        logger.info("Artist tags already being processed", extra={"artist_id": artist_id})


def _process_artist_tags(artist_id: int) -> None:
    logger.info(f"🔍 START _process_artist_tags for artist_id={artist_id}")

    lastfm = (
        ArtistLastFMData.objects
        .select_related("artist")
        .filter(artist_id=artist_id)
        .first()
    )

    if not lastfm:
        logger.warning(f"❌ No ArtistLastFMData found for artist_id={artist_id}")
        return

    logger.info(f"✅ Found ArtistLastFMData for {lastfm.artist.name}")

    if not lastfm.raw_tags:
        logger.warning(f"❌ No raw_tags for {lastfm.artist.name}, raw_tags={lastfm.raw_tags}")
        return

    logger.info(f"✅ Found {len(lastfm.raw_tags)} raw tags")

    artist = lastfm.artist

    # Idempotency
    deleted_count = ArtistTag.objects.filter(artist=artist, source="lastfm").delete()[0]
    logger.info(f"🗑️ Deleted {deleted_count} existing ArtistTag records")

    to_create: list[ArtistTag] = []

    for idx, raw in enumerate(lastfm.raw_tags):
        logger.info(f"📝 Processing tag {idx}: {raw}")

        name = raw.get("name")
        if not name:
            logger.warning(f"⚠️ Tag {idx} has no name, skipping")
            continue

        try:
            count = int(raw.get("count"))
            weight = min(count / 100.0, 1.0)
            logger.info(f"✅ Tag '{name}': count={count}, weight={weight}")
        except (TypeError, ValueError) as e:
            weight = max(0.1, 1.0 - idx * 0.1)
            count = None
            logger.info(f"⚠️ Tag '{name}': no count, using fallback weight={weight}, error={e}")

        normalized = Tag.normalize(name)
        tag = get_cached_tag(normalized, name)

        logger.info(f"✅ Got/created Tag: {tag.name} (normalized: {normalized})")

        to_create.append(
            ArtistTag(
                artist=artist,
                tag=tag,
                source="lastfm",
                raw_count=count,
                weight=weight,
                is_active=True,
            )
        )

    logger.info(f"📊 Prepared {len(to_create)} ArtistTag objects to create")

    if to_create:
        try:
            result = ArtistTag.objects.bulk_create(
                to_create,
                ignore_conflicts=True,
            )
            logger.info(f"✅ bulk_create returned {len(result)} objects")

            actual_count = ArtistTag.objects.filter(artist=artist, source="lastfm").count()
            logger.info(f"✅ Database now has {actual_count} ArtistTag records for {artist.name}")

        except Exception as e:
            logger.error(f"❌ bulk_create failed: {e}", exc_info=True)
    else:
        logger.warning(f"⚠️ No tags to create for {artist.name}")

    track_ids = artist.tracks.values_list("id", flat=True)

    for track_id in track_ids:
        inherit_track_tags_task.delay(track_id)

    logger.info(
        "Scheduled tag inheritance for artist tracks",
        extra={
            "artist": artist.name,
            "tracks": len(track_ids),
        }
    )
    get_similar_artists_task.delay(artist.id)
    logger.info(f"🏁 END _process_artist_tags for {artist.name}")


# ============================================================
# SIMILAR ARTISTS
# ============================================================

def get_similar_artists(artist_id: int) -> None:
    artist = Artist.objects.filter(id=artist_id).first()
    if not artist:
        return

    data = lastfm_get({
        "method": "artist.getSimilar",
        "artist": artist.name,
        "autocorrect": 1,
        "limit": 50,
    })

    if not data:
        # lastfm_get zwraca None dla 404 lub błędów klienta
        return

    similar_items = data.get("similarartists", {}).get("artist")

    if not similar_items:
        logger.warning(
            "No similar artists from Last.fm",
            extra={"artist": artist.name, "response": data}
        )
        return

    for item in similar_items:
        name = item.get("name")
        if not name:
            continue

        try:
            match = float(item.get("match", 0.0))
        except (ValueError, TypeError):
            continue

        if match <= 0:
            continue

        images = item.get("image", [])
        image_url = images[-1].get("#text") if images else None

        process_similar_artist.delay(
            artist_id=artist.id,
            similar_name=name,
            score=match,
            image_url=image_url,
            mbid=item.get("mbid"),
        )


@shared_task(
    autoretry_for=(requests.RequestException,),
    retry_kwargs={"max_retries": 3, "countdown": 10},
    retry_backoff=True,
    retry_backoff_max=600,
    retry_jitter=True,
)
def get_similar_artists_task(artist_id: int) -> None:
    get_similar_artists(artist_id)


@shared_task(
    autoretry_for=(Exception,),
    retry_kwargs={"max_retries": 3, "countdown": 10},
)
def process_similar_artist(
        artist_id: int,
        similar_name: str,
        score: float,
        image_url: str | None = None,
        mbid: str | None = None,
) -> None:
    lock_id = safe_cache_key(f"{artist_id}:{similar_name}")
    try:
        with ResourceLock("artist-similar-lastfm", lock_id, timeout=600):
            _process_similar_artist(
                artist_id,
                similar_name,
                score,
                image_url,
                mbid,
            )
    except ResourceLockedException:
        logger.info(
            "Last.fm similar artist already processed",
            extra={"artist_id": artist_id, "similar": similar_name},
        )


def _process_similar_artist(
        artist_id: int,
        similar_name: str,
        score: float,
        image_url: str | None = None,
        mbid: str | None = None,
) -> None:
    artist = Artist.objects.filter(id=artist_id).first()
    if not artist:
        return

    similar_artist = Artist.objects.filter(name__iexact=similar_name).first()

    if not similar_artist:
        similar_artist = Artist.objects.create(
            name=similar_name,
            image_url=image_url,
        )

    score = max(0.0, min(float(score), 1.0))

    ArtistSimilarity.objects.update_or_create(
        from_artist=artist,
        to_artist=similar_artist,
        source="lastfm",
        defaults={
            "score": score,
            "score_breakdown": {
                "lastfm_match": score,
            },
        },
    )


# ============================================================
# TRACKS
# ============================================================

@shared_task
def sync_user_top_tracks(user_id: int) -> None:
    tracks_ids = set(
        UserTopItem.objects.filter(
            user_id=user_id,
            item_type="track",
        ).values_list("track_id", flat=True)
    )

    for track_id in tracks_ids:
        get_track_info.delay(track_id)
        get_similar_track_task.delay(track_id)


@shared_task(
    bind=True,
    autoretry_for=(requests.RequestException,),
    retry_kwargs={"max_retries": 3, "countdown": 10},
    retry_backoff=True,
    retry_backoff_max=600,
    retry_jitter=True,
)
def get_track_info(self, track_id: int):
    try:
        with ResourceLock("track-info", track_id, timeout=600):
            _fetch_track_info(track_id)
    except ResourceLockedException:
        logger.info("Track info already being processed", extra={"track_id": track_id})


def _fetch_track_info(track_id: int):
    track = (
        Track.objects
        .select_related('album', 'lastfm_cache')
        .prefetch_related('artists')
        .filter(id=track_id)
        .first()
    )

    if not track:
        logger.info("Track not found", extra={"track_id": track_id})
        return

    if hasattr(track, 'lastfm_cache'):
        lastfm = track.lastfm_cache
        if timezone.now() - lastfm.fetched_at < timedelta(days=LASTFM_DAYS_TTL):
            return

    artist = track.artists.first()
    if not artist:
        logger.warning("Track has no artists", extra={"track_id": track_id})
        return

    data = lastfm_get({
        "method": "track.getInfo",
        "track": track.name,
        "artist": artist.name,
        "autocorrect": 1,
    })

    if not data or "error" in data or "track" not in data:
        logger.warning(
            "Last.fm track.getInfo failed",
            extra={"track_id": track_id, "response": data}
        )
        return

    track_data = data["track"]
    stats = track_data.get("stats", {})
    artist_data = track_data.get("artist")
    artist_name = None

    if isinstance(artist_data, dict):
        artist_name = artist_data.get("name")

    TrackLastFMData.objects.update_or_create(
        track=track,
        defaults={
            "lastfm_name": track_data.get("name", track.name),
            "lastfm_artist_name": artist_name,
            "lastfm_url": track_data.get("url"),
            "mbid": track_data.get("mbid"),
            "listeners": int(stats.get("listeners", 0) or 0),
            "playcount": int(stats.get("playcount", 0) or 0),
            "fetched_at": timezone.now(),
            "raw_tags": track_data.get("tags", {}).get("tag", []),
        },
    )

    artist_name = track_data.get("artist", {}).get("name")
    if artist_name:
        artist_obj = Artist.objects.filter(name__iexact=artist_name).first()
        if not artist_obj:
            artist_obj = Artist.objects.create(name=artist_name)

        get_artist_info.delay(artist_obj.id)

    logger.info(f'Fetch for {track_id} was successful')


# ============================================================
# SIMILAR TRACKS
# ============================================================

@shared_task(
    autoretry_for=(requests.RequestException,),
    retry_kwargs={"max_retries": 3, "countdown": 10},
    retry_backoff=True,
    retry_backoff_max=600,
    retry_jitter=True,
)
def get_similar_track_task(track_id: int) -> None:
    get_similar_tracks(track_id)


def get_similar_tracks(track_id: int) -> None:
    """Pobiera podobne utwory z Last.fm"""
    try:
        track = (
            Track.objects
            .select_related('lastfm_cache')
            .prefetch_related('artists')
            .get(id=track_id)
        )
    except Track.DoesNotExist:
        return

    params = {
        "method": "track.getSimilar",
        "autocorrect": 1,
        "limit": 50,
    }

    if hasattr(track, "lastfm_cache") and track.lastfm_cache.mbid:
        params["mbid"] = track.lastfm_cache.mbid
    else:
        artist = track.artists.first()
        if not artist:
            logger.info("Track has no artist", extra={"track_id": track_id})
            return
        params["track"] = track.name
        params["artist"] = artist.name

    data = lastfm_get(params)
    if not data:
        # lastfm_get zwraca None dla 404 lub błędów klienta
        logger.info(
            "No similar tracks data from Last.fm",
            extra={"track_id": track_id, "params": params}
        )
        return

    similar_items = data.get("similartracks", {}).get("track", [])
    if not similar_items:
        logger.info("No similar tracks found", extra={"track_id": track_id})
        return

    for item in similar_items:
        name = item.get("name")
        if not name:
            continue

        try:
            match = float(item.get("match", 0.0))
        except (ValueError, TypeError):
            continue

        if match <= 0:
            continue

        # Extract artist info from response
        artist_data = item.get("artist")
        artist_name = None

        if isinstance(artist_data, dict):
            artist_name = artist_data.get("name")
        elif isinstance(artist_data, str):
            artist_name = artist_data

        images = item.get("image", [])
        image_url = images[-1].get("#text") if images else None

        # Pass artist_name to processing task
        process_similar_track.delay(
            track_id=track.id,
            similar_name=name,
            similar_artist_name=artist_name,
            score=match,
            image_url=image_url,
            mbid=item.get("mbid"),
        )


@shared_task(
    autoretry_for=(Exception,),
    retry_kwargs={"max_retries": 3, "countdown": 10},
)
def process_similar_track(
        track_id: int,
        similar_name: str,
        similar_artist_name: str | None,
        score: float,
        image_url: str | None = None,
        mbid: str | None = None,
) -> None:
    raw_key = f"{track_id}:{similar_name}:{similar_artist_name}"
    lock_id = safe_cache_key(raw_key)

    try:
        with ResourceLock(
                resource_type="track-similar-lastfm",
                resource_id=lock_id,
                timeout=600
        ):
            _process_similar_track(
                track_id,
                similar_name,
                similar_artist_name,
                score,
                image_url,
                mbid,
            )
    except ResourceLockedException:
        logger.info(
            "Similar track already being processed",
            extra={"track_id": track_id, "similar": similar_name},
        )


def _process_similar_track(
        track_id: int,
        similar_name: str,
        similar_artist_name: str | None,
        score: float,
        image_url: str | None = None,
        mbid: str | None = None,
) -> None:
    track = Track.objects.filter(id=track_id).first()
    if not track:
        return

    logger.info(
        "Processing similar track",
        extra={
            "from_track": track.name,
            "similar_track": similar_name,
            "artist": similar_artist_name,
            "mbid": mbid,
        }
    )

    similar_track = None

    if mbid:
        similar_track = (
            Track.objects
            .filter(lastfm_cache__mbid=mbid)
            .select_related("lastfm_cache")
            .first()
        )

    # 2️⃣ Name + artist (soft match)
    if not similar_track and similar_artist_name:
        candidates = (
            Track.objects
            .filter(name__iexact=similar_name)
            .prefetch_related("artists")
        )

        for candidate in candidates:
            artist = candidate.artists.first()
            if artist and artist_names_compatible(
                    artist.name,
                    similar_artist_name
            ):
                similar_track = candidate
                break

    # 3️⃣ Name-only fallback (VERY soft)
    if not similar_track:
        similar_track = (
            Track.objects
            .filter(name__iexact=similar_name)
            .first()
        )

    # 4️⃣ CREATE — NORMAL & EXPECTED PATH
    if not similar_track:
        logger.info(
            "Creating similar track from Last.fm",
            extra={
                "track": similar_name,
                "artist": similar_artist_name,
                "mbid": mbid,
            }
        )

        similar_track = _create_track_from_lastfm(
            track_name=similar_name,
            artist_name=similar_artist_name,
            mbid=mbid,
            image_url=image_url,
        )

        if not similar_track:
            logger.warning(
                "Failed to create similar track",
                extra={"track": similar_name}
            )
            return

    # 5️⃣ Clamp score
    score = max(0.0, min(float(score), 1.0))

    # 6️⃣ Save similarity (idempotent)
    TrackSimilarity.objects.update_or_create(
        from_track=track,
        to_track=similar_track,
        source="lastfm",
        defaults={
            "score": score,
            "score_breakdown": {
                "lastfm_match": score,
                "used_mbid": bool(mbid),
            },
        },
    )

    logger.info(
        "Created track similarity",
        extra={
            "from": track.name,
            "to": similar_track.name,
            "artist": (
                similar_track.artists.first().name
                if similar_track.artists.exists()
                else None
            ),
            "score": score,
        }
    )


def _create_track_from_lastfm(
        track_name: str,
        artist_name: str | None,
        mbid: str | None = None,
        image_url: str | None = None,
) -> Track | None:
    """
    Create minimal Track stub from Last.fm track.getSimilar response.
    Full data will be enriched async via track.getInfo.
    """

    if not track_name:
        return None

    with transaction.atomic():

        # 1️⃣ Artist (soft create)
        artist = None
        if artist_name:
            artist = Artist.objects.filter(
                name__iexact=artist_name
            ).first()

            if not artist:
                artist = Artist.objects.create(
                    name=artist_name,
                    popularity=None,
                    image_url=None,
                )

        # 2️⃣ Album stub (required by model)
        album_name = f"Unknown album – {artist.name if artist else 'Unknown'}"

        album, _ = Album.objects.get_or_create(
            name=album_name,
            defaults={
                "album_type": Album.AlbumTypes.SINGLE,
                "release_date": None,
                "image_url": image_url,
            },
        )

        if artist:
            album.artists.add(artist)

        # 3️⃣ Track stub
        track = Track.objects.create(
            name=track_name,
            album=album,
            duration_ms=0,  # unknown
            popularity=None,
            preview_url=None,
            image_url=image_url,
        )

        if artist:
            track.artists.add(artist)

        # 4️⃣ Last.fm cache (MINIMAL, REAL DATA)
        TrackLastFMData.objects.update_or_create(
            track=track,
            defaults={
                "lastfm_name": track_name,
                "lastfm_artist_name": artist_name or "",
                "mbid": mbid,
                "listeners": None,
                "playcount": None,
                "raw_tags": [],
                "match_confidence": 0.4,  # created from similar, not direct info
            },
        )

    logger.info(
        "Created Track stub from Last.fm",
        extra={
            "track": track_name,
            "artist": artist_name,
            "mbid": mbid,
        }
    )

    # 5️⃣ ASYNC enrichment (REAL DATA COMES LATER)
    get_track_info.delay(track.id)

    return track


# ============================================================
# TAG INHERITANCE
# ============================================================
@shared_task
def inherit_track_tags_task(track_id: int):
    _inherit_track_tags(track_id)


def _inherit_track_tags(track_id: int) -> None:
    track = (
        Track.objects
        .prefetch_related("artists__artist_tags__tag")
        .filter(id=track_id)
        .first()
    )

    if not track:
        return

    artists = track.artists.all()
    if not artists.exists():
        return

    tag_accumulator = defaultdict(float)
    artist_count = artists.count()

    for artist in artists:
        artist_tags = artist.artist_tags.filter(
            is_active=True,
            source__in=["lastfm", "computed"]
        )

        for at in artist_tags:
            tag_accumulator[at.tag_id] += at.weight

    TrackTag.objects.filter(track=track, source="artist").delete()

    to_create = []
    for tag_id, total_weight in tag_accumulator.items():
        weight = min(total_weight / artist_count, 1.0)

        if weight < 0.05:
            continue

        to_create.append(
            TrackTag(
                track=track,
                tag_id=tag_id,
                weight=weight,
                source="artist",
                is_active=True,
            )
        )

    if to_create:
        TrackTag.objects.bulk_create(to_create, ignore_conflicts=True)

    compute_track_tag_similarity(track)

    logger.info(
        "Inherited track tags from artists",
        extra={
            "track_id": track_id,
            "artists": artist_count,
            "tags_created": len(to_create),
        },
    )


def normalize_name(value: str) -> str:
    return (
        value.lower()
        .replace("&", "and")
        .replace("feat.", "")
        .replace("ft.", "")
        .strip()
    )


def artist_names_compatible(a: str, b: str) -> bool:
    if not a or not b:
        return True

    a = normalize_name(a)
    b = normalize_name(b)

    return a == b or a in b or b in a


# ============================================================
# TAG SIMILARITY - OPTYMALIZACJA
# ============================================================

def compute_track_tag_similarity(track: Track, max_candidates=1000):
    """
    ZOPTYMALIZOWANA wersja:
    1. Pobiera tylko tracki mające wspólne tagi
    2. Limituje liczbę kandydatów
    3. Batch operations
    """
    track_tags = {
        tt.tag_id: tt.weight
        for tt in track.track_tags.filter(is_active=True)
    }

    if not track_tags:
        return

    tag_ids = list(track_tags.keys())
    candidate_ids = (
        TrackTag.objects
        .filter(tag_id__in=tag_ids, is_active=True)
        .exclude(track_id=track.id)
        .values_list('track_id', flat=True)
        .distinct()[:max_candidates]  # LIMIT!
    )

    if not candidate_ids:
        return

    #  Batch prefetch
    candidates = Track.objects.filter(id__in=candidate_ids).prefetch_related('track_tags')

    similarities_to_create = []

    for other in candidates:
        other_tags = {
            tt.tag_id: tt.weight
            for tt in other.track_tags.filter(is_active=True)
        }

        common = set(track_tags) & set(other_tags)
        if not common:
            continue

        score = sum(track_tags[t] * other_tags[t] for t in common)

        if score < 0.3:
            continue

        similarities_to_create.append(
            TrackSimilarity(
                from_track=track,
                to_track=other,
                source="tags",
                score=min(score, 1.0),
                score_breakdown={
                    "common_tags": len(common),
                },
            )
        )

    if similarities_to_create:
        TrackSimilarity.objects.filter(
            from_track=track,
            source="tags"
        ).delete()

        TrackSimilarity.objects.bulk_create(
            similarities_to_create,
            ignore_conflicts=True
        )
