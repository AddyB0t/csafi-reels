"""Video download. Meta CDN links first (they expire fast), yt-dlp as fallback.

yt-dlp never receives browser cookies.
"""
import hashlib
import shutil
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0 Safari/537.36"


class DownloadError(RuntimeError):
    pass


@dataclass
class Downloaded:
    path: Path
    sha256: str
    method: str
    meta: dict[str, Any] = field(default_factory=dict)


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _http_download(url: str, dest: Path) -> None:
    tmp = dest.with_suffix(".part")
    with httpx.stream("GET", url, follow_redirects=True, timeout=120, headers={"User-Agent": UA}) as r:
        if r.status_code != 200:
            raise DownloadError(f"HTTP {r.status_code} from media link")
        ctype = r.headers.get("content-type", "")
        if ctype.startswith("text/html"):
            raise DownloadError("media link returned a web page, not a video")
        with open(tmp, "wb") as f:
            for chunk in r.iter_bytes(1 << 16):
                f.write(chunk)
    if tmp.stat().st_size < 10_000:
        tmp.unlink(missing_ok=True)
        raise DownloadError("downloaded file is too small to be a video")
    tmp.replace(dest)


def _ytdlp_download(url: str, dest: Path) -> dict[str, Any]:
    import yt_dlp

    opts = {
        "outtmpl": str(dest.with_suffix(".%(ext)s")),
        "format": "bv*[ext=mp4]+ba[ext=m4a]/b[ext=mp4]/bv*+ba/b",
        "merge_output_format": "mp4",
        "quiet": True,
        "no_warnings": True,
        "noplaylist": True,
        "retries": 2,
        "socket_timeout": 30,
    }
    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(url, download=True) or {}
    produced = sorted(dest.parent.glob(dest.stem + ".*"), key=lambda p: p.stat().st_mtime, reverse=True)
    produced = [p for p in produced if p.suffix not in (".part", ".ytdl") and p != dest]
    if not dest.exists():
        if not produced:
            raise DownloadError("yt-dlp finished but no file was written")
        produced[0].replace(dest)
    return {
        "caption": info.get("description"),
        "creator": info.get("channel") or info.get("uploader_id") or info.get("uploader"),
        "title": info.get("title"),
        "webpage_url": info.get("webpage_url"),
        "duration": info.get("duration"),
    }


def download(
    *, dest: Path, media_url: str | None = None, page_url: str | None = None, local_file: Path | None = None,
    attempts: int = 3,
) -> Downloaded:
    dest.parent.mkdir(parents=True, exist_ok=True)
    if local_file:
        shutil.copyfile(local_file, dest)
        return Downloaded(dest, sha256_file(dest), "local")

    errors: list[str] = []
    plan: list[tuple[str, str]] = []
    if media_url:
        plan.append(("meta-cdn", media_url))
    if page_url:
        plan.append(("yt-dlp", page_url))
    elif media_url:
        plan.append(("yt-dlp", media_url))
    if not plan:
        raise DownloadError("no link to download")

    for method, url in plan:
        for i in range(attempts):
            try:
                meta: dict[str, Any] = {}
                if method == "meta-cdn":
                    _http_download(url, dest)
                else:
                    meta = _ytdlp_download(url, dest)
                return Downloaded(dest, sha256_file(dest), method, meta)
            except Exception as e:  # noqa: BLE001 - every failure is retried then reported
                errors.append(f"{method} try {i + 1}: {str(e)[:200]}")
                time.sleep(2 * (i + 1))
    raise DownloadError("; ".join(errors[-3:]))
