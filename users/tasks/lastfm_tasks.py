from celery import shared_task
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
)

logger = logging.getLogger(__name__)

LASTFM_URL = "https://ws.audioscrobbler.com/2.0/"
LASTFM_DAYS_TTL = 30

# ============================================================
# HELPERS
# ============================================================

def get_lastfm_api_key() -> str | None:
    return getattr(settings, "LAST_FM_API_KEY", None)


def lastfm_get(params: dict) -> dict | None:
    api_key = get_lastfm_api_key()
    if not api_key:
        logger.error("LAST_FM_API_KEY not set")
        return None

    try:
        response = requests.get(
            LASTFM_URL,
            params={**params, "api_key": api_key, "format": "json"},
            timeout=10,
        )
        response.raise_for_status()
        return response.json()
    except requests.RequestException as e:
        logger.warning("Last.fm request failed", exc_info=e)
        return None


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


# ============================================================
# ORCHESTRATION – USER TOP ARTISTS
# ============================================================

def sync_user_top_artists(user_id: int) -> None:
    artist_ids = (
        UserTopItem.objects
        .filter(user_id=user_id, item_type="artist")
        .values_list("artist_id", flat=True)
    )

    for artist_id in set(artist_ids):
        get_artist_info.delay(artist_id)


# ============================================================
# TASK – FETCH ARTIST INFO
# ============================================================

@shared_task(
    bind=True,
    autoretry_for=(requests.RequestException,),
    retry_kwargs={"max_retries": 3, "countdown": 10},
)
def get_artist_info(self, artist_id: int) -> None:
    try:
        with ResourceLock("artist-info", artist_id, timeout=600):
            _fetch_artist_info(artist_id)
    except ResourceLockedException:
        logger.info("Artist info already being processed", extra={"artist_id": artist_id})


def _fetch_artist_info(artist_id: int) -> None:
    artist = Artist.objects.filter(id=artist_id).first()
    if not artist:
        return

    lastfm = ArtistLastFMData.objects.filter(artist=artist).first()
    if lastfm and timezone.now() - lastfm.fetched_at < timedelta(days=LASTFM_DAYS_TTL):
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
    lastfm = (
        ArtistLastFMData.objects
        .select_related("artist")
        .filter(artist_id=artist_id)
        .first()
    )

    if not lastfm or not lastfm.raw_tags:
        return

    artist = lastfm.artist

    # Idempotency
    ArtistTag.objects.filter(artist=artist, source="lastfm").delete()

    to_create: list[ArtistTag] = []

    for raw in lastfm.raw_tags:
        name = raw.get("name")
        count = int(raw.get("count", 0))

        if not name or count <= 0:
            continue

        normalized = Tag.normalize(name)
        tag = get_cached_tag(normalized, name)

        to_create.append(
            ArtistTag(
                artist=artist,
                tag=tag,
                source="lastfm",
                raw_count=count,
                weight=min(count / 100.0, 1.0),
                is_active=True,
            )
        )

    if to_create:
        ArtistTag.objects.bulk_create(
            to_create,
            ignore_conflicts=True,
        )

    logger.info(
        "Processed Last.fm artist tags",
        extra={
            "artist_id": artist.id,
            "artist_name": artist.name,
            "tags_count": len(to_create),
        }
    )

