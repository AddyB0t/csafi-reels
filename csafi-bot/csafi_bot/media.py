"""ffprobe metadata, a small proxy copy for analysis, and key-frame extraction."""
import json
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any


class MediaError(RuntimeError):
    pass


def _run(args: list[str], timeout: int = 600) -> subprocess.CompletedProcess:
    try:
        return subprocess.run(args, capture_output=True, text=True, timeout=timeout, check=True)
    except FileNotFoundError as e:
        raise MediaError(f"{args[0]} is not installed or not on PATH") from e
    except subprocess.CalledProcessError as e:
        raise MediaError(f"{args[0]} failed: {(e.stderr or '')[-400:]}") from e


def have(tool: str) -> bool:
    return shutil.which(tool) is not None


def probe(path: Path) -> dict[str, Any]:
    out = _run(["ffprobe", "-v", "error", "-print_format", "json", "-show_format", "-show_streams", str(path)])
    data = json.loads(out.stdout)
    v = next((s for s in data.get("streams", []) if s.get("codec_type") == "video"), None)
    a = next((s for s in data.get("streams", []) if s.get("codec_type") == "audio"), None)
    if not v:
        raise MediaError("file has no video stream")
    w, h = int(v.get("width") or 0), int(v.get("height") or 0)
    rotation = 0
    for sd in v.get("side_data_list") or []:
        if "rotation" in sd:
            rotation = abs(int(sd["rotation"]))
    if rotation in (90, 270):
        w, h = h, w
    fps = 0.0
    rate = v.get("avg_frame_rate") or v.get("r_frame_rate") or "0/1"
    try:
        num, den = rate.split("/")
        fps = round(float(num) / float(den), 2) if float(den) else 0.0
    except ValueError:
        pass
    duration = float(data.get("format", {}).get("duration") or v.get("duration") or 0)
    return {
        "duration_s": round(duration, 2),
        "width": w,
        "height": h,
        "fps": fps,
        "aspect_ratio": _aspect(w, h),
        "resolution": f"{w}x{h}",
        "has_audio": a is not None,
        "size_mb": round(int(data.get("format", {}).get("size") or 0) / 1_048_576, 2),
    }


def _aspect(w: int, h: int) -> str:
    if not w or not h:
        return "unknown"
    r = w / h
    for name, val in (("9:16", 9 / 16), ("4:5", 4 / 5), ("1:1", 1.0), ("16:9", 16 / 9), ("3:4", 3 / 4)):
        if abs(r - val) < 0.03:
            return name
    return f"{w}:{h}"


def make_proxy(src: Path, dest: Path, height: int = 720) -> Path:
    _run([
        "ffmpeg", "-y", "-v", "error", "-i", str(src),
        "-vf", f"scale=-2:'min({height},ih)'", "-c:v", "libx264", "-preset", "veryfast", "-crf", "28",
        "-c:a", "aac", "-b:a", "96k", "-movflags", "+faststart", str(dest),
    ])
    return dest


@dataclass
class Frame:
    index: int
    t: float
    path: Path


def scene_times(path: Path) -> list[float]:
    """Midpoints of detected scenes. Empty list when PySceneDetect is unavailable."""
    try:
        from scenedetect import ContentDetector, detect
    except ImportError:
        return []
    try:
        scenes = detect(str(path), ContentDetector(threshold=27.0, min_scene_len=10))
    except Exception:  # noqa: BLE001 - fall back to interval sampling
        return []
    return [round((s.get_seconds() + e.get_seconds()) / 2, 2) for s, e in scenes]


def pick_times(duration: float, scenes: list[float], max_frames: int, interval: float = 2.5) -> list[float]:
    """One frame per scene, gaps filled every `interval` seconds, thinned evenly to max_frames."""
    if duration <= 0:
        return [0.0]
    times = sorted(t for t in scenes if 0 <= t < duration)
    t = min(0.5, duration / 2)
    while t < duration:
        if all(abs(t - s) >= interval / 2 for s in times):
            times.append(round(t, 2))
        t += interval
    times = sorted(set(times))
    first = min(0.3, duration / 4)
    if not times or times[0] > 1.0:
        times.insert(0, round(first, 2))
    if len(times) > max_frames:
        step = (len(times) - 1) / (max_frames - 1)
        times = [times[round(i * step)] for i in range(max_frames)]
    return times


def extract_frames(src: Path, out_dir: Path, times: list[float], width: int = 768) -> list[Frame]:
    out_dir.mkdir(parents=True, exist_ok=True)
    frames: list[Frame] = []
    for i, t in enumerate(times, start=1):
        dest = out_dir / f"frame_{i:02d}.jpg"
        _run([
            "ffmpeg", "-y", "-v", "error", "-ss", f"{t:.2f}", "-i", str(src), "-frames:v", "1",
            "-vf", f"scale='min({width},iw)':-2", "-q:v", "3", str(dest),
        ], timeout=120)
        if dest.exists():
            frames.append(Frame(i, t, dest))
    return frames
