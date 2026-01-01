from django.contrib import admin
from users.models import User,ListeningHistory,SpotifyAccount,YoutubeAccount

@admin.register(User)
class UserAdmin(admin.ModelAdmin):
    pass
