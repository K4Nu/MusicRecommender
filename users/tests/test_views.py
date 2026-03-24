import pytest
from unittest.mock import MagicMock, patch

from users.models import SpotifyAccount


def _mock_token_response(access_token="acc123", refresh_token="ref123", expires_in=3600):
    mock = MagicMock()
    mock.json.return_value = {
        "access_token": access_token,
        "refresh_token": refresh_token,
        "expires_in": expires_in,
    }
    mock.raise_for_status = MagicMock()
    return mock


def _mock_profile_response(spotify_id="spotify_user_1", display_name="Test User"):
    mock = MagicMock()
    mock.json.return_value = {"id": spotify_id, "display_name": display_name}
    mock.raise_for_status = MagicMock()
    return mock


# --- SpotifyConnect ---

def test_spotify_connect_requires_auth(client):
    res = client.post("/auth/spotify/connect/", {}, format="json")
    assert res.status_code == 401


@pytest.mark.django_db
def test_spotify_connect_missing_params(auth_client):
    res = auth_client.post("/auth/spotify/connect/", {}, format="json")
    assert res.status_code == 400
    assert "Missing" in res.data["detail"]


@pytest.mark.django_db
@patch("users.views.requests.post")
def test_spotify_connect_token_request_error(mock_post, auth_client):
    import requests as req
    mock_post.side_effect = req.exceptions.RequestException("timeout")
    res = auth_client.post("/auth/spotify/connect/", {
        "code": "abc", "redirect_uri": "http://localhost"
    }, format="json")
    assert res.status_code == 400
    assert "Failed to exchange" in res.data["detail"]


@pytest.mark.django_db
@patch("users.views.requests.post")
def test_spotify_connect_missing_tokens_in_response(mock_post, auth_client):
    mock = MagicMock()
    mock.json.return_value = {"access_token": None, "refresh_token": None}
    mock.raise_for_status = MagicMock()
    mock_post.return_value = mock

    res = auth_client.post("/auth/spotify/connect/", {
        "code": "abc", "redirect_uri": "http://localhost"
    }, format="json")
    assert res.status_code == 400
    assert "Invalid response" in res.data["detail"]


@pytest.mark.django_db
@patch("users.views.requests.get")
@patch("users.views.requests.post")
def test_spotify_connect_profile_request_error(mock_post, mock_get, auth_client):
    import requests as req
    mock_post.return_value = _mock_token_response()
    mock_get.side_effect = req.exceptions.RequestException("timeout")

    res = auth_client.post("/auth/spotify/connect/", {
        "code": "abc", "redirect_uri": "http://localhost"
    }, format="json")
    assert res.status_code == 400
    assert "Failed to fetch Spotify profile" in res.data["detail"]


@pytest.mark.django_db
@patch("users.views.fetch_spotify_initial_data")
@patch("users.views.requests.get")
@patch("users.views.requests.post")
def test_spotify_connect_success(mock_post, mock_get, mock_task, auth_client, user):
    mock_post.return_value = _mock_token_response()
    mock_get.return_value = _mock_profile_response()

    res = auth_client.post("/auth/spotify/connect/", {
        "code": "abc", "redirect_uri": "http://localhost"
    }, format="json")

    assert res.status_code == 200
    assert res.data["spotify_id"] == "spotify_user_1"
    assert SpotifyAccount.objects.filter(user=user).exists()
    mock_task.delay.assert_called_once_with(user.id)


# --- SpotifyAccountDisconnect ---

def test_spotify_disconnect_requires_auth(client):
    res = client.delete("/auth/spotify/disconnect/")
    assert res.status_code == 401


@pytest.mark.django_db
def test_spotify_disconnect_deletes_account(auth_client, user):
    SpotifyAccount.objects.create(
        user=user,
        spotify_id="sp1",
        access_token="acc",
        refresh_token="ref",
        expires_at="2099-01-01T00:00:00Z",
    )
    res = auth_client.delete("/auth/spotify/disconnect/")
    assert res.status_code == 200
    assert not SpotifyAccount.objects.filter(user=user).exists()


# --- DeleteAccountView ---

def test_delete_account_requires_auth(client):
    res = client.delete("/auth/delete-account/", {}, format="json")
    assert res.status_code == 401


@pytest.mark.django_db
def test_delete_account_missing_password(auth_client):
    res = auth_client.delete("/auth/delete-account/", {}, format="json")
    assert res.status_code == 400
    assert "Password required" in res.data["detail"]


@pytest.mark.django_db
def test_delete_account_wrong_password(auth_client):
    res = auth_client.delete("/auth/delete-account/", {
        "password": "wrongpassword"
    }, format="json")
    assert res.status_code == 400
    assert "Incorrect password" in res.data["detail"]


@pytest.mark.django_db
def test_delete_account_success(auth_client, user):
    from django.contrib.auth import get_user_model
    res = auth_client.delete("/auth/delete-account/", {
        "password": "test123"
    }, format="json")
    assert res.status_code == 204
    assert not get_user_model().objects.filter(id=user.id).exists()


# --- UserTopTracks ---

@pytest.mark.django_db
def test_user_top_tracks_empty(auth_client):
    res = auth_client.get("/user/top_track/")
    assert res.status_code == 200
    assert res.data == []
