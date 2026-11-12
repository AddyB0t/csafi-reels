"""faster-whisper transcription. The model is loaded per reel and freed after, so the
GPU is empty again before the vision model loads."""
import gc
import logging
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)


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

    model, used = _load(model_size, device)
    try:
        segments, info = model.transcribe(
            str(path), language=language or None, vad_filter=True, beam_size=5, condition_on_previous_text=False
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
