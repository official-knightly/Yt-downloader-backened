from __future__ import annotations

import hmac
import ipaddress
import os
import re
import shutil
import tempfile
import threading
from pathlib import Path
from urllib.parse import urlsplit

import yt_dlp
from fastapi import FastAPI, Header, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from pydantic import BaseModel, ConfigDict, Field, field_validator
from starlette.background import BackgroundTask
from starlette.concurrency import run_in_threadpool

APP_NAME = "Media Download API"
APP_VERSION = "2.0.0"
MAX_DURATION_SECONDS = 15 * 60
MAX_FILE_SIZE_BYTES = 500 * 1024 * 1024
MAX_CONCURRENT_DOWNLOADS = max(1, int(os.getenv("MAX_CONCURRENT_DOWNLOADS", "2")))
DOWNLOAD_TIMEOUT_SECONDS = max(30, int(os.getenv("DOWNLOAD_TIMEOUT_SECONDS", "900")))
QUALITY_HEIGHTS = {"1080": 1080, "720": 720, "480": 480, "360": 360}
ALLOWED_HOSTS = {
    "youtube.com", "youtu.be", "youtube-nocookie.com",
    "instagram.com", "tiktok.com",
    "twitter.com", "x.com", "t.co",
    "facebook.com", "fb.watch",
}
DOWNLOAD_SLOTS = threading.BoundedSemaphore(MAX_CONCURRENT_DOWNLOADS)

app = FastAPI(
    title=APP_NAME,
    version=APP_VERSION,
    description="Download public media that you own or are authorized to save.",
)

origins = [
    item.strip()
    for item in os.getenv(
        "CORS_ORIGINS",
        "http://localhost:3000,http://localhost:5173,"
        "http://127.0.0.1:3000,http://127.0.0.1:5173",
    ).split(",")
    if item.strip()
]
frontend_origin = os.getenv("FRONTEND_ORIGIN", "").strip()
if frontend_origin and frontend_origin not in origins:
    origins.append(frontend_origin)

app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_credentials=False,
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["Content-Type", "x-api-key"],
)


class DownloadRequest(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    url: str = Field(min_length=8, max_length=2048)
    quality: str = "best"
    format: str = "mp4"

    @field_validator("quality")
    @classmethod
    def validate_quality(cls, value: str) -> str:
        value = value.lower()
        if value not in {"best", *QUALITY_HEIGHTS}:
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
            raise ValueError("Invalid media URL") from exc

        if parsed.scheme not in {"http", "https"} or not host:
            raise ValueError("Only valid HTTP or HTTPS media URLs are accepted")
        if parsed.username or parsed.password:
            raise ValueError("URLs containing credentials are not accepted")
        if port not in {None, 80, 443}:
            raise ValueError("Non-standard URL ports are not accepted")

        # Reject IP literals and local/private hostnames before yt-dlp sees the URL.
        try:
            ipaddress.ip_address(host)
        except ValueError:
            pass
        else:
            raise ValueError("IP-address URLs are not accepted")

        if host in {"localhost", "localhost.localdomain"} or "." not in host:
            raise ValueError("Local and non-public hostnames are not accepted")
        if not any(host == allowed or host.endswith("." + allowed) for allowed in ALLOWED_HOSTS):
            raise ValueError("This media platform is not supported")
        return value


class DownloadRejected(Exception):
    pass


def check_api_key(provided_key: str | None) -> None:
    expected_key = os.getenv("API_KEY")
    if expected_key and not hmac.compare_digest(provided_key or "", expected_key):
        raise HTTPException(status_code=401, detail="Invalid or missing API key")


def _media_options(request: DownloadRequest, workdir: str) -> dict:
    output_template = str(Path(workdir) / "%(title).100B [%(id)s].%(ext)s")

    def policy_filter(info: dict, *, incomplete: bool = False):
        duration = info.get("duration")
        if duration is not None and duration > MAX_DURATION_SECONDS:
            return "Videos longer than 15 minutes are not allowed"
        filesize = info.get("filesize") or info.get("filesize_approx")
        if filesize is not None and filesize > MAX_FILE_SIZE_BYTES:
            return "Media exceeds the 500 MB limit"
        return None

    common = {
        "outtmpl": output_template,
        "noplaylist": True,
        "max_downloads": 1,
        "max_filesize": MAX_FILE_SIZE_BYTES,
        "match_filter": policy_filter,
        "socket_timeout": 25,
        "retries": 3,
        "fragment_retries": 3,
        "file_access_retries": 2,
        "quiet": True,
        "no_warnings": True,
        "ignoreerrors": False,
        "overwrites": False,
        "restrictfilenames": True,
        "continuedl": True,
        "cachedir": False,
    }

    if request.format == "mp3":
        common.update({
            "format": "bestaudio/best",
            "postprocessors": [{
                "key": "FFmpegExtractAudio",
                "preferredcodec": "mp3",
                "preferredquality": "192",
            }],
        })
        return common

    height = QUALITY_HEIGHTS.get(request.quality)
    if height:
        selector = (
            f"bestvideo[height<={height}]+bestaudio/"
            f"best[height<={height}]/best"
        )
    else:
        selector = "bestvideo+bestaudio/best"

    common.update({
        "format": selector,
        "merge_output_format": "mp4",
        "postprocessors": [{
            "key": "FFmpegVideoRemuxer",
            "preferedformat": "mp4",
        }],
    })
    return common


def download_media(request: DownloadRequest, workdir: str) -> Path:
    try:
        with yt_dlp.YoutubeDL(_media_options(request, workdir)) as downloader:
            result = downloader.extract_info(request.url, download=True)
    except yt_dlp.utils.DownloadError as exc:
        message = str(exc)
        lowered = message.lower()
        if "15 minutes" in lowered or "500 mb" in lowered or "longer than" in lowered:
            raise DownloadRejected(message) from exc
        raise

    if not result:
        raise DownloadRejected("No downloadable media was returned")

    extension = ".mp3" if request.format == "mp3" else ".mp4"
    candidates = [
        path for path in Path(workdir).iterdir()
        if path.is_file() and path.suffix.lower() == extension
        and not path.name.endswith((".part", ".ytdl"))
    ]
    if not candidates:
        # Some extractors expose the final filename in the result metadata.
        requested = downloader.prepare_filename(result)
        candidate = Path(requested)
        if candidate.exists() and candidate.suffix.lower() == extension:
            candidates = [candidate]

    if not candidates:
        raise DownloadRejected("The media processor did not produce the requested file")

    output = max(candidates, key=lambda path: path.stat().st_size)
    size = output.stat().st_size
    if size <= 0:
        raise DownloadRejected("The downloaded file is empty")
    if size > MAX_FILE_SIZE_BYTES:
        raise DownloadRejected("Media exceeds the 500 MB limit")
    return output


@app.get("/")
async def root():
    return {
        "service": APP_NAME,
        "version": APP_VERSION,
        "health": "/health",
        "download": "POST /api/download",
        "docs": "/docs",
    }


@app.get("/health")
async def health():
    return {
        "status": "ok",
        "service": APP_NAME,
        "version": APP_VERSION,
        "ffmpeg_available": shutil.which("ffmpeg") is not None,
    }


@app.post("/api/download")
async def download(
    payload: DownloadRequest,
    x_api_key: str | None = Header(default=None, alias="x-api-key"),
):
    check_api_key(x_api_key)

    if not DOWNLOAD_SLOTS.acquire(blocking=False):
        raise HTTPException(
            status_code=429,
            detail="The download service is busy. Please try again shortly.",
            headers={"Retry-After": "10"},
        )

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
    except DownloadRejected as exc:
        shutil.rmtree(workdir, ignore_errors=True)
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except yt_dlp.utils.DownloadError as exc:
        shutil.rmtree(workdir, ignore_errors=True)
        raise HTTPException(
            status_code=422,
            detail=(
                "Unable to download this media. It may be private, unavailable, "
                "region-restricted, or temporarily unsupported by the platform."
            ),
        ) from exc
    except Exception as exc:
        shutil.rmtree(workdir, ignore_errors=True)
        raise HTTPException(
            status_code=500,
            detail="Media processing failed. Verify FFmpeg and try again.",
        ) from exc
    finally:
        DOWNLOAD_SLOTS.release()
