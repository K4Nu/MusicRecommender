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
    ArtistSimilarity,
    TrackTag,
    TrackSimilarity,
    Track,
    TrackLastFMData
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
            timeout=(3,20),
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

    logger.info(f"🚀 Calling get_artist_tags.delay for {artist.name} (id={artist.id})")
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

            # Sprawdź ile faktycznie zostało utworzonych
            actual_count = ArtistTag.objects.filter(artist=artist, source="lastfm").count()
            logger.info(f"✅ Database now has {actual_count} ArtistTag records for {artist.name}")

        except Exception as e:
            logger.error(f"❌ bulk_create failed: {e}", exc_info=True)
    else:
        logger.warning(f"⚠️ No tags to create for {artist.name}")

    logger.info(f"🏁 END _process_artist_tags for {artist.name}")

def get_similiar_artists(artist_id: int) -> None:
    artist = Artist.objects.filter(id=artist_id).first()
    if not artist:
        return

    data = lastfm_get({
        "method": "artist.getSimilar",
        "artist": artist.name,
        "autocorrect": 1,
        "limit": 50,
    })

    similar_items = (
        data
        .get("similarartists", {})
        .get("artist", [])
        if data else []
    )

    for item in similar_items:
        name = item.get("name")
        match = float(item.get("match", 0))

        if not name or match <= 0:
            continue

        process_similar_artist.delay(
            artist_id=artist.id,
            similar_name=name,
            score=match,
            image_url=item.get("image", [{}])[-1].get("#text"),
            mbid=item.get("mbid"),
        )

@shared_task(
    autoretry_for=(Exception,),
    retry_kwargs={"max_retries": 3, "countdown": 10},
)
def get_similar_artists_task(artist_id: int) -> None:
    get_similiar_artists(artist_id)

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
    lock_id = f"{artist_id}:{similar_name.lower()}"
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

    similar_artist = (
        Artist.objects
        .filter(name__iexact=similar_name)
        .first()
    )

    if not similar_artist:
        similar_artist = Artist.objects.create(
            name=similar_name,
            image_url=image_url,
        )

    # Normalize score to [0,1]
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

@shared_task
def sync_user_top_tracks(user_id:int)->None:
    tracks_ids=set(
        UserTopItem.objects.filter(
            user_id=user_id,
            item_type="track",
        ).values_list("track_id", flat=True)
    )

    for track_id in tracks_ids:
        get_track_info.delay(track_id)
        get_similiar_tracks.delay(track_id)

@shared_task(
    bind=True,
    autoretry_for=(requests.RequestException,),
    retry_kwargs={"max_retries": 3, "countdown": 10},
)
def get_track_info(track_id: int):
    pass

def get_similar_track_task(artist_id: int) -> None:
    get_similiar_artists(artist_id)

def get_similiar_tracks(artist_id: int) -> None:
    pass