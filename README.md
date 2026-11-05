# Media Download API

A lightweight FastAPI media-download backend built with **yt-dlp** and **FFmpeg**. It accepts public media URLs from YouTube, Instagram, TikTok, X/Twitter, and Facebook, subject to each platform's access rules and availability.

Only download media you own or are authorized to save. This service does not support private/login-only content or bypass platform access controls.

## Features

- MP4 video downloads with best, 1080, 720, 480, and 360 quality selection.
- MP3 audio extraction.
- Single-item downloads (playlists are disabled).
- 15-minute duration and 500 MB output-size limits.
- URL host validation, optional API-key protection, CORS allowlisting, and bounded concurrent processing.
- Temporary per-request directories, removed after the response is sent.
- Docker image with FFmpeg included.

## API

### GET /

Returns service name, version, and API documentation paths.

### GET /health

Returns service status and whether FFmpeg is available.

### POST /api/download

Request:

```json
{
  "url": "https://www.youtube.com/watch?v=...",
  "quality": "1080",
  "format": "mp4"
}
```

Fields:

| Field | Values | Default |
| --- | --- | --- |
| url | Supported public media URL | Required |
| quality | best, 1080, 720, 480, 360 | best |
| format | mp4, mp3 | mp4 |

The endpoint responds with the media as an attachment. For example:

```bash
curl -X POST "http://localhost:8000/api/download" \
  -H "Content-Type: application/json" \
  -H "x-api-key: YOUR_API_KEY" \
  -d '{"url":"https://www.youtube.com/watch?v=...","quality":"720","format":"mp4"}' \
  --output video.mp4
```

Common errors:

- 401: API key is missing or invalid when API-key protection is enabled.
- 422: URL is unsupported, media is unavailable, or processing was rejected.
- 429: all download slots are busy; retry after the supplied Retry-After interval.
- 500: unexpected processing failure.

## Run locally

Requires Python 3.11+ and FFmpeg on your system.

```bash
python -m venv .venv
# Windows:
.venv\\Scripts\\activate
# macOS/Linux:
source .venv/bin/activate

pip install -r requirements.txt
uvicorn main:app --reload --host 0.0.0.0 --port 8000
```

Open http://localhost:8000/docs for interactive API documentation.

## Environment variables

| Variable | Purpose |
| --- | --- |
| API_KEY | Optional shared key. When configured, requests must send it in x-api-key. |
| CORS_ORIGINS | Comma-separated allowed browser origins. |
| FRONTEND_ORIGIN | Optional additional frontend origin. |
| MAX_CONCURRENT_DOWNLOADS | Maximum simultaneous media-processing jobs (default: 2). |

Do not expose a shared API key in frontend JavaScript. Browser users can inspect it. For a public frontend, put the backend behind an authenticated server/proxy or implement per-user authentication, rate limits, quotas, and abuse monitoring.

## Docker

```bash
docker build -t media-download-api .
docker run --rm -p 8000:8000 \
  -e API_KEY="replace-with-a-long-random-secret" \
  -e FRONTEND_ORIGIN="https://your-frontend.example" \
  media-download-api
```

FFmpeg is installed in the included Docker image. Deploy the repository as a Docker web service on a host that supports persistent process execution and sufficient temporary disk space. Configure API_KEY and the exact frontend origin in the hosting provider's environment settings.

## Deployment notes

- The service listens on the host-provided PORT (default 8000).
- Keep yt-dlp updated; social platforms frequently change their extractors.
- Free hosting plans may sleep, impose request timeouts, or restrict CPU, memory, and temporary disk. Large downloads may fail on those plans.
- The endpoint processes a download synchronously and streams the completed file as the response. It is not a background job queue.
- The service deliberately rejects unsupported hosts, non-standard ports, IP-address URLs, private content, and login-only content.
- API-key protection and CORS are not substitutes for user authentication or rate limiting.
