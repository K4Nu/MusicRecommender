import requests
from django.utils import timezone
from users.models import YoutubeChannel, UserYoutubeChannel
import logging

logger = logging.getLogger(__name__)


def sync_youtube_user(youtube_account):
    """
    Sync user YouTube subscriptions:
    - fetch user subscribed channels
    - creates YoutubeChannel
    - creates UserYoutubeChannel
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

        # Get all existing channel IDs for this batch
        channel_ids = [
            item.get("snippet", {}).get("resourceId", {}).get("channelId")
            for item in items
        ]
        channel_ids = [cid for cid in channel_ids if cid]  # Filter out None values

        existing_channels = {
            ch.channel_id: ch
            for ch in YoutubeChannel.objects.filter(channel_id__in=channel_ids)
        }

        channels_to_create = []
        channels_to_update = []

        for item in items:
            snippet = item.get("snippet", {})
            resource = snippet.get("resourceId", {})

            channel_id = resource.get("channelId")
            title = snippet.get("title")

            if not channel_id:
                continue

            # 🔹 1. Global channel
            if channel_id not in existing_channels:
                channel = YoutubeChannel(
                    channel_id=channel_id,
                    title=title,
                )
                channels_to_create.append(channel)

        # Bulk create new channels
        if channels_to_create:
            YoutubeChannel.objects.bulk_create(channels_to_create, ignore_conflicts=True)


        # Refresh channel lookup after creation
        all_channels = {
            ch.channel_id: ch
            for ch in YoutubeChannel.objects.filter(channel_id__in=channel_ids)
        }

        # 🔹 2. user ↔ channel (BULK OPERATION)
        # Get existing user-channel relationships
        existing_user_channels = set(
            UserYoutubeChannel.objects.filter(
                user=youtube_account.user,
                channel_id__in=[ch.id for ch in all_channels.values()]
            ).values_list('channel_id', flat=True)
        )

        user_channels_to_create = []

        for item in items:
            snippet = item.get("snippet", {})
            resource = snippet.get("resourceId", {})
            channel_id = resource.get("channelId")

            if not channel_id or channel_id not in all_channels:
                continue

            channel = all_channels[channel_id]

            # Only create if relationship doesn't exist
            if channel.id not in existing_user_channels:
                user_channels_to_create.append(
                    UserYoutubeChannel(
                        user=youtube_account.user,
                        channel=channel,
                        subscribed_at=snippet.get("publishedAt"),
                    )
                )

        # Bulk create user-channel relationships
        if user_channels_to_create:
            UserYoutubeChannel.objects.bulk_create(
                user_channels_to_create,
                ignore_conflicts=True
            )

        # pagination
        next_token = data.get("nextPageToken")
        if next_token:
            params["pageToken"] = next_token
            url = "https://www.googleapis.com/youtube/v3/subscriptions"
        else:
            url = None

    youtube_account.last_synced_at = timezone.now()
    youtube_account.save(update_fields=["last_synced_at"])