import requests
from django.utils import timezone
from users.models import YoutubeChannel, UserYoutubeChannel, YoutubeAccount
import logging
from celery import shared_task
from users.youtube_classifiers import compute_music_score
from users.services import ensure_youtube_token
from utils.locks import ResourceLock, ResourceLockedException

logger = logging.getLogger(__name__)


@shared_task
def sync_youtube_user(youtube_account_id):
    try:
        with ResourceLock("youtube_user_sync", youtube_account_id, timeout=900):
            created_channels = fetch_user_channels(youtube_account_id)
            if created_channels:
                channel_ids = [ch.id for ch in created_channels]
                classify_channels.delay(channel_ids, youtube_account_id)
    except ResourceLockedException:
        logger.info(f"User {youtube_account_id} sync already in progress, skipping")
        return


def fetch_user_channels(youtube_account_id):
    """
    Sync user YouTube subscriptions:
    - fetch user subscribed channels
    - creates YoutubeChannel
    - creates UserYoutubeChannel
    """
    try:
        account = YoutubeAccount.objects.get(id=youtube_account_id)
    except YoutubeAccount.DoesNotExist:
        logger.error("YouTube account does not exist")
        return []

    token = ensure_youtube_token(account.user)
    url = "https://www.googleapis.com/youtube/v3/subscriptions"
    headers = {"Authorization": f"Bearer {token}"}
    params = {
        "part": "snippet",
        "mine": "true",
        "maxResults": 50,
    }

    all_created_channels = []

    while url:
        try:
            response = requests.get(url, headers=headers, params=params, timeout=15)
            response.raise_for_status()
            data = response.json()
        except requests.exceptions.RequestException as e:
            logger.error(f"YouTube subscriptions sync failed: {e}")
            return all_created_channels

        items = data.get("items", [])

        # Get all existing channel IDs for this batch
        channel_ids = [
            item.get("snippet", {}).get("resourceId", {}).get("channelId")
            for item in items
        ]
        channel_ids = [cid for cid in channel_ids if cid]

        existing_channels = {
            ch.channel_id: ch
            for ch in YoutubeChannel.objects.filter(channel_id__in=channel_ids)
        }

        channels_to_create = []

        for item in items:
            snippet = item.get("snippet", {})
            resource = snippet.get("resourceId", {})

            channel_id = resource.get("channelId")
            title = snippet.get("title")

            if not channel_id:
                continue

            if channel_id not in existing_channels:
                channel = YoutubeChannel(
                    channel_id=channel_id,
                    title=title,
                )
                channels_to_create.append(channel)

        # Bulk create new channels
        if channels_to_create:
            YoutubeChannel.objects.bulk_create(channels_to_create, ignore_conflicts=True)

        existing_channels_ids = set(existing_channels.keys())
        new_channels_ids = set(channel_ids) - existing_channels_ids
        created_channels = YoutubeChannel.objects.filter(
            channel_id__in=new_channels_ids
        )

        all_created_channels.extend(list(created_channels))

        # Refresh channel lookup after creation
        all_channels = {
            ch.channel_id: ch
            for ch in YoutubeChannel.objects.filter(channel_id__in=channel_ids)
        }

        # Get existing user-channel relationships
        existing_user_channels = set(
            UserYoutubeChannel.objects.filter(
                user=account.user,
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

            if channel.id not in existing_user_channels:
                user_channels_to_create.append(
                    UserYoutubeChannel(
                        user=account.user,
                        channel=channel,
                        subscribed_at=snippet.get("publishedAt"),
                    )
                )

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

    account.last_synced_at = timezone.now()
    account.save(update_fields=["last_synced_at"])
    return all_created_channels


def fetch_channel_recent_videos(channel_id, youtube_account_id):
    try:
        account = YoutubeAccount.objects.get(id=youtube_account_id)
    except YoutubeAccount.DoesNotExist:
        logger.error("YouTube account does not exist")
        return []

    token = ensure_youtube_token(account.user)
    search_url = "https://www.googleapis.com/youtube/v3/search"
    headers = {"Authorization": f"Bearer {token}"}
    params = {
        "part": "snippet",
        "channelId": channel_id,
        "type": "video",
        "order": "date",
        "maxResults": 20,
    }

    try:
        response = requests.get(search_url, headers=headers, params=params, timeout=15)
        response.raise_for_status()
        data = response.json()
    except requests.exceptions.RequestException as e:
        logger.error(f"Failed to fetch videos for channel {channel_id}: {e}")
        return []

    video_ids = [item["id"]["videoId"] for item in data.get("items", [])]
    if not video_ids or len(video_ids) < 5:
        return []

    videos_url = "https://www.googleapis.com/youtube/v3/videos"
    params = {"part": "snippet", "id": ",".join(video_ids)}

    try:
        r = requests.get(videos_url, headers=headers, params=params, timeout=15)
        r.raise_for_status()
        data = r.json()
    except requests.exceptions.RequestException as e:
        logger.error(f"Failed to fetch video details for channel {channel_id}: {e}")
        return []

    return [
        int(item["snippet"]["categoryId"])
        for item in data.get("items", [])
        if "snippet" in item and "categoryId" in item["snippet"]
    ]


@shared_task
def classify_channels(channel_ids, youtube_account_id):
    """Klasyfikuj kanały - channel_ids to lista ID kanałów"""
    if not channel_ids:
        return

    for channel_id in channel_ids:
        classify_channel.delay(channel_id, youtube_account_id)


@shared_task(bind=True, max_retries=3)
def classify_channel(self, channel_id, youtube_account_id):
    try:
        with ResourceLock("channel_classify", channel_id, timeout=300):
            try:
                account = YoutubeAccount.objects.get(id=youtube_account_id)
                token = ensure_youtube_token(account.user)
                channel = YoutubeChannel.objects.get(id=channel_id)
            except (YoutubeAccount.DoesNotExist, YoutubeChannel.DoesNotExist) as e:
                logger.error(f"Channel classification failed: {e}")
                return

            url = 'https://www.googleapis.com/youtube/v3/channels/'
            params = {
                'part': 'snippet,topicDetails',
                'id': channel.channel_id,
            }
            headers = {"Authorization": f"Bearer {token}"}

            try:
                response = requests.get(url, headers=headers, params=params, timeout=15)
                response.raise_for_status()
                data = response.json()
            except requests.exceptions.RequestException as e:
                logger.error(f"YouTube API error for channel {channel_id}: {e}")
                raise self.retry(exc=e, countdown=60)

            items = data.get('items', [])
            if not items:
                logger.warning(f"No data returned for channel {channel_id}")
                return

            snippet = items[0].get("snippet", {})
            channel_name = snippet.get("title", "Unknown")
            channel_videos = fetch_channel_recent_videos(channel.channel_id, youtube_account_id)
            result = compute_music_score(data, channel_videos)

            if result.get("is_music"):
                logger.info(
                    f"[MUSIC] {channel_name} (score={result['total_score']}, "
                    f"topics={result['score_topics']}, text={result['score_text']}, "
                    f"videos={result['score_videos']})"
                )

            channel.is_music = result.get("is_music", False)
            channel.confidence_score = result["total_score"]
            channel.last_classified_at = timezone.now()
            channel.save(update_fields=["is_music", "confidence_score", "last_classified_at"])

    except ResourceLockedException:
        logger.info(f"Channel {channel_id} is already being classified, skipping")
        return