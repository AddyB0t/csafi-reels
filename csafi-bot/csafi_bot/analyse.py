"""AI stages: per-frame vision notes, then two structured text passes.

Pass 1 (content) writes what the reel says. Pass 2 (production) writes how it is made. Two small
answers are far more complete than one large one on local 7-8B models.

Facts that code can measure (duration, cuts, script, links, handles, hashtags, opening line) are
measured by code. The model writes the explanation around them.
"""
import json
import logging
import math
import re
import time
from dataclasses import dataclass, field
from typing import Any, Callable, TypeVar

from pydantic import BaseModel, ValidationError

from . import links
from .config import Settings
from .media import Frame
from .ollama import Ollama, OllamaError, image_b64
from .schema import (
    ContentPass,
    FrameDescription,
    FrameNote,
    ProductionPass,
    ReelAnalysis,
    ResourceNote,
    TimedLine,
    strict_schema,
)

log = logging.getLogger(__name__)
Emit = Callable[[str], None]
T = TypeVar("T", bound=BaseModel)

VISION_PROMPT = """This is one frame, taken at {t:.1f} seconds, from a short vertical video (an Instagram reel).
Describe it for a video producer who cannot see it. Be concrete and stay with what is visible.

- shot_type: close-up, medium, wide, screen recording, overhead, slide or similar.
- subject: who or what the frame is about, for example "man in a black hoodie talking to the camera"
  or "laptop screen showing a code editor".
- setting: where it is: a room, a desk, outdoors, a studio, a plain background, a full-screen app.
- action: what is happening at this moment.
- visible_items: apps, websites, devices, products, logos, charts or props you can identify with
  confidence, for example "terminal window", "spreadsheet", "smartphone". Write "none" if there are none.
- text_style: how any text overlay looks (position, colour, size, subtitles or headline). Describe
  the look, not the words.
- camera: static, handheld, pan, zoom or similar.

Text that OCR read in this frame: {ocr}
Use that text to work out what the screen is showing. Do not copy it out.

Keep every field under 25 words. If you cannot tell what something is, say so instead of guessing
a name. Answer with one JSON object and nothing else."""

CONTENT_PROMPT = """You are writing the content notes for one Instagram reel. A content team reads your notes
instead of watching the reel, then decides whether to make their own version. If a tip, a number,
a tool name or a step is missing from your notes, the team never learns it. Completeness matters
more than brevity.

All you know about the reel is the material in the user message. Software measured it from the
video file:
- CAPTION: the text the creator posted with the reel.
- TRANSCRIPT: the speech, with start and end times in seconds.
- ON-SCREEN TEXT: text read from the picture by OCR, with the seconds it was visible. Subtitles
  that only repeat the speech were left out.
- FRAME NOTES: what a vision model saw in sampled frames.
- LINKS, HANDLES, HASHTAGS: found by code in the text above.

How to read the material
- Speech recognition often mishears the names of tools, brands and people. When the on-screen text
  or the caption spells a name differently from the transcript, trust the screen or the caption.
- OCR can drop or swap a letter. Fix an obvious slip, but do not rewrite the text.
- Many reels have no speech, only music and text on screen. Then the on-screen text and the
  caption carry the message. Treat them as the script.
- The reel can be in Hindi, Hinglish or any other language. Write your notes in English. Keep
  call_to_action.exact_text and keyword_if_any in the original words.

What to write, in this order
1. key_points: the substance of the reel as a list. Go through the transcript and the on-screen
   text from start to finish and write one item for every tip, step, instruction, command,
   setting, fact, number, price, result, claim or example. Each item is one complete sentence
   that makes sense on its own and keeps the specifics: exact names, numbers and steps. For a
   tutorial, write the steps so that someone could follow them. Leave out greetings, filler and
   requests to like or follow. A 30-second talking reel normally gives 5 to 12 items. A reel with
   almost no words may give 1 to 3.
   Too vague: "She shares a tip about cooking pasta."
   Useful: "Salt the pasta water until it tastes like the sea, about 1 tablespoon per litre."
   (That example is from a different reel. Never reuse it.)
2. tools_and_products: every app, website, AI model, software library, device, product, brand,
   company, book or person that is named in the speech, the on-screen text or the caption, or
   that a frame note clearly identifies. For each one give what it is, how this reel uses or
   describes it, and where it appeared (spoken, on screen, caption, frame). Use an empty list if
   nothing is named.
3. call_to_action: what the creator asks the viewer to do. type is one of comment, follow, like,
   save, share, link-in-bio, dm, none. If viewers must comment or DM a specific word to get
   something, put that word in keyword_if_any exactly as written. exact_text is the creator's
   request, word for word.
4. resources_mentioned: things the viewer is told to get or visit, such as repos, downloads,
   templates, courses, websites, prompts and files. Set url only by copying a line from LINKS
   character for character. If the resource has no line in LINKS, leave url empty. how_to_use
   says what the reel tells you to do with it.
5. target_audience: who the reel is for, in one sentence.
6. overview: 3 to 5 sentences. What the reel is about, what it shows or teaches, and the result
   it promises. Concrete, no hype.
7. one_line_summary: one sentence of at most 25 words that states the main takeaway.
8. title_topic: a short topic label of at most 60 characters, like a folder label. Not a
   sentence, no emoji.
9. short_description: 4 to 8 plain words on the format or angle, such as "three-step setup
   walkthrough" or "talking-head myth busting".
10. creator_handle: the creator's Instagram handle if CREATOR gives it, otherwise empty.
11. tags: 5 to 10 lowercase search keywords covering the topic, the tools and the format. No "#".
12. uncertainties: anything you could not confirm, in short sentences: garbled speech, names you
    were unsure of, a missing caption, things the reel refers to but does not show.

Rules
- Use only the material. Do not add facts, numbers, tools or links from your own knowledge, even
  when you recognise the topic.
- If the material is thin, write less and say so in uncertainties. Never pad.
- Answer with one JSON object that matches the schema, and nothing else."""

PRODUCTION_PROMPT = """You are writing the production notes for one Instagram reel. A content team uses your
notes to film and edit their own version without watching the original. They need to know what is
on screen second by second, how it is edited, and why it holds attention.

All you know about the reel is the material in the user message. Software measured it from the
video file:
- VIDEO FACTS and PACING: duration, cuts and speech rate. These numbers are exact. Use them and
  do not estimate your own.
- OPENING: the exact first words spoken and the first text shown.
- CONTENT SUMMARY: what the reel says, already written.
- TIMELINE: the reel split into time blocks at the detected cuts. Each block lists what is said,
  what text is on screen, and what a vision model saw in a frame from that block.
- CAPTION.

What to write, in this order
1. scene_shot_list: one entry per TIMELINE block, in order, with that block's start and end
   times. visual is what the viewer sees: subject, setting, action, and any app or object on
   screen. camera is the framing and movement, for example "close-up, static" or "screen
   recording". on_screen_text is the text shown in that block, copied from the timeline.
   transition is how the reel moves to the next block when the material shows it (hard cut,
   zoom, swipe), otherwise "cut". Neighbouring blocks that show the same thing can be merged
   into one entry.
2. audio: music says whether there is background music only when the material indicates it,
   otherwise "not determined from the material". voiceover says who speaks and in what style
   (to camera, voiceover on a screen recording, no speech) and gives the measured words per
   minute. sfx and notes hold only what the material supports.
3. format_specs: captions_style describes how text appears on screen (burned-in subtitles or
   not, headline text, position, colour, size), from the frame notes and the SUBTITLES fact.
   pacing is one or two sentences built on the measured cuts and average shot length.
   editing_techniques lists the concrete techniques visible in the material, such as jump cuts,
   screen recording, zoom-ins, B-roll, split screen, text pop-ups or picture-in-picture.
4. hook: two or three sentences on the first 3 seconds. Say what is said and shown, using
   OPENING, and what makes a viewer stop scrolling.
5. why_it_works: 3 to 5 reasons this reel holds attention or gets shared. Each reason points at
   something specific in this reel: a line, a visual, the pacing, the call to action. No generic
   advice.
6. how_to_recreate: 6 to 12 steps, as plain sentences without numbers, that the team can follow
   to make their own version on the same topic. Cover what to prepare, what to film or
   screen-record for each part, what to say (the script beats in order), what text to put on
   screen and when, how to edit (pacing, cuts, captions), and how to end (the call to action).
   Name a specific tool only if the reel shows it. Otherwise use generic terms such as phone
   camera, screen recorder or video editor.
7. uncertainties: what you could not determine about the production from the material.

Rules
- Describe only what the material supports. Frame notes cover single frames, so do not claim
  movement or effects that are not indicated.
- Write in English.
- Answer with one JSON object that matches the schema, and nothing else."""


@dataclass
class AnalysisInput:
    facts: dict[str, Any]
    caption: str = ""
    creator_hint: str = ""
    transcript: dict[str, Any] = field(default_factory=dict)
    ocr: dict[str, Any] = field(default_factory=dict)
    frames: list[Frame] = field(default_factory=list)
    frame_notes: list[FrameNote] = field(default_factory=list)
    scenes: list[tuple[float, float]] = field(default_factory=list)
    pacing: dict[str, Any] = field(default_factory=dict)

    @property
    def segments(self) -> list[dict[str, Any]]:
        return self.transcript.get("segments") or []

    @property
    def ocr_lines(self) -> list[dict[str, Any]]:
        return self.ocr.get("lines") or []

    @property
    def screen_text(self) -> list[dict[str, Any]]:
        """On-screen text that is not just a subtitle of the speech."""
        return [ln for ln in self.ocr_lines if not ln.get("subtitle")]

    def ocr_at(self, t: float, slack: float = 0.6) -> list[str]:
        return [ln["text"] for ln in self.ocr_lines if ln["t"] - slack <= t <= ln.get("end", ln["t"]) + slack]

    def all_text(self) -> str:
        parts = [self.caption or "", self.transcript.get("text", "")]
        parts += [ln["text"] for ln in self.ocr_lines]
        return "\n".join(parts)

    def opening(self) -> tuple[str, str]:
        """(first words spoken, text on screen in the first 3 seconds)."""
        spoken = next((s["text"] for s in self.segments if s["start"] <= 5), "")
        early = [ln for ln in self.ocr_lines if ln["t"] <= 3.0]
        shown = [ln["text"] for ln in early if not ln.get("subtitle")] or [ln["text"] for ln in early]
        return spoken, " / ".join(shown[:3])


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
    def describe_frames(
        self, frames: list[Frame], reel_id: int | None, emit: Emit, ocr_at: Callable[[float], list[str]] | None = None
    ) -> list[FrameNote]:
        notes: list[FrameNote] = []
        model = self.s.vision_model
        schema = strict_schema(FrameDescription)
        try:
            for fr in frames:
                try:
                    seen = "; ".join((ocr_at(fr.t) if ocr_at else [])[:8])[:400] or "(none)"
                    res = self.ollama.chat(
                        model,
                        [{"role": "user", "content": VISION_PROMPT.format(t=fr.t, ocr=seen),
                          "images": [image_b64(fr.path)]}],
                        fmt=schema,
                        options={"temperature": 0.1, "num_predict": 400, "num_ctx": 6144},
                        think=None,
                    )
                    d = FrameDescription.model_validate_json(_strip_fences(res.content))
                    notes.append(FrameNote(t=fr.t, **d.model_dump()))
                    self._log_llm(reel_id=reel_id, stage="vision", model=model, seconds=res.seconds, ok=True,
                                  prompt_tokens=res.prompt_tokens, output_tokens=res.output_tokens)
                    emit(f"frame {fr.index} at {fr.t:.1f}s: {d.shot_type}, {d.subject or d.action}"[:200])
                except (OllamaError, ValidationError, ValueError) as e:
                    self._log_llm(reel_id=reel_id, stage="vision", model=model, seconds=0, ok=False, error=str(e)[:300])
                    emit(f"frame {fr.index}: vision failed ({str(e)[:120]})")
        finally:
            self.ollama.unload(model)
        return notes

    # ---------- synthesis ----------
    def synthesize(self, inp: AnalysisInput, ex: Extracted, reel_id: int | None, emit: Emit) -> ReelAnalysis:
        model = self.s.text_model
        try:
            content = self._structured(
                model, CONTENT_PROMPT, build_content_prompt(inp, ex), ContentPass, "content", reel_id, emit,
                check=lambda c: thin_content(c, inp),
            )
            try:
                production = self._structured(
                    model, PRODUCTION_PROMPT, build_production_prompt(inp, content), ProductionPass, "production",
                    reel_id, emit,
                )
            except (ValueError, OllamaError) as e:
                # The content notes are the valuable half. File them rather than fail the reel.
                emit(f"production notes failed, filing content notes only ({str(e)[:160]})")
                production = ProductionPass(
                    uncertainties=["The production notes (shot list, recreation steps) could not be generated. "
                                   "Press Re-run to try again."]
                )
        finally:
            self.ollama.unload(model)
        merged = {**production.model_dump(), **content.model_dump()}
        merged["uncertainties"] = [*content.uncertainties, *production.uncertainties]
        return ReelAnalysis.model_validate(merged)

    def _structured(
        self, model: str, system: str, user: str, schema: type[T], stage: str, reel_id: int | None, emit: Emit,
        check: Callable[[T], str | None] | None = None,
    ) -> T:
        """One schema-constrained answer. Invalid JSON is retried; a thin answer is questioned once."""
        messages: list[dict[str, Any]] = [{"role": "system", "content": system}, {"role": "user", "content": user}]
        fmt = strict_schema(schema)
        last_err, first = "", None
        for attempt in range(3):
            res = self.ollama.chat(
                model, messages, fmt=fmt,
                options={"temperature": 0.2, "num_ctx": self.s.text_num_ctx, "num_predict": 4096},
                think=False,
            )
            try:
                data = schema.model_validate_json(_strip_fences(res.content))
            except ValidationError as e:
                last_err = str(e)[:800]
                self._log_llm(reel_id=reel_id, stage=stage, model=model, seconds=res.seconds, ok=False, error=last_err[:300])
                emit(f"{stage} notes: attempt {attempt + 1} returned invalid JSON, retrying")
                messages += [
                    {"role": "assistant", "content": res.content[:4000]},
                    {"role": "user", "content": f"That JSON failed validation:\n{last_err}\nReturn the corrected JSON only."},
                ]
                continue
            self._log_llm(reel_id=reel_id, stage=stage, model=model, seconds=res.seconds, ok=True,
                          prompt_tokens=res.prompt_tokens, output_tokens=res.output_tokens)
            emit(f"{stage} notes written in {res.seconds:.0f}s ({res.output_tokens or '?'} tokens)")
            complaint = check(data) if check else None
            if first is not None:
                # Second opinion after a complaint: keep whichever answer says more.
                return data if not complaint or len(data.model_dump_json()) >= len(first.model_dump_json()) else first
            if not complaint:
                return data
            first = data
            emit(f"{stage} notes look thin, asking the model to go through the material again")
            messages += [
                {"role": "assistant", "content": res.content[:6000]},
                {"role": "user", "content": f"{complaint}\nReturn the full JSON again, complete this time."},
            ]
        if first is not None:
            return first
        raise ValueError(f"{stage} notes failed validation 3 times: {last_err[:300]}")


def thin_content(c: ContentPass, inp: AnalysisInput) -> str | None:
    """A complaint for the model when key_points is clearly shorter than the material supports."""
    words = len(inp.transcript.get("text", "").split())
    shown = len(inp.screen_text)
    if words >= 60 or shown >= 6:
        expected = 3
    elif words >= 12 or shown >= 2 or len(inp.caption or "") >= 80:
        expected = 1
    else:
        expected = 0
    if len(c.key_points) >= expected:
        return None
    return (
        f"key_points has only {len(c.key_points)} item(s), but the transcript has {words} words and there are "
        f"{shown} lines of on-screen text. Go through the transcript and the on-screen text again from start to "
        "finish and list every tip, step, fact, number and example."
    )


def _strip_fences(s: str) -> str:
    s = s.strip()
    if s.startswith("```"):
        s = re.sub(r"^```(?:json)?\s*|\s*```$", "", s)
    return s


def _clip(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[:limit].rsplit("\n", 1)[0] + "\n[... cut for length]"


def _span(ln: dict[str, Any]) -> str:
    end = ln.get("end", ln["t"])
    return f"[{ln['t']:.1f}-{end:.1f}s]" if end > ln["t"] else f"[{ln['t']:.1f}s]"


def _note(n: FrameNote, fields: tuple[str, ...]) -> str:
    parts = []
    for name in fields:
        value = (getattr(n, name) or "").strip()
        if value and value.lower() not in ("none", "n/a", "unknown"):
            parts.append(f"{name.replace('_', ' ')}: {value}")
    return "; ".join(parts)


def _section(title: str, body: str) -> str:
    return f"### {title}\n{body}"


def build_content_prompt(inp: AnalysisInput, ex: Extracted) -> str:
    transcript = "\n".join(f"[{s['start']:.1f}-{s['end']:.1f}] {s['text']}" for s in inp.segments)
    shown = "\n".join(f"{_span(ln)} {ln['text']}" for ln in inp.screen_text)
    subtitles = len(inp.ocr_lines) - len(inp.screen_text)
    if subtitles:
        shown += f"\n({subtitles} more lines were burned-in subtitles that repeat the speech. They are left out.)"
    notes = "\n".join(
        f"[{n.t:.1f}s] {_note(n, ('subject', 'action', 'visible_items', 'setting'))}" for n in inp.frame_notes
    )
    lang = inp.transcript.get("language") or "unknown"
    return "\n\n".join([
        _section("CREATOR", "@" + inp.creator_hint if inp.creator_hint else "unknown"),
        _section("CAPTION", _clip(inp.caption, 3000) or "(no caption was provided)"),
        _section(f"TRANSCRIPT (language: {lang}, {inp.facts.get('duration_s')} s video)",
                 _clip(transcript, 9000) or "(no speech detected)"),
        _section("ON-SCREEN TEXT", _clip(shown, 4000) or "(no on-screen text detected)"),
        _section("FRAME NOTES", _clip(notes, 4000) or "(no frame notes)"),
        _section("LINKS (the only urls you may use)", "\n".join(ex.urls) or "(none)"),
        _section("HANDLES", ", ".join("@" + h for h in ex.handles) or "(none)"),
        _section("HASHTAGS", ", ".join("#" + h for h in ex.hashtags) or "(none)"),
        "Write the content notes for this reel now. Start with key_points and miss nothing.",
    ])


def build_timeline(inp: AnalysisInput, max_blocks: int = 14) -> list[dict[str, Any]]:
    """The reel as time blocks, cut at the detected scene changes, each with its speech, text and frame notes."""
    duration = float(inp.facts.get("duration_s") or 0)
    spans = [(s, e) for s, e in inp.scenes if e > s]
    if len(spans) < 2:
        # No cuts found: one block around each sampled frame.
        times = sorted({n.t for n in inp.frame_notes} or {f.t for f in inp.frames})
        edges = [0.0, *[round((a + b) / 2, 2) for a, b in zip(times, times[1:])], max(duration, times[-1] if times else 0)]
        spans = [(a, b) for a, b in zip(edges, edges[1:]) if b > a] or [(0.0, duration)]
    if len(spans) > max_blocks:
        size = math.ceil(len(spans) / max_blocks)
        spans = [(spans[i][0], spans[min(i + size, len(spans)) - 1][1]) for i in range(0, len(spans), size)]

    blocks = []
    for i, (start, end) in enumerate(spans):
        last = i == len(spans) - 1

        def inside(t: float) -> bool:
            return start <= t < end or (last and t >= start)

        blocks.append({
            "start": start,
            "end": end,
            "said": " ".join(s["text"] for s in inp.segments if inside((s["start"] + s["end"]) / 2)),
            "text": [ln["text"] for ln in inp.screen_text if inside(ln["t"])],
            "notes": [n for n in inp.frame_notes if inside(n.t)],
        })
    return blocks


def build_production_prompt(inp: AnalysisInput, content: ContentPass) -> str:
    facts = {k: inp.facts.get(k) for k in ("duration_s", "resolution", "aspect_ratio", "fps", "has_audio")}
    pacing = {k: v for k, v in inp.pacing.items() if v is not None}
    subtitles = len(inp.ocr_lines) - len(inp.screen_text)
    spoken, shown = inp.opening()

    blocks = []
    for b in build_timeline(inp):
        lines = [f"[{b['start']:.1f}-{b['end']:.1f}s]"]
        lines.append(f"  said: {b['said']}" if b["said"] else "  said: (nothing)")
        if b["text"]:
            lines.append("  on-screen text: " + " | ".join(b["text"][:6]))
        for n in b["notes"][:2]:
            lines.append(f"  frame at {n.t:.1f}s: {_note(n, ('shot_type', 'subject', 'setting', 'action', 'visible_items', 'text_style', 'camera'))}")
        blocks.append("\n".join(lines))

    summary = "\n".join([
        f"Topic: {content.title_topic}",
        f"Summary: {content.one_line_summary}",
        "Key points:",
        *[f"- {p}" for p in content.key_points[:14]],
        f"Call to action: {content.call_to_action.type}"
        + (f' ("{content.call_to_action.exact_text}")' if content.call_to_action.exact_text else ""),
    ])
    return "\n\n".join([
        _section("VIDEO FACTS", json.dumps(facts)),
        _section("PACING (measured)", json.dumps(pacing) if pacing else "(not measured)"),
        _section("SUBTITLES", f"{subtitles} of {len(inp.ocr_lines)} on-screen text lines repeat the speech, so the reel "
                 + ("has burned-in subtitles." if subtitles >= 3 else "has no clear burned-in subtitles.")),
        _section("OPENING", f'first words spoken: "{spoken}"\nfirst text on screen: "{shown}"'),
        _section("CONTENT SUMMARY", _clip(summary, 3000)),
        _section("TIMELINE", _clip("\n".join(blocks), 12000) or "(no timeline)"),
        _section("CAPTION", _clip(inp.caption, 1500) or "(no caption was provided)"),
        "Write the production notes for this reel now. Start with scene_shot_list, one entry per timeline block.",
    ])


def _dedupe(items: list[str], limit: int) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for item in items:
        text = " ".join((item or "").split()).lstrip("-•*0123456789.) ").strip()
        if text and text.lower() not in seen:
            seen.add(text.lower())
            out.append(text)
    return out[:limit]


def enforce_facts(a: ReelAnalysis, inp: AnalysisInput, ex: Extracted) -> tuple[ReelAnalysis, list[dict[str, str]]]:
    """Replace model-claimed facts with measured ones. Returns (analysis, resources)."""
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
    a.key_points = _dedupe(a.key_points, 24)
    a.why_it_works = _dedupe(a.why_it_works, 8)
    a.how_to_recreate = _dedupe(a.how_to_recreate, 14)
    a.uncertainties = _dedupe(a.uncertainties, 12)
    tools, names = [], set()
    for t in a.tools_and_products:
        name = t.name.strip()
        if name and name.lower() not in names:
            names.add(name.lower())
            tools.append(t)
    a.tools_and_products = tools

    # Measured, never model-written.
    a.timed_script = [TimedLine(**s) for s in inp.segments]
    a.caption_and_hashtags = (inp.caption or "").strip()
    a.hook_line, a.hook_on_screen_text = inp.opening()

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
    if any(bare in ln["text"].lower().replace(" ", "") or bare in ln["text"].lower() for ln in inp.ocr_lines):
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
    shots = []
    for b in build_timeline(inp):
        note = b["notes"][0] if b["notes"] else None
        if not (note or b["text"]):
            continue
        shots.append({
            "start": b["start"], "end": b["end"],
            "visual": _note(note, ("subject", "setting", "action", "visible_items")) if note else "",
            "camera": _note(note, ("shot_type", "camera")) if note else "",
            "on_screen_text": " | ".join(b["text"][:4]),
        })
    return ReelAnalysis.model_validate({
        "title_topic": topic,
        "short_description": " ".join(speech.split()[:6]) or "reel breakdown",
        "creator_handle": inp.creator_hint,
        "one_line_summary": (speech[:160] or cap[:160] or "No speech or caption was available."),
        "overview": "Draft generated without a local AI model (Ollama was not running). "
        "It lists the measured material only: transcript, on-screen text and links.",
        "scene_shot_list": shots,
        "tags": ex.hashtags[:10],
        "uncertainties": ["Generated in mock mode. Start Ollama and press Retry for a full AI breakdown."],
    })
