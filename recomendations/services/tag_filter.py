# recomendations/services/tag_filter.py

# =========================================================
# TAG QUALITY FILTER
#
# LastFM tags are user-submitted - lots of noise:
# "guys I would fuck", "hairy chest", "seen live", etc.
#
# Two-layer filter:
# 1. Blocklist - explicit bad tags
# 2. Min usage count - tags used by < N people are noise
# =========================================================

# Explicit blocklist - personal/subjective/NSFW tags
BLOCKED_TAG_NAMES = {
    # NSFW / personal
    "guys i would fuck",
    "hairy chest",
    "sexy",
    "hot",
    "beautiful",
    "cute",
    "gorgeous",
    "handsome",

    # Meta tags (about listening behavior, not music)
    "seen live",
    "favourites",
    "favorite",
    "favorites",
    "favourite",
    "my favourite",
    "love",
    "loved",
    "awesome",
    "amazing",
    "great",
    "good",
    "best",
    "brilliant",
    "perfect",
    "interesting",

    # Useless descriptors
    "music",
    "songs",
    "tracks",
    "audio",
    "albums",
    "album",
    "artists",
    "artist",

    # Language/region noise
    "heard on pandora",
    "spotify",
    "youtube",
    "soundcloud",
    "lastfm",

    # Other noise
    "artistes",
    "male vocalists",
    "female vocalists",
    "under 2000 listeners",
    "all",
    "various",
    "misc",
}

# Minimum number of times a tag must be used globally to be considered valid
MIN_TAG_USAGE_COUNT = 3

def is_valid_tag(tag) -> bool:
    """
    Returns True if a tag is worth using for recommendations.
    tag = Tag model instance (needs .normalized_name and .total_usage_count)
    """
    # Blocklist check
    if tag.normalized_name.lower() in BLOCKED_TAG_NAMES:
        return False

    # Usage count check - ignore obscure/personal tags
    if tag.total_usage_count and tag.total_usage_count < MIN_TAG_USAGE_COUNT:
        return False

    return True


def filter_track_tags(track_tags_qs):
    """
    Filter a TrackTag queryset to only include quality tags.
    Usage:
        track_tags = filter_track_tags(TrackTag.objects.filter(track=track))
    """
    return track_tags_qs.filter(
        is_active=True,
        tag__total_usage_count__gte=MIN_TAG_USAGE_COUNT,
    ).exclude(
        tag__normalized_name__in=BLOCKED_TAG_NAMES,
    )

def filter_artist_tags(artist_tags_qs):
    """
    Filter an ArtistTag queryset to only include quality tags.
    """
    return artist_tags_qs.filter(
        is_active=True,
        tag__total_usage_count__gte=MIN_TAG_USAGE_COUNT,
    ).exclude(
        tag__normalized_name__in=BLOCKED_TAG_NAMES,
    )