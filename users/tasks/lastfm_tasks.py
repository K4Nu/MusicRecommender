from celery import shared_task,chord
from django.utils import timezone
from datetime import timedelta, datetime
import logging
from utils.locks import ResourceLock,ResourceLockedException
from users.models import *
logger = logging.getLogger(__name__)

def get_artists_info(user_id):

    items=UserTopItem.objects.filter(id=user_id,item_type="artist",time_range="short_term")
    for item in items:
        try:
            with ResourceLock(item.artist.id) as lock:
                get_artist_info.delay(item.artist.id)
        except ResourceLockedException:
            logger.info(f'Sync already in run')


def get_artist_info(artist_id):
    pass