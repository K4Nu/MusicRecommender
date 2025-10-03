import pytest
from django.contrib.auth import get_user_model
from helper import generate_password

User = get_user_model()

"""
    Tests for non-stuff user
"""

@pytest.mark.django_db
def test_user_create():
    count = User.objects.count()
    password = generate_password(12)
    user = User.objects.create_user(
        email='tomek2115@onet.pl',
        password=password,
    )

    assert User.objects.count() == count + 1
    assert user.email == "tomek2115@onet.pl"
    assert user.check_password(password)
    assert user.is_active is True
    assert user.is_staff is False
    assert str(user) == "tomek2115@onet.pl"

@pytest.mark.django_db
def test_create_user_requires_email():
    with pytest.raises(ValueError, match="must be set"):
        User.objects.create_user(email=None, password=generate_password(12))

@pytest.mark.django_db
def test_create_user_requires_password():
    with pytest.raises(ValueError, match="must be set"):
        User.objects.create_user(email="asd123@onet.pl", password=None)

@pytest.mark.django_db
def test_create_user_bad_email():
    with pytest.raises(ValueError, match="Invalid email address"):
        User.objects.create_user(email="asd123@onetpl", password=generate_password(12))

"""
    Tests for admin/staff user
"""

@pytest.mark.django_db
def test_create_staff_user():
    user=User.objects.create_superuser(
        email="admin@net.com",
        password=generate_password(12),
    )
    assert user.is_staff
    assert user.is_superuser
    assert user.is_active

@pytest.mark.django_db
def test_create_staff_user_is_staff_error():
    with pytest.raises(ValueError, match="Superuser must have is_staff=True."):
        User.objects.create_superuser(email="admin@net.com", password=generate_password(12),is_staff=False)

@pytest.mark.django_db
def test_create_staff_user_is_superuser_error():
    with pytest.raises(ValueError, match="Superuser must have is_superuser=True."):
        User.objects.create_superuser(email="admin@net.com", password=generate_password(12), is_superuser=False)