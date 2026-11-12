"""AI stages: per-frame vision notes, then one structured synthesis call.

Facts that code can measure (duration, resolution, links, handles, hashtags) are
measured by code. The model writes the explanation around them.
"""
import json
import logging
import re
import time
from dataclasses import dataclass, field
from typing import Any, Callable

from pydantic import ValidationError

from . import links
from .config import Settings
from .media import Frame
from .ollama import Ollama, OllamaError, image_b64
from .schema import FrameDescription, FrameNote, ReelAnalysis, ResourceNote

log = logging.getLogger(__name__)
Emit = Callable[[str], None]

VISION_PROMPT = (
    "You are looking at one frame from a short vertical video (Instagram reel). "
    "Describe only what is visible. Do not read or guess any text, links or names. "
    "Answer in JSON with: shot_type, setting, action, camera. Keep each field under 15 words."
)

SYSTEM_PROMPT = """You turn the raw material of one Instagram reel into a production breakdown that a
content team can use to recreate it. You receive: video facts, the caption, a timed transcript,
on-screen text read by OCR, notes for sampled frames, and a LINKS list extracted by code.

Strict rules:
- Use only the material given. Never invent links, repos, tools, numbers or creator claims.
- A resource url must be copied exactly from LINKS, or left empty.
- Keep any comment keyword exactly as written in the caption or on-screen text.
- If something is unclear, missing or guessed, add a short note to "uncertainties".
- timed_script: use the transcript segments (clean up obvious errors only).
- scene_shot_list: one entry per distinct scene, using frame notes and on-screen text.
- how_to_recreate: 5 to 10 concrete steps, naming tools only if they appear in the material
  or are generic (phone camera, CapCut-style editor, screen recorder).
- title_topic is a short descriptive topic (max 60 chars), not a sentence.
Answer with JSON only, matching the schema."""


@dataclass
class AnalysisInput:
    facts: dict[str, Any]
    caption: str = ""
    creator_hint: str = ""
    transcript: dict[str, Any] = field(default_factory=dict)
    ocr: dict[str, Any] = field(default_factory=dict)
    frames: list[Frame] = field(default_factory=list)
    frame_notes: list[FrameNote] = field(default_factory=list)

    def all_text(self) -> str:
        parts = [self.caption or "", self.transcript.get("text", "")]
        parts += [ln["text"] for ln in self.ocr.get("lines", [])]
        return "\n".join(parts)


@dataclass
class Extracted:
    urls: list[str]
    handles: list[str]
    hashtags: list[str]


def extract(inp: AnalysisInput) -> Extracted:
    text = inp.all_text()
    return Extracted(links.extract_urls(text), links.extract_handles(text), links.extract_hashtags(inp.caption))


class Analyser:
    def __init__(self, settings: Settings, log_llm: Callable[..., None] | None = None):
        self.s = settings
        self.ollama = Ollama(settings.ollama_url, settings.ollama_timeout_s)
        self._log_llm = log_llm or (lambda **_: None)
        self._label, self._label_at = "", -1e9

    def backend_label(self) -> str:
        """Short status for the dashboard, cached for 15 s."""
        now = time.monotonic()
        if now - self._label_at > 15:
            if self.s.ai_backend == "mock":
                self._label = "Mock mode (no AI)"
            elif self.ollama.available():
                self._label = f"Ollama ready: {self.s.vision_model} + {self.s.text_model}"
            else:
                self._label = "Ollama not running: reels get a mock draft"
            self._label_at = now
        return self._label

    def backend(self) -> str:
        if self.s.ai_backend == "mock":
            return "mock"
        if self.ollama.available():
            return "ollama"
        if self.s.ai_backend == "ollama":
            raise OllamaError(f"Ollama is not running at {self.s.ollama_url}. Start it with: ollama serve")
        return "mock"

    # ---------- vision ----------
    def describe_frames(self, frames: list[Frame], reel_id: int | None, emit: Emit) -> list[FrameNote]:
        notes: list[FrameNote] = []
        model = self.s.vision_model
        try:
            for fr in frames:
                try:
                    res = self.ollama.chat(
                        model,
                        [{"role": "user", "content": VISION_PROMPT, "images": [image_b64(fr.path)]}],
                        fmt=FrameDescription.model_json_schema(),
                        options={"temperature": 0.1, "num_predict": 200, "num_ctx": 4096},
                        think=None,
                    )
                    d = FrameDescription.model_validate_json(res.content)
                    notes.append(FrameNote(t=fr.t, shot_type=d.shot_type, setting=d.setting, action=d.action))
                    self._log_llm(reel_id=reel_id, stage="vision", model=model, seconds=res.seconds, ok=True,
                                  prompt_tokens=res.prompt_tokens, output_tokens=res.output_tokens)
                    emit(f"frame {fr.index} at {fr.t:.1f}s: {d.shot_type}, {d.action}"[:200])
                except (OllamaError, ValidationError, ValueError) as e:
                    self._log_llm(reel_id=reel_id, stage="vision", model=model, seconds=0, ok=False, error=str(e)[:300])
                    emit(f"frame {fr.index}: vision failed ({str(e)[:120]})")
        finally:
            self.ollama.unload(model)
        return notes

    # ---------- synthesis ----------
    def synthesize(self, inp: AnalysisInput, ex: Extracted, reel_id: int | None, emit: Emit) -> ReelAnalysis:
        model = self.s.text_model
        user = build_user_prompt(inp, ex)
        messages: list[dict[str, Any]] = [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user},
        ]
        last_err = ""
        try:
            for attempt in range(3):
                res = self.ollama.chat(
                    model,
                    messages,
                    fmt=ReelAnalysis.model_json_schema(),
                    options={"temperature": 0.2, "num_ctx": self.s.text_num_ctx, "num_predict": 4096},
                    think=False,
                )
                try:
                    data = ReelAnalysis.model_validate_json(_strip_fences(res.content))
                    self._log_llm(reel_id=reel_id, stage="synthesis", model=model, seconds=res.seconds, ok=True,
                                  prompt_tokens=res.prompt_tokens, output_tokens=res.output_tokens)
                    emit(f"synthesis ok in {res.seconds:.0f}s ({res.output_tokens or '?'} tokens)")
                    return data
                except ValidationError as e:
                    last_err = str(e)[:800]
                    self._log_llm(reel_id=reel_id, stage="synthesis", model=model, seconds=res.seconds, ok=False,
                                  error=last_err[:300])
                    emit(f"synthesis attempt {attempt + 1} returned invalid JSON, retrying")
                    messages += [
                        {"role": "assistant", "content": res.content[:4000]},
                        {"role": "user", "content": f"That JSON failed validation:\n{last_err}\nReturn corrected JSON only."},
                    ]
        finally:
            self.ollama.unload(model)
        raise ValueError(f"model output failed validation 3 times: {last_err[:300]}")


def _strip_fences(s: str) -> str:
    s = s.strip()
    if s.startswith("```"):
        s = re.sub(r"^```(?:json)?\s*|\s*```$", "", s)
    return s


def build_user_prompt(inp: AnalysisInput, ex: Extracted) -> str:
    segs = inp.transcript.get("segments") or []
    transcript = "\n".join(f"[{s['start']:.1f}-{s['end']:.1f}] {s['text']}" for s in segs) or "(no speech detected)"
    ocr = "\n".join(f"[{ln['t']:.1f}s] {ln['text']}" for ln in inp.ocr.get("lines", [])) or "(no on-screen text)"
    notes = "\n".join(
        f"[{n.t:.1f}s] shot: {n.shot_type}; setting: {n.setting}; action: {n.action}" for n in inp.frame_notes
    ) or "(no frame notes)"
    facts = {k: inp.facts.get(k) for k in ("duration_s", "resolution", "aspect_ratio", "fps", "has_audio")}
    return "\n\n".join([
        f"VIDEO FACTS: {json.dumps(facts)}",
        f"CREATOR (if known): {inp.creator_hint or 'unknown'}",
        f"CAPTION:\n{inp.caption or '(none)'}",
        f"TRANSCRIPT (language: {inp.transcript.get('language') or 'unknown'}):\n{transcript}",
        f"ON-SCREEN TEXT (OCR):\n{ocr}",
        f"FRAME NOTES:\n{notes}",
        "LINKS (extracted by code, the only allowed urls):\n" + ("\n".join(ex.urls) or "(none)"),
        "HANDLES SEEN: " + (", ".join("@" + h for h in ex.handles) or "(none)"),
        "HASHTAGS: " + (", ".join("#" + h for h in ex.hashtags) or "(none)"),
    ])


def enforce_facts(a: ReelAnalysis, inp: AnalysisInput, ex: Extracted) -> tuple[ReelAnalysis, list[dict[str, str]]]:
    """Replace model-claimed links with code-extracted ones. Returns (analysis, resources)."""
    allowed = {u.lower().rstrip("/"): u for u in ex.urls}
    notes_by_url: dict[str, ResourceNote] = {}
    unverified: list[str] = []
    named_only: list[ResourceNote] = []
    for r in a.resources_mentioned:
        key = (r.url or "").lower().rstrip("/")
        if key and key in allowed:
            notes_by_url[key] = r
        elif r.url:
            unverified.append(r.url)
            named_only.append(ResourceNote(name=r.name, kind=r.kind, url="", how_to_use=r.how_to_use))
        else:
            named_only.append(r)

    resources: list[dict[str, str]] = []
    for key, url in allowed.items():
        note = notes_by_url.get(key)
        resources.append({
            "name": (note.name if note else "") or url.split("//", 1)[-1][:60],
            "kind": (note.kind if note and note.kind else links.classify_url(url)),
            "url": url,
            "how_to_use": note.how_to_use if note else "",
            "found_in": _where(url, inp),
        })
    for r in named_only:
        resources.append({"name": r.name, "kind": r.kind or "mentioned", "url": "", "how_to_use": r.how_to_use,
                          "found_in": "named in the reel, no link visible"})
    for u in unverified:
        a.uncertainties.append(f"The model suggested the link {u}, but it does not appear in the reel, so it was removed.")

    creator = (inp.creator_hint or a.creator_handle or "").strip().lstrip("@")
    if not creator and ex.handles:
        creator = ex.handles[0]
        a.uncertainties.append(f"Creator handle guessed from on-screen text: @{creator}.")
    a.creator_handle = creator
    a.title_topic = a.title_topic.strip()[:60]
    a.tags = [t.strip().lstrip("#").lower() for t in a.tags if t.strip()][:12]
    if a.call_to_action.keyword_if_any and a.call_to_action.keyword_if_any.lower() not in inp.all_text().lower():
        a.uncertainties.append(
            f'Comment keyword "{a.call_to_action.keyword_if_any}" was not found word for word in the caption, transcript or on-screen text.'
        )
    return a, resources


def _where(url: str, inp: AnalysisInput) -> str:
    bare = url.lower().split("//", 1)[-1].removeprefix("www.").rstrip("/")
    places = []
    if bare in (inp.caption or "").lower():
        places.append("caption")
    if any(bare in ln["text"].lower().replace(" ", "") or bare in ln["text"].lower() for ln in inp.ocr.get("lines", [])):
        places.append("on screen")
    if bare in inp.transcript.get("text", "").lower():
        places.append("spoken")
    return ", ".join(places) or "reel text"


def mock_analysis(inp: AnalysisInput, ex: Extracted) -> ReelAnalysis:
    """Offline fallback when Ollama is not running. Built from the measured material only."""
    cap = (inp.caption or "").strip()
    first = re.split(r"[\n.!?]", cap, maxsplit=1)[0].strip() if cap else ""
    first = re.sub(r"[#@]\S+", "", first).strip()
    speech = inp.transcript.get("text", "").strip()
    topic = (first or " ".join(speech.split()[:7]) or "Untitled reel")[:60]
    segs = inp.transcript.get("segments") or []
    ocr_lines = inp.ocr.get("lines", [])
    shots = []
    for n in inp.frame_notes:
        shots.append({"start": n.t, "end": n.t, "visual": f"{n.setting} {n.action}".strip(), "camera": n.shot_type})
    if not shots:
        for ln in ocr_lines[:12]:
            shots.append({"start": ln["t"], "end": ln["t"], "visual": "", "on_screen_text": ln["text"]})
    return ReelAnalysis.model_validate({
        "title_topic": topic,
        "short_description": " ".join(speech.split()[:6]) or "reel breakdown",
        "creator_handle": inp.creator_hint,
        "one_line_summary": (speech[:160] or cap[:160] or "No speech or caption was available."),
        "overview": "Draft generated without a local AI model (Ollama was not running). "
        "It lists the measured material only: transcript, on-screen text and links.",
        "hook": segs[0]["text"] if segs else (ocr_lines[0]["text"] if ocr_lines else ""),
        "timed_script": segs,
        "scene_shot_list": shots,
        "caption_and_hashtags": cap,
        "tags": ex.hashtags[:10],
        "uncertainties": ["Generated in mock mode. Start Ollama and press Retry for a full AI breakdown."],
    })
