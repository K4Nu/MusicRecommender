from typing import Optional, List

MUSIC_TOPICS_STRONG = {
    "music", "pop_music", "rock_music", "hip_hop_music", "electronic_music",
    "dance_music", "classical_music", "jazz", "blues", "country_music",
    "folk_music", "metal_music", "punk_rock", "rap_music", "rapping",
    "rhythm_and_blues", "soul_music", "reggae", "techno", "house_music",
    "trance_music", "dubstep", "alternative_rock", "hard_rock",
    "heavy_metal_music", "indie_rock", "grunge", "electro_music", "ska",
    "disco", "funk", "gospel_music", "opera", "orchestra", "choral_music",
    "punk_music", "progressive_rock", "experimental_music", "world_music",
    "latin_music", "african_music", "k_pop", "j_pop", "lo_fi_music",
    "soundtrack", "film_score", "game_music", "video_game_music",
    "music_genre",
}

MUSIC_TOPICS_WEAK = {
    "musical_instrument", "guitar", "bass_guitar", "drums",
    "percussion_instrument", "piano", "keyboard_instrument", "violin",
    "cello", "flute", "saxophone", "trumpet", "trombone", "singing",
    "song", "songwriting", "composer", "lyricist", "arranger", "conductor",
    "music_production", "music_video", "recording", "record_label", "album",
    "sound_recording_and_reproduction", "sheet_music", "notation",
    "audiophile", "mixing_console", "audio_engineering", "studio_recording",
    "microphone", "concert", "live_music", "performance", "tour",
    "music_festival", "dj", "disc_jockey", "music_industry", "band_(music)",
    "music_award", "music_chart", "musician", "singer", "vocal_music",
    "chorus", "ensemble_(music)", "performing_arts", "creative_industries",
    "sound", "audiovisual", "media", "karaoke", "remix", "cover_song",
    "music_theory", "sound_design", "musicology", "ethnomusicology",
    "musical_theatre", "sound_engineering", "audio_production",
    "acoustic_music", "digital_audio", "record_producer",
    "art", "culture", "popular_culture",
}

MUSIC_KEYWORDS_STRONG = [
    "music", "official music", "band", "dj", "producer", "record label",
    "records", "vevo", "music video",
]

MUSIC_KEYWORDS_WEAK = [
    "soundtrack", "ost", "cover", "remix", "instrumental", "session",
]


def compute_music_score(
    channel_data: dict,
    recent_video_categories: Optional[List[int]] = None,
) -> dict:
    """
    channel_data – JSON z YouTube /channels?part=snippet,topicDetails
    recent_video_categories – lista videoCategoryId (int) dla ostatnich filmów, np. [10, 22, 10, ...]
    Returns:
      - total_score
      - score_topics, score_text, score_videos
      - is_music (bool)
    """
    score_topics = 0.0
    score_text = 0.0
    score_videos = 0.0

    items = channel_data.get("items") or []
    if not items:
        return {
            "total_score": 0.0,
            "score_topics": 0.0,
            "score_text": 0.0,
            "score_videos": 0.0,
            "is_music": False,
        }

    ch = items[0]
    snippet = ch.get("snippet") or {}
    topic_details = ch.get("topicDetails") or {}
    topic_categories = topic_details.get("topicCategories") or []

    # --- 1) Scoring topicCategories ---
    slugs = []
    for url in topic_categories:
        if "/wiki/" not in url:
            continue
        slug = url.rsplit("/wiki/", 1)[-1].lower()
        slugs.append(slug)

    for slug in slugs:
        if slug in MUSIC_TOPICS_STRONG:
            score_topics += 2.0
        elif slug in MUSIC_TOPICS_WEAK:
            score_topics += 1.0
        elif "music" in slug:
            score_topics += 1.0

    score_topics = min(score_topics, 3.0)

    # --- 2) Scoring title / desscription ---
    title = (snippet.get("title") or "").lower()
    description = (snippet.get("description") or "").lower()
    text = f"{title}\n{description}"

    for kw in MUSIC_KEYWORDS_STRONG:
        if kw in text:
            score_text += 1.0

    for kw in MUSIC_KEYWORDS_WEAK:
        if kw in text:
            score_text += 0.5

    score_text = min(score_text, 2.0)

    # --- 3) Scoring po ostatnich filmach (videoCategoryId) ---
    if recent_video_categories:
        total = len(recent_video_categories)
        if total > 0:
            music_count = sum(1 for cat in recent_video_categories if cat == 10)
            ratio = music_count / total

            if ratio >= 0.7:
                score_videos = 2.0
            elif ratio >= 0.4:
                score_videos = 1.0
            else:
                score_videos = 0.0

    total_score = score_topics + score_text + score_videos
    is_music = total_score >= 3.0

    return {
        "total_score": total_score,
        "score_topics": score_topics,
        "score_text": score_text,
        "score_videos": score_videos,
        "is_music": is_music,
    }
