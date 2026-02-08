"""
URL configuration for Recommender project.

The `urlpatterns` list routes URLs to views. For more information please see:
    https://docs.djangoproject.com/en/5.2/topics/http/urls/
Examples:
Function views
    1. Add an import:  from my_app import views
    2. Add a URL to urlpatterns:  path('', views.home, name='home')
Class-based views
    1. Add an import:  from other_app.views import Home
    2. Add a URL to urlpatterns:  path('', Home.as_view(), name='home')
Including another URLconf
    1. Import the include() function: from django.urls import include, path
    2. Add a URL to urlpatterns:  path('blog/', include('blog.urls'))
"""
from django.contrib import admin
from django.urls import path, include
from users.views import SpotifyConnect,UserTopTracks,TestView,YoutubeConnect,TestLastFM
from drf_spectacular.views import SpectacularAPIView, SpectacularRedocView, SpectacularSwaggerView
from debug_toolbar.toolbar import debug_toolbar_urls
from recomendations.views import ColdTest,InitialSetupView,GetFeature, OnboardingInteractView,UserStatus
urlpatterns = [
    path("", include("django_prometheus.urls")),
    path('admin/', admin.site.urls),
    path("auth/spotify/connect/",SpotifyConnect.as_view()),
    path("auth/youtube/connect/",YoutubeConnect.as_view()),
    path("auth/", include("djoser.urls")),
    path("auth/", include("djoser.urls.jwt")),
    path("auth/social/", include("allauth.socialaccount.urls")),
    path("user/top_track/", UserTopTracks.as_view()),
    path('api/schema/', SpectacularAPIView.as_view(), name='schema'),
    path('api/schema/swagger-ui/', SpectacularSwaggerView.as_view(url_name='schema'), name='swagger-ui'),
    path('api/schema/redoc/', SpectacularRedocView.as_view(url_name='schema'), name='redoc'),
    path("test/", TestLastFM.as_view()),
    path("t/", ColdTest.as_view()),
    path("cold_start/", InitialSetupView.as_view()),
    path("essa/",GetFeature.as_view()),
    path("api/onboarding/", OnboardingInteractView.as_view()),
    path("api/me/", UserStatus.as_view()),

]+debug_toolbar_urls()
