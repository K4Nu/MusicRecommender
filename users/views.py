import os
from datetime import timedelta
from http import HTTPStatus
from .services import ensure_valid_external_tokens
import requests
from django.utils import timezone
from djoser.serializers import UserSerializer
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework import permissions, status
from .tasks.spotify_tasks import fetch_spotify_initial_data,youtube_test_fetch,check_youtube_channel_category,fetch_recently_played
from users.models import SpotifyAccount,UserTopItem,YoutubeAccount
from rest_framework import generics
from .serializers import UserTopTrackSerializer
from drf_spectacular.utils import extend_schema

class SpotifyConnect(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request):
        code = request.data.get('code')
        redirect_uri = request.data.get('redirect_uri')

        if not code or not redirect_uri:
            return Response(
                {"detail": "Missing 'code' or 'redirect_uri'."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        token_url = "https://accounts.spotify.com/api/token"
        token_data = {
            'grant_type': 'authorization_code',
            'code': code,
            'redirect_uri': redirect_uri,
        }

        auth = (os.environ.get('SPOTIFY_CLIENT_ID'), os.environ.get('SPOTIFY_CLIENT_SECRET'))

        try:
            token_response = requests.post(token_url, data=token_data, auth=auth)
            token_response.raise_for_status()
            token_json = token_response.json()

        except requests.exceptions.RequestException as e:
            return Response(
                {"detail": f"Failed to exchange code for token: {str(e)}"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        access_token = token_json.get("access_token")
        refresh_token = token_json.get("refresh_token")
        expires_in = token_json.get("expires_in")

        if not access_token or not refresh_token:
            return Response(
                {"detail": "Invalid response from Spotify."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        expires_at = timezone.now() + timedelta(seconds=expires_in)
        profile_url = "https://api.spotify.com/v1/me"
        headers = {"Authorization": f"Bearer {access_token}"}

        try:
            profile_response = requests.get(profile_url, headers=headers)
            profile_response.raise_for_status()
            profile_json = profile_response.json()
        except requests.exceptions.RequestException as e:
            return Response(
                {"detail": f"Failed to fetch Spotify profile: {str(e)}"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        spotify_id = profile_json.get("id")

        if not spotify_id:
            return Response(
                {"detail": "Could not retrieve Spotify user ID."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        spotify_account, created = SpotifyAccount.objects.update_or_create(
            user=request.user,
            defaults={
                "spotify_id": spotify_id,
                "access_token": access_token,
                "refresh_token": refresh_token,
                "expires_at": expires_at,
            }
        )

        if created:
            fetch_spotify_initial_data.delay(request.user.id)

        return Response(
            {
                "detail": "Spotify account connected successfully.",
                "spotify_id": spotify_id,
                "display_name": profile_json.get('display_name'),
            },
            status=status.HTTP_200_OK,
        )
class UserTopTracks(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request):
        time_range=request.query_params.get('time_range', "medium_term")

        top_items = UserTopItem.objects.filter(
            user=request.user,
            item_type='track',
            time_range=time_range
        ).select_related('track')[:20]

        data=[{
            "rank":item.rank,
            "name":item.track.name,
            "artists":[a.name for a in item.track.artists.all()],
            "image_url":item.track.image_url,
            "spotify_id":item.track.spotify_id,
        } for item in top_items]

        return Response(data)

class TestView(generics.ListAPIView):
    permission_classes = [permissions.IsAuthenticated]
    serializer_class = UserTopTrackSerializer

    def get_queryset(self):
        time_range=self.request.query_params.get('time_range', "medium_term")
        return(UserTopItem.objects.filter(user=self.request.user, item_type='track', time_range=time_range).select_related('track')
               .prefetch_related("track__artists").order_by('rank'))


class SpotifyRefreshTokenView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request):
        user = self.request.user

        try:
            spotify_account = SpotifyAccount.objects.get(user=user)
        except SpotifyAccount.DoesNotExist:
            return Response(
                {"status": HTTPStatus.NOT_FOUND, "message": "Spotify account not found."},
                status=HTTPStatus.NOT_FOUND
            )

        URL = "https://accounts.spotify.com/api/token"
        token_data = {
            'grant_type': 'refresh_token',
            'refresh_token': spotify_account.refresh_token,
            'client_id': os.environ.get('SPOTIFY_CLIENT_ID'),
            'client_secret': os.environ.get('SPOTIFY_CLIENT_SECRET')
        }

        try:
            token_response = requests.post(URL, data=token_data)
            token_response.raise_for_status()
            token_json = token_response.json()

            access_token = token_json.get('access_token')
            refresh_token = token_json.get('refresh_token')
            expires_in = token_json.get('expires_in', 3600)

            spotify_account.update_tokens(access_token=access_token, refresh_token=refresh_token, expires_in=expires_in)

            return Response(
                {
                    "status": HTTPStatus.OK,
                    "message": "Successfully refreshed token.",
                },
                status=HTTPStatus.OK
            )

        except requests.exceptions.RequestException as e:
            return Response(
                {
                    "status": HTTPStatus.INTERNAL_SERVER_ERROR,
                    "message": f"Failed to refresh token: {str(e)}"
                },
                status=HTTPStatus.INTERNAL_SERVER_ERROR
            )


class YoutubeConnect(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request):
        code = request.data.get('code')
        redirect_uri = request.data.get('redirect_uri')
        code_verifier = request.data.get('codeVerifier') or request.data.get('code_verifier')

        # Debug
        print("=" * 60)
        print("📥 YouTube Connect Request")
        print(f"  User: {request.user.email} (ID: {request.user.id})")
        print(f"  Code: {code[:20]}... (length: {len(code) if code else 0})")
        print(f"  Redirect URI: {redirect_uri}")
        print(f"  Code Verifier: {code_verifier[:20] if code_verifier else 'MISSING'}...")
        print("=" * 60)

        if not code or not redirect_uri:
            return Response(
                {"detail": "Missing 'code' or 'redirect_uri'."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        # Wymiana code na token
        token_url = "https://oauth2.googleapis.com/token"
        token_data = {
            'grant_type': 'authorization_code',
            'code': code,
            'redirect_uri': redirect_uri,
            'client_id': os.environ.get('YOUTUBE_CLIENT_ID'),
            'client_secret': os.environ.get('YOUTUBE_CLIENT_SECRET'),
        }

        if code_verifier:
            token_data['code_verifier'] = code_verifier

        try:
            print("📤 Requesting token from Google...")
            token_response = requests.post(token_url, data=token_data, timeout=10)

            print(f"📥 Google response status: {token_response.status_code}")

            if not token_response.ok:
                error_text = token_response.text
                print(f"❌ Google error: {error_text}")

                return Response(
                    {
                        "detail": "Failed to exchange code for token",
                        "error": error_text,
                        "status": token_response.status_code,
                    },
                    status=status.HTTP_400_BAD_REQUEST,
                )

            token_json = token_response.json()
            print("✅ Token received from Google")

        except requests.exceptions.RequestException as e:
            print(f"❌ Request error: {str(e)}")
            return Response(
                {"detail": f"Request error: {str(e)}"},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

        access_token = token_json.get("access_token")
        refresh_token = token_json.get("refresh_token")
        expires_in = token_json.get("expires_in", 3600)

        if not access_token:
            return Response(
                {"detail": "No access token in Google response"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        # Pobierz YouTube channel ID
        try:
            print("📤 Fetching YouTube channel ID...")
            youtube_id = self.get_youtube_channel_id(access_token)
            print(f"✅ YouTube ID: {youtube_id}")
        except Exception as e:
            print(f"❌ Failed to fetch YouTube channel: {str(e)}")
            return Response(
                {"detail": f"Failed to fetch YouTube channel: {str(e)}"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        # Zapisz konto
        expires_at = timezone.now() + timedelta(seconds=expires_in)


        youtube_account, created = YoutubeAccount.objects.update_or_create(
            user=request.user,
            defaults={
                'youtube_id': youtube_id,
                'access_token': access_token,
                'refresh_token': refresh_token,
                'expires_at': expires_at,
            }
        )

        action = "created" if created else "updated"
        print(f"✅ YouTube account {action} for user {request.user.email}")

        check_youtube_channel_category.delay(
            access_token=access_token,
            channel_id=youtube_id,
            user_id=request.user.id
        )

        return Response(
            {
                "message": "Successfully connected YouTube account",
                "youtube_id": youtube_id,
                "action": action
            },
            status=status.HTTP_200_OK,
        )

    def get_youtube_channel_id(self, access_token):
        """
        Gets user YT channel ID
        """
        url = "https://www.googleapis.com/youtube/v3/channels"
        headers = {"Authorization": f"Bearer {access_token}"}
        params = {
            "part": "id,snippet",
            "mine": "true"
        }

        response = requests.get(url, headers=headers, params=params, timeout=10)
        response.raise_for_status()
        data = response.json()

        items = data.get("items", [])
        if not items:
            raise ValueError("No YouTube channel found for this account")

        return items[0]["id"]

class RecommendationsView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request):
        ensure_valid_external_tokens(self.request.user)

        return Response(
            {"detail":"Tokens valid, Recommendations placeholder",
             },status=200
        )

class TestCelery(APIView):

    permission_classes = [permissions.IsAuthenticated]

    def get(self, request):
        spotify_account=SpotifyAccount.objects.get(user=self.request.user)
        if not spotify_account:
            return Response(
                {"detail": "No spotify account found"},
                status=status.HTTP_404_NOT_FOUND,
            )
        access_token=spotify_account.access_token
        headers = {"Authorization": f"Bearer {access_token}"}
        fetch_recently_played(headers,request.user.id)

        return Response(
            {"detail":"Tokens valid, Recommendations placeholder",
             },status=200
        )