import os
from datetime import timedelta
import requests
from django.utils import timezone
from djoser.serializers import UserSerializer
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework import permissions, status
from .tasks import fetch_spotify_initial_data
from users.models import SpotifyAccount,UserTopItem
from rest_framework import generics
from .serializers import UserTopTrackSerializer

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
            "name":item.name,
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


class YoutubeConnect(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request):
        code = request.data.get('code')
        redirect_uri = request.data.get('redirect_uri')
        codeVerifier = request.data.get("codeVerifier")

        if not code or not redirect_uri:
            return Response(
                {"detail": "Missing 'code' or 'redirect_uri'."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        print(f'📥 Received params:')
        print(f'  code: {code[:20]}...')
        print(f'  redirect_uri: {redirect_uri}')
        print(f'  codeVerifier: {codeVerifier[:20] if codeVerifier else None}...')
        print(f'  client_id: {os.environ.get("YOUTUBE_CLIENT_ID")}')

        token_url = "https://oauth2.googleapis.com/token"
        token_data = {
            'grant_type': 'authorization_code',
            'code': code,
            'redirect_uri': redirect_uri,
            'client_id': os.environ['YOUTUBE_CLIENT_ID'],
            "code_verifier": codeVerifier,
            "client_secret":os.environ['YOUTUBE_CLIENT_SECRET'],
        }

        try:
            token_response = requests.post(token_url, data=token_data)  # <- WCIĘCIE!

            print(f"📊 Google response status: {token_response.status_code}")  # <- WCIĘCIE!
            print(f"📄 Google response body: {token_response.text}")  # <- WCIĘCIE!

            if not token_response.ok:  # <- WCIĘCIE!
                return Response(
                    {
                        "detail": "Failed to exchange code for token",
                        "status": token_response.status_code,
                        "google_error": token_response.text
                    },
                    status=status.HTTP_400_BAD_REQUEST,
                )

            token_json = token_response.json()  # <- WCIĘCIE! (i usuń poprzednie wcięcie przed tym)

        except requests.exceptions.RequestException as e:
            print(f"❌ Request completely failed: {str(e)}")
            return Response(
                {"detail": f"Request error: {str(e)}"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        print(f"✅ Token received: {token_json}")

        return Response(
            {"message": "Successfully logged in."},
            status=status.HTTP_200_OK,
        )