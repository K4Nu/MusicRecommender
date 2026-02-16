from rest_framework import serializers
from recomendations.models import ColdStartTrack, OnboardingEvent,RecommendationItem,Recommendation, UserTag, RecommendationFeedback

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

class RecommendationItemSerializer(serializers.ModelSerializer):
    user_feedback = serializers.SerializerMethodField()
    track_name = serializers.CharField(source="track.name", default=None)
    spotify_id = serializers.CharField(source="track.spotify_id", default=None)
    preview_url = serializers.CharField(source="track.preview_url", default=None)
    image_url = serializers.CharField(source="track.image_url", default=None)
    duration_ms = serializers.IntegerField(source="track.duration_ms", default=None)
    album_name = serializers.CharField(source="track.album.name", default=None)  # ← add
    album_image = serializers.CharField(source="track.album.image_url", default=None)  # ← add
    embed_url = serializers.SerializerMethodField()
    artists = serializers.SerializerMethodField()
    tags = serializers.SerializerMethodField()

    class Meta:
        model = RecommendationItem
        fields = [
            "id",
            "rank",
            "score",
            "track_name",
            "spotify_id",
            "preview_url",
            "image_url",
            "album_name",   # ← add
            "album_image",  # ← add
            "duration_ms",
            "embed_url",
            "artists",
            "tags",
            "reason",
            "user_feedback",
        ]

    def get_embed_url(self, obj):
        if obj.track and obj.track.spotify_id:
            return f"https://open.spotify.com/embed/track/{obj.track.spotify_id}"
        return None

    def get_artists(self, obj):
        if not obj.track:
            return []
        return [
            {"name": a.name, "spotify_id": a.spotify_id}
            for a in obj.track.artists.all()
        ]

    def get_tags(self, obj):
        if not obj.track:
            return []
        return [
            {"name": tt.tag.name, "weight": tt.weight}
            for tt in obj.track.track_tags.all()[:5]
        ]

    def get_user_feedback(self, obj):
        request = self.context.get("request")
        if not request or not request.user.is_authenticated:
            return None

        feedback = obj.feedback.filter(user=request.user).first()
        return feedback.action if feedback else None

class RecommendationSerializer(serializers.ModelSerializer):
    items = RecommendationItemSerializer(many=True)

    class Meta:
        model = Recommendation
        fields = [
            "id",
            "strategy",
            "status",
            "context",
            "created_at",
            "finished_at",
            "items",
        ]

class UserTagSerializer(serializers.ModelSerializer):
    tag_name = serializers.CharField(source="tag.name")
    tag_id = serializers.IntegerField(source="tag.id")

    class Meta:
        model = UserTag
        fields = [
            "tag_id",
            "tag_name",
            "weight",
        ]


class HomeSerializer(serializers.Serializer):
    """
    Combines profile taste tags + top recommendation items + lighter items.
    Used by HomeApiView.
    """
    strategy = serializers.CharField()
    profile_tags = UserTagSerializer(many=True)
    top_items = RecommendationItemSerializer(many=True)
    lighter_items = RecommendationItemSerializer(many=True)

class RecommendationFeedbackSerializer(serializers.Serializer):
    recommendation_item_id = serializers.IntegerField()
    action = serializers.ChoiceField(
        choices=RecommendationFeedback.Action.choices
    )

