from rest_framework import serializers
from recomendations.models import ColdStartTrack, OnboardingEvent

class ColdStartTrackSerializer(serializers.ModelSerializer):
    track_name = serializers.CharField(source="track.name")
    spotify_id = serializers.CharField(source="track.spotify_id")
    artists = serializers.SerializerMethodField()
    embed_url = serializers.SerializerMethodField()

    class Meta:
        model = ColdStartTrack
        fields = [
            "id",
            "track_name",
            "spotify_id",
            "artists",
            "embed_url",
        ]

    def get_artists(self, obj):
        return [a.name for a in obj.track.artists.all()]

    def get_embed_url(self, obj):
        return f"https://open.spotify.com/embed/track/{obj.track.spotify_id}"


class OnboardingEventSerializer(serializers.Serializer):
    cold_start_track_id = serializers.IntegerField()
    action = serializers.ChoiceField(
        choices=OnboardingEvent.Action.choices
    )
    position = serializers.IntegerField(
        min_value=1,
        required=False
    )

    def validate_cold_start_track_id(self, value):
        if not ColdStartTrack.objects.filter(id=value).exists():
            raise serializers.ValidationError(
                "ColdStartTrack does not exist"
            )
        return value