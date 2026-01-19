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
from datetime import timedelta
from cryptography.fernet import Fernet
import base64


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
        ('short_term', 'Last 4 weeks'),
        ('medium_term', 'Last 6 months'),
        ('long_term', 'All time'),
    ]

    ITEM_TYPE_CHOICES = [
        ('artist', 'Artist'),
        ('track', 'Track'),
    ]

    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='top_items')
    item_type = models.CharField(max_length=255, choices=ITEM_TYPE_CHOICES)
    time_range = models.CharField(max_length=20, choices=TIME_RANGE_CHOICES)

    artist = models.ForeignKey(Artist, on_delete=models.CASCADE,null=True,blank=True)
    track = models.ForeignKey(Track, on_delete=models.CASCADE,null=True,blank=True)

    rank = models.IntegerField()
    fetched_at=models.DateTimeField(auto_now_add=True)

    ordering =[ "rank"]
    unique_together = ["user","item_type","time_range","rank"]

    constraints = [
        models.CheckConstraint(
            check=(
                    models.Q(artist__isnull=False, track__isnull=True) |
                    models.Q(artist__isnull=True, track__isnull=False)
            ),
            name='either_artist_or_track_not_both'
        )
    ]

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

    # 🔔 Dodatkowe opcje
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