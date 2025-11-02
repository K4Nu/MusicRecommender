from djoser.serializers import UserCreateSerializer
from rest_framework import serializers
from django.contrib.auth import get_user_model

User = get_user_model()

class CustomRegisterSerializer(UserCreateSerializer):
    password1 = serializers.CharField(write_only=True)
    password2 = serializers.CharField(write_only=True)

    class Meta(UserCreateSerializer.Meta):
        model = User
        fields = ("id", "email", "password1", "password2")

    def validate(self, attrs):
        if attrs["password1"] != attrs["password2"]:
            raise serializers.ValidationError({"password2": "Passwords do not match."})
        return attrs

    def create(self, validated_data):
        password = validated_data.pop("password1")
        validated_data.pop("password2", None)
        user = User.objects.create(**validated_data)
        user.set_password(password)
        user.save()
        return user

class UserTopTrackSerializer(serializers.Serializer):
    rank=serializers.IntegerField()
    name=serializers.CharField(source="track.name")
    artists=serializers.SerializerMethodField()
    image_url=serializers.URLField("track.image_url", allow_null=True)
    spotify_id=serializers.CharField(source="track.spotify_id")

    def get_artists(self, obj):
        return [a.name for a in obj.track.artists.all()]