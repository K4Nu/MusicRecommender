from django.shortcuts import render
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework import permissions, status
from .services.cold_start import cold_start_fetch_spotify_global
from recomendations.models import ColdStartTrack
from recomendations.serializers import ColdStartTrackSerializer

class ColdTest(APIView):
    permission_classes = [permissions.AllowAny]

    def get(self,request):
        cold_start_fetch_spotify_global.delay()
        return Response(
            {"message": "Cold start"},
            status=status.HTTP_200_OK
        )
"""
By now without postgres
"""
class ColdStartRecommender(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request):
        limit = 7

        qs = (
            ColdStartTrack.objects
            .filter(track__spotify_id__isnull=False)
            .select_related("track")
            .prefetch_related("track__artists")
            .order_by("?")[:50]  # pobieramy więcej
        )

        seen_artists = set()
        selected = []

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

        serializer = ColdStartTrackSerializer(selected, many=True)
        return Response(serializer.data)