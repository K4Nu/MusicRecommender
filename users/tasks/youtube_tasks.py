import requests
from django.utils import timezone
from users.models import YoutubeChannel, UserYoutubeChannel, YoutubeAccount
import logging
from celery import shared_task
from users.youtube_classifiers import compute_music_score
from users.services import ensure_youtube_token

logger = logging.getLogger(__name__)

@shared_task
def sync_youtube_user(youtube_account_id):

    new_channels_data=fetch_user_channels(youtube_account_id)
    try:
        access_code=YoutubeAccount.objects.get(youtube_account_id=youtube_account_id).access_code
    except YoutubeAccount.DoesNotExist:
        logger.info('Youtube Account does not exist')
        return
    classify_channels(new_channels_data, youtube_account_id)


def fetch_user_channels(youtube_account):
    """
    Sync user YouTube subscriptions:
    - fetch user subscribed channels
    - creates YoutubeChannel
    - creates UserYoutubeChannel
    """
    try:
        account=YoutubeAccount.objects.get(id=youtube_account)
    except YoutubeAccount.DoesNotExist:
        logger.error("YouTube account does not exist")
        return
    token=ensure_youtube_token(account.user)
    url = "https://www.googleapis.com/youtube/v3/subscriptions"
    headers = {
        "Authorization": f"Bearer {token}"
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

        existing_channels_ids=set(existing_channels.keys())
        new_channels_ids=set(channel_ids)-existing_channels_ids
        created_channels=YoutubeChannel.objects.filter(
            channel_id__in=new_channels_ids
        )

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
    return created_channels


@shared_task
def classify_channels(channels,youtube_account_id):
    try:
        account = YoutubeAccount.objects.get(id=youtube_account_id)
    except YoutubeAccount.DoesNotExist:
        logger.error("YouTube account does not exist")
        return
    token = ensure_youtube_token(account.user)
    for ids in channels:
        classify_channel.delay(ids,token)

@shared_task(bind=True,max_retries=3)
def classify_channel(channel_id,youtube_account_id):
    try:
        account = YoutubeAccount.objects.get(id=youtube_account_id)
    except YoutubeAccount.DoesNotExist:
        logger.error("YouTube account does not exist")
        return
    token = ensure_youtube_token(account.user)
    try:
        channel=YoutubeChannel.objects.get(id=channel_id)
    except YoutubeChannel.DoesNotExist:
        return

    url='https://www.googleapis.com/youtube/v3/channels/'
    params = {
        'part': 'snippet,topicDetails',
        'id': channel_id,
    }
    headers = {"Authorization": f"Bearer {token}"}
    try:
        response = requests.get(url, headers=headers, params=params)
        response.raise_for_status()
        data=response.json()
    except requests.exceptions.RequestException as e:
        print(f"[check_youtube_channel_category] error: {e}")
        return

    items=data.get('items',[])
    if not items:
        return
    snippet = items[0].get("snippet", {})
    channel_name = snippet.get("title", "Unknown")
    #fetch_channel_recent_videos

