from celery import shared_task
from django.contrib.auth import get_user_model
from recomendations.services.recomendation import get_or_build_recommendation

User = get_user_model()

@shared_task
def build_recommendation_task(user_id: int, force_rebuild: bool = False):
    try:
        user = User.objects.get(id=user_id)
    except User.DoesNotExist:
        return

    get_or_build_recommendation(user, force_rebuild=force_rebuild)