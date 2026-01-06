from django.contrib.auth.models import BaseUserManager
from django.contrib.auth.models import AbstractBaseUser
from django.contrib.auth.models import PermissionsMixin
from django.db import models
from django.db.models import Q
from django.core.validators import validate_email
from django.core.exceptions import ValidationError
from django.core.validators import MinValueValidator, MaxValueValidator
from django.utils import timezone
from datetime import timedelta


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
    album_name=models.ForeignKey(Album, on_delete=models.CASCADE, related_name='tracks')
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
    youtube_id = models.CharField(max_length=255, unique=True)
    access_token = models.CharField(max_length=512)
    refresh_token = models.CharField(max_length=512)
    expires_at = models.DateTimeField()
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def is_token_expired(self):
        return timezone.now() > self.expires_at

    def __str__(self):
        return f"{self.user.email} - {self.youtube_id}"

    class Meta:
        verbose_name = "YouTube Account"
        verbose_name_plural = "YouTube Accounts"