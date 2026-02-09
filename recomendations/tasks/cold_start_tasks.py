from django.db.models import Q
from music.models import Track
from users.tasks.lastfm_tasks import get_track_info

def create_cold_start_lastfm_tracks():
    cold_tracks_missing_lastfm = (
        Track.objects
        .filter(cold_start_entries__isnull=False)
        .filter(lastfm_cache__isnull=True)
        .distinct()
    )
    for track in cold_tracks_missing_lastfm:
        get_track_info.delay(track.id)