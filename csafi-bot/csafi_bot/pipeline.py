"""One reel, end to end. GPU stages run one after another and each frees its model."""
import logging
import shutil
import threading
import time
from pathlib import Path
from typing import Any

from . import library, media, naming
from .analyse import AnalysisInput, Analyser, enforce_facts, extract, mock_analysis
from .config import Settings
from .db import DB, now
from .download import download
from .ocr import mark_subtitles, ocr_frames
from .transcribe import hint_prompt, transcribe

log = logging.getLogger(__name__)


class Pipeline:
    def __init__(self, settings: Settings, db: DB):
        self.s = settings
        self.db = db
        self.analyser = Analyser(settings, log_llm=db.log_llm)
        self.index_lock = threading.Lock()

    def process(self, reel: dict[str, Any], local_file: Path | None = None) -> dict[str, Any]:
        rid = reel["id"]
        started = time.monotonic()
        stage = "download"

        def emit(msg: str, level: str = "info") -> None:
            log.info("reel %s: %s", rid, msg)
            self.db.event(rid, msg, level)

        work = self.s.work_dir / f"reel-{rid}"
        work.mkdir(parents=True, exist_ok=True)
        if local_file is None and reel.get("local_path"):
            local_file = Path(reel["local_path"])
        try:
            if local_file is not None and not local_file.is_file():
                raise FileNotFoundError(f"uploaded file is missing: {local_file}")
            # 1. download
            self.db.update(rid, status="downloading", stage=stage)
            emit("downloading")
            got = download(
                dest=work / "original.mp4",
                media_url=reel.get("media_url"),
                page_url=reel.get("source_url"),
                local_file=local_file,
            )
            emit(f"downloaded via {got.method}")
            caption = reel.get("caption") or got.meta.get("caption") or ""
            creator = (got.meta.get("creator") or reel.get("creator") or "").strip().lstrip("@")
            self.db.update(rid, video_sha256=got.sha256, caption=caption or None)
            dup = self.db.find_filed_by_sha(got.sha256, rid)
            if dup:
                self.db.update(rid, status="duplicate", stage=None, duplicate_of=dup["id"], folder_name=None)
                emit(f"same video as reel #{dup['id']} ({dup['folder_name']}); skipped")
                return self.db.get(rid)

            # 2. analyse
            stage = "probe"
            self.db.update(rid, status="analysing", stage=stage)
            facts = media.probe(got.path)
            self.db.update(rid, duration_s=facts["duration_s"])
            emit(f"{facts['duration_s']}s, {facts['resolution']}, {facts['fps']} fps")
            # The small proxy is only for scene detection. Frames, OCR and audio use the original,
            # because small on-screen text is unreadable after downscaling.
            proxy = media.make_proxy(got.path, work / "proxy.mp4")

            inp = AnalysisInput(facts=facts, caption=caption, creator_hint=creator)

            stage = "frames"
            self.db.update(rid, stage=stage)
            inp.scenes = media.detect_scenes(proxy)
            times = media.pick_times(facts["duration_s"], media.scene_midpoints(inp.scenes), self.s.max_frames)
            inp.frames = media.extract_frames(got.path, work / "frames", times, self.s.frame_width)
            emit(f"{len(inp.scenes) or 'no'} scenes detected, {len(inp.frames)} key frames")

            stage = "ocr"
            self.db.update(rid, stage=stage)
            if self.s.ocr_enabled:
                dense = media.extract_frames(
                    got.path, work / "ocr",
                    media.ocr_times(facts["duration_s"], self.s.ocr_interval_s, self.s.ocr_max_frames),
                    self.s.ocr_frame_width,
                )
                emit(f"reading on-screen text in {len(dense)} frames")
                inp.ocr = ocr_frames([(f.index, f.t, f.path) for f in dense])
                emit(f"OCR: {len(inp.ocr.get('lines', []))} text blocks"
                     + (f" ({inp.ocr['note']})" if inp.ocr.get("note") else ""))

            stage = "transcribe"
            self.db.update(rid, stage=stage)
            if self.s.transcribe_enabled and facts["has_audio"]:
                emit(f"transcribing with Whisper {self.s.whisper_model}")
                hints = hint_prompt(caption, creator, *[ln["text"] for ln in inp.ocr.get("lines", [])])
                inp.transcript = transcribe(
                    got.path, model_size=self.s.whisper_model, device=self.s.whisper_device,
                    language=self.s.whisper_language, prompt=hints,
                )
                emit(f"transcript: {len(inp.transcript.get('segments', []))} segments, "
                     f"{len(inp.transcript.get('text', '').split())} words, "
                     f"language {inp.transcript.get('language')}, on {inp.transcript.get('device', 'n/a')}"
                     + (f" ({inp.transcript['note']})" if inp.transcript.get("note") else ""))
            else:
                inp.transcript = {"segments": [], "text": "", "language": None}
            subtitles = mark_subtitles(inp.ocr.get("lines", []), inp.transcript.get("text", ""))
            if subtitles:
                emit(f"{subtitles} on-screen text blocks are subtitles of the speech")
            inp.pacing = media.pacing_stats(facts["duration_s"], inp.scenes, inp.transcript.get("segments") or [])

            backend = self.analyser.backend()
            if backend == "ollama" and self.s.vision_enabled:
                stage = "vision"
                self.db.update(rid, stage=stage)
                emit(f"describing frames with {self.s.vision_model}")
                inp.frame_notes = self.analyser.describe_frames(inp.frames, rid, emit, inp.ocr_at)
                if inp.frames and not inp.frame_notes:
                    emit(f"no frame could be described. Check that the model {self.s.vision_model} is pulled", "warning")

            stage = "synthesis"
            self.db.update(rid, stage=stage)
            ex = extract(inp)
            if backend == "ollama":
                emit(f"writing content and production notes with {self.s.text_model}")
                analysis = self.analyser.synthesize(inp, ex, rid, emit)
            else:
                emit("Ollama not running: writing a mock draft from transcript, OCR and caption", "warning")
                analysis = mock_analysis(inp, ex)
            analysis, resources = enforce_facts(analysis, inp, ex)

            # 3. write package
            stage = "write"
            self.db.update(rid, status="writing", stage=stage)
            base = naming.folder_name(analysis.title_topic, analysis.short_description, analysis.creator_handle)
            storyboard = media.storyboard(inp.frames, work / "storyboard.jpg")
            try:  # the dashboard thumbnail is cosmetic and must never fail a reel
                thumb = media.extract_frames(got.path, work / "thumb", [min(1.0, facts["duration_s"] / 3)], 320)
                if thumb:
                    shutil.copyfile(thumb[0].path, self.s.thumbs_dir / f"{rid}.jpg")
            except (media.MediaError, OSError) as e:
                log.warning("reel %s: no thumbnail (%s)", rid, str(e)[:120])
            with self.index_lock:
                folder = naming.unique_folder_name(base, self.db.folder_names(exclude_id=rid), analysis.tags[:3])
                frames = None
                if self.s.save_frames:
                    labels = {round(n.t, 2): (n.subject or n.action or n.shot_type) for n in inp.frame_notes}
                    frames = [(f.index, labels.get(round(f.t, 2)) or f"at {f.t:.1f}s", f.path) for f in inp.frames]
                ctx = {
                    "a": analysis,
                    "facts": facts,
                    "pacing": inp.pacing,
                    "reel_key": reel["reel_key"],
                    "reel_url": reel.get("source_url") or got.meta.get("webpage_url"),
                    "caption": caption,
                    "resources": resources,
                    "ocr_lines": inp.ocr.get("lines", []),
                    "backend": backend if backend == "mock" else f"Ollama ({self.s.vision_model} + {self.s.text_model})",
                    "transcript_lang": inp.transcript.get("language"),
                }
                library.write_package(
                    self.s.library_dir, folder, ctx=ctx, video=got.path, frames=frames, storyboard=storyboard,
                    replace_folder=reel.get("folder_name"),
                )
                self.db.update(
                    rid,
                    status="filed",
                    stage=None,
                    error=None,
                    folder_name=folder,
                    topic=analysis.title_topic,
                    creator=analysis.creator_handle or None,
                    summary=analysis.one_line_summary,
                    tags=analysis.tags,
                    resources=resources,
                    analysis={**analysis.model_dump(), "backend": backend, "facts": facts, "pacing": inp.pacing},
                    processing_s=round(time.monotonic() - started, 1),
                    filed_at=now(),
                )
                library.rebuild_index(self.s.library_dir, self.db.filed_reels())
            emit(f"filed as \"{folder}\" in {time.monotonic() - started:.0f}s")

            if self.s.rclone_remote:
                err = library.rclone_mirror(self.s.rclone_remote, self.s.library_dir, folder)
                emit("mirrored to Google Drive" if not err else f"Drive mirror failed: {err}", "info" if not err else "warning")
            return self.db.get(rid)

        except Exception as e:  # noqa: BLE001 - every failure is recorded on the reel
            msg = f"{type(e).__name__}: {str(e)[:500]}"
            self.db.update(rid, status="failed", stage=stage, error=msg)
            emit(f"failed at {stage}: {msg}", "error")
            return self.db.get(rid)
        finally:
            shutil.rmtree(work, ignore_errors=True)
