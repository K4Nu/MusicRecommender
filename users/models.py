from django.contrib.auth.models import BaseUserManager
from django.contrib.auth.models import AbstractBaseUser
from django.contrib.auth.models import PermissionsMixin
from django.db.models import Q
from django.core.validators import validate_email
from django.core.exceptions import ValidationError
from django.core.validators import MinValueValidator, MaxValueValidator
from django.db import models
from django.contrib.auth.models import User
from django.utils import timezone
from django.conf import settings
from cryptography.fernet import Fernet
import base64
from datetime import timedelta


class EncryptedTextField(models.TextField):
    """Pole tekstowe z automatycznym szyfrowaniem/deszyfrowaniem"""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        key = base64.urlsafe_b64encode(settings.SECRET_KEY[:32].encode().ljust(32)[:32])
        self.cipher = Fernet(key)

    def get_prep_value(self, value):
        if value is None:
            return value
        encrypted = self.cipher.encrypt(value.encode())
        return encrypted.decode()

    def from_db_value(self, value, expression, connection):
        if value is None:
            return value
        # Deszyfruj po pobraniu z bazy
        return self.cipher.decrypt(value.encode()).decode()

    def to_python(self, value):
        if isinstance(value, str) and value:
            try:
                return self.cipher.decrypt(value.encode()).decode()
            except:
                return value
        return value


class YoutubeAccountManager(models.Manager):
    """Manager do wygodnych zapytań o konta YouTube"""

    def expired(self):
        """Zwraca konta z wygasłymi tokenami"""
        return self.filter(expires_at__lt=timezone.now())

    def needs_refresh(self):
        """Zwraca konta wymagające odświeżenia tokenu (wygasające w ciągu 5 min)"""
        threshold = timezone.now() + timedelta(minutes=5)
        return self.filter(expires_at__lt=threshold)

class MyUserManager(BaseUserManager):
    """
    A custom user manager to deal with emails as unique identifiers for auth
    instead of usernames. The default that's used is "UserManager"
    """

    def _create_user(self, email, password, **extra_fields):
        if not email:
            raise ValueError('The email must be set')
        if not password:
            raise ValueError('The password must be set')
        try:
            validate_email(email)
        except ValidationError:
            raise ValueError('Invalid email address')
        email = self.normalize_email(email)
        user = self.model(email=email, **extra_fields)
        user.set_password(password)
        user.save(using=self._db)
        return user

    def create_user(self, email, password=None, **extra_fields):
        extra_fields.setdefault("is_active", True)
        return self._create_user(email, password, **extra_fields)

    def create_superuser(self, email, password, **extra_fields):
        extra_fields.setdefault('is_staff', True)
        extra_fields.setdefault('is_superuser', True)
        extra_fields.setdefault('is_active', True)

        if extra_fields.get('is_staff') is not True:
            raise ValueError('Superuser must have is_staff=True.')
        if extra_fields.get('is_superuser') is not True:
            raise ValueError('Superuser must have is_superuser=True.')
        return self._create_user(email, password, **extra_fields)

class User(AbstractBaseUser, PermissionsMixin):
    email = models.EmailField(max_length=255, unique=True)
    is_staff = models.BooleanField(
        default=False)
    is_active = models.BooleanField(
        default=True)

    USERNAME_FIELD = 'email'
    objects = MyUserManager()

    def __str__(self):
        return self.email

class SpotifyAccount(models.Model):
    user = models.OneToOneField(User, on_delete=models.CASCADE)
    spotify_id = models.CharField(max_length=255, unique=True)
    access_token = models.CharField(max_length=255)
    refresh_token = models.CharField(max_length=255)
    expires_at = models.DateTimeField()
    created_at = models.DateTimeField(auto_now_add=True)
    playlists_etag = models.CharField(max_length=128, null=True, blank=True)
    last_synced_at = models.DateTimeField(null=True, blank=True)

    def __str__(self):
        return self.user.email

    def is_expired(self):
        return self.expires_at <= timezone.now()

    def update_tokens(self, access_token, refresh_token=None, expires_in=3600):
        self.access_token = access_token
        if refresh_token:
            self.refresh_token = refresh_token
        self.expires_at = timezone.now() + timedelta(seconds=expires_in)
        self.save()

class Genre(models.Model):
    name=models.CharField(max_length=255)

    def __str__(self):
        return self.name

class Artist(models.Model):
    spotify_id = models.CharField(max_length=255, unique=True)
    name=models.CharField(max_length=255)
    genres=models.ManyToManyField(Genre, related_name='artists')
    popularity=models.IntegerField(null=True)
    image_url=models.URLField(null=True,blank=True)

    def __str__(self):
        return self.name

class Album(models.Model):
    class AlbumTypes(models.TextChoices):
        ALBUM = "album", "Album"
        SINGLE = "single", "Single"
        COMPILATION = "compilation", "Compilation"

    spotify_id = models.CharField(max_length=255, unique=True)
    name = models.CharField(max_length=255)

    album_type = models.CharField(
        max_length=20,
        choices=AlbumTypes.choices,
        default=AlbumTypes.ALBUM
    )

    release_date = release_date = models.DateField(null=True, blank=True)

    artists = models.ManyToManyField(
        Artist,
        related_name="albums"
    )

    image_url = models.URLField(null=True, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return self.name

class Track(models.Model):
    spotify_id = models.CharField(max_length=255, unique=True)
    name=models.CharField(max_length=255)
    artists=models.ManyToManyField(Artist, related_name='tracks')
    album=models.ForeignKey(Album, on_delete=models.CASCADE, related_name='tracks')
    duration_ms=models.IntegerField()
    popularity=models.IntegerField(null=True)
    preview_url=models.URLField(null=True,blank=True)
    image_url=models.URLField(null=True,blank=True)

    def __str__(self):
        return self.name

class UserTopItem(models.Model):
    TIME_RANGE_CHOICES = [
        ("short_term", "Last 4 weeks"),
        ("medium_term", "Last 6 months"),
        ("long_term", "All time"),
    ]

    ITEM_TYPE_CHOICES = [
        ("artist", "Artist"),
        ("track", "Track"),
    ]

    user = models.ForeignKey(
        User,
        on_delete=models.CASCADE,
        related_name="top_items"
    )

    item_type = models.CharField(
        max_length=20,
        choices=ITEM_TYPE_CHOICES
    )

    time_range = models.CharField(
        max_length=20,
        choices=TIME_RANGE_CHOICES
    )

    artist = models.ForeignKey(
        Artist,
        on_delete=models.CASCADE,
        null=True,
        blank=True
    )

    track = models.ForeignKey(
        Track,
        on_delete=models.CASCADE,
        null=True,
        blank=True
    )

    rank = models.PositiveIntegerField()
    fetched_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["rank"]

        unique_together = [
            ("user", "item_type", "time_range", "rank"),
        ]

        constraints = [
            # exactly one of artist / track must be set
            models.CheckConstraint(
                check=(
                    Q(artist__isnull=False, track__isnull=True) |
                    Q(artist__isnull=True, track__isnull=False)
                ),
                name="either_artist_or_track"
            ),
            # item_type must match the populated field
            models.CheckConstraint(
                check=(
                    Q(item_type="artist", artist__isnull=False, track__isnull=True) |
                    Q(item_type="track", track__isnull=False, artist__isnull=True)
                ),
                name="item_type_matches_object"
            ),
        ]

    def __str__(self):
        if self.item_type == "artist":
            return f"{self.user.email} · #{self.rank} artist · {self.artist.name}"
        return f"{self.user.email} · #{self.rank} track · {self.track.name}"

class ListeningHistory(models.Model):
    class EventType(models.TextChoices):
        LISTEN = "LISTEN", "Listen"
        LIKE = "LIKE", "Like"
        PLAYLIST = "PLAYLIST", "Playlist add"

    user=models.ForeignKey(User, on_delete=models.CASCADE, related_name='listening_history')
    track=models.ForeignKey(Track, on_delete=models.CASCADE)
    played_at=models.DateTimeField()
    event_type=models.CharField(max_length=20, choices=EventType.choices,default=EventType.LISTEN)

    class Meta:
        ordering = ['-played_at']

    def __str__(self):
        return f'{self.user.email} {self.event_type} {self.played_at} {self.track.name}'


class AudioFeatures(models.Model):
    track = models.OneToOneField(
        Track,
        on_delete=models.CASCADE,
        related_name='audio_features'
    )

    # Wartości 0-1
    danceability = models.FloatField(
        validators=[MinValueValidator(0.0), MaxValueValidator(1.0)]
    )
    energy = models.FloatField(
        validators=[MinValueValidator(0.0), MaxValueValidator(1.0)]
    )
    valence = models.FloatField(
        validators=[MinValueValidator(0.0), MaxValueValidator(1.0)]
    )
    acousticness = models.FloatField(
        validators=[MinValueValidator(0.0), MaxValueValidator(1.0)]
    )
    instrumentalness = models.FloatField(
        validators=[MinValueValidator(0.0), MaxValueValidator(1.0)]
    )
    speechiness = models.FloatField(
        validators=[MinValueValidator(0.0), MaxValueValidator(1.0)]
    )
    liveness = models.FloatField(
        validators=[MinValueValidator(0.0), MaxValueValidator(1.0)]
    )

    # Loudness w dB (-60 do 0)
    loudness = models.FloatField(
        validators=[MinValueValidator(-60.0), MaxValueValidator(0.0)]
    )

    # Key (0-11, lub -1 = no key detected)
    key = models.IntegerField(
        validators=[MinValueValidator(-1), MaxValueValidator(11)]
    )

    # Mode (0=minor, 1=major)
    mode = models.IntegerField(
        validators=[MinValueValidator(0), MaxValueValidator(1)]
    )

    # Tempo (BPM)
    tempo = models.FloatField(
        validators=[MinValueValidator(0.0)]  # Tempo nie może być ujemne
    )

    # Time signature (3-7)
    time_signature = models.IntegerField(
        validators=[MinValueValidator(3), MaxValueValidator(7)]
    )

    # Duration (opcjonalne - masz już w Track, ale Spotify zwraca też tutaj)
    duration_ms = models.PositiveIntegerField(null=True, blank=True)

    # Metadata
    fetched_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"AudioFeatures for {self.track.name}"

    @property
    def mood_score(self):
        return (self.valence+self.energy)/2

    @property
    def danceability_category(self):
        if self.danceability >=0.8:
            return "Very Danceable"
        elif self.danceability >=0.6:
            return "Danceable"
        elif self.danceability >=0.4:
            return "Moderately Danceable"
        else:
            return "Not Danceable"

    @property
    def key_name(self):
        """Convert key number to note name"""
        keys = ['C', 'C♯/D♭', 'D', 'D♯/E♭', 'E', 'F',
                'F♯/G♭', 'G', 'G♯/A♭', 'A', 'A♯/B♭', 'B']
        return keys[self.key] if 0 <= self.key <= 11 else "Unknown"

    @property
    def mode_name(self):
        return "Major" if self.mode == 1 else "Minor"

    class Meta:
        verbose_name_plural = "Audio Features"

class SpotifyPlaylist(models.Model):
    # The basic info
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='spotify_playlists')
    spotify_id = models.CharField(max_length=100, unique=True, db_index=True)
    name = models.CharField(max_length=255)

    # Additional usefull things
    description = models.TextField(blank=True, null=True)
    image_url = models.URLField(max_length=500, blank=True, null=True)
    tracks_etag = models.CharField(max_length=128, null=True, blank=True)

    # Info about it
    is_public = models.BooleanField(default=True)
    is_collaborative = models.BooleanField(default=False)
    tracks_count = models.IntegerField(default=0)

    # Owner can differ
    owner_spotify_id = models.CharField(max_length=100)
    owner_display_name = models.CharField(max_length=255, blank=True)

    # playlist URL
    external_url = models.URLField(max_length=500, blank=True)

    # Timestamps
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    last_synced_at = models.DateTimeField(null=True, blank=True)

    # Snapshot ID
    snapshot_id = models.CharField(max_length=100, blank=True)
    tracks_snapshot_id = models.CharField(
        max_length=100,
        null=True,
        blank=True
    )
    class Meta:
        ordering = ['-updated_at']
        verbose_name = 'Spotify Playlist'
        verbose_name_plural = 'Spotify Playlists'

    def __str__(self):
        return f"{self.name} ({self.user.username})"

class SpotifyPlaylistTrack(models.Model):
    playlist = models.ForeignKey(
        SpotifyPlaylist,
        on_delete=models.CASCADE,
        related_name="playlist_tracks"
    )
    track = models.ForeignKey(
        Track,
        on_delete=models.CASCADE,
        related_name="playlist_entries"
    )

    position = models.PositiveIntegerField()  # The rank counts
    added_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        unique_together = ("playlist", "track")
        ordering = ["position"]
        indexes = [
            models.Index(fields=["playlist"]),
            models.Index(fields=["track"]),
        ]

class Tag(models.Model):
    """Normalized, canonical tags"""
    name = models.CharField(max_length=100)
    normalized_name = models.CharField(max_length=100, unique=True, db_index=True)

    category = models.CharField(
        max_length=50,
        choices=[
            ("genre", "Genre"),
            ("mood", "Mood"),
            ("instrument", "Instrument"),
            ("era", "Era"),
            ("style", "Style"),
            ("theme", "Theme"),
            ("other", "Other"),
        ],
        default="other"
    )

    total_usage_count = models.BigIntegerField(default=0)

    is_active=models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-total_usage_count', 'name']
        indexes = [
            models.Index(fields=['normalized_name']),
            models.Index(fields=['category', 'normalized_name']),
            models.Index(fields=['category', '-total_usage_count']),
        ]

    def save(self, *args, **kwargs):
        self.normalized_name = self.normalize(self.name)
        super().save(*args, **kwargs)

    def __str__(self):
        return self.name

    @staticmethod
    def normalize(tag_name):
        return tag_name.lower().strip().replace('-', ' ')

class ArtistTagManager(models.Manager):
    def top_tags(self, artist, limit=10, source="computed"):
        return (
            self.filter(
                artist=artist,
                source=source,
                tag__is_active=True
            )
            .select_related("tag")
            .order_by("-weight")[:limit]
        )

    def by_category(self, artist, category, source="computed"):
        return (
            self.filter(
                artist=artist,
                tag__category=category,
                source=source,
                tag__is_active=True
            )
            .select_related("tag")
            .order_by("-weight")
        )

class ArtistTag(models.Model):
    """
    Normalized tag assignments for artists.
    Each artist has MANY tags with weights → vector representation.
    """
    artist = models.ForeignKey(
        Artist,
        on_delete=models.CASCADE,
        related_name="artist_tags"
    )

    tag = models.ForeignKey(
        Tag,
        on_delete=models.CASCADE,
        related_name="tagged_artists"
    )

    # Final normalized weight used in similarity (0–1)
    weight = models.FloatField(
        validators=[MinValueValidator(0.0), MaxValueValidator(1.0)]
    )

    source = models.CharField(
        max_length=20,
        choices=[
            ("lastfm", "Last.fm raw"),
            ("spotify", "Spotify genres"),
            ("computed", "Computed canonical"),
            ("manual", "Manual"),
        ],
        default="lastfm"
    )

    # Raw value from source (e.g. Last.fm tag count)
    raw_count = models.IntegerField(null=True, blank=True)

    is_active = models.BooleanField(default=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    objects = ArtistTagManager()

    class Meta:
        unique_together = ("artist", "tag", "source")
        ordering = ['-weight']
        indexes = [
            models.Index(fields=['artist', '-weight']),
            models.Index(fields=['tag', '-weight']),
            models.Index(fields=['source']),
        ]

    def __str__(self):
        return f"{self.artist.name} · {self.tag.name} ({self.weight:.2f})"

class TrackTagManager(models.Manager):
    def top_tags(self, track, limit=10, source="computed"):
        return (
            self.filter(
                track=track,
                source=source,
                tag__is_active=True
            )
            .select_related("tag")
            .order_by("-weight")[:limit]
        )

    def by_category(self, track, category, source="computed"):
        return (
            self.filter(
                track=track,
                tag__category=category,
                source=source,
                tag__is_active=True
            )
            .select_related("tag")
            .order_by("-weight")
        )


class TrackTag(models.Model):
    track = models.ForeignKey(
        Track,
        on_delete=models.CASCADE,
        related_name="track_tags"
    )

    tag = models.ForeignKey(
        Tag,
        on_delete=models.CASCADE,
        related_name="tagged_tracks"
    )

    weight = models.FloatField(
        validators=[MinValueValidator(0.0), MaxValueValidator(1.0)]
    )

    source = models.CharField(
        max_length=20,
        choices=[
            ("lastfm", "Last.fm raw"),
            ("audio", "Audio features"),
            ("artist", "Inherited from artist"),
            ("computed", "Computed canonical"),
            ("manual", "Manual"),
        ],
        default="lastfm"
    )

    raw_count = models.IntegerField(null=True, blank=True)
    is_active = models.BooleanField(default=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    objects = TrackTagManager()

    class Meta:
        unique_together = ("track", "tag", "source")
        ordering = ["-weight"]
        indexes = [
            models.Index(fields=["track", "-weight"]),
            models.Index(fields=["tag", "-weight"]),
            models.Index(fields=["source"]),
        ]

    def __str__(self):
        return f"{self.track.name} · {self.tag.name} ({self.weight:.2f})"


class ArtistLastFMData(models.Model):
    """Raw cache of Last.fm API responses"""
    artist = models.OneToOneField(
        Artist,
        on_delete=models.CASCADE,
        related_name="lastfm_cache"
    )
    lastfm_name = models.CharField(max_length=255)
    lastfm_url = models.URLField(null=True, blank=True)
    mbid = models.CharField(max_length=64, null=True, blank=True, db_index=True)

    listeners = models.BigIntegerField(null=True, blank=True)
    playcount = models.BigIntegerField(null=True, blank=True)

    # Raw tags from Last.fm (not normalized yet)
    raw_tags = models.JSONField(default=list, blank=True)
    # [{"name": "indie rock", "count": 100}, ...]

    bio_summary = models.TextField(null=True, blank=True)
    image_url = models.URLField(null=True, blank=True)

    match_confidence = models.FloatField(
        default=1.0,
        validators=[MinValueValidator(0.0), MaxValueValidator(1.0)]
    )

    fetched_at = models.DateTimeField(auto_now=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'lastfm_artist_cache'
        indexes = [
            models.Index(fields=['fetched_at']),
            models.Index(fields=['playcount']),
        ]

    def needs_refresh(self, days=30):
        return self.fetched_at < timezone.now() - timedelta(days=days)


class TrackLastFMData(models.Model):
    """Raw cache of Last.fm API responses"""
    track = models.OneToOneField(
        Track,
        on_delete=models.CASCADE,
        related_name="lastfm_cache"
    )
    lastfm_name = models.CharField(max_length=255)
    lastfm_artist_name = models.CharField(max_length=255)
    lastfm_url = models.URLField(null=True, blank=True)
    mbid = models.CharField(max_length=64, null=True, blank=True, db_index=True)

    listeners = models.BigIntegerField(null=True, blank=True)
    playcount = models.BigIntegerField(null=True, blank=True)

    raw_tags = models.JSONField(default=list, blank=True)

    match_confidence = models.FloatField(
        default=1.0,
        validators=[MinValueValidator(0.0), MaxValueValidator(1.0)]
    )

    fetched_at = models.DateTimeField(auto_now=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'lastfm_track_cache'
        indexes = [
            models.Index(fields=['fetched_at']),
            models.Index(fields=['playcount']),
        ]

    def needs_refresh(self, days=30):
        return self.fetched_at < timezone.now() - timedelta(days=days)


class BaseSimiliarityManager(models.Manager):
    """
        Base manager for similarity models.
        Subclasses MUST define:
        - from_field
        - to_field
        """
    from_field:str|None = None
    to_field:str|None = None

    def _validate_fields(self):
        if not self.from_field or not self.to_field:
            raise NotImplementedError(
                "from_field and to_field must be defined in subclass"
            )

    def top_similiar(self, obj, source=None,limit=20):
        """
                Get top N similar items for a single object.
                """
        self._validate_fields()
        qs = self.filter(**{self.from_field: obj})
        if source:
            qs=qs.filter(source=source)

        return (qs.select_related(self.to_field).order_by("-score")[:limit]
                )

    def batch_similiar(self,objects,source=None):
        """
        Get similarities for MANY objects.

        IMPORTANT:
        - This does NOT apply per-object limits.
        - Limit per object MUST be applied in Python or SQL window functions.
        """
        self._validate_fields()

        filter_key=f'{self.from_field}__in'
        qs=self.filter(**{filter_key: objects})
        if source:
            qs.filter(source=source)

        return (
            qs.select_related(self.to_field,self.to_field).order_by(self.from_field,"-score")
        )

    def for_object(self, obj):
        """
        Get all similarity records where obj appears
        either as 'from' or 'to'.
        Intended for debugging / introspection.
        """
        self._validate_fields()
        from_q=Q(**{self.from_field:obj})
        to_q=Q(**{self.to_field:obj})
        return self.filter(from_q|to_q)

class ArtistSimilarityManager(BaseSimiliarityManager):
    from_field = "from_artist"
    to_field = "to_artist"


class TrackSimilarityManager(BaseSimiliarityManager):
    from_field = "from_track"
    to_field = "to_track"

class BaseSimilarity(models.Model):
    """
    Abstract base model for similarity records.
    """
    score=models.FloatField(
        validators=[MinValueValidator(0.0), MaxValueValidator(1.0)],
        db_index=True
    )

    source = models.CharField(
        max_length=20,
        choices=[
            ("lastfm", "Last.fm"),
            ("tags", "Tag-based"),
            ("audio", "Audio-based"),
            ("hybrid", "Hybrid"),
        ],
        default="tags"
    )

    score_breakdown=models.JSONField(
        null=True,
        blank=True,
        help_text='Explainability data, e.g. {"tag_sim": 0.7, "audio_sim": 0.5}'
    )

    computed_at=models.DateTimeField(auto_now=True)
    created_at=models.DateTimeField(auto_now_add=True)

    class Meta:
        abstract = True
        ordering=['-score']

    def __str__(self):
        return f"{self.get_from()} → {self.get_to()} ({self.score:.2f})"

    def get_from(self):
        raise NotImplementedError

    def get_to(self):
        raise NotImplementedError

class ArtistSimilarity(BaseSimilarity):
    from_artist = models.ForeignKey(
        "Artist",
        on_delete=models.CASCADE,
        related_name="similar_to"
    )
    to_artist = models.ForeignKey(
        "Artist",
        on_delete=models.CASCADE,
        related_name="similar_from"
    )

    objects = ArtistSimilarityManager()

    class Meta:
        unique_together = ("from_artist", "to_artist", "source")
        ordering=['-score']
        indexes = [
            models.Index(fields=["from_artist", "source", "-score"]),
            models.Index(fields=["to_artist", "-score"]),
        ]
        verbose_name = "Artist Similarity"
        verbose_name_plural = "Artist Similarities"

    def get_from(self):
        return self.from_artist.name

    def get_to(self):
        return self.to_artist.name

class TrackSimilarity(BaseSimilarity):
    from_track = models.ForeignKey(
        "Track",
        on_delete=models.CASCADE,
        related_name="similar_to"
    )
    to_track = models.ForeignKey(
        "Track",
        on_delete=models.CASCADE,
        related_name="similar_from"
    )

    objects = TrackSimilarityManager()

    class Meta:
        unique_together = ("from_track", "to_track", "source")
        ordering = ["-score"]
        indexes = [
            models.Index(fields=["from_track", "source", "-score"]),
            models.Index(fields=["to_track", "-score"]),
        ]
        verbose_name = "Track Similarity"
        verbose_name_plural = "Track Similarities"

    def get_from(self):
        return self.from_track.name

    def get_to(self):
        return self.to_track.name


class YoutubeAccount(models.Model):
    user = models.OneToOneField(User, on_delete=models.CASCADE)

    access_token = models.CharField(max_length=512)
    refresh_token = models.CharField(max_length=512)
    expires_at = models.DateTimeField()

    # 🔹 sync helpers
    last_synced_at = models.DateTimeField(null=True, blank=True)
    subscriptions_etag = models.CharField(max_length=255, null=True, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    objects = YoutubeAccountManager()

    def is_token_expired(self):
        """Sprawdza czy token wygasł"""
        return timezone.now() > self.expires_at

    def needs_token_refresh(self):
        """Sprawdza czy token wymaga odświeżenia (wygasa w ciągu 5 min)"""
        threshold = timezone.now() + timedelta(minutes=5)
        return self.expires_at < threshold

    def __str__(self):
        return f"{self.user.email} - {self.youtube_id}"

    def update_tokens(self, access_token, refresh_token=None, expires_in=3600):
        self.access_token = access_token
        if refresh_token:
            self.refresh_token = refresh_token
        self.expires_at = timezone.now() + timedelta(seconds=expires_in)
        self.save()

    class Meta:
        verbose_name = "YouTube Account"
        verbose_name_plural = "YouTube Accounts"
        indexes = [
            models.Index(fields=['expires_at']),
            models.Index(fields=['last_synced_at']),
        ]


class YoutubeChannel(models.Model):
    channel_id = models.CharField(max_length=255, unique=True)
    title = models.CharField(max_length=255)

    is_music = models.BooleanField(default=False)
    confidence_score = models.FloatField(null=True, blank=True)

    # meta
    last_classified_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "YouTube Channel"
        verbose_name_plural = "YouTube Channels"

    def __str__(self):
        return self.title

class UserYoutubeChannelManager(models.Manager):
    """Manager do wygodnych zapytań o subskrypcje"""

    def for_user(self, user):
        """Zwraca subskrypcje użytkownika"""
        return self.filter(user=user).select_related('channel')

    def music_subscriptions(self, user):
        """Zwraca subskrypcje kanałów muzycznych"""
        return self.filter(
            user=user,
            channel__is_music=True,
        ).select_related('channel')


class UserYoutubeChannel(models.Model):
    user = models.ForeignKey(
        User,
        on_delete=models.CASCADE,
        related_name='youtube_subscriptions'
    )
    channel = models.ForeignKey(
        YoutubeChannel,
        on_delete=models.CASCADE,
        related_name='subscribers'
    )

    subscribed_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    #  Dodatkowe opcje
    notifications_enabled = models.BooleanField(default=True)

    objects = UserYoutubeChannelManager()

    class Meta:
        unique_together = ("user", "channel")
        verbose_name = "User YouTube Channel"
        verbose_name_plural = "User YouTube Channels"
        indexes = [
            models.Index(fields=['user', 'created_at']),
            models.Index(fields=['channel', 'notifications_enabled']),
        ]

    def __str__(self):
        return f"{self.user.email} -> {self.channel.title}"