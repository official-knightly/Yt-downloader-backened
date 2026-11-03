from __future__ import annotations

import hmac
import os
import re
import shutil
import tempfile
from pathlib import Path
from urllib.parse import urlsplit

import yt_dlp
from fastapi import FastAPI, Header, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field, field_validator
from starlette.background import BackgroundTask
from starlette.concurrency import run_in_threadpool

APP_NAME = "Media Download API"
MAX_DURATION_SECONDS = 15 * 60
MAX_FILE_SIZE_BYTES = 500 * 1024 * 1024  # 500 MB safety cap
QUALITY_HEIGHTS = {"1080": 1080, "720": 720, "480": 480, "360": 360}
ALLOWED_HOSTS = {
    "youtube.com", "youtu.be", "youtube-nocookie.com",
    "instagram.com", "tiktok.com",
    "twitter.com", "x.com", "t.co",
    "facebook.com", "fb.watch",
}

app = FastAPI(title=APP_NAME, version="1.0.0")

origins = [
    origin.strip()
    for origin in os.getenv(
        "CORS_ORIGINS",
        "http://localhost:3000,http://localhost:5173,http://127.0.0.1:3000,http://127.0.0.1:5173",
    ).split(",")
    if origin.strip()
]
frontend_origin = os.getenv("FRONTEND_ORIGIN")
if frontend_origin and frontend_origin.strip() not in origins:
    origins.append(frontend_origin.strip())

app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_credentials=False,
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["Content-Type", "x-api-key"],
)


class DownloadRequest(BaseModel):
    url: str = Field(min_length=8, max_length=2048)
    quality: str = "best"
    format: str = "mp4"

    @field_validator("quality")
    @classmethod
    def validate_quality(cls, value: str) -> str:
        if value not in {"best", *QUALITY_HEIGHTS.keys()}:
            raise ValueError("quality must be best, 1080, 720, 480, or 360")
        return value

    @field_validator("format")
    @classmethod
    def validate_format(cls, value: str) -> str:
        value = value.lower()
        if value not in {"mp4", "mp3"}:
            raise ValueError("format must be mp4 or mp3")
        return value

    @field_validator("url")
    @classmethod
    def validate_url(cls, value: str) -> str:
        value = value.strip()
        try:
            parsed = urlsplit(value)
            host = (parsed.hostname or "").lower().rstrip(".")
            port = parsed.port
        except ValueError as exc:
            raise ValueError("Invalid URL") from exc

        if parsed.scheme not in {"http", "https"} or not host:
            raise ValueError("Only valid HTTP or HTTPS URLs are accepted")
        if parsed.username or parsed.password:
            raise ValueError("URLs containing credentials are not accepted")
        if port not in {None, 80, 443}:
            raise ValueError("Non-standard URL ports are not accepted")
        if not any(host == allowed or host.endswith("." + allowed) for allowed in ALLOWED_HOSTS):
            raise ValueError("This media platform is not supported")
        return value


def check_api_key(provided_key: str | None) -> None:
    expected_key = os.getenv("API_KEY")
    # API-key protection is optional. Set API_KEY in production to enable it.
    if expected_key and not hmac.compare_digest(provided_key or "", expected_key):
        raise HTTPException(status_code=401, detail="Invalid or missing API key")


def download_media(request: DownloadRequest, workdir: str) -> Path:
    output_template = str(Path(workdir) / "%(title).100B [%(id)s].%(ext)s")

    if request.format == "mp3":
        options = {
            "format": "bestaudio/best",
            "outtmpl": output_template,
            "noplaylist": True,
            "max_downloads": 1,
            "max_filesize": MAX_FILE_SIZE_BYTES,
            "match_filter": yt_dlp.utils.match_filter_func(
                f"duration > {MAX_DURATION_SECONDS}"
            ),
            "postprocessors": [
                {
                    "key": "FFmpegExtractAudio",
                    "preferredcodec": "mp3",
                    "preferredquality": "320",
                }
            ],
        }
    else:
        height = QUALITY_HEIGHTS.get(request.quality)
        if height:
            format_selector = (
                f"bestvideo[height<={height}][ext=mp4]+bestaudio[ext=m4a]/"
                f"best[height<={height}][ext=mp4]/"
                f"bestvideo[height<={height}]+bestaudio/"
                f"best[height<={height}]"
            )
        else:
            format_selector = (
                "bestvideo[ext=mp4]+bestaudio[ext=m4a]/"
                "best[ext=mp4]/bestvideo+bestaudio/best"
            )

        options = {
            "format": format_selector,
            "outtmpl": output_template,
            "noplaylist": True,
            "max_downloads": 1,
            "max_filesize": MAX_FILE_SIZE_BYTES,
            "match_filter": yt_dlp.utils.match_filter_func(
                f"duration > {MAX_DURATION_SECONDS}"
            ),
            "merge_output_format": "mp4",
            "postprocessors": [
                {"key": "FFmpegVideoRemuxer", "preferedformat": "mp4"}
            ],
        }

    options.update(
        {
            "quiet": True,
            "no_warnings": True,
            "socket_timeout": 20,
            "retries": 2,
            "fragment_retries": 2,
            "ignoreerrors": False,
            "overwrites": True,
            "restrictfilenames": True,
        }
    )

    with yt_dlp.YoutubeDL(options) as downloader:
        downloader.download([request.url])

    extension = ".mp3" if request.format == "mp3" else ".mp4"
    candidates = [
        path for path in Path(workdir).glob("*")
        if path.is_file() and path.suffix.lower() == extension
    ]
    if not candidates:
        raise RuntimeError("The media processor did not produce the requested file")

    result = max(candidates, key=lambda path: path.stat().st_size)
    if result.stat().st_size > MAX_FILE_SIZE_BYTES:
        raise RuntimeError("The downloaded file exceeds the 500 MB limit")
    return result


@app.get("/health")
async def health():
    return {"status": "ok", "service": APP_NAME}


@app.post("/api/download")
async def download(
    payload: DownloadRequest,
    x_api_key: str | None = Header(default=None, alias="x-api-key"),
):
    check_api_key(x_api_key)
    workdir = tempfile.mkdtemp(prefix="media-download-")

    try:
        output_path = await run_in_threadpool(download_media, payload, workdir)
        media_type = "audio/mpeg" if payload.format == "mp3" else "video/mp4"
        safe_filename = re.sub(r"[^A-Za-z0-9._() -]", "_", output_path.name)
        return FileResponse(
            path=output_path,
            media_type=media_type,
            filename=safe_filename,
            content_disposition_type="attachment",
            background=BackgroundTask(shutil.rmtree, workdir, ignore_errors=True),
        )
    except yt_dlp.utils.DownloadError as exc:
        shutil.rmtree(workdir, ignore_errors=True)
        message = str(exc)
        if "duration" in message.lower():
            raise HTTPException(
                status_code=413,
                detail="Videos longer than 15 minutes are not allowed",
            ) from exc
        if "file size" in message.lower() or "filesize" in message.lower():
            raise HTTPException(status_code=413, detail="Media exceeds the 500 MB limit") from exc
        raise HTTPException(
            status_code=422,
            detail="Unable to download this URL. It may be private, unavailable, or unsupported.",
        ) from exc
    except HTTPException:
        shutil.rmtree(workdir, ignore_errors=True)
        raise
    except Exception as exc:
        shutil.rmtree(workdir, ignore_errors=True)
        raise HTTPException(
            status_code=500,
            detail="Media processing failed. Check that FFmpeg is installed and the URL is accessible.",
        ) from exc
