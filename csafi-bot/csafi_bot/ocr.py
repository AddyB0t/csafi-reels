"""On-screen text via RapidOCR (PaddleOCR models on ONNX Runtime, CPU, no torch).

Small vision models misread text, so URLs and exact wording come from OCR."""
import gc
import re
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any

MIN_SCORE = 0.5
SAME_TEXT_RATIO = 0.85


def _norm(s: str) -> str:
    return re.sub(r"\W+", "", s.lower())


def _words(s: str) -> list[str]:
    return re.findall(r"\w+", s.lower())


def _same(a: str, b: str) -> bool:
    if a == b:
        return True
    m = SequenceMatcher(None, a, b)
    return m.real_quick_ratio() >= SAME_TEXT_RATIO and m.quick_ratio() >= SAME_TEXT_RATIO and m.ratio() >= SAME_TEXT_RATIO


def group_lines(per_frame: list[tuple[float, list[str]]], frame_gap: float) -> list[dict[str, Any]]:
    """Merge readings of the same text across frames into blocks with a start and end time.

    OCR jitters between frames ("Step 1: Opne" vs "Step 1: Open"), so near-identical lines are
    merged and the longest reading is kept."""
    blocks: list[dict[str, Any]] = []
    for t, lines in per_frame:
        for ln in lines:
            key = _norm(ln)
            match = next((b for b in blocks if _same(b["key"], key)), None)
            if match:
                match["end"] = max(match["end"], t)
                match["seen"] += 1
                if len(ln) > len(match["text"]):
                    match["text"], match["key"] = ln, key
            else:
                blocks.append({"t": t, "end": t, "text": ln, "key": key, "seen": 1})
    for b in blocks:
        b["end"] = round(b["end"] + frame_gap, 2)
        del b["key"]
    blocks.sort(key=lambda b: b["t"])
    return blocks


def mark_subtitles(lines: list[dict[str, Any]], transcript_text: str, min_ratio: float = 0.8) -> int:
    """Flag text blocks that only repeat the speech (burned-in subtitles). Returns how many."""
    spoken = set(_words(transcript_text))
    count = 0
    for ln in lines:
        words = _words(ln["text"])
        ln["subtitle"] = bool(spoken and words and sum(w in spoken for w in words) / len(words) >= min_ratio)
        count += ln["subtitle"]
    return count


def ocr_frames(frame_paths: list[tuple[int, float, Path]]) -> dict[str, Any]:
    """Input: (index, time, path). Returns per-frame lines and unique text blocks in order of appearance."""
    try:
        from rapidocr_onnxruntime import RapidOCR
    except ImportError:
        return {"per_frame": {}, "lines": [], "note": "rapidocr-onnxruntime is not installed"}

    engine = RapidOCR()
    per_frame: dict[int, list[str]] = {}
    timed: list[tuple[float, list[str]]] = []
    try:
        for idx, t, path in frame_paths:
            result, _ = engine(str(path))
            lines = [str(text).strip() for _box, text, score in (result or []) if float(score) >= MIN_SCORE]
            lines = [ln for ln in lines if len(_norm(ln)) >= 2]
            per_frame[idx] = lines
            timed.append((t, lines))
    finally:
        del engine
        gc.collect()
    times = sorted(t for t, _ in timed)
    gap = round(min((b - a for a, b in zip(times, times[1:])), default=1.0), 2)
    return {"per_frame": per_frame, "lines": group_lines(timed, gap)}
