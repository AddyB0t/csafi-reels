"""On-screen text via RapidOCR (PaddleOCR models on ONNX Runtime, CPU, no torch).

Small vision models misread text, so URLs and exact wording come from OCR."""
import gc
import re
from pathlib import Path
from typing import Any

MIN_SCORE = 0.6


def _norm(s: str) -> str:
    return re.sub(r"\W+", "", s.lower())


def ocr_frames(frame_paths: list[tuple[int, float, Path]]) -> dict[str, Any]:
    """Input: (index, time, path). Returns per-frame lines and unique lines in order of appearance."""
    try:
        from rapidocr_onnxruntime import RapidOCR
    except ImportError:
        return {"per_frame": {}, "lines": [], "note": "rapidocr-onnxruntime is not installed"}

    engine = RapidOCR()
    per_frame: dict[int, list[str]] = {}
    unique: list[dict[str, Any]] = []
    seen: set[str] = set()
    try:
        for idx, t, path in frame_paths:
            result, _ = engine(str(path))
            lines = [str(text).strip() for _box, text, score in (result or []) if float(score) >= MIN_SCORE]
            lines = [ln for ln in lines if len(_norm(ln)) >= 2]
            per_frame[idx] = lines
            for ln in lines:
                key = _norm(ln)
                if key not in seen:
                    seen.add(key)
                    unique.append({"t": t, "text": ln})
    finally:
        del engine
        gc.collect()
    return {"per_frame": per_frame, "lines": unique}
