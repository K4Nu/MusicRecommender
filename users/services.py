from django.utils import timezone
from datetime import timedelta
import requests
from .models import SpotifyAccount, YoutubeAccount
import os
import logging

logger = logging.getLogger(__name__)


def refresh_spotify_account(spotify_account):
    """
    Odświeża access token dla Spotify account
    Returns: True jeśli sukces, False jeśli błąd
    """
    try:
        response = requests.post(
            "https://accounts.spotify.com/api/token",
            data={
                "grant_type": "refresh_token",
                "refresh_token": spotify_account.refresh_token,
                "client_id": os.getenv("SPOTIFY_CLIENT_ID"),
                "client_secret": os.getenv("SPOTIFY_CLIENT_SECRET"),
            },
            timeout=10
        )
        response.raise_for_status()
        data = response.json()

        spotify_account.update_tokens(
            access_token=data["access_token"],
            refresh_token=data.get("refresh_token"),
            expires_in=data.get("expires_in", 3600)
        )

        logger.info(f"Refreshed Spotify token for user {spotify_account.user.id}")
        return True

    except requests.exceptions.RequestException as e:
        logger.error(f"Failed to refresh Spotify token for user {spotify_account.user.id}: {e}")
        return False


def ensure_spotify_token(user):
    """
    Sprawdza i odświeża token jeśli potrzeba
    Returns: SpotifyAccount object lub None jeśli błąd
    """
    try:
        spotify = SpotifyAccount.objects.get(user=user)
    except SpotifyAccount.DoesNotExist:
        logger.warning(f"No Spotify account for user {user.id}")
        return None

    # Sprawdź czy token wygasł lub wygaśnie za < 5 minut
    if spotify.expires_at <= timezone.now() + timedelta(minutes=5):
        success = refresh_spotify_account(spotify)
        if not success:
            return None

        spotify.refresh_from_db()

    return spotify


def refresh_youtube_account(yt_account):
    """
    Odświeża access token dla YouTube account
    Returns: True jeśli sukces, False jeśli błąd
    """
    try:
        response = requests.post(
            "https://oauth2.googleapis.com/token",
            data={
                "grant_type": "refresh_token",
                "refresh_token": yt_account.refresh_token,
                "client_id": os.getenv("YOUTUBE_CLIENT_ID"),
                "client_secret": os.getenv("YOUTUBE_CLIENT_SECRET"),
            },
            timeout=10
        )
        response.raise_for_status()
        data = response.json()

        yt_account.update_tokens(
            access_token=data["access_token"],
            refresh_token=data.get("refresh_token"),
            expires_in=data.get("expires_in", 3600)
        )

        logger.info(f"Refreshed YouTube token for user {yt_account.user.id}")
        return True

    except requests.exceptions.RequestException as e:
        logger.error(f"Failed to refresh YouTube token for user {yt_account.user.id}: {e}")
        return False


def ensure_youtube_token(user):
    """
    Sprawdza i odświeża YouTube token jeśli potrzeba
    Returns: YoutubeAccount object lub None jeśli błąd
    """
    try:
        yt = YoutubeAccount.objects.get(user=user)
    except YoutubeAccount.DoesNotExist:
        logger.warning(f"No YouTube account for user {user.id}")
        return None

    # Sprawdź czy token wygasł lub wygaśnie za < 5 minut
    if yt.expires_at <= timezone.now() + timedelta(minutes=5):
        success = refresh_youtube_account(yt)
        if not success:
            return None

        # ✅ Przeładuj obiekt z bazy
        yt.refresh_from_db()

    return yt


def ensure_valid_external_tokens(user):
    """
    Upewnia się że wszystkie external tokeny są świeże
    Returns: dict z accountami lub None dla brakujących
    """
    spotify = ensure_spotify_token(user)
    youtube = ensure_youtube_token(user)

    return {
        'spotify': spotify,
        'youtube': youtube
    }