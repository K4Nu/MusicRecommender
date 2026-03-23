"""
Tests for recomendations/services/tag_filter.py

Covers: is_valid_tag, filter_track_tags, filter_artist_tags
"""
import pytest
from music.models import Tag, TrackTag, ArtistTag, Artist, Album, Track
from recomendations.services.tag_filter import (
    is_valid_tag,
    filter_track_tags,
    filter_artist_tags,
    MIN_TAG_USAGE_COUNT,
)


# =========================================================
# FIXTURES
# =========================================================

@pytest.fixture
def valid_tag(db):
    return Tag.objects.create(
        name="rock",
        category="genre",
        total_usage_count=5000,
    )


@pytest.fixture
def blocked_tag(db):
    # "seen live" is in BLOCKED_TAG_NAMES
    return Tag.objects.create(
        name="seen live",
        category="other",
        total_usage_count=9999,
    )


@pytest.fixture
def low_count_tag(db):
    return Tag.objects.create(
        name="very obscure tag",
        category="other",
        total_usage_count=1,
    )


@pytest.fixture
def artist(db):
    return Artist.objects.create(name="Filter Test Artist", spotify_id="filter_artist_1")


@pytest.fixture
def album(db, artist):
    a = Album.objects.create(name="Filter Test Album", spotify_id="filter_album_1")
    a.artists.add(artist)
    return a


@pytest.fixture
def track(db, artist, album):
    t = Track.objects.create(
        name="Filter Test Track",
        spotify_id="filter_track_1",
        album=album,
        duration_ms=200000,
    )
    t.artists.add(artist)
    return t


# =========================================================
# 1. is_valid_tag
# =========================================================

class TestIsValidTag:

    def test_valid_tag_passes(self, valid_tag):
        assert is_valid_tag(valid_tag) is True

    def test_blocked_tag_is_rejected(self, blocked_tag):
        assert is_valid_tag(blocked_tag) is False

    def test_low_usage_count_is_rejected(self, low_count_tag):
        assert is_valid_tag(low_count_tag) is False

    def test_zero_usage_count_passes(self, db):
        # total_usage_count=0 is falsy → usage check is skipped → tag passes
        tag = Tag.objects.create(
            name="electronic",
            category="genre",
            total_usage_count=0,
        )
        assert is_valid_tag(tag) is True

    def test_exactly_min_count_passes(self, db):
        tag = Tag.objects.create(
            name="indie",
            category="genre",
            total_usage_count=MIN_TAG_USAGE_COUNT,
        )
        assert is_valid_tag(tag) is True

    def test_one_below_min_count_fails(self, db):
        tag = Tag.objects.create(
            name="obscure sub genre",
            category="genre",
            total_usage_count=MIN_TAG_USAGE_COUNT - 1,
        )
        assert is_valid_tag(tag) is False

    def test_another_blocked_tag(self, db):
        # "spotify" is also in BLOCKED_TAG_NAMES
        tag = Tag.objects.create(
            name="spotify",
            category="other",
            total_usage_count=99999,
        )
        assert is_valid_tag(tag) is False


# =========================================================
# 2. filter_track_tags
# =========================================================

class TestFilterTrackTags:

    def test_active_valid_tag_passes(self, db, track, valid_tag):
        TrackTag.objects.create(
            track=track, tag=valid_tag, weight=0.8, source="lastfm", is_active=True
        )
        result = filter_track_tags(TrackTag.objects.filter(track=track))
        assert result.count() == 1

    def test_inactive_tag_is_excluded(self, db, track, valid_tag):
        TrackTag.objects.create(
            track=track, tag=valid_tag, weight=0.8, source="lastfm", is_active=False
        )
        result = filter_track_tags(TrackTag.objects.filter(track=track))
        assert result.count() == 0

    def test_blocked_tag_is_excluded(self, db, track, blocked_tag):
        TrackTag.objects.create(
            track=track, tag=blocked_tag, weight=0.8, source="lastfm", is_active=True
        )
        result = filter_track_tags(TrackTag.objects.filter(track=track))
        assert result.count() == 0

    def test_low_count_tag_is_excluded(self, db, track, low_count_tag):
        TrackTag.objects.create(
            track=track, tag=low_count_tag, weight=0.8, source="lastfm", is_active=True
        )
        result = filter_track_tags(TrackTag.objects.filter(track=track))
        assert result.count() == 0

    def test_mixed_tags_only_valid_returned(self, db, track, valid_tag, blocked_tag):
        TrackTag.objects.create(
            track=track, tag=valid_tag, weight=0.8, source="lastfm", is_active=True
        )
        TrackTag.objects.create(
            track=track, tag=blocked_tag, weight=0.5, source="lastfm", is_active=True
        )
        result = filter_track_tags(TrackTag.objects.filter(track=track))
        assert result.count() == 1
        assert result.first().tag == valid_tag

    def test_empty_queryset_returns_empty(self, db, track):
        result = filter_track_tags(TrackTag.objects.filter(track=track))
        assert result.count() == 0


# =========================================================
# 3. filter_artist_tags
# =========================================================

class TestFilterArtistTags:

    def test_active_valid_tag_passes(self, db, artist, valid_tag):
        ArtistTag.objects.create(
            artist=artist, tag=valid_tag, weight=0.7, source="lastfm", is_active=True
        )
        result = filter_artist_tags(ArtistTag.objects.filter(artist=artist))
        assert result.count() == 1

    def test_inactive_tag_is_excluded(self, db, artist, valid_tag):
        ArtistTag.objects.create(
            artist=artist, tag=valid_tag, weight=0.7, source="lastfm", is_active=False
        )
        result = filter_artist_tags(ArtistTag.objects.filter(artist=artist))
        assert result.count() == 0

    def test_blocked_tag_is_excluded(self, db, artist, blocked_tag):
        ArtistTag.objects.create(
            artist=artist, tag=blocked_tag, weight=0.7, source="lastfm", is_active=True
        )
        result = filter_artist_tags(ArtistTag.objects.filter(artist=artist))
        assert result.count() == 0

    def test_low_count_tag_is_excluded(self, db, artist, low_count_tag):
        ArtistTag.objects.create(
            artist=artist, tag=low_count_tag, weight=0.7, source="lastfm", is_active=True
        )
        result = filter_artist_tags(ArtistTag.objects.filter(artist=artist))
        assert result.count() == 0

    def test_empty_queryset_returns_empty(self, db, artist):
        result = filter_artist_tags(ArtistTag.objects.filter(artist=artist))
        assert result.count() == 0
