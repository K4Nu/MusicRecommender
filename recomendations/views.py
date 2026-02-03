from django.shortcuts import render
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework import permissions, status
from .services.cold_start import cold_start_fetch_spotify_global

class ColdTest(APIView):
    permission_classes = [permissions.AllowAny]

    def get(self,request):
        cold_start_fetch_spotify_global.delay()
        return Response(
            {"message": "Cold start"},
            status=status.HTTP_200_OK
        )