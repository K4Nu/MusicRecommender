from celery import chain, chord, shared_task
import logging

logger=logging.getLogger(__name__)

def cold_start_refresh_all():
    cold_start_fetch_spotify_global()
    cold_start_fetch_spotify_viral()
    cold_start_fetch_lastfm_global()
    cold_start_finalize()

@shared_task
def cold_start_finalize():
    logger.info('cold_start_finalize')
    return

def cold_start_fetch_spotify_global():
    pass

def cold_start_fetch_spotify_viral():
    pass

def cold_start_fetch_lastfm_global():
    pass