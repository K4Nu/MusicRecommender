import requests
from django.utils import timezone
from users.models import YoutubeChannel, UserYoutubeChannel
import logging

logger = logging.getLogger(__name__)


def sync_youtube_user(youtube_account):
    """
    Sync user YouTube subcriptions:
    - fetch user subsribed channels
    - tworzy YoutubeChannel
    - tworzy UserYoutubeChannel
    """

    url = "https://www.googleapis.com/youtube/v3/subscriptions"
    headers = {
        "Authorization": f"Bearer {youtube_account.access_token}"
    }
    params = {
        "part": "snippet",
        "mine": "true",
        "maxResults": 50,
    }

    while url:
        try:
            response = requests.get(url, headers=headers, params=params, timeout=15)
            response.raise_for_status()
            data = response.json()
        except requests.exceptions.RequestException as e:
            logger.error(f"YouTube subscriptions sync failed: {e}")
            return

        items = data.get("items", [])

        for item in items:
            snippet = item.get("snippet", {})
            resource = snippet.get("resourceId", {})

            channel_id = resource.get("channelId")
            title = snippet.get("title")

            if not channel_id:
                continue

            # 🔹 1. Global channel
            channel, _ = YoutubeChannel.objects.get_or_create(
                channel_id=channel_id,
                defaults={
                    "title": title,
                }
            )

            # 🔹 2.user ↔ channel
            UserYoutubeChannel.objects.get_or_create(
                user=youtube_account.user,
                channel=channel,
                defaults={
                    "subscribed_at": snippet.get("publishedAt"),
                }
            )

        # pagination
        url = data.get("nextPageToken")
        if url:
            params["pageToken"] = url
            url = "https://www.googleapis.com/youtube/v3/subscriptions"

    youtube_account.last_synced_at = timezone.now()
    youtube_account.save(update_fields=["last_synced_at"])