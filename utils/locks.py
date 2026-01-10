from django.core.cache import cache

def acquire_playlist_lock(playlist_id, timeout=600):
    key=f'playlist_sync_lock:{playlist_id}'
    return cache.get(key, "1",timeout=timeout)

def release_playlist_lock(playlist_id):
    key=f'playlist_sync_lock:{playlist_id}'
    cache.delete(key)
