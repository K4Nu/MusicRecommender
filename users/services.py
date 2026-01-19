from django.utils import timezone
from datetime import timedelta
import requests
from .models import SpotifyAccount, YoutubeAccount
import os

def refresh_spotify_account(spotify):
    response = requests.post(
        "https://accounts.spotify.com/api/token",
        data={
            "grant_type": "refresh_token",
            "refresh_token": spotify.refresh_token,
            "client_id": os.getenv("SPOTIFY_CLIENT_ID"),
            "client_secret": os.getenv("SPOTIFY_CLIENT_SECRET"),
        },
        timeout=10
    )
    response.raise_for_status()
    data=response.json()
    spotify.update_tokens(data.get("access_token"),
                          data.get("refresh_token") or spotify.refresh_token,
                          data.get("expires_in",3600))
    return

def ensure_spotify_token(user):
    try:
        spotify = SpotifyAccount.objects.get(user=user)
    except SpotifyAccount.DoesNotExist:
        return

    if spotify.expires_at <= timezone.now():
        refresh_spotify_account(spotify)
    return spotify


def refresh_youtube_account(youtube):
    response = requests.post(
        "https://oauth2.googleapis.com/token",
        data={
            "grant_type": "refresh_token",
            "refresh_token": youtube.refresh_token,
        },
        timeout=10
    )
    response.raise_for_status()
    data = response.json()
    youtube.update_tokens(
        access_token=data["access_token"],
        refresh_token=data.get("refresh_token") or youtube.refresh_token,
        expires_in=data.get("expires_in",3600)
    )
    return

def ensure_youtube_token(user):
    try:
        youtube = YoutubeAccount.objects.get(user=user)
    except YoutubeAccount.DoesNotExist:
        return

    if youtube.expires_at <= timezone.now():
        refresh_youtube_account(youtube)
    return youtube


def ensure_valid_external_tokens(user):
    ensure_spotify_token(user)
    ensure_youtube_token(user)
