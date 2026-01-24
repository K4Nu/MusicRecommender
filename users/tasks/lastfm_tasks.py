from celery import shared_task,chord
from django.utils import timezone
from datetime import timedelta, datetime
import logging
from utils.locks import ResourceLock,ResourceLockedException
from users.models import *
import os
import requests

logger = logging.getLogger(__name__)

URL='https://ws.audioscrobbler.com/2.0/'

def sync_user_top_artists(user_id):
    artist_ids = set(
        UserTopItem.objects
        .filter(user_id=user_id, item_type="artist")
        .values_list("artist_id", flat=True)
    )
    print(f'ids {len(artist_ids)}')
    sync_artists_info(artist_ids)

def sync_artists_info(items):
    for item in items:
        try:
            with ResourceLock(f'info-{item}',item,timeout=600) as lock:
                get_artist_info.delay(item)
        except ResourceLockedException:
            logger.info(f'Sync already in run')
            continue

@shared_task(bind=True, autoretry_for=(Exception,), retry_kwargs={"max_retries": 3, "countdown": 10})
def get_artist_info(self, artist_id):
    try:
        artist = Artist.objects.get(id=artist_id)
    except Artist.DoesNotExist:
        logger.info(f"Artist {artist_id} not found")
        return

    lastfm = ArtistLastFMData.objects.filter(artist=artist).first()
    if lastfm and timezone.now() - lastfm.fetched_at < timedelta(days=30):
        logger.info(f"Artist {artist.name} recently fetched")
        return

    params = {
        "method": "artist.getInfo",
        "artist": artist.name,
        "api_key": os.environ.get("LAST_FM_API_KEY"),
        "format": "json",
        "autocorrect": 1,
    }

    response = requests.get(URL, params=params, timeout=10)
    response.raise_for_status()
    data = response.json()

    if "error" in data:
        logger.warning(f"Last.fm error for {artist.name}: {data.get('message')}")
        return

    artist_data = data["artist"]

    ArtistLastFMData.objects.update_or_create(
        artist=artist,
        defaults={
            "lastfm_name": artist_data["name"],
            "lastfm_url": artist_data.get("url"),
            "mbid": artist_data.get("mbid"),
            "listeners": artist_data["stats"]["listeners"],
            "playcount": artist_data["stats"]["playcount"],
            "raw_tags": artist_data.get("tags", {}).get("tag", []),
        },
    )

    logger.info(f"Fetched Last.fm data for {artist.name}")

