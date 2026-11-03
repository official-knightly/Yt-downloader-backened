# Media Download API

Lightweight FastAPI backend using yt-dlp and FFmpeg. Supports public media URLs from YouTube, Instagram, TikTok, X/Twitter, and Facebook, subject to each platform's access rules.

## API

### `GET /health`

Returns `{"status":"ok","service":"Media Download API"}`.

### `POST /api/download`

JSON body:

```json
{
  "url": "https://www.youtube.com/watch?v=...",
  "quality": "1080",
  "format": "mp4"
}
```

- `quality`: `best`, `1080`, `720`, `480`, or `360`
- `format`: `mp4` or `mp3`
- Optional API key: send `x-api-key: YOUR_API_KEY` when `API_KEY` is configured.
- The endpoint returns the media file as an attachment. Temporary files are deleted after the response finishes.
- Maximum duration: 15 minutes. Maximum file size: 500 MB.

Example:

```bash
curl -X POST "http://localhost:8000/api/download" \
  -H "Content-Type: application/json" \
  -H "x-api-key: YOUR_API_KEY" \
  -d '{"url":"https://www.youtube.com/watch?v=...","quality":"720","format":"mp4"}' \
  --output video.mp4
```

## Run locally

Requires Python 3.11+ and FFmpeg installed on your machine.

```bash
python -m venv .venv
# Windows: .venv\\Scripts\\activate
# macOS/Linux: source .venv/bin/activate
pip install -r requirements.txt
uvicorn main:app --reload --host 0.0.0.0 --port 8000
```

Open `http://localhost:8000/docs` for interactive API documentation.

## Environment variables

| Variable | Purpose |
| --- | --- |
| `API_KEY` | Optional shared API key. If set, every download request must send it in `x-api-key`. |
| `CORS_ORIGINS` | Comma-separated allowed origins. Defaults to localhost ports 3000 and 5173. |
| `FRONTEND_ORIGIN` | Optional additional frontend origin, e.g. `https://your-site.example`. |
| `PORT` | HTTP port; hosting platforms normally provide this automatically. |

Set a strong, private `API_KEY` in the hosting provider's environment settings. Do not put a shared API key in browser-side JavaScript: visitors can inspect it. For a public frontend, put the API behind your own authenticated server/proxy or use per-user authentication and rate limiting.

## Deploy on Render (free tier, when available)

1. Push this repository to GitHub.
2. In Render, choose **New → Web Service** and connect this repository.
3. Select **Docker** as the runtime. Render will build the included `Dockerfile`.
4. Choose the Free instance type if it is offered for your account/region.
5. Add environment variables:
   - `API_KEY`: generate a long random secret.
   - `FRONTEND_ORIGIN`: your exact frontend origin, such as `https://your-frontend.onrender.com`.
   - Optionally set `CORS_ORIGINS` to a comma-separated list of your frontend and any required local development origins.
6. Deploy. Test `https://YOUR-SERVICE.onrender.com/health`.
7. Configure your frontend to POST to `https://YOUR-SERVICE.onrender.com/api/download`.

Free instances may sleep, have limited memory/runtime, and are not suitable for large or frequent downloads. Temporary local files are ephemeral.

## Deploy on Railway

1. Push this repository to GitHub and create a Railway project.
2. Choose **Deploy from GitHub Repo** and select this repository.
3. Railway detects the Dockerfile and builds the container (confirm Dockerfile as the builder if prompted).
4. In **Variables**, set `API_KEY`, `FRONTEND_ORIGIN`, and optionally `CORS_ORIGINS`.
5. In **Settings → Networking**, generate a public domain.
6. Deploy and test `https://YOUR-DOMAIN/health`.

Railway generally offers usage-based trial/credit plans rather than a permanently free hosting tier; check its current pricing before deploying.

## Production notes

- yt-dlp and social platforms change frequently; keep yt-dlp updated if extractors stop working.
- Only download content you own or are authorized to download, and comply with the platforms' terms and applicable law.
- Platform login-required/private content is not supported by this public endpoint.
- A shared API key and CORS are not user authentication. Add rate limiting, per-user authentication, request concurrency limits, and abuse monitoring before exposing this service publicly.
- The service rejects unsupported URL hosts and non-standard ports. Do not add arbitrary URL support without SSRF protections.
- Downloads are processed synchronously in the request and returned when complete. Hosting request timeouts and memory/disk quotas can still interrupt long jobs.
