from django.shortcuts import render, get_object_or_404
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework import permissions, status
from recomendations.models import ColdStartTrack,UserTag,Recommendation, RecommendationItem, RecommendationFeedback
from music.models import TrackTag, ArtistTag
from .tasks.cold_start_tasks import create_cold_start_lastfm_tracks
from .services.cold_start import cold_start_refresh_all
from .services.recomendation import get_or_build_recommendation, detect_strategy
from django.db import IntegrityError, transaction
from django.views.decorators.cache import cache_page
from django.utils.decorators import method_decorator
from django.db.models import Count, Q
from django.utils import timezone
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import permissions
import logging
from users.models import SpotifyAccount, YoutubeAccount
from recomendations.models import ColdStartTrack, OnboardingEvent
from recomendations.serializers import ColdStartTrackSerializer,OnboardingEventSerializer, RecommendationSerializer, HomeSerializer, RecommendationFeedbackSerializer
from recomendations.services.tag_filter import filter_track_tags, filter_artist_tags
from recomendations.tasks.recommendation_tasks import build_recommendation_task
from django.shortcuts import get_object_or_404
from django.db.models import Prefetch
from .services.feedback_service import apply_feedback_to_tags

class ColdTest(APIView):
    permission_classes = [permissions.AllowAny]

    def get(self, request):
        cold_start_refresh_all.delay()

        return Response(
            {"message": "Cold start"},
            status=status.HTTP_200_OK
        )


"""
By now without postgres
"""

logger = logging.getLogger(__name__)


class InitialSetupView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    REQUIRED_LIKES = 3
    TRACKS_PER_BATCH = 20

    def get(self, request):
        user = request.user
        profile = user.profile

        has_spotify = SpotifyAccount.objects.filter(user=user).exists()
        has_youtube = YoutubeAccount.objects.filter(user=user).exists()
        has_any_integration = has_spotify or has_youtube

        needs_onboarding = not profile.onboarding_completed
        needs_integration = not has_any_integration

        stats = OnboardingEvent.objects.filter(user=user).aggregate(
            likes_count=Count(
                "id",
                filter=Q(action=OnboardingEvent.Action.LIKE),
            )
        )

        likes = stats["likes_count"] or 0

        response = {
            "needs_onboarding": needs_onboarding,
            "needs_integration": needs_integration,
            "tracks": [],
            "stats": {
                "likes_count": likes,
                "required_likes": self.REQUIRED_LIKES,
            },
        }

        if not needs_onboarding:
            logger.info(f"InitialSetupView response: {response}")
            return Response(response)

        # 🎧 REAL ONBOARDING (tylko jeśli NIE ma integracji)
        if needs_integration:
            if not profile.onboarding_started_at:
                profile.onboarding_started_at = timezone.now()
                profile.save(update_fields=["onboarding_started_at"])

            tracks = self._get_coldstart_tracks(limit=self.TRACKS_PER_BATCH)
            response["tracks"] = ColdStartTrackSerializer(tracks, many=True).data

        logger.info(f"InitialSetupView response: {response}")
        return Response(response)

    def _get_coldstart_tracks(self, limit: int):
        """
        Returns a batch of cold start tracks with artist diversity.
        """
        qs = (
            ColdStartTrack.objects
            .filter(track__spotify_id__isnull=False)
            .select_related("track")
            .prefetch_related("track__artists")
            .order_by("?")[:50]
        )

        selected = []
        seen_artists = set()

        for cst in qs:
            artists = list(cst.track.artists.all())
            if not artists:
                continue

            main_artist_id = artists[0].id
            if main_artist_id in seen_artists:
                continue

            seen_artists.add(main_artist_id)
            selected.append(cst)

            if len(selected) >= limit:
                break

        # fallback
        if len(selected) < limit:
            selected = list(qs[:limit])

        return selected


class GetFeature(APIView):
    permission_classes = [permissions.AllowAny]

    def get(self, request):
        features = create_cold_start_lastfm_tracks()

        return Response(
            {"message": "Cold start"},
            status=status.HTTP_200_OK
        )


class OnboardingInteractView(APIView):
    """
    Handle batch onboarding event submissions.
    Endless until user reaches MIN_LIKES_TO_COMPLETE.
    """
    permission_classes = [permissions.IsAuthenticated]

    MAX_EVENTS_PER_BATCH = 20
    MIN_LIKES_TO_COMPLETE = 3

    def _apply_onboarding_like(self, user, track):
        """
        Build user taste profile from liked onboarding track.

        Priority:
        1. TrackTag (strongest signal)
        2. ArtistTag (fallback)
        """

        logger.info(
            f"DEBUG: _apply_onboarding_like called for user={user.id}, track={track.id}"
        )

        # ==========================================
        # PRIMARY: TrackTag
        # ==========================================
        track_tags = filter_track_tags(
            TrackTag.objects.filter(track=track)
        )

        if track_tags.exists():
            logger.info(f"DEBUG: Using {track_tags.count()} TrackTags")

            for tt in track_tags:
                UserTag.objects.update_or_create(
                    user=user,
                    tag=tt.tag,
                    source="onboarding_track",
                    defaults={
                        "weight": tt.weight,
                        "confidence": 0.7,
                        "is_active": True,
                    },
                )

            logger.info("DEBUG: UserTag created from TrackTag")
            return

        # ==========================================
        # FALLBACK: ArtistTag
        # ==========================================
        artist_tags = filter_artist_tags(
            ArtistTag.objects.filter(
                artist__in=track.artists.all(),
            )
        )

        if artist_tags.exists():
            logger.info(f"DEBUG: Track has no tags. Using {artist_tags.count()} ArtistTags")

            for at in artist_tags:
                UserTag.objects.update_or_create(
                    user=user,
                    tag=at.tag,
                    source="onboarding_artist_fallback",
                    defaults={
                        "weight": at.weight * 0.8,  # slightly weaker signal
                        "confidence": 0.5,
                        "is_active": True,
                    },
                )

            logger.info("DEBUG: UserTag created from ArtistTag fallback")
            return

        # ==========================================
        #NO SIGNAL
        # ==========================================
        logger.warning(
            f"WARNING: No TrackTag or ArtistTag found for track={track.id}"
        )

    def post(self, request):
        user = request.user
        profile = user.profile

        if profile.onboarding_completed:
            return Response(
                {
                    "status": "already_completed",
                    "quality": profile.onboarding_quality,
                },
                status=status.HTTP_200_OK,
            )

        events_data = request.data.get("events", [])

        if not events_data:
            return Response(
                {"error": "No events provided"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        if len(events_data) > self.MAX_EVENTS_PER_BATCH:
            return Response(
                {"error": "Too many events in one request"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        serializer = OnboardingEventSerializer(data=events_data, many=True)
        serializer.is_valid(raise_exception=True)

        if not profile.onboarding_started_at:
            profile.onboarding_started_at = timezone.now()
            profile.save(update_fields=["onboarding_started_at"])

        validated_events = serializer.validated_data

        track_ids = {e["cold_start_track_id"] for e in validated_events}
        cold_start_tracks = {
            t.id: t
            for t in ColdStartTrack.objects.filter(id__in=track_ids)
        }

        events_written = 0
        events_ignored = 0

        with transaction.atomic():
            for event_data in validated_events:
                cold_start_track = cold_start_tracks.get(
                    event_data["cold_start_track_id"]
                )

                if not cold_start_track:
                    logger.warning(
                        "ColdStartTrack %s not found",
                        event_data["cold_start_track_id"],
                    )
                    continue

                event, created = OnboardingEvent.objects.get_or_create(
                    user=user,
                    cold_start_track=cold_start_track,
                    defaults={
                        "action": event_data["action"],
                        "position": event_data.get("position"),
                    },
                )

                if created:
                    events_written += 1
                    # Apply taste profile for new LIKE events
                    if event_data["action"] == OnboardingEvent.Action.LIKE:
                        self._apply_onboarding_like(
                            user=user,
                            track=cold_start_track.track
                        )
                    continue

                updated_fields = []
                action_changed_to_like = False

                if event.action != event_data["action"]:
                    event.action = event_data["action"]
                    updated_fields.append("action")

                    # Check if action changed TO like
                    if event_data["action"] == OnboardingEvent.Action.LIKE:
                        action_changed_to_like = True

                if (
                        event_data.get("position") is not None
                        and event.position != event_data["position"]
                ):
                    event.position = event_data["position"]
                    updated_fields.append("position")

                if updated_fields:
                    event.save(update_fields=updated_fields)
                    events_written += 1

                    # Apply taste profile if action changed to LIKE
                    if action_changed_to_like:
                        self._apply_onboarding_like(
                            user=user,
                            track=cold_start_track.track
                        )
                else:
                    events_ignored += 1

        stats = OnboardingEvent.objects.filter(user=user).aggregate(
            total_count=Count("id"),
            likes_count=Count(
                "id",
                filter=Q(action=OnboardingEvent.Action.LIKE),
            ),
        )

        likes = stats["likes_count"] or 0

        if likes >= self.MIN_LIKES_TO_COMPLETE:
            profile.onboarding_completed = True
            profile.onboarding_completed_at = timezone.now()
            profile.onboarding_quality = "GOOD"

            profile.save(update_fields=[
                "onboarding_completed",
                "onboarding_completed_at",
                "onboarding_quality",
            ])

            UserTag.objects.recompute_computed(user)

            build_recommendation_task.delay(user.id)

            return Response(
                {
                    "status": "onboarding_completed",
                    "quality": profile.onboarding_quality,
                    "events_written": events_written,
                    "events_ignored": events_ignored,
                    "stats": stats,
                },
                status=status.HTTP_200_OK,
            )

        return Response(
            {
                "status": "needs_more_likes",
                "events_written": events_written,
                "events_ignored": events_ignored,
                "stats": stats,
                "likes_missing": self.MIN_LIKES_TO_COMPLETE - likes,
                "onboarding_completed": False,
            },
            status=status.HTTP_200_OK,
        )

@method_decorator(cache_page(10), name="get")
class UserStatus(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request):
        user=request.user

        return Response({
            "onboarding_completed": user.profile.onboarding_completed,
            "has_spotify": SpotifyAccount.objects.filter(user=user).exists(),
            "has_youtube": YoutubeAccount.objects.filter(user=user).exists(),
        })

class RecommendationView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request):
        user = request.user

        if not user.profile.onboarding_completed:
            return Response(
                {
                    "error": "onboarding_not_completed",
                    "message": "Complete onboarding before getting recommendations",
                },
                status=status.HTTP_403_FORBIDDEN,
            )

        force_rebuild = request.query_params.get("rebuild", "").lower() == "true"

        recommendation = get_or_build_recommendation(
            user=user,
            limit=20,
            force_rebuild=force_rebuild,
        )

        #Prefetch all relations to avoid N+1
        recommendation = (
            Recommendation.objects
            .prefetch_related(
                "items__track__artists",
                "items__track__track_tags__tag",
                "items__track__album",
            )
            .get(id=recommendation.id)
        )

        serializer = RecommendationSerializer(recommendation)
        return Response(serializer.data)

class HomeApiView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request):
        user = request.user

        if not user.profile.onboarding_completed:
            return Response(
                {"error": "onboarding_not_completed"},
                status=status.HTTP_403_FORBIDDEN,
            )
        spotify = SpotifyAccount.objects.filter(user=user).first()

        is_spotify_connected = (
                spotify is not None and
                spotify.refresh_token is not None
        )

        rec = get_or_build_recommendation(user)

        # Prefetch feedback for current user - single query
        all_items = list(
            RecommendationItem.objects
            .filter(recommendation=rec)
            .select_related("track__album", "artist")
            .prefetch_related(
                "track__artists",
                "track__track_tags__tag",
                # Only prefetch feedback for current user
                Prefetch(
                    "feedback",
                    queryset=RecommendationFeedback.objects.filter(user=user),
                ),
            )
            .order_by("rank")
        )

        top_items = all_items[:5]
        lighter_items = all_items[5:10]
        profile_tags = UserTag.objects.top_tags(user=user, limit=5)

        return Response(
            HomeSerializer({
                "strategy": rec.strategy,
                "profile_tags": profile_tags,
                "top_items": top_items,
                "lighter_items": lighter_items,
                "is_spotify_connected":is_spotify_connected,
            }).data
        )

class RecommendationFeedbackView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    TOP_ITEMS_COUNT = 5  # rebuild when all top items are rated

    def post(self, request):
        serializer = RecommendationFeedbackSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        user = request.user
        item_id = serializer.validated_data["recommendation_item_id"]
        action = serializer.validated_data["action"]

        # Get item with relations
        item = get_object_or_404(
            RecommendationItem.objects.select_related(
                "recommendation",
                "track",
            ),
            id=item_id,
        )

        #  Security: verify ownership
        if item.recommendation.user_id != user.id:
            return Response(
                {"error": "invalid_recommendation_item"},
                status=status.HTTP_403_FORBIDDEN,
            )

        # Save feedback (update if exists)
        feedback, created = RecommendationFeedback.objects.update_or_create(
            user=user,
            recommendation_item=item,
            defaults={
                "recommendation": item.recommendation,
                "action": action,
            },
        )

        # Update user taste profile
        apply_feedback_to_tags(user=user, item=item, action=action)

        #  Rebuild only if needed and snapshot still active
        should_rebuild = False

        if item.recommendation.is_active:
            should_rebuild = self._check_should_rebuild(
                user=user,
                recommendation=item.recommendation,
            )

            if should_rebuild:
                build_recommendation_task.delay(
                    user.id,
                    force_rebuild=True,
                )

        return Response(
            {
                "status": "created" if created else "updated",
                "action": feedback.action,
                "rebuild_triggered": should_rebuild,
            },
            status=status.HTTP_200_OK,
        )

    def _check_should_rebuild(self, user, recommendation) -> bool:
        """
        Returns True when all top items (rank < TOP_ITEMS_COUNT)
        in the current active snapshot have feedback.
        """

        top_qs = RecommendationItem.objects.filter(
            recommendation=recommendation,
            rank__lt=self.TOP_ITEMS_COUNT,
        )

        top_count = top_qs.count()

        if top_count == 0:
            return False

        rated_count = RecommendationFeedback.objects.filter(
            user=user,
            recommendation_item__in=top_qs,
        ).count()

        return rated_count >= top_count

