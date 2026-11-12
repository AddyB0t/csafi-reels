"""faster-whisper transcription. The model is loaded per reel and freed after, so the
GPU is empty again before the vision model loads."""
import gc
import logging
import subprocess
from pathlib import Path
from typing import Any

from .media import MediaError

log = logging.getLogger(__name__)
SAMPLE_RATE = 16000


def load_audio(path: Path, sample_rate: int = SAMPLE_RATE):
    """Mono float32 PCM via ffmpeg. faster-whisper's own PyAV decoder breaks on newer PyAV releases."""
    import numpy as np

    try:
        out = subprocess.run(
            ["ffmpeg", "-nostdin", "-v", "error", "-i", str(path), "-vn", "-f", "s16le", "-ac", "1",
             "-ar", str(sample_rate), "-"],
            capture_output=True, check=True, timeout=600,
        )
    except FileNotFoundError as e:
        raise MediaError("ffmpeg is not installed or not on PATH") from e
    except subprocess.CalledProcessError as e:
        raise MediaError(f"ffmpeg could not read the audio: {e.stderr.decode('utf-8', 'replace')[-400:]}") from e
    return np.frombuffer(out.stdout, np.int16).astype(np.float32) / 32768.0


def _load(model_size: str, device: str):
    from faster_whisper import WhisperModel

    if device in ("auto", "cuda"):
        try:
            return WhisperModel(model_size, device="cuda", compute_type="float16"), "cuda"
        except Exception as e:  # noqa: BLE001 - missing CUDA/cuDNN libs are common on Windows
            if device == "cuda":
                raise
            log.info("CUDA not usable for Whisper (%s); using CPU int8", str(e)[:120])
    return WhisperModel(model_size, device="cpu", compute_type="int8"), "cpu"


def transcribe(path: Path, *, model_size: str = "small", device: str = "auto", language: str = "") -> dict[str, Any]:
    try:
        import faster_whisper  # noqa: F401
    except ImportError:
        return {"language": None, "segments": [], "text": "", "note": "faster-whisper is not installed"}

    audio = load_audio(path)
    if audio.size == 0:
        return {"language": None, "segments": [], "text": "", "device": "n/a"}
    model, used = _load(model_size, device)
    try:
        segments, info = model.transcribe(
            audio, language=language or None, vad_filter=True, beam_size=5, condition_on_previous_text=False
        )
        segs = [
            {"start": round(s.start, 1), "end": round(s.end, 1), "text": s.text.strip()}
            for s in segments
            if s.text.strip()
        ]
        return {
            "language": info.language,
            "segments": segs,
            "text": " ".join(s["text"] for s in segs),
            "device": used,
        }
    finally:
        del model
        gc.collect()
