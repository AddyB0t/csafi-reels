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


def detect_scenes(path: Path) -> list[tuple[float, float]]:
    """(start, end) of each detected scene. Empty list when PySceneDetect is unavailable."""
    try:
        from scenedetect import ContentDetector, detect
    except ImportError:
        return []
    try:
        scenes = detect(str(path), ContentDetector(threshold=27.0, min_scene_len=10))
    except Exception:  # noqa: BLE001 - fall back to interval sampling
        return []
    return [(round(s.get_seconds(), 2), round(e.get_seconds(), 2)) for s, e in scenes]


def scene_midpoints(scenes: list[tuple[float, float]]) -> list[float]:
    return [round((s + e) / 2, 2) for s, e in scenes]


def pacing_stats(duration: float, scenes: list[tuple[float, float]], segments: list[dict[str, Any]]) -> dict[str, Any]:
    """Measured editing and speech pace, so the model does not have to guess it."""
    cuts = max(0, len(scenes) - 1)
    speech_s = sum(max(0.0, s["end"] - s["start"]) for s in segments)
    words = sum(len(s["text"].split()) for s in segments)
    return {
        "scene_count": len(scenes) or None,
        "cuts": cuts if scenes else None,
        "avg_shot_s": round(duration / len(scenes), 1) if scenes and duration else None,
        "cuts_per_10s": round(cuts / duration * 10, 1) if scenes and duration else None,
        "speech_coverage_pct": round(min(100.0, speech_s / duration * 100)) if duration else 0,
        "words_spoken": words,
        "words_per_minute": round(words / speech_s * 60) if speech_s >= 2 else None,
    }


def storyboard(frames: list["Frame"], dest: Path, tile_width: int = 240) -> Path | None:
    """One grid image of the key frames. Returns None if ffmpeg cannot build it."""
    if not frames:
        return None
    n = len(frames)
    inputs: list[str] = []
    for f in frames:
        inputs += ["-i", str(f.path)]
    scaled = ";".join(f"[{i}:v]scale={tile_width}:-2,setsar=1[v{i}]" for i in range(n))
    if n == 1:
        graph = f"{scaled};[v0]null[out]"
    else:
        stack = "".join(f"[v{i}]" for i in range(n))
        graph = f"{scaled};{stack}xstack=inputs={n}:layout={_grid(n, min(n, 7))}:fill=black[out]"
    try:
        _run(["ffmpeg", "-y", "-v", "error", *inputs, "-filter_complex", graph,
              "-map", "[out]", "-frames:v", "1", "-q:v", "3", str(dest)], timeout=120)
    except MediaError:
        return None
    return dest if dest.exists() else None


def _grid(n: int, cols: int) -> str:
    """xstack layout for n equal tiles, `cols` per row."""
    cells = []
    for i in range(n):
        c, r = i % cols, i // cols
        cells.append(f"{'+'.join(['w0'] * c) or '0'}_{'+'.join(['h0'] * r) or '0'}")
    return "|".join(cells)


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


def ocr_times(duration: float, interval: float = 1.0, max_frames: int = 60) -> list[float]:
    """Dense, even sampling for on-screen text, which often shows for only a second or two."""
    if duration <= 0:
        return [0.0]
    step = max(interval, duration / max_frames)
    times, t = [], min(0.3, duration / 4)
    while t < duration and len(times) < max_frames:
        times.append(round(t, 2))
        t += step
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
