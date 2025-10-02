import pytest
from django.contrib.auth import get_user_model
User = get_user_model()

@pytest.mark.django_db
def test_user_create():
    count = User.objects.count()
    user = User.objects.create_user(
        email='tomek2115@onet.pl',
        password='QWEQSDASd123312312#'
    )

    assert User.objects.count() == count + 1
    assert user.email == "tomek2115@onet.pl"
    assert user.check_password("QWEQSDASd123312312#")
    assert user.is_active is True
    assert user.is_staff is False

