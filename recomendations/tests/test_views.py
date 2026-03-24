import pytest
from unittest.mock import patch
from recomendations.models import OnboardingEvent, RecommendationFeedback

@pytest.mark.django_db
def test_user_status(auth_client):
    res = auth_client.get("/api/me/")
    assert res.status_code == 200
    assert "onboarding_completed" in res.data
    assert "has_spotify" in res.data
    assert "has_youtube" in res.data

@pytest.mark.django_db
def test_initial_setup_view(auth_client):
    res = auth_client.get("/api/cold_start/")
    assert res.status_code == 200
    assert "needs_onboarding" in res.data

def test_onboarding_requires_auth(client):
    res = client.post("/api/onboarding/", {"events": []}, format="json")
    assert res.status_code == 401

@pytest.mark.django_db
def test_onboarding_no_events(auth_client):
    res = auth_client.post("/api/onboarding/", {"events": []}, format="json")
    assert res.status_code == 400

@pytest.mark.django_db
def test_onboarding_too_many_events(auth_client):
    events = [{"cold_start_track_id": 1, "action": "LIKE"}] * 21
    res = auth_client.post("/api/onboarding/", {"events": events}, format="json")
    assert res.status_code == 400

@pytest.mark.django_db
def test_onboarding_already_completed(auth_client, user):
    user.profile.onboarding_completed = True
    user.profile.save()
    res = auth_client.post("/api/onboarding/", {"events": []}, format="json")
    assert res.status_code == 200
    assert res.data["status"] == "already_completed"

@pytest.mark.django_db
def test_onboarding_like_creates_event(auth_client, cold_start_track):
    res = auth_client.post("/api/onboarding/", {
        "events": [{"cold_start_track_id": cold_start_track.id, "action": "LIKE"}]
    }, format="json")
    assert res.status_code == 200
    assert res.data["status"] == "needs_more_likes"
    assert OnboardingEvent.objects.count() == 1

@pytest.mark.django_db
def test_onboarding_duplicate_event_ignored(auth_client, user, cold_start_track):
    OnboardingEvent.objects.create(
        user=user, cold_start_track=cold_start_track, action="LIKE"
    )
    res = auth_client.post("/api/onboarding/", {
        "events": [{"cold_start_track_id": cold_start_track.id, "action": "LIKE"}]
    }, format="json")
    assert res.data["events_ignored"] == 1
    assert OnboardingEvent.objects.count() == 1

@pytest.mark.django_db
def test_onboarding_completes_after_3_likes(auth_client, user, cold_start_tracks):
    events = [
        {"cold_start_track_id": t.id, "action": "LIKE"}
        for t in cold_start_tracks[:3]
    ]
    with patch("recomendations.views.build_recommendation_task"):
        res = auth_client.post("/api/onboarding/", {"events": events}, format="json")
    assert res.data["status"] == "onboarding_completed"
    user.profile.refresh_from_db()
    assert user.profile.onboarding_completed is True


# --- HomeApiView ---

@pytest.mark.django_db
def test_home_view_requires_onboarding(auth_client):
    res = auth_client.get("/api/home/")
    assert res.status_code == 403
    assert res.data["error"] == "onboarding_not_completed"


@pytest.mark.django_db
def test_home_view_returns_data(auth_client, recommendation):
    with patch("recomendations.views.get_or_build_recommendation", return_value=recommendation):
        res = auth_client.get("/api/home/")
    assert res.status_code == 200
    assert "strategy" in res.data


# --- RecommendationFeedbackView ---

def test_feedback_requires_auth(client):
    res = client.post("/api/feedback/", {}, format="json")
    assert res.status_code == 401


@pytest.mark.django_db
def test_feedback_item_not_found(auth_client):
    res = auth_client.post("/api/feedback/", {
        "recommendation_item_id": 9999,
        "action": "LIKE",
    }, format="json")
    assert res.status_code == 404


@pytest.mark.django_db
def test_feedback_wrong_user_returns_403(auth_client, recommendation_item):
    from django.contrib.auth import get_user_model
    other_user = get_user_model().objects.create_user(
        email="other@test.com", password="test123"
    )
    recommendation_item.recommendation.user = other_user
    recommendation_item.recommendation.save()

    res = auth_client.post("/api/feedback/", {
        "recommendation_item_id": recommendation_item.id,
        "action": "LIKE",
    }, format="json")
    assert res.status_code == 403


@pytest.mark.django_db
def test_feedback_like_creates_feedback(auth_client, recommendation_item):
    with patch("recomendations.views.build_recommendation_task"):
        res = auth_client.post("/api/feedback/", {
            "recommendation_item_id": recommendation_item.id,
            "action": "LIKE",
        }, format="json")
    assert res.status_code == 200
    assert res.data["status"] == "created"
    assert res.data["action"] == "LIKE"
    assert RecommendationFeedback.objects.count() == 1


@pytest.mark.django_db
def test_feedback_updates_existing(auth_client, recommendation_item, user):
    RecommendationFeedback.objects.create(
        user=user,
        recommendation_item=recommendation_item,
        recommendation=recommendation_item.recommendation,
        action="LIKE",
    )
    with patch("recomendations.views.build_recommendation_task"):
        res = auth_client.post("/api/feedback/", {
            "recommendation_item_id": recommendation_item.id,
            "action": "DISLIKE",
        }, format="json")
    assert res.status_code == 200
    assert res.data["status"] == "updated"
    assert res.data["action"] == "DISLIKE"
