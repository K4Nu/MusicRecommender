"""
Tests for users/services.py

Covers: refresh_spotify_account, ensure_spotify_token,
        refresh_youtube_account, ensure_youtube_token,
        ensure_valid_external_tokens
"""
import pytest
import requests as req
from unittest.mock import patch, MagicMock
from django.utils import timezone
from datetime import timedelta

from users.models import User, SpotifyAccount, YoutubeAccount
from users.services import (
    refresh_spotify_account,
    ensure_spotify_token,
    refresh_youtube_account,
    ensure_youtube_token,
    ensure_valid_external_tokens,
)


# =========================================================
# FIXTURES
# =========================================================

@pytest.fixture
def user(db):
    return User.objects.create_user(
        email="svc_test@example.com", password="testpass123"
    )


@pytest.fixture
def expired_spotify(db, user):
    """SpotifyAccount with an already-expired token."""
    return SpotifyAccount.objects.create(
        user=user,
        spotify_id="sp_expired",
        access_token="old_access",
        refresh_token="old_refresh",
        expires_at=timezone.now() - timedelta(hours=1),
    )


@pytest.fixture
def fresh_spotify(db, user):
    """SpotifyAccount with a still-valid token."""
    return SpotifyAccount.objects.create(
        user=user,
        spotify_id="sp_fresh",
        access_token="fresh_access",
        refresh_token="fresh_refresh",
        expires_at=timezone.now() + timedelta(hours=1),
    )


@pytest.fixture
def expired_youtube(db, user):
    """YoutubeAccount with an already-expired token."""
    return YoutubeAccount.objects.create(
        user=user,
        access_token="yt_old_access",
        refresh_token="yt_old_refresh",
        expires_at=timezone.now() - timedelta(hours=1),
    )


@pytest.fixture
def fresh_youtube(db, user):
    """YoutubeAccount with a still-valid token."""
    return YoutubeAccount.objects.create(
        user=user,
        access_token="yt_fresh_access",
        refresh_token="yt_fresh_refresh",
        expires_at=timezone.now() + timedelta(hours=1),
    )


def _mock_token_response(access_token="new_access", expires_in=3600, refresh_token=None):
    mock_resp = MagicMock()
    data = {"access_token": access_token, "expires_in": expires_in}
    if refresh_token:
        data["refresh_token"] = refresh_token
    mock_resp.json.return_value = data
    return mock_resp


# =========================================================
# 1. refresh_spotify_account
# =========================================================

class TestRefreshSpotifyAccount:

    def test_calls_spotify_token_endpoint(self, expired_spotify):
        mock_resp = _mock_token_response("new_token")
        with patch("users.services.requests.post", return_value=mock_resp) as mock_post:
            refresh_spotify_account(expired_spotify)
        url = mock_post.call_args[0][0]
        assert "accounts.spotify.com/api/token" in url

    def test_updates_access_token_in_db(self, expired_spotify):
        mock_resp = _mock_token_response("brand_new_token")
        with patch("users.services.requests.post", return_value=mock_resp):
            refresh_spotify_account(expired_spotify)
        expired_spotify.refresh_from_db()
        assert expired_spotify.access_token == "brand_new_token"

    def test_uses_new_refresh_token_when_returned(self, expired_spotify):
        mock_resp = _mock_token_response("new_access", refresh_token="new_refresh")
        with patch("users.services.requests.post", return_value=mock_resp):
            refresh_spotify_account(expired_spotify)
        expired_spotify.refresh_from_db()
        assert expired_spotify.refresh_token == "new_refresh"

    def test_keeps_old_refresh_token_when_not_returned(self, expired_spotify):
        mock_resp = _mock_token_response("new_access")  # no refresh_token key
        with patch("users.services.requests.post", return_value=mock_resp):
            refresh_spotify_account(expired_spotify)
        expired_spotify.refresh_from_db()
        assert expired_spotify.refresh_token == "old_refresh"

    def test_raises_on_http_error(self, expired_spotify):
        mock_resp = MagicMock()
        mock_resp.raise_for_status.side_effect = req.HTTPError("401 Unauthorized")
        with patch("users.services.requests.post", return_value=mock_resp):
            with pytest.raises(req.HTTPError):
                refresh_spotify_account(expired_spotify)

    def test_expires_at_is_updated(self, expired_spotify):
        old_expires = expired_spotify.expires_at
        mock_resp = _mock_token_response("new_access", expires_in=7200)
        with patch("users.services.requests.post", return_value=mock_resp):
            refresh_spotify_account(expired_spotify)
        expired_spotify.refresh_from_db()
        assert expired_spotify.expires_at > old_expires


# =========================================================
# 2. ensure_spotify_token
# =========================================================

class TestEnsureSpotifyToken:

    def test_returns_none_when_no_account(self, user):
        result = ensure_spotify_token(user)
        assert result is None

    def test_returns_account_when_token_valid(self, user, fresh_spotify):
        result = ensure_spotify_token(user)
        assert result == fresh_spotify

    def test_calls_refresh_when_token_expired(self, user, expired_spotify):
        mock_resp = _mock_token_response("refreshed_token")
        with patch("users.services.requests.post", return_value=mock_resp):
            result = ensure_spotify_token(user)
        assert result is not None
        expired_spotify.refresh_from_db()
        assert expired_spotify.access_token == "refreshed_token"

    def test_does_not_call_refresh_when_valid(self, user, fresh_spotify):
        with patch("users.services.requests.post") as mock_post:
            ensure_spotify_token(user)
        mock_post.assert_not_called()


# =========================================================
# 3. refresh_youtube_account
# =========================================================

class TestRefreshYoutubeAccount:

    def test_calls_google_token_endpoint(self, expired_youtube):
        mock_resp = _mock_token_response("new_yt_access")
        with patch("users.services.requests.post", return_value=mock_resp) as mock_post:
            refresh_youtube_account(expired_youtube)
        url = mock_post.call_args[0][0]
        assert "googleapis.com" in url

    def test_updates_access_token_in_db(self, expired_youtube):
        mock_resp = _mock_token_response("new_yt_token")
        with patch("users.services.requests.post", return_value=mock_resp):
            refresh_youtube_account(expired_youtube)
        expired_youtube.refresh_from_db()
        assert expired_youtube.access_token == "new_yt_token"

    def test_keeps_old_refresh_token_when_not_returned(self, expired_youtube):
        mock_resp = _mock_token_response("new_yt_access")
        with patch("users.services.requests.post", return_value=mock_resp):
            refresh_youtube_account(expired_youtube)
        expired_youtube.refresh_from_db()
        assert expired_youtube.refresh_token == "yt_old_refresh"

    def test_raises_on_http_error(self, expired_youtube):
        mock_resp = MagicMock()
        mock_resp.raise_for_status.side_effect = req.HTTPError("403 Forbidden")
        with patch("users.services.requests.post", return_value=mock_resp):
            with pytest.raises(req.HTTPError):
                refresh_youtube_account(expired_youtube)


# =========================================================
# 4. ensure_youtube_token
# =========================================================

class TestEnsureYoutubeToken:

    def test_returns_none_when_no_account(self, user):
        result = ensure_youtube_token(user)
        assert result is None

    def test_returns_account_when_token_valid(self, user, fresh_youtube):
        result = ensure_youtube_token(user)
        assert result == fresh_youtube

    def test_calls_refresh_when_token_expired(self, user, expired_youtube):
        mock_resp = _mock_token_response("refreshed_yt")
        with patch("users.services.requests.post", return_value=mock_resp):
            result = ensure_youtube_token(user)
        assert result is not None
        expired_youtube.refresh_from_db()
        assert expired_youtube.access_token == "refreshed_yt"

    def test_does_not_call_refresh_when_valid(self, user, fresh_youtube):
        with patch("users.services.requests.post") as mock_post:
            ensure_youtube_token(user)
        mock_post.assert_not_called()


# =========================================================
# 5. ensure_valid_external_tokens
# =========================================================

class TestEnsureValidExternalTokens:

    def test_calls_both_spotify_and_youtube(self, user):
        with patch("users.services.ensure_spotify_token") as mock_sp, \
             patch("users.services.ensure_youtube_token") as mock_yt:
            ensure_valid_external_tokens(user)
        mock_sp.assert_called_once_with(user)
        mock_yt.assert_called_once_with(user)
