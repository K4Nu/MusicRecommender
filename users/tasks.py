# users/tasks.py
from celery import shared_task
from django.utils import timezone
from datetime import timedelta
import requests
from .models import SpotifyAccount
import json

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

        # TODO: Zapisz do bazy danych
        # for item in items:
        #     save_artist_or_track(item, user_id, time_range)

    except requests.exceptions.RequestException as e:
        print(f"Failed to fetch top {item_type} ({time_range}): {e}")


def fetch_recently_played(headers, user_id):
    """
    Pobiera ostatnio słuchane utwory (max 50).
    """
    url = "https://api.spotify.com/v1/me/player/recently-played"
    params = {'limit': 50}

    try:
        response = requests.get(url, headers=headers, params=params)
        response.raise_for_status()
        data = response.json()

        items = data.get('items', [])
        print(f"Fetched {len(items)} recently played tracks for user {user_id}")

        # TODO: Zapisz do bazy danych
        # for item in items:
        #     save_listening_history(item, user_id)

    except requests.exceptions.RequestException as e:
        print(f"Failed to fetch recently played: {e}")


def fetch_saved_tracks(headers, user_id):
    """
    Pobiera zapisane utwory (liked songs).
    Może być ich dużo - użyj paginacji.
    """
    url = "https://api.spotify.com/v1/me/tracks"
    params = {'limit': 50}
    all_tracks = []

    try:
        while url:
            response = requests.get(url, headers=headers, params=params)
            response.raise_for_status()
            data = response.json()

            all_tracks.extend(data.get('items', []))
            url = data.get('next')  # Kolejna strona lub None
            params = {}  # Usuń params, bo 'next' ma już wszystko w URL

        print(f"Fetched {len(all_tracks)} saved tracks for user {user_id}")

        # TODO: Zapisz do bazy danych
        # for item in all_tracks:
        #     save_saved_track(item, user_id)

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