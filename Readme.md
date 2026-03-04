# MusicRecommender — Backend

A Django-based music recommendation engine that generates personalized track recommendations using tag-based user profiling, similarity analysis, and adaptive recommendation strategies.

## Tech Stack

- **Python 3.11+**
- **Django 5 + Django REST Framework**
- **PostgreSQL** — primary database
- **Redis** — Celery broker & cache
- **Celery + Eventlet** — async task processing
- **Djoser + SimpleJWT** — authentication
- **Docker Compose** — infrastructure services
- **Prometheus + Grafana** — metrics monitoring
- **Sentry** — error tracking

## Features

- OAuth 2.0 integration with Spotify (listening history, top tracks, playlists)
- Last.fm API integration (tags, artist/track similarity)
- Tag-based user profile construction with weighted vectors
- Hybrid recommendation engine (cold-start, warm-start, hybrid strategies)
- Artist diversity mechanism to prevent single-artist domination
- Recommendation explainability (matched tags, similar tracks, score breakdown)
- Async prebuild system — next recommendation batch built before current one is exhausted
- Feedback loop — LIKE/DISLIKE updates user profile in real-time
- Encrypted OAuth token storage (Fernet)
- ResourceLock mechanism preventing concurrent pipeline execution

## Quick Start

### 1. Clone

```bash
git clone https://github.com/K4Nu/MusicRecommender.git
cd MusicRecommender
```

### 2. Environment

Copy `.env.example` to `.env` and fill in required values:

```bash
cp .env.example .env
```

Required variables:
- `DJANGO_SECRET_KEY`
- `POSTGRES_DB`, `POSTGRES_USER`, `POSTGRES_PASSWORD`, `POSTGRES_HOST`, `POSTGRES_PORT`
- `REDIS_HOST`, `REDIS_PORT`
- `SPOTIFY_CLIENT_ID`, `SPOTIFY_CLIENT_SECRET`, `SPOTIFY_REDIRECT_URI`
- `LASTFM_API_KEY`
- `SENTRY_DSN` (optional)

### 3. Infrastructure

```bash
docker-compose up -d
```

This starts PostgreSQL, Redis, Prometheus, and Grafana.

### 4. Install & Run

```bash
pip install -r requirements.txt
python manage.py migrate
python manage.py runserver
```

### 5. Celery Worker

```bash
celery -A MusicRecommender worker -l info -P eventlet -c 50
```

API available at `http://localhost:8000`

## Project Structure

```
├── music/              # Domain models (Track, Artist, Tag, Similarity)
├── recommendations/    # Recommendation engine, scoring, feedback
│   ├── models/         # Recommendation, UserTag, ColdStartTrack
│   ├── services/       # Core algorithms (scoring, cold start, feedback)
│   └── tasks/          # Async recommendation building
├── users/              # User management, OAuth, Spotify/LastFM sync
│   └── tasks/          # Async data synchronization
├── docker-compose.yml
└── manage.py
```

## API Endpoints

| Endpoint | Method | Description |
|---|---|---|
| `/api/auth/` | POST | Registration & login (Djoser) |
| `/api/cold_start/` | GET | Get onboarding tracks |
| `/api/cold_start/` | POST | Submit onboarding reaction |
| `/api/home/` | GET | Get current recommendations |
| `/api/feedback/` | POST | Submit LIKE/DISLIKE |
| `/api/spotify/connect/` | POST | Connect Spotify account |

## Tests

```bash
pytest recommendations/tests/test_recommendation_engine.py -v
```

## License

This project was developed as an engineering thesis at the University of Silesia.