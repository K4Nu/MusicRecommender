# users/tasks.py
from celery import shared_task
from django.utils import timezone
from datetime import timedelta
import requests
from sqlparse.utils import offset

from .models import UserTopItem, Artist, Track, User, SpotifyAccount, AudioFeatures, ListeningHistory
import json
from .youtube_classifiers import compute_music_score
from django.core.cache import cache
from requests.exceptions import HTTPError


@shared_task
def fetch_spotify_initial_data(user_id):
    """
    Pobiera wszystkie początkowe dane ze Spotify po pierwszym połączeniu konta.
    """
    try:
        spotify_account = SpotifyAccount.objects.get(user_id=user_id)
    except SpotifyAccount.DoesNotExist:
        print(f"SpotifyAccount not found for user {user_id}")
        return

    # Sprawdź czy token jest świeży
    access_token = get_valid_token(spotify_account)
    if not access_token:
        print(f"Failed to get valid token for user {user_id}")
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
    fetch_playlists(headers, user_id)

    # Zaktualizuj last_synced_at
    spotify_account.last_synced_at = timezone.now()
    spotify_account.save()

    print(f"✅ Initial Spotify data fetched for user {user_id}")


def get_valid_token(spotify_account):
    """
    Zwraca ważny access_token, odświeża jeśli wygasł.
    """
    # Jeśli token jest świeży
    if spotify_account.expires_at > timezone.now():
        return spotify_account.access_token

    # Odśwież token
    token_url = "https://accounts.spotify.com/api/token"
    data = {
        'grant_type': 'refresh_token',
        'refresh_token': spotify_account.refresh_token,
    }

    import os
    auth = (os.environ.get('SPOTIFY_CLIENT_ID'), os.environ.get('SPOTIFY_CLIENT_SECRET'))

    try:
        response = requests.post(token_url, data=data, auth=auth)
        response.raise_for_status()
        token_data = response.json()

        # Zaktualizuj token w bazie
        spotify_account.access_token = token_data['access_token']
        spotify_account.expires_at = timezone.now() + timedelta(seconds=token_data['expires_in'])

        # Czasami Spotify zwraca nowy refresh_token
        if 'refresh_token' in token_data:
            spotify_account.refresh_token = token_data['refresh_token']

        spotify_account.save()

        return spotify_account.access_token

    except requests.exceptions.RequestException as e:
        print(f"Failed to refresh token: {e}")
        return None


def fetch_top_items(headers, item_type, time_range, user_id):
    """
    Pobiera top artists lub tracks dla danego time_range.
    item_type: 'artists' lub 'tracks'
    time_range: 'short_term', 'medium_term', 'long_term'
    """
    url = f"https://api.spotify.com/v1/me/top/{item_type}"
    params = {
        'time_range': time_range,
        'limit': 50,  # max 50
    }

    try:
        response = requests.get(url, headers=headers, params=params)
        response.raise_for_status()
        data = response.json()


        items = data.get('items', [])
        print(f"Fetched {len(items)} top {item_type} ({time_range}) for user {user_id}")

        user=User.objects.filter(id=user_id)
        UserTopItem.objects.filter(user=user,
                                   item_type=item_type[:-1],
                                   time_range=time_range).delete()

        for rank,item in enumerate(items,start=1):
            if item_type == 'artists':
                artist=save_artists(item)
                UserTopItem.objects.create(user=user,
                                           item_type="artist",
                                           time_range=time_range,
                                           rank=rank,)
            else:
                track = save_track(item)
                UserTopItem.objects.create(
                    user=user,
                    item_type='track',
                    time_range=time_range,
                    track=track,
                    rank=rank
                )


    except requests.exceptions.RequestException as e:
        print(f"Failed to fetch top {item_type} ({time_range}): {e}")


def fetch_recently_played(headers, user):
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
        spotify_account = SpotifyAccount.objects.get(user=user)
        user = spotify_account.user

        # ----------------------------------
        # LAST LISTENED TRACK
        # ----------------------------------
        last_event = (
            ListeningHistory.objects
            .filter(user=user)
            .order_by("-played_at")
            .first()
        )

        last_played_at = last_event.played_at if last_event else None

        history_events = []

        # ----------------------------------
        # ITERATE RECENTLY PLAYED
        # ----------------------------------
        for item in items:
            played_at = item.get("played_at")
            track_data = item.get("track")

            if not played_at or not track_data:
                continue

            if last_played_at and played_at <= last_played_at:
                break

            track_id = track_data.get("id")
            if not track_id:
                continue

            # ----------------------------------
            # TRACK
            # ----------------------------------
            try:
                track = Track.objects.get(spotify_id=track_id)
            except Track.DoesNotExist:
                artists = save_artists(track_data.get("artists", []))

                track = Track.objects.create(
                    spotify_id=track_id,
                    name=track_data.get("name"),
                    duration_ms=track_data.get("duration_ms"),
                    popularity=track_data.get("popularity"),
                )
                track.artists.add(*artists)

            # ----------------------------------
            # LISTENING EVENT
            # ----------------------------------
            history_events.append(
                ListeningHistory(
                    user=user,
                    track=track,
                    played_at=played_at,
                )
            )

        # ----------------------------------
        # BULK INSERT
        # ----------------------------------
        if history_events:
            ListeningHistory.objects.bulk_create(history_events)

        print(f"Saved {len(history_events)} new listening events for user {user.id}")

    except requests.exceptions.RequestException as e:
        print(f"Failed to fetch recently played: {e}")


def fetch_saved_tracks(headers, user_id):
    """
    Pobiera zapisane utwory (liked songs).
    Może być ich dużo - użyj paginacji.
    """
    url = "https://api.spotify.com/v1/me/tracks"
    idx=0
    limit=50
    all_tracks = []

    try:
        while True:
            params = {'limit': limit,offset:limit*idx}
            response = requests.get(url, headers=headers, params=params)
            response.raise_for_status()
            data = response.json()

            all_tracks.extend(data.get('items', []))

            url = data.get('next')




        # TODO: Zapisz do bazy danych
        # for item in all_tracks:
        #     save_saved_track(item, user_id)
            idx+=1
        print(f"Fetched {len(all_tracks)} saved tracks for user {user_id}")
    except requests.exceptions.RequestException as e:
        print(f"Failed to fetch saved tracks: {e}")


def fetch_playlists(headers, user_id):
    """
    Pobiera playlisty użytkownika.
    """
    url = "https://api.spotify.com/v1/me/playlists"
    params = {'limit': 50}

    try:
        response = requests.get(url, headers=headers, params=params)
        response.raise_for_status()
        data = response.json()

        playlists = data.get('items', [])
        print(f"Fetched {len(playlists)} playlists for user {user_id}")

        # TODO: Zapisz do bazy danych
        # for playlist in playlists:
        #     save_playlist(playlist, user_id)

    except requests.exceptions.RequestException as e:
        print(f"Failed to fetch playlists: {e}")

    print(json.dumps(playlists, indent=2))

def save_artists(artists_data):
    """
      artists_data: list of Spotify SimplifiedArtistObject
      returns: list[Artist]
      """
    spotify_ids = [a["id"] for a in artists_data]

    existing_artists = {
        a.spotify_id: a
        for a in Artist.objects.filter(spotify_id__in=spotify_ids)
    }

    to_create = []

    for art in artists_data:
        if art["id"] not in existing_artists:
            to_create.append(
                Artist(
                    spotify_id=art["id"],
                    name=art["name"],
                )
            )
    if to_create:
        Artist.objects.bulk_create(to_create)

    return list(Artist.objects.filter(spotify_id__in=spotify_ids))


def save_track(track_data):
    track,created=Track.objects.update_or_create(
        spotify_id=track_data.get('id'),
        defaults={
            "name":track_data.get('name'),
            "album_name":track_data.get('album_name'),
            "duration_ms":track_data.get('duration_ms'),
            "popularity":track_data.get('popularity'),
            "preview_url":track_data.get('preview_url'),
                        'image_url': track_data['album']['images'][0]['url'] if track_data['album'].get('images') else None,
        }
    )
    if created:
        for artist_data in track_data.get('artists'):
            artist=save_artists(artist_data)
            track.artists.add(artist)
        return track

@shared_task
def refresh_spotify_data(time_term):
    spotify_users=SpotifyAccount.objects.all()
    for spotify_user in spotify_users:
        refresh_spotify_user_data.delay(spotify_user.id, time_term)


@shared_task
def refresh_spotify_user_data(spotify_account_id, time_term):
    try:
        spotify_account = SpotifyAccount.objects.get(id=spotify_account_id)
        access_token = get_valid_token(spotify_account)

        if not access_token:
            print(f"Failed to get token for SpotifyAccount {spotify_account_id}")
            return

        headers = {"Authorization": f"Bearer {access_token}"}
        fetch_top_items(headers, "artists", time_term, spotify_account.user.id)
        fetch_top_items(headers, "tracks", time_term, spotify_account.user.id)

        spotify_account.last_synced_at = timezone.now()
        spotify_account.save()

    except SpotifyAccount.DoesNotExist:
        print(f"SpotifyAccount {spotify_account_id} does not exist")

@shared_task
def fetch_tracks_audio_features():
    track_ids = Track.objects.filter(audio_features__isnull=True).values_list('spotify_id', flat=True)
    chunks = [track_ids[i:i+100] for i in range(0,len(track_ids),100)]
    for chunk in chunks:
        chunk_audio_features.delay(",".join(chunk))


@shared_task
def chunk_audio_features(data_chunk):
    url = f"https://api.spotify.com/v1/audio-features?ids={data_chunk}"
    spotify_user = SpotifyAccount.objects.first()

    if not spotify_user:
        print("No Spotify account available")
        return

    access_token = get_valid_token(spotify_user)
    if not access_token:
        print("Failed to get token")
        return

    headers = {"Authorization": f"Bearer {access_token}"}

    try:
        response = requests.get(url, headers=headers)
        response.raise_for_status()
        data = response.json()
    except Exception as e:
        print(f"API request failed: {e}")
        return

    audio_features = data.get('audio_features', [])
    features_to_create = []

    for feature in audio_features:
        if not feature:
            continue

        track_spotify_id = feature.get('id')

        try:
            track_obj = Track.objects.get(spotify_id=track_spotify_id)


            audio_feature = AudioFeatures(
                track=track_obj,  # ← Obiekt Track, nie string!
                danceability=feature.get('danceability'),
                energy=feature.get('energy'),
                valence=feature.get('valence'),
                acousticness=feature.get('acousticness'),
                instrumentalness=feature.get('instrumentalness'),
                speechiness=feature.get('speechiness'),
                liveness=feature.get('liveness'),
                loudness=feature.get('loudness'),
                key=feature.get('key'),
                mode=feature.get('mode'),
                tempo=feature.get('tempo'),
                time_signature=feature.get('time_signature'),
                duration_ms=feature.get('duration_ms'),
            )
            features_to_create.append(audio_feature)

        except Track.DoesNotExist:
            print(f"Track {track_spotify_id} not found in DB")
            continue

    if features_to_create:
        AudioFeatures.objects.bulk_create(features_to_create, ignore_conflicts=True)
        print(f"Created {len(features_to_create)} audio features")

@shared_task
def youtube_test_fetch(access_token, page_token=None):

    url='https://www.googleapis.com/youtube/v3/subscriptions'
    headers = {"Authorization": f"Bearer {access_token}"}
    params = {
        'part': 'snippet',
        'mine': 'true',
        "maxResults": 50,
    }

    if page_token:
        params['pageToken'] = page_token

    try:
        response = requests.get(url, headers=headers, params=params)
        response.raise_for_status()
        data=response.json()
    except requests.exceptions.RequestException as e:
        print(e)
        return

    items=data.get('items', [])
    for item in items:
        current=item['snippet']['resourceId']['channelId']
        check_youtube_channel_category.delay(access_token,current)


    next_page_token = data.get('nextPageToken')
    if next_page_token:
        youtube_test_fetch.delay(access_token,next_page_token)

@shared_task(bind=True,max_retries=3)
def check_youtube_channel_category(self,access_token,channel_id,user_id):
    cache_key = f"music_check:{channel_id}"
    cached_result=cache.get(cache_key)
    if cached_result:
        return cached_result
    url='https://www.googleapis.com/youtube/v3/channels'
    headers = {"Authorization": f"Bearer {access_token}"}
    params = {
        'part': 'snippet,topicDetails',
        'id': channel_id,

    }
    try:
        response=requests.get(url,headers=headers,params=params)
        response.raise_for_status()
        data=response.json()
    except requests.exceptions.RequestException as e:
        print(f"[check_youtube_channel_category] error: {e}")
        return
    except HTTPError as e:
        if e.response.status_code == 429:  # Rate limit
            raise self.retry(exc=e, countdown=60)
        return

    items=data.get('items', [])
    if not items:
        return

    snippet=items[0].get("snippet",{})
    channel_name=snippet.get("title","Unknown")
    channel_videos=get_recent_video_categories(channel_id,access_token)

    result = compute_music_score(data, recent_video_categories=channel_videos)

    if result["is_music"]:
        print(
            f"[MUSIC] {channel_name} "
            f"(score={result['total_score']}, "
            f"topics={result['score_topics']}, "
            f"text={result['score_text']}, "
            f"videos={result['score_videos']})"
        )
        # TODO: database save / user connect
    return


def get_recent_video_categories(channel_id,access_token):
    search_url = "https://www.googleapis.com/youtube/v3/search"
    headers = {"Authorization": f"Bearer {access_token}"}
    params = {
        "part": "snippet",
        "channelId": channel_id,
        "type": "video",
        "order": "date",
        "maxResults": 20,
    }
    response=requests.get(search_url,headers=headers,params=params)
    response.raise_for_status()
    data=response.json()
    video_ids = [item["id"]["videoId"] for item in data.get("items", [])]
    if not video_ids:
        return []
    if len(video_ids) < 5:
        return []
    videos_url="https://www.googleapis.com/youtube/v3/videos"
    params={"part":"snippet", "id":",".join(video_ids)}
    r=requests.get(videos_url,headers=headers,params=params)
    r.raise_for_status()
    data=r.json()

    return [
        int(item["snippet"]["categoryId"])
        for item in data.get("items", [])
        if "snippet" in item
    ]

