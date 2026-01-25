from celery import shared_task
from django.utils import timezone
from datetime import timedelta
import logging
import os
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
api_key=os.environ['LAST_FM_API_KEY']

# In-memory cache (per Celery worker)
TAG_CACHE: dict[str, Tag] = {}

# ============================================================
# ORCHESTRATION – USER TOP ARTISTS
# ============================================================

def sync_user_top_artists(user_id: int) -> None:
    """
    Queue Last.fm fetch for all artists appearing
    in user's top items (all time ranges).
    """
    artist_ids = (
        UserTopItem.objects
        .filter(user_id=user_id, item_type="artist")
        .values_list("artist_id", flat=True)
    )

    for artist_id in set(artist_ids):
        get_artist_info.delay(artist_id)


# ============================================================
# TASK – FETCH ARTIST INFO FROM LAST.FM
# ============================================================

@shared_task(
    bind=True,
    autoretry_for=(requests.RequestException,),
    retry_kwargs={"max_retries": 3, "countdown": 10},
)
def get_artist_info(self, artist_id: int) -> None:
    """
    Celery task: fetch Last.fm artist info.
    """
    try:
        with ResourceLock("artist-info", artist_id, timeout=600):
            _fetch_artist_info(artist_id)
    except ResourceLockedException:
        logger.info(f"Artist {artist_id} already being processed")


def _fetch_artist_info(artist_id: int) -> None:
    """
    Internal fetch logic (sync, idempotent).
    """
    artist = Artist.objects.filter(id=artist_id).first()
    if not artist:
        return

    lastfm = ArtistLastFMData.objects.filter(artist=artist).first()
    if lastfm and timezone.now() - lastfm.fetched_at < timedelta(days=LASTFM_DAYS_TTL):
        return

    api_key = os.environ.get("LAST_FM_API_KEY")
    if not api_key:
        logger.error("LAST_FM_API_KEY not set")
        return

    params = {
        "method": "artist.getInfo",
        "artist": artist.name,
        "api_key": api_key,
        "format": "json",
        "autocorrect": 1,
    }

    response = requests.get(LASTFM_URL, params=params, timeout=10)
    response.raise_for_status()

    artist_data = response.json().get("artist")
    if not artist_data:
        return

    ArtistLastFMData.objects.update_or_create(
        artist=artist,
        defaults={
            "lastfm_name": artist_data.get("name", artist.name),
            "lastfm_url": artist_data.get("url"),
            "mbid": artist_data.get("mbid"),
            "listeners": int(artist_data["stats"]["listeners"]),
            "playcount": int(artist_data["stats"]["playcount"]),
            "raw_tags": artist_data.get("tags", {}).get("tag", []),
            "fetched_at": timezone.now(),
        },
    )

    logger.info(f"Fetched Last.fm data for {artist.name}")


# ============================================================
# ORCHESTRATION – TAG PROCESSING
# ============================================================

def sync_all_artist_tags() -> None:
    """
    Queue tag processing for all artists
    that have Last.fm data.
    """
    artist_ids = (
        ArtistLastFMData.objects
        .values_list("artist_id", flat=True)
    )

    for artist_id in set(artist_ids):
        get_artist_tags.delay(artist_id)


# ============================================================
# TASK – PROCESS ARTIST TAGS
# ============================================================

@shared_task
def get_artist_tags(artist_id: int) -> None:
    """
    Celery task: process raw Last.fm tags into canonical ArtistTag.
    """
    try:
        with ResourceLock("artist-tags", artist_id, timeout=600):
            _process_artist_tags(artist_id)
    except ResourceLockedException:
        logger.info(f"Tags already processing for artist {artist_id}")


def _process_artist_tags(artist_id: int) -> None:
    """
    Normalize raw tags and bulk-create ArtistTag records.
    """
    lastfm = (
        ArtistLastFMData.objects
        .select_related("artist")
        .filter(artist_id=artist_id)
        .first()
    )

    if not lastfm or not lastfm.raw_tags:
        return

    artist = lastfm.artist

    # Idempotency: remove old Last.fm tags
    ArtistTag.objects.filter(
        artist=artist,
        source="lastfm"
    ).delete()

    to_create: list[ArtistTag] = []

    for raw in lastfm.raw_tags:
        name = raw.get("name")
        count = int(raw.get("count", 0))

        if not name or count <= 0:
            continue

        normalized = Tag.normalize(name)

        tag = TAG_CACHE.get(normalized)
        if not tag:
            tag, _ = Tag.objects.get_or_create(
                normalized_name=normalized,
                defaults={"name": name}
            )
            TAG_CACHE[normalized] = tag

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
        ArtistTag.objects.bulk_create(to_create)

    logger.info(f"Processed {len(to_create)} tags for {artist.name}")

def get_similiar_artists(artist_id):
    try:
        artist=Artist.objects.get(id=artist_id)
    except Artist.DoesNotExist:
        logger.error(f"Artist {artist_id} not found")

    params = {
        "method": "artist.getSimilar",
        "artist": artist.name,
        "api_key": api_key,
        "format": "json",
    }

    try:
        response = requests.get(LASTFM_URL, params=params, timeout=10)
        response.raise_for_status()
        data = response.json()
    except requests.exceptions.RequestException as e:
        logger.error(e)
        return

