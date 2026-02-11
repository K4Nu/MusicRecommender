from celery import shared_task,chord
from django.utils import timezone
from datetime import timedelta, datetime
import requests
from users.models import UserTopItem, User, SpotifyAccount, ListeningHistory, SpotifyPlaylist, SpotifyPlaylistTrack
from utils.locks import ResourceLock,ResourceLockedException
from datetime import date
import logging
from music.models import Artist, Track, Album, Genre
from users.services import ensure_spotify_token

logger = logging.getLogger(__name__)

def parse_spotify_release_date(value: str):
    if not value:
        return None

    if len(value) == 4:          # YYYY
        return date(int(value), 1, 1)
    if len(value) == 7:          # YYYY-MM
        year, month = value.split("-")
        return date(int(year), int(month), 1)
    if len(value) == 10:         # YYYY-MM-DD
        return date.fromisoformat(value)

    return None


@shared_task
def fetch_spotify_initial_data(user_id):
    """
    Fetch initial data from Spotify
    """
    try:
        with ResourceLock('spotify_initial_sync', user_id, timeout=1800):
            try:
                spotify_account = SpotifyAccount.objects.get(user_id=user_id)
            except SpotifyAccount.DoesNotExist:
                logger.error(f"SpotifyAccount not found for user {user_id}")
                return

            spotify = ensure_spotify_token(spotify_account.user)
            if not spotify:
                logger.error(f"Spotify token not found for user {user_id}")
                return

            access_token = spotify.access_token
            if not access_token:
                logger.error(f"Failed to get valid token for user {user_id}")
                return

            headers = {"Authorization": f"Bearer {access_token}"}

            # 1. Top Artists (short, medium, long term)
            fetch_top_items(headers, "artists", "short_term", user_id)
            fetch_top_items(headers, "artists", "medium_term", user_id)
            fetch_top_items(headers, "artists", "long_term", user_id)

            # 2. Top Tracks (short, medium, long term)
            fetch_top_items(headers, "tracks", "short_term", user_id)
            fetch_top_items(headers, "tracks", "medium_term", user_id)
            fetch_top_items(headers, "tracks", "long_term", user_id)

            # 3. Recently Played
            fetch_recently_played(headers, user_id)

            # 4. Saved Tracks
            fetch_saved_tracks(headers, user_id)

            # 5. Playlists
            sync_user_playlists.delay(user_id)

            spotify_account.last_synced_at = timezone.now()
            spotify_account.save(update_fields=["last_synced_at"])

            logger.info(f"✅ Initial Spotify data fetched for user {user_id}")

    except ResourceLockedException:
        logger.info(f"User {user_id} initial sync already in progress, skipping")
        return

def fetch_top_items(headers, item_type, time_range, user_id):
    """
    Pobiera top artists lub tracks - ZOPTYMALIZOWANA WERSJA
    """
    url = f"https://api.spotify.com/v1/me/top/{item_type}"
    params = {'time_range': time_range, 'limit': 50}

    try:
        response = requests.get(url, headers=headers, params=params)
        response.raise_for_status()
        data = response.json()

        items = data.get('items', [])
        logger.info(f"Fetched {len(items)} top {item_type} ({time_range}) for user {user_id}")

        user = User.objects.get(id=user_id)  # ✅ .get() zamiast .filter()

        UserTopItem.objects.filter(
            user=user,
            item_type=item_type[:-1],
            time_range=time_range
        ).delete()

        # ============================================
        # FETCH ALL IDs
        # ============================================
        if item_type == 'artists':
            all_artist_ids = [item['id'] for item in items]
            all_artists_data = items
        else:
            all_artist_ids = []
            all_track_ids = [item['id'] for item in items]
            all_artists_data = []
            seen_ids = set()

            for item in items:
                for artist_data in item.get('artists', []):
                    if artist_data['id'] not in seen_ids:
                        all_artists_data.append(artist_data)
                        seen_ids.add(artist_data['id'])
                        all_artist_ids.append(artist_data['id'])

        # ============================================
        # BULK SAVE ARTISTS
        # ============================================
        save_artists_bulk(all_artists_data)

        artists_cache = {
            a.spotify_id: a
            for a in Artist.objects.filter(spotify_id__in=all_artist_ids)
        }

        # ============================================
        # BULK SAVE TRACKS
        # ============================================
        tracks_cache = {}
        if item_type == 'tracks':
            tracks_cache=save_tracks_bulk(items)


        # ============================================
        # BULK CREATE UserTopItems
        # ============================================
        top_items_to_create = []

        for rank, item in enumerate(items, start=1):
            if item_type == 'artists':
                artist = artists_cache.get(item['id'])
                if artist:
                    top_items_to_create.append(
                        UserTopItem(
                            user=user,
                            item_type="artist",
                            time_range=time_range,
                            artist=artist,
                            track=None,
                            rank=rank
                        )
                    )
            else:
                track = tracks_cache.get(item['id'])
                if track:
                    top_items_to_create.append(
                        UserTopItem(
                            user=user,
                            item_type='track',
                            time_range=time_range,
                            track=track,
                            artist=None,
                            rank=rank
                        )
                    )

        if top_items_to_create:
            UserTopItem.objects.bulk_create(top_items_to_create)
            logger.info(f"✅ Bulk created {len(top_items_to_create)} items")

    except requests.exceptions.RequestException as e:
        logger.error(
            f"Failed to fetch top {item_type} ({time_range}) for user {user_id}",
            exc_info=e
        )


def fetch_recently_played(headers, user_id):
    """
    Saves last 50 played songs by user
    """
    url = "https://api.spotify.com/v1/me/player/recently-played"
    params = {"limit": 50}

    try:
        response = requests.get(url, headers=headers, params=params)
        response.raise_for_status()
        data = response.json()

        items = data.get("items", [])
        user = User.objects.get(id=user_id)

        last_event = (
            ListeningHistory.objects
            .filter(user=user)
            .order_by("-played_at")
            .first()
        )

        last_played_at = last_event.played_at if last_event else None
        new_items = []

        for item in items:
            played_at = datetime.fromisoformat(item.get("played_at").replace("Z", "+00:00"))
            track_data = item.get("track")

            if not played_at or not track_data:
                continue

            if last_played_at and played_at <= last_played_at:
                break

            track_id = track_data.get("id")
            if not track_id:
                continue


            new_items.append(item)

        if not new_items:
            logger.debug("No new items found")
            return

        tracks_data=[item.get('track') for item in new_items]
        tracks_cache=save_tracks_bulk(tracks_data)

        history_events=[]
        for item in new_items:
            played_at = datetime.fromisoformat(item.get("played_at").replace("Z", "+00:00"))
            track=tracks_cache.get(item.get('track',{}).get("id"))

            if track:
                history_events.append(
                    ListeningHistory(
                        user=user,
                        track=track,
                        played_at=played_at,
                    )
                )

        if history_events:
            ListeningHistory.objects.bulk_create(history_events)

    except requests.exceptions.RequestException as e:
        logger.info('f"Failed to fetch recently played: {e}"')


def fetch_saved_tracks(headers, user_id):
    """
    Pobiera zapisane utwory (liked songs) z paginacją.
    """
    url = "https://api.spotify.com/v1/me/tracks"

    try:
        while url:  # ✅ Pętla dopóki jest URL
            params = {'limit': 50}
            response = requests.get(url, headers=headers, params=params)
            response.raise_for_status()
            data = response.json()
            items = data.get('items', [])

            tracks_data = [item.get("track") for item in items if item.get("track")]
            save_tracks_bulk(tracks_data)

            url = data.get('next')  # None finish the loop

        logger.info(f"Fetched saved tracks for user {user_id}")

    except requests.exceptions.RequestException as e:
        logger.error("Failed to fetch saved tracks", exc_info=e)

@shared_task
def spotify_sync_finished(results, user_id):
    logger.info(f"✅ FULL Spotify sync finished for user {user_id}")

@shared_task
def sync_user_playlists(user_id):
    try:
        with ResourceLock("playlists_sync",user_id, timeout=900):
            changed_playlists = fetch_spotify_playlists(user_id)
            if not changed_playlists:
                spotify_sync_finished.delay(user_id)
                return
            chord(
                fetch_playlist_tracks.s(pid)
                for pid in changed_playlists
            )(spotify_sync_finished.s(user_id))
    except ResourceLockedException:
        logger.info(f"User {user_id} playlists sync already in progress, skipping")
        return

def fetch_spotify_playlists(user_id):
    user = User.objects.get(id=user_id)
    spotify = ensure_spotify_token(user)
    if not spotify:
        return []

    headers = {
        "Authorization": f"Bearer {spotify.access_token}"
    }

    if spotify.playlists_etag:
        headers["If-None-Match"] = spotify.playlists_etag

    url = "https://api.spotify.com/v1/me/playlists"
    now = timezone.now()

    existing = {
        p.spotify_id: p
        for p in SpotifyPlaylist.objects.filter(user=user)
    }

    to_create = []
    to_update = []
    changed_playlists = []
    first_page = True

    while url:
        response = requests.get(url, headers=headers, params={"limit": 50})

        if response.status_code == 304:
            spotify.last_synced_at = timezone.now()
            spotify.save(update_fields=["last_synced_at"])
            return []

        response.raise_for_status()

        if first_page:
            spotify.playlists_etag = response.headers.get("ETag")
            spotify.save(update_fields=["playlists_etag"])
            headers.pop("If-None-Match", None)
            first_page = False

        data = response.json()

        for item in data.get("items", []):
            defaults = {
                "user": user,
                "name": item.get("name"),
                "description": item.get("description"),
                "image_url": item["images"][0]["url"] if item.get("images") else None,
                "is_public": item.get("public") or False,
                "is_collaborative": item.get("collaborative", False),
                "tracks_count": item["tracks"]["total"],
                "owner_spotify_id": item["owner"]["id"],
                "owner_display_name": item["owner"].get("display_name"),
                "external_url": item["external_urls"]["spotify"],
                "snapshot_id": item.get("snapshot_id"),
                "last_synced_at": now,
            }

            playlist = existing.get(item["id"])

            if playlist:
                if playlist.snapshot_id != item.get("snapshot_id"):
                    changed_playlists.append(playlist.id)

                for field, value in defaults.items():
                    setattr(playlist, field, value)

                to_update.append(playlist)
            else:
                to_create.append(
                    SpotifyPlaylist(
                        spotify_id=item["id"],
                        **defaults,
                    )
                )

        url = data.get("next")

    if to_create:
        SpotifyPlaylist.objects.bulk_create(to_create)

        changed_playlists.extend(
            SpotifyPlaylist.objects.filter(
                spotify_id__in=[p.spotify_id for p in to_create]
            ).values_list("id", flat=True)
        )

    if to_update:
        SpotifyPlaylist.objects.bulk_update(
            to_update,
            [
                "name",
                "description",
                "image_url",
                "is_public",
                "is_collaborative",
                "tracks_count",
                "owner_spotify_id",
                "owner_display_name",
                "external_url",
                "snapshot_id",
                "last_synced_at",
            ],
        )

    return changed_playlists


@shared_task(bind=True, max_retries=3)
def fetch_playlist_tracks(self, playlist_id):
    try:
        with ResourceLock("playlist_sync", playlist_id, timeout=600):

            try:
                playlist = SpotifyPlaylist.objects.get(id=playlist_id)
            except SpotifyPlaylist.DoesNotExist:
                return

            # 🔑 SNAPSHOT GUARD
            if playlist.tracks_snapshot_id == playlist.snapshot_id:
                return

            spotify = ensure_spotify_token(playlist.user)
            if not spotify:
                return

            headers = {
                "Authorization": f"Bearer {spotify.access_token}"
            }

            # 🔑 ETag tylko dla pierwszej strony
            if playlist.tracks_etag:
                headers["If-None-Match"] = playlist.tracks_etag

            url = f"https://api.spotify.com/v1/playlists/{playlist.spotify_id}/tracks"

            relations = []
            seen_track_ids = set()
            position = 0
            first_page = True

            while url:
                try:
                    r = requests.get(
                        url,
                        headers=headers,
                        params={"limit": 100},
                        timeout=15
                    )
                except requests.exceptions.RequestException as e:
                    raise self.retry(exc=e, countdown=30)

                # 🔥 PLAYLIST SIĘ NIE ZMIENIŁA
                if r.status_code == 304:
                    playlist.tracks_snapshot_id = playlist.snapshot_id
                    playlist.last_synced_at = timezone.now()
                    playlist.save(update_fields=[
                        "tracks_snapshot_id",
                        "last_synced_at",
                    ])
                    return

                r.raise_for_status()
                data = r.json()

                # 🔑 tylko po pierwszej stronie
                if first_page:
                    playlist.tracks_etag = r.headers.get("ETag")
                    playlist.save(update_fields=["tracks_etag"])

                    # ❗ FULL REPLACE — kasujemy stare tracki
                    SpotifyPlaylistTrack.objects.filter(
                        playlist=playlist
                    ).delete()

                    headers.pop("If-None-Match", None)
                    first_page = False

                # --- zapis tracków ---
                tracks_data = []
                for item in data.get("items", []):
                    track_data = item.get("track")
                    if track_data and track_data.get("id"):
                        tracks_data.append(track_data)

                tracks_cache = save_tracks_bulk(tracks_data)

                for item in data.get("items", []):
                    track_data = item.get("track")
                    track_id = track_data.get("id") if track_data else None

                    if not track_id:
                        continue

                    # 🔒 deduplikacja globalna (cała playlista)
                    if track_id in seen_track_ids:
                        continue
                    seen_track_ids.add(track_id)

                    track = tracks_cache.get(track_id)
                    if not track:
                        continue

                    relations.append(
                        SpotifyPlaylistTrack(
                            playlist=playlist,
                            track=track,
                            position=position,
                            added_at=item.get("added_at"),
                        )
                    )
                    position += 1

                url = data.get("next")

            if relations:
                SpotifyPlaylistTrack.objects.bulk_create(relations)

            playlist.tracks_snapshot_id = playlist.snapshot_id
            playlist.last_synced_at = timezone.now()
            playlist.save(update_fields=[
                "tracks_snapshot_id",
                "last_synced_at",
            ])

    except ResourceLockedException:
        logger.warning(
            f"Playlist {playlist_id} sync already in progress, skipping"
        )
        return


def save_artists_bulk(artists_data):
    """
    OPTIMIZED: Bulk save/update artists with genres

    Key optimizations:
    1. Use artist IDs instead of spotify_ids for M2M lookup
    2. Batch genre processing
    3. Minimize database queries
    """
    if not artists_data:
        return

    spotify_ids = [a['id'] for a in artists_data]
    has_any_genres = any(a.get("genres") for a in artists_data)
    # ============================================
    # 1. SPRAWDŹ KTÓRZY ARTYŚCI ISTNIEJĄ
    # ============================================
    existing_artists = {
        a.spotify_id: a
        for a in Artist.objects.filter(spotify_id__in=spotify_ids)
    }
    existing_ids = set(existing_artists.keys())

    # ============================================
    # 2. BULK CREATE/UPDATE GENRES
    # ============================================
    # Skip genre processing if no artists have genres
    artists_with_genres = [a for a in artists_data if a.get("genres")]

    if not artists_with_genres:
        # Track artists often don't include genres - skip M2M entirely
        return

    all_genres = set()
    for artist in artists_with_genres:
        all_genres.update(artist.get("genres", []))

    if all_genres:
        existing_genre_names = set(
            Genre.objects.filter(name__in=all_genres).values_list("name", flat=True)
        )

        new_genres = [
            Genre(name=genre)
            for genre in all_genres
            if genre not in existing_genre_names
        ]

        if new_genres:
            Genre.objects.bulk_create(new_genres, ignore_conflicts=True)

    # Cache wszystkich gatunków
    genres_cache = {
        g.name: g
        for g in Genre.objects.filter(name__in=all_genres)
    }

    # ============================================
    # 3. BULK CREATE NOWYCH ARTYSTÓW
    # ============================================
    to_create = []
    for item in artists_data:
        if item["id"] not in existing_ids:
            to_create.append(
                Artist(
                    spotify_id=item["id"],
                    name=item["name"],
                    popularity=item.get("popularity"),
                    image_url=item["images"][0]["url"] if item.get("images") else None,
                )
            )

    if to_create:
        Artist.objects.bulk_create(to_create, ignore_conflicts=True)

    # ============================================
    # 4. BULK UPDATE ISTNIEJĄCYCH ARTYSTÓW
    # ============================================
    to_update = []
    for item in artists_data:
        if item["id"] in existing_ids:
            artist = existing_artists[item["id"]]
            is_full_artist = bool(item.get("genres") or item.get("images"))
            if not is_full_artist:
                continue

            artist.name = item["name"]
            artist.popularity = item.get("popularity")
            artist.image_url = item["images"][0]["url"] if item.get("images") else None

            to_update.append(artist)

    if to_update:
        Artist.objects.bulk_update(
            to_update,
            ['name', 'popularity', 'image_url'],
            batch_size=100
        )

    # ============================================
    # 5. ODŚWIEŻ CACHE ARTYSTÓW (po create/update)
    # ============================================
    artists_cache = {
        a.spotify_id: a
        for a in Artist.objects.filter(spotify_id__in=spotify_ids)
    }

    # ✅ OPTIMIZATION: Get artist DB IDs upfront
    artist_db_ids = [a.id for a in artists_cache.values()]

    # ============================================
    # 6. POBIERZ ISTNIEJĄCE RELACJE ARTIST↔GENRE
    # ============================================
    existing_relations = set(
        Artist.genres.through.objects
        .filter(artist_id__in=artist_db_ids)  # ← CHANGED: Use IDs
        .values_list('artist_id', 'genre_id')
    )

    # ============================================
    # 7. BULK CREATE NOWYCH RELACJI M2M
    # ============================================
    artist_genre_relations = []

    for item in artists_data:
        artist = artists_cache.get(item["id"])
        if not artist:
            continue

        for genre_name in item.get("genres", []):
            genre = genres_cache.get(genre_name)
            if genre:
                if (artist.id, genre.id) not in existing_relations:
                    artist_genre_relations.append(
                        Artist.genres.through(
                            artist_id=artist.id,
                            genre_id=genre.id
                        )
                    )

    if artist_genre_relations:
        Artist.genres.through.objects.bulk_create(
            artist_genre_relations,
            ignore_conflicts=True,
            batch_size=500  # ✅ Added batch size
        )


def save_artists(artists_data):
    """
    WRAPPER - obsługuje dict LUB list
    """
    if not artists_data:
        return [] if isinstance(artists_data, list) else None

    if isinstance(artists_data, list):
        save_artists_bulk(artists_data)
        spotify_ids = [a["id"] for a in artists_data]
        return list(Artist.objects.filter(spotify_id__in=spotify_ids))
    else:
        save_artists_bulk([artists_data])
        return Artist.objects.get(spotify_id=artists_data["id"])


def save_track(track_data):
    """
    Zapisuje track wraz z artystami i albumem
    """
    if not track_data or not track_data.get("id"):
        return None

    # =========================
    # ALBUM
    # =========================
    album = None
    album_data = track_data.get("album")

    if album_data and album_data.get("id"):
        album, _ = Album.objects.update_or_create(
            spotify_id=album_data["id"],
            defaults={
                "name": album_data.get("name"),
                "album_type": album_data.get(
                    "album_type", Album.AlbumTypes.ALBUM
                ),
                "release_date": parse_spotify_release_date(
                    album_data.get("release_date")
                ),
                "image_url": album_data["images"][0]["url"]
                if album_data.get("images") else None,
            }
        )

        album_artists = save_artists(album_data.get("artists", []))
        if album_artists:
            album.artists.set(album_artists)

    # =========================
    # TRACK
    # =========================
    track, _ = Track.objects.update_or_create(
        spotify_id=track_data["id"],
        defaults={
            "name": track_data.get("name"),
            "duration_ms": track_data.get("duration_ms"),
            "popularity": track_data.get("popularity"),
            "preview_url": track_data.get("preview_url"),
            "image_url": album.image_url if album else None,
            "album": album,
        }
    )

    # =========================
    # TRACK ARTISTS
    # =========================
    track_artists = save_artists(track_data.get("artists", []))
    if track_artists:
        track.artists.set(track_artists)


    return track


def save_tracks_bulk(tracks_data):
    """
    Bulk save/update tracks z albums i artists

    Optymalizacje:
    - Bulk create/update dla albums
    - Bulk create/update dla tracks
    - Bulk M2M relations (Album ↔ Artist, Track ↔ Artist)
    - Minimalna liczba zapytań SQL

    Args:
        tracks_data: list[dict] - lista tracków ze Spotify API

    Returns:
        dict[str, Track] - {spotify_id: Track object}
    """
    if not tracks_data:
        return {}

    # ============================================
    # 1. ZBIERZ WSZYSTKIE IDs
    # ============================================
    track_ids = [t['id'] for t in tracks_data if t.get('id')]
    album_ids = [t['album']['id'] for t in tracks_data if t.get('album', {}).get('id')]

    # Zbierz wszystkich artystów (z tracks i albums)
    all_artists_data = []
    seen_artist_ids = set()

    for track_data in tracks_data:
        # Track artists
        for artist_data in track_data.get('artists', []):
            if artist_data['id'] not in seen_artist_ids:
                all_artists_data.append(artist_data)
                seen_artist_ids.add(artist_data['id'])

        # Album artists
        album_data = track_data.get('album', {})
        for artist_data in album_data.get('artists', []):
            if artist_data['id'] not in seen_artist_ids:
                all_artists_data.append(artist_data)
                seen_artist_ids.add(artist_data['id'])

    # ============================================
    # 2. BULK SAVE ARTISTS
    # ============================================
    save_artists_bulk(all_artists_data)

    artists_cache = {
        a.spotify_id: a
        for a in Artist.objects.filter(spotify_id__in=list(seen_artist_ids))
    }

    # ============================================
    # 3. BULK SAVE ALBUMS
    # ============================================
    existing_albums = {
        a.spotify_id: a
        for a in Album.objects.filter(spotify_id__in=album_ids)
    }
    existing_album_ids = set(existing_albums.keys())

    # Create new albums
    albums_to_create = []
    for track_data in tracks_data:
        album_data = track_data.get('album')
        if not album_data or not album_data.get('id'):
            continue

        if album_data['id'] not in existing_album_ids:
            albums_to_create.append(
                Album(
                    spotify_id=album_data['id'],
                    name=album_data.get('name'),
                    album_type=album_data.get('album_type', Album.AlbumTypes.ALBUM),
                    release_date=parse_spotify_release_date(album_data.get('release_date')),
                    image_url=album_data['images'][0]['url'] if album_data.get('images') else None,
                )
            )

    if albums_to_create:
        Album.objects.bulk_create(albums_to_create, ignore_conflicts=True)

    # Update existing albums
    albums_to_update = []
    for track_data in tracks_data:
        album_data = track_data.get('album')
        if not album_data or not album_data.get('id'):
            continue

        if album_data['id'] in existing_album_ids:
            album = existing_albums[album_data['id']]
            album.name = album_data.get('name')
            album.album_type = album_data.get('album_type', Album.AlbumTypes.ALBUM)
            album.release_date = parse_spotify_release_date(album_data.get('release_date'))
            album.image_url = album_data['images'][0]['url'] if album_data.get('images') else None
            albums_to_update.append(album)

    if albums_to_update:
        Album.objects.bulk_update(
            albums_to_update,
            ['name', 'album_type', 'release_date', 'image_url'],
            batch_size=100
        )

    # Refresh albums cache
    albums_cache = {
        a.spotify_id: a
        for a in Album.objects.filter(spotify_id__in=album_ids)
    }

    # ============================================
    # 4. BULK M2M: ALBUM ↔ ARTIST
    # ============================================
    # Pobierz istniejące relacje
    album_db_ids = [a.id for a in albums_cache.values()]
    existing_album_artist_relations = set(
        Album.artists.through.objects
        .filter(album_id__in=album_db_ids)
        .values_list('album_id', 'artist_id')
    )

    album_artist_relations = []
    for track_data in tracks_data:
        album_data = track_data.get('album')
        if not album_data or not album_data.get('id'):
            continue

        album = albums_cache.get(album_data['id'])
        if not album:
            continue

        for artist_data in album_data.get('artists', []):
            artist = artists_cache.get(artist_data['id'])
            if artist and (album.id, artist.id) not in existing_album_artist_relations:
                album_artist_relations.append(
                    Album.artists.through(
                        album_id=album.id,
                        artist_id=artist.id
                    )
                )

    if album_artist_relations:
        Album.artists.through.objects.bulk_create(
            album_artist_relations,
            ignore_conflicts=True
        )

    # ============================================
    # 5. BULK SAVE TRACKS
    # ============================================
    existing_tracks = {
        t.spotify_id: t
        for t in Track.objects.filter(spotify_id__in=track_ids)
    }
    existing_track_ids = set(existing_tracks.keys())

    # Create new tracks
    tracks_to_create = []
    for track_data in tracks_data:
        if not track_data.get('id'):
            continue

        if track_data['id'] not in existing_track_ids:
            album_id = track_data.get('album', {}).get('id')
            album = albums_cache.get(album_id) if album_id else None

            tracks_to_create.append(
                Track(
                    spotify_id=track_data['id'],
                    name=track_data.get('name'),
                    duration_ms=track_data.get('duration_ms'),
                    popularity=track_data.get('popularity'),
                    preview_url=track_data.get('preview_url'),
                    image_url=album.image_url if album else None,
                    album=album,
                )
            )

    if tracks_to_create:
        Track.objects.bulk_create(tracks_to_create, ignore_conflicts=True)

    # Update existing tracks
    tracks_to_update = []
    for track_data in tracks_data:
        if not track_data.get('id'):
            continue

        if track_data['id'] in existing_track_ids:
            track = existing_tracks[track_data['id']]
            album_id = track_data.get('album', {}).get('id')
            album = albums_cache.get(album_id) if album_id else None

            track.name = track_data.get('name')
            track.duration_ms = track_data.get('duration_ms')
            track.popularity = track_data.get('popularity')
            track.preview_url = track_data.get('preview_url')
            track.image_url = album.image_url if album else None
            track.album = album

            tracks_to_update.append(track)

    if tracks_to_update:
        Track.objects.bulk_update(
            tracks_to_update,
            ['name', 'duration_ms', 'popularity', 'preview_url', 'image_url', 'album'],
            batch_size=100
        )

    # Refresh tracks cache
    tracks_cache = {
        t.spotify_id: t
        for t in Track.objects.filter(spotify_id__in=track_ids)
    }

    # ============================================
    # 6. BULK M2M: TRACK ↔ ARTIST
    # ============================================
    # Pobierz istniejące relacje
    track_db_ids = [t.id for t in tracks_cache.values()]
    existing_track_artist_relations = set(
        Track.artists.through.objects
        .filter(track_id__in=track_db_ids)
        .values_list('track_id', 'artist_id')
    )

    track_artist_relations = []
    for track_data in tracks_data:
        if not track_data.get('id'):
            continue

        track = tracks_cache.get(track_data['id'])
        if not track:
            continue

        for artist_data in track_data.get('artists', []):
            artist = artists_cache.get(artist_data['id'])
            if artist and (track.id, artist.id) not in existing_track_artist_relations:
                track_artist_relations.append(
                    Track.artists.through(
                        track_id=track.id,
                        artist_id=artist.id
                    )
                )

    if track_artist_relations:
        Track.artists.through.objects.bulk_create(
            track_artist_relations,
            ignore_conflicts=True
        )

    return tracks_cache

def save_albums_bulk(albums_data):
    if not albums_data:
        return

    # ============================
    # FETCH SPOTIFY_IDS
    # ============================
    spotify_ids = [a["id"] for a in albums_data if a.get("id")]

    existing_ids = set(
        Album.objects.filter(spotify_id__in=spotify_ids)
        .values_list("spotify_id", flat=True)
    )

    # ============================
    # FETCH ARTISTS
    # ============================
    all_artists_data = []
    seen_artist_ids = set()

    for album in albums_data:
        for artist in album.get("artists", []):
            if artist["id"] not in seen_artist_ids:
                all_artists_data.append(artist)
                seen_artist_ids.add(artist["id"])

    save_artists_bulk(all_artists_data)

    artists_cache = {
        a.spotify_id: a
        for a in Artist.objects.filter(
            spotify_id__in=[a["id"] for a in all_artists_data]
        )
    }

    # ============================
    # CREATE ALBUMS
    # ============================
    albums_to_create = []

    for album in albums_data:
        spotify_id = album.get("id")
        if not spotify_id or spotify_id in existing_ids:
            continue

        albums_to_create.append(
            Album(
                spotify_id=spotify_id,
                name=album.get("name"),
                album_type=album.get(
                    "album_type", Album.AlbumTypes.ALBUM
                ),
                release_date=parse_spotify_release_date(
                    album.get("release_date")
                ),
                image_url=album["images"][0]["url"]
                if album.get("images") else None,
            )
        )

    if albums_to_create:
        Album.objects.bulk_create(albums_to_create, ignore_conflicts=True)

    # ============================
    # UPDATE EXISTING ALBUMS
    # ============================
    existing_albums_objs = {
        a.spotify_id: a
        for a in Album.objects.filter(spotify_id__in=existing_ids)
    }

    albums_to_update = []
    for album in albums_data:
        spotify_id = album.get("id")
        if not spotify_id:
            continue

        if spotify_id in existing_ids:
            album_obj = existing_albums_objs[spotify_id]
            album_obj.name = album.get("name")
            album_obj.album_type = album.get("album_type", Album.AlbumTypes.ALBUM)
            album_obj.release_date = parse_spotify_release_date(album.get("release_date"))
            album_obj.image_url = album["images"][0]["url"] if album.get("images") else None
            albums_to_update.append(album_obj)

    if albums_to_update:
        Album.objects.bulk_update(
            albums_to_update,
            ['name', 'album_type', 'release_date', 'image_url'],
            batch_size=100
        )

    # ============================
    # M2M Album ↔ Artist
    # ============================
    albums_cache = {
        a.spotify_id: a
        for a in Album.objects.filter(spotify_id__in=spotify_ids)
    }

    album_db_ids = [a.id for a in albums_cache.values()]
    existing_album_artist_relations = set(
        Album.artists.through.objects
        .filter(album_id__in=album_db_ids)
        .values_list('album_id', 'artist_id')
    )

    album_artist_relations = []

    for album_data in albums_data:
        album = albums_cache.get(album_data["id"])
        if not album:
            continue

        for artist_data in album_data.get("artists", []):
            artist = artists_cache.get(artist_data["id"])
            if artist and (album.id, artist.id) not in existing_album_artist_relations:
                album_artist_relations.append(
                    Album.artists.through(
                        album_id=album.id,
                        artist_id=artist.id
                    )
                )

    if album_artist_relations:
        Album.artists.through.objects.bulk_create(
            album_artist_relations,
            ignore_conflicts=True
        )

@shared_task
def refresh_spotify_data(time_term):
    spotify_users = SpotifyAccount.objects.all()
    for spotify_user in spotify_users:
        refresh_spotify_user_data.delay(spotify_user.id, time_term)


@shared_task
def refresh_spotify_user_data(spotify_account_id, time_term):
    try:
        spotify_account = SpotifyAccount.objects.get(id=spotify_account_id)
        ensure_spotify_token(spotify_account.user)
        access_token = spotify_account.access_token

        if not access_token:
            logger.info(f"Failed to get token for SpotifyAccount {spotify_account_id}")
            return

        headers = {"Authorization": f"Bearer {access_token}"}

        fetch_top_items(headers, "artists", time_term, spotify_account.user.id)
        fetch_top_items(headers, "tracks", time_term, spotify_account.user.id)

        spotify_account.last_synced_at = timezone.now()
        spotify_account.save()

    except SpotifyAccount.DoesNotExist:
        logger.info(f"SpotifyAccount {spotify_account_id} does not exist")


