from django.contrib.auth.models import BaseUserManager
from django.contrib.auth.models import AbstractBaseUser
from django.contrib.auth.models import PermissionsMixin
from django.db import models
from django.core.validators import validate_email
from django.core.exceptions import ValidationError


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


class Track(models.Model):
    spotify_id = models.CharField(max_length=255, unique=True)
    name=models.CharField(max_length=255)
    artists=models.ManyToManyField(Artist, related_name='tracks')
    album_name=models.CharField(max_length=255)
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

    artist = models.ForeignKey(Artist, on_delete=models.CASCADE)
    track = models.ForeignKey(Track, on_delete=models.CASCADE)

    rank = models.IntegerField()
    fetched_at=models.DateTimeField(auto_now_add=True)

    ordering =[ "rank"]
    unique_together = ["user","item_type","time_range","rank"]

class ListeningHistory(models.Model):
    user=models.ForeignKey(User, on_delete=models.CASCADE, related_name='listening_history')
    track=models.ForeignKey(Track, on_delete=models.CASCADE)
    played_at=models.DateTimeField()

    class Meta:
        ordering = ['-played_at']