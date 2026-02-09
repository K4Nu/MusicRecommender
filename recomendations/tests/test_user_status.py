import pytest
from django.urls import reverse

@pytest.mark.django_db
def test_user_status(auth_client):
    res = auth_client.get("/api/me/")

    assert res.status_code == 200
    assert "onboarding_completed" in res.data
    assert "has_spotify" in res.data
    assert "has_youtube" in res.data
