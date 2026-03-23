import pytest
from music.models import Artist, Album, Track, Tag, TrackTag, ArtistTag


# =========================================================
# FIXTURES
# =========================================================

@pytest.fixture
def artist(db):
    return Artist.objects.create(name="Music Test Artist", spotify_id="mt_artist_1")


@pytest.fixture
def album(db, artist):
    a = Album.objects.create(name="Music Test Album", spotify_id="mt_album_1")
    a.artists.add(artist)
    return a


@pytest.fixture
def track(db, artist, album):
    t = Track.objects.create(
        name="Music Test Track",
        spotify_id="mt_track_1",
        album=album,
        duration_ms=210000,
        popularity=80,
    )
    t.artists.add(artist)
    return t


@pytest.fixture
def tag(db):
    return Tag.objects.create(
        name="Rock",
        category="genre",
        total_usage_count=10000,
    )


# =========================================================
# 1. Tag model
# =========================================================

class TestTagModel:

    def test_create_tag(self, tag):
        assert tag.pk is not None
        assert tag.name == "Rock"

    def test_save_auto_normalizes_name(self, tag):
        # save() calls normalize(name), so "Rock" → "rock"
        assert tag.normalized_name == "rock"

    def test_str_returns_name(self, tag):
        assert str(tag) == "Rock"

    def test_normalize_lowercases(self):
        assert Tag.normalize("Metal") == "metal"

    def test_normalize_strips_whitespace(self):
        assert Tag.normalize("  punk  ") == "punk"

    def test_normalize_replaces_hyphens_with_spaces(self):
        assert Tag.normalize("post-rock") == "post rock"

    def test_normalize_combined(self):
        assert Tag.normalize("Alt-Rock ") == "alt rock"

    def test_save_normalizes_hyphenated_name(self, db):
        tag = Tag.objects.create(name="Post-Punk", category="genre", total_usage_count=5000)
        assert tag.normalized_name == "post punk"

    def test_default_category_is_other(self, db):
        tag = Tag.objects.create(name="somethingelse", total_usage_count=100)
        assert tag.category == "other"

    def test_default_usage_count_is_zero(self, db):
        tag = Tag.objects.create(name="brandnew tag")
        assert tag.total_usage_count == 0


# =========================================================
# 2. Artist model
# =========================================================

class TestArtistModel:

    def test_create_artist(self, artist):
        assert artist.pk is not None
        assert artist.name == "Music Test Artist"

    def test_spotify_id_stored(self, artist):
        assert artist.spotify_id == "mt_artist_1"

    def test_str_contains_name(self, artist):
        assert "Music Test Artist" in str(artist)


# =========================================================
# 3. Album model
# =========================================================

class TestAlbumModel:

    def test_create_album(self, album):
        assert album.pk is not None
        assert album.name == "Music Test Album"

    def test_album_has_artist(self, album, artist):
        assert artist in album.artists.all()

    def test_album_spotify_id(self, album):
        assert album.spotify_id == "mt_album_1"


# =========================================================
# 4. Track model
# =========================================================

class TestTrackModel:

    def test_create_track(self, track):
        assert track.pk is not None
        assert track.name == "Music Test Track"

    def test_track_duration(self, track):
        assert track.duration_ms == 210000

    def test_track_popularity(self, track):
        assert track.popularity == 80

    def test_track_has_artist(self, track, artist):
        assert artist in track.artists.all()

    def test_track_has_album(self, track, album):
        assert track.album == album

    def test_str_returns_name(self, track):
        assert str(track) == "Music Test Track"

    def test_track_without_preview_url(self, db, artist, album):
        t = Track.objects.create(
            name="No Preview Track",
            spotify_id="no_preview_1",
            album=album,
            duration_ms=180000,
        )
        t.artists.add(artist)
        assert t.preview_url is None

    def test_track_default_preview_type_is_embed(self, track):
        assert track.preview_type == "embed"


# =========================================================
# 5. TrackTag model + manager
# =========================================================

class TestTrackTagModel:

    def test_create_track_tag(self, db, track, tag):
        tt = TrackTag.objects.create(
            track=track, tag=tag, weight=0.85, source="lastfm", is_active=True
        )
        assert tt.pk is not None
        assert tt.weight == 0.85
        assert tt.is_active is True

    def test_str_representation(self, db, track, tag):
        tt = TrackTag.objects.create(
            track=track, tag=tag, weight=0.7, source="lastfm", is_active=True
        )
        s = str(tt)
        assert "Music Test Track" in s
        assert "Rock" in s

    def test_top_tags_manager_returns_correct_source(self, db, track, tag):
        TrackTag.objects.create(
            track=track, tag=tag, weight=0.9, source="computed", is_active=True
        )
        top = TrackTag.objects.top_tags(track, limit=5, source="computed")
        assert top.count() == 1

    def test_top_tags_manager_respects_limit(self, db, track):
        for i in range(5):
            t = Tag.objects.create(
                name=f"genre_{i}", category="genre", total_usage_count=1000 + i
            )
            TrackTag.objects.create(
                track=track, tag=t, weight=0.5 + i * 0.05, source="computed", is_active=True
            )
        top = TrackTag.objects.top_tags(track, limit=3, source="computed")
        assert top.count() == 3

    def test_inactive_tag_not_returned_by_top_tags(self, db, track, tag):
        # top_tags filters on tag__is_active (the Tag model's field)
        tag.is_active = False
        tag.save()
        TrackTag.objects.create(
            track=track, tag=tag, weight=0.9, source="computed", is_active=True
        )
        top = TrackTag.objects.top_tags(track, limit=5, source="computed")
        assert top.count() == 0


# =========================================================
# 6. ArtistTag model + manager
# =========================================================

class TestArtistTagModel:

    def test_create_artist_tag(self, db, artist, tag):
        at = ArtistTag.objects.create(
            artist=artist, tag=tag, weight=0.75, source="lastfm", is_active=True
        )
        assert at.pk is not None
        assert at.weight == 0.75

    def test_str_representation(self, db, artist, tag):
        at = ArtistTag.objects.create(
            artist=artist, tag=tag, weight=0.6, source="lastfm", is_active=True
        )
        s = str(at)
        assert "Music Test Artist" in s
        assert "Rock" in s

    def test_top_tags_manager(self, db, artist, tag):
        ArtistTag.objects.create(
            artist=artist, tag=tag, weight=0.8, source="computed", is_active=True
        )
        top = ArtistTag.objects.top_tags(artist, limit=5, source="computed")
        assert top.count() == 1

    def test_by_category_manager(self, db, artist, tag):
        ArtistTag.objects.create(
            artist=artist, tag=tag, weight=0.8, source="computed", is_active=True
        )
        result = ArtistTag.objects.by_category(artist, category="genre", source="computed")
        assert result.count() == 1

    def test_by_category_excludes_other_categories(self, db, artist):
        mood_tag = Tag.objects.create(
            name="happy", category="mood", total_usage_count=2000
        )
        ArtistTag.objects.create(
            artist=artist, tag=mood_tag, weight=0.5, source="computed", is_active=True
        )
        result = ArtistTag.objects.by_category(artist, category="genre", source="computed")
        assert result.count() == 0
