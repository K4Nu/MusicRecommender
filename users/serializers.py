from dj_rest_auth.registration.serializers import RegisterSerializer
from rest_framework import serializers

class CustomRegisterSerializer(RegisterSerializer):
    username = None  # Make sure username isn't used

    email = serializers.EmailField(required=True)

    def get_cleaned_data(self):
        return {
            "email": self.validated_data.get("email", ""),
            "password1": self.validated_data.get("password1", ""),

        }

    def save(self, request):
        user = super().save(request)
        user.save()
        return user
