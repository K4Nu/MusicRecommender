import pytest
import requests
from datetime import date
from unittest.mock import patch, MagicMock

from music.models import Artist, Track, Album, Genre
from users.models import UserTopItem
from users.tasks.lastfm_tasks import (clean_lastfm_image,
                                      normalize_name,
                                      artist_names_compatible,
                                      safe_cache_key
                                      )

@pytest.mark.parametrize("value,expected", [
    (None, None),
    ("https://lastfm.freetls.fastly.net/i/u/2a96cbd8b46e442fc41c2b86b821562f.png", None),
    ("https://www.top.pl/image.jpg", "https://www.top.pl/image.jpg"),
])
def test_clean_lastfm_image(value, expected):
    assert clean_lastfm_image(value) == expected

@pytest.mark.parametrize("value,expected", [
    ("Rock",              "rock"),
    ("JAZZ",              "jazz"),
    ("R&B",               "randb"),
    ("artist feat. name", "artist  name"),
    ("artist ft. name",   "artist  name"),
    ("  lo-fi  ",         "lo-fi"),
])
def test_normalize_name(value, expected):
    assert normalize_name(value) == expected

@pytest.mark.parametrize("a,b,expected", [
    ("",          "Radiohead",  True),
    ("Radiohead", "",           True),
    ("Radiohead", "Radiohead",  True),
    ("radiohead", "RADIOHEAD",  True),
    ("Radiohead",  "Coldplay",  False),
    ("The Beatles feat. Paul", "The Beatles", True),
    ("The Beatles", "The Beatles feat. Paul", True),
])
def test_artist_names_compatible(a, b, expected):
    assert artist_names_compatible(a, b) == expected

def test_safe_cache_key_returns_hex_string():
    result = safe_cache_key("rock")
    assert isinstance(result, str)
    assert len(result) == 64

def test_safe_cache_key_same_input_same_output():
    assert safe_cache_key("rock") == safe_cache_key("rock")

def test_safe_cache_key_different_inputs_differ():
    assert safe_cache_key("rock") != safe_cache_key("jazz")


