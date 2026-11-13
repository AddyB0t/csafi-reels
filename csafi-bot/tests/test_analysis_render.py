import json
from pathlib import Path

from csafi_bot import library, media
from csafi_bot.analyse import (
    Analyser,
    AnalysisInput,
    build_content_prompt,
    build_production_prompt,
    build_timeline,
    enforce_facts,
    extract,
    mock_analysis,
    thin_content,
)
from csafi_bot.ocr import group_lines, mark_subtitles
from csafi_bot.ollama import ChatResult
from csafi_bot.schema import ContentPass, FrameNote, ProductionPass, ReelAnalysis, ToolMention, strict_schema
from csafi_bot.transcribe import hint_prompt

FACTS = {"duration_s": 30.0, "resolution": "1080x1920", "aspect_ratio": "9:16", "fps": 30.0, "has_audio": True}


def make_input():
    return AnalysisInput(
        facts=FACTS,
        caption="New TTS repo! Comment VOICE for the link #ai #tts",
        creator_hint="githubsignals",
        transcript={"language": "en", "segments": [{"start": 0, "end": 2, "text": "This repo clones voices"}],
                    "text": "This repo clones voices"},
        ocr={"lines": [{"t": 1.0, "text": "github.com/acme/audio8"}]},
    )


def test_links_come_from_code_not_model():
    inp = make_input()
    ex = extract(inp)
    a = ReelAnalysis(
        title_topic="Audio8 TTS Voice Cloning", short_description="open-source TTS repo walkthrough",
        one_line_summary="s", overview="o",
        resources_mentioned=[
            {"name": "Audio8", "url": "https://github.com/acme/audio8", "how_to_use": "clone it"},
            {"name": "Fake", "url": "https://invented.example.com/x", "how_to_use": "n/a"},
        ],
        call_to_action={"type": "comment", "keyword_if_any": "VOICE"},
    )
    a, resources = enforce_facts(a, inp, ex)
    urls = [r["url"] for r in resources if r["url"]]
    assert urls == ["https://github.com/acme/audio8"]
    assert resources[0]["how_to_use"] == "clone it"
    assert resources[0]["found_in"] == "on screen"
    assert any("invented.example.com" in u for u in a.uncertainties)
    assert a.creator_handle == "githubsignals"
    assert not any("keyword" in u.lower() for u in a.uncertainties)


def test_measured_fields_replace_model_text():
    inp = make_input()
    a = ReelAnalysis(
        title_topic="t", one_line_summary="s", overview="o",
        timed_script=[{"start": 0, "end": 9, "text": "invented line"}], hook_line="invented hook",
        key_points=["1. Clone the repo.", "clone the repo.", "  ", "- Run it locally."],
        tools_and_products=[{"name": "Audio8"}, {"name": "audio8"}, {"name": " "}],
    )
    a, _ = enforce_facts(a, inp, extract(inp))
    assert [l.text for l in a.timed_script] == ["This repo clones voices"]
    assert a.hook_line == "This repo clones voices"
    assert a.hook_on_screen_text == "github.com/acme/audio8"
    assert a.caption_and_hashtags == inp.caption
    assert a.key_points == ["Clone the repo.", "Run it locally."]
    assert [t.name for t in a.tools_and_products] == ["Audio8"]


def test_keyword_not_in_source_is_flagged():
    inp = make_input()
    a = ReelAnalysis(title_topic="t", short_description="d", one_line_summary="s", overview="o",
                     call_to_action={"type": "comment", "keyword_if_any": "GUIDE"})
    a, _ = enforce_facts(a, inp, extract(inp))
    assert any("GUIDE" in u for u in a.uncertainties)


def test_strict_schema_forces_every_key():
    schema = strict_schema(ContentPass)
    assert set(schema["required"]) == set(ContentPass.model_fields)
    assert list(schema["properties"])[0] == "key_points"  # evidence before summary
    tool = schema["$defs"]["ToolMention"]
    assert set(tool["required"]) == {"name", "what_it_is", "how_it_is_used", "where_seen"}
    assert "default" not in json.dumps(schema)
    assert ContentPass.model_validate({"key_points": [], "overview": "o", "one_line_summary": "s", "title_topic": "t"})


def test_ocr_blocks_merge_and_subtitles_are_flagged():
    blocks = group_lines([
        (1.0, ["Step 1: Opne the app", "@maker"]),
        (2.0, ["Step 1: Open the app", "@maker"]),
        (3.0, ["this repo clones", "@maker"]),
    ], frame_gap=1.0)
    by_text = {b["text"]: b for b in blocks}
    assert len(blocks) == 3
    assert by_text["@maker"]["t"] == 1.0 and by_text["@maker"]["end"] == 4.0 and by_text["@maker"]["seen"] == 3
    step = next(b for b in blocks if b["text"].startswith("Step 1"))
    assert step["end"] == 3.0
    assert mark_subtitles(blocks, "This repo clones voices") == 1
    assert by_text["this repo clones"]["subtitle"] and not by_text["@maker"]["subtitle"]
    assert mark_subtitles(blocks, "") == 0


def test_hint_prompt_keeps_names_only():
    hint = hint_prompt("New Audio8 repo from @acme, try it with ffmpeg7 today #TTS https://x.example/a")
    assert hint.startswith("Glossary: ")
    for name in ("Audio8", "acme", "ffmpeg7", "TTS"):
        assert name in hint
    assert "today" not in hint and "https" not in hint
    assert hint_prompt("nothing special here") == ""


def test_timeline_uses_scene_cuts():
    inp = make_input()
    inp.scenes = [(0.0, 10.0), (10.0, 30.0)]
    inp.ocr["lines"].append({"t": 12.0, "end": 15.0, "text": "pip install audio8"})
    inp.frame_notes = [FrameNote(t=5.0, subject="man at a desk"), FrameNote(t=20.0, subject="terminal window")]
    first, second = build_timeline(inp)
    assert (first["start"], first["end"]) == (0.0, 10.0)
    assert first["said"] == "This repo clones voices" and first["text"] == ["github.com/acme/audio8"]
    assert second["text"] == ["pip install audio8"] and second["notes"][0].subject == "terminal window"
    inp.scenes = [(i, i + 1.0) for i in range(30)]
    merged = build_timeline(inp)
    assert len(merged) <= 14 and merged[0]["start"] == 0 and merged[-1]["end"] == 30.0
    inp.scenes = []
    assert len(build_timeline(inp)) == 2  # one block around each frame note


def test_prompts_carry_the_material():
    inp = make_input()
    inp.pacing = {"scene_count": 2, "cuts": 1, "words_per_minute": 150, "avg_shot_s": None}
    inp.ocr["lines"].append({"t": 0.5, "end": 2.0, "text": "This repo clones", "subtitle": True})
    content = build_content_prompt(inp, extract(inp))
    assert "[0.0-2.0] This repo clones voices" in content and "github.com/acme/audio8" in content
    assert "1 more lines were burned-in subtitles" in content and "@githubsignals" in content
    production = build_production_prompt(inp, ContentPass(
        key_points=["It clones a voice from 5 seconds of audio."], overview="o", one_line_summary="s", title_topic="Audio8"))
    assert '"words_per_minute": 150' in production and "avg_shot_s" not in production
    assert 'first words spoken: "This repo clones voices"' in production
    assert "- It clones a voice from 5 seconds of audio." in production


class FakeOllama:
    """Answers the content pass (thin first, then full) and the production pass."""

    def __init__(self, production_ok: bool = True):
        self.calls: list[dict] = []
        self.unloaded = 0
        self.production_ok = production_ok

    def chat(self, model, messages, *, fmt=None, options=None, think=False, keep_alive="5m"):
        self.calls.append({"messages": messages, "fmt": fmt})
        if "key_points" in fmt["properties"]:
            asked_again = any("key_points has only" in m["content"] for m in messages)
            body = {"key_points": ["Point one.", "Point two.", "Point three."] if asked_again else [],
                    "overview": "It shows a voice cloning repo.", "one_line_summary": "A repo clones voices.",
                    "title_topic": "Audio8 voice cloning", "short_description": "repo walkthrough",
                    "tags": ["tts"], "uncertainties": ["No caption."]}
        elif self.production_ok:
            body = {"scene_shot_list": [{"start": 0, "end": 30, "visual": "screen recording"}],
                    "hook": "Opens on the claim.", "why_it_works": ["Fast payoff."],
                    "how_to_recreate": ["Record the screen."], "uncertainties": ["Music not determined."]}
        else:
            return ChatResult("not json", 1.0, 10, 10)
        return ChatResult(json.dumps(body), 1.0, 10, 10)

    def unload(self, model):
        self.unloaded += 1


def long_input():
    inp = make_input()
    text = " ".join(["word"] * 80)
    inp.transcript = {"language": "en", "segments": [{"start": 0, "end": 28, "text": text}], "text": text}
    return inp


def test_two_pass_synthesis_and_thin_answer_retry(settings):
    inp = long_input()
    analyser = Analyser(settings)
    analyser.ollama = FakeOllama()
    events: list[str] = []
    a = analyser.synthesize(inp, extract(inp), None, events.append)
    assert a.key_points == ["Point one.", "Point two.", "Point three."]
    assert a.how_to_recreate == ["Record the screen."] and a.scene_shot_list[0].visual == "screen recording"
    assert a.uncertainties == ["No caption.", "Music not determined."]
    assert len(analyser.ollama.calls) == 3 and analyser.ollama.unloaded == 1
    assert any("thin" in e for e in events)
    empty = ContentPass(key_points=[], overview="o", one_line_summary="s", title_topic="t")
    assert "80 words" in thin_content(empty, inp)
    assert thin_content(empty, make_input()) is None  # 4 words of speech: nothing to demand


def test_content_is_filed_when_production_pass_fails(settings):
    inp = long_input()
    analyser = Analyser(settings)
    analyser.ollama = FakeOllama(production_ok=False)
    a = analyser.synthesize(inp, extract(inp), None, lambda _msg: None)
    assert len(a.key_points) == 3 and a.how_to_recreate == []
    assert any("production notes" in u for u in a.uncertainties)
    assert ProductionPass().scene_shot_list == []


def test_pick_times_caps_and_covers_start():
    times = media.pick_times(60, [5, 10, 20, 40], max_frames=8)
    assert len(times) == 8
    assert times[0] < 1.0
    assert times == sorted(times)
    assert media.pick_times(0, [], 8) == [0.0]


def test_ocr_times_and_pacing():
    times = media.ocr_times(20, interval=1.0, max_frames=45)
    assert len(times) == 20 and times[0] < 1 and times[-1] < 20
    assert len(media.ocr_times(300, interval=1.0, max_frames=45)) == 45
    p = media.pacing_stats(30.0, [(0, 10), (10, 20), (20, 30)], [{"start": 0, "end": 12, "text": "one two three four five six"}])
    assert p["scene_count"] == 3 and p["cuts"] == 2 and p["avg_shot_s"] == 10.0 and p["cuts_per_10s"] == 0.7
    assert p["words_spoken"] == 6 and p["words_per_minute"] == 30 and p["speech_coverage_pct"] == 40
    assert media.pacing_stats(30.0, [], [])["scene_count"] is None


def test_render_package_and_index(tmp_path: Path):
    inp = make_input()
    ex = extract(inp)
    a, resources = enforce_facts(mock_analysis(inp, ex), inp, ex)
    a.key_points = ["It clones a voice from 5 seconds of audio."]
    a.tools_and_products = [ToolMention(name="Audio8", what_it_is="TTS repo | open source", where_seen="on screen")]
    video = tmp_path / "v.mp4"
    video.write_bytes(b"0" * 100)
    board = tmp_path / "board.jpg"
    board.write_bytes(b"jpg")
    ctx = {"a": a, "facts": FACTS, "pacing": {}, "reel_key": "sc_x", "reel_url": "https://www.instagram.com/reel/x/",
           "caption": inp.caption, "resources": resources, "ocr_lines": inp.ocr["lines"], "backend": "mock",
           "transcript_lang": "en"}
    lib = tmp_path / "lib"
    folder = library.write_package(lib, "Test Folder (@me)", ctx=ctx, video=video, storyboard=board)
    names = sorted(p.name for p in folder.iterdir())
    assert library.README_NAME in names and library.RESOURCES_NAME in names and library.MANIFEST_NAME in names
    assert library.STORYBOARD_NAME in names
    assert any(n.startswith("VIDEO - ") for n in names)
    readme = (folder / library.README_NAME).read_text(encoding="utf-8")
    assert "sc_x" in readme and "This repo clones voices" in readme
    assert "1. It clones a voice from 5 seconds of audio." in readme
    assert "| Audio8 | TTS repo \\| open source |" in readme
    assert '**First words spoken:** "This repo clones voices"' in readme
    assert library.STORYBOARD_NAME in (folder / library.MANIFEST_NAME).read_text(encoding="utf-8")
    res = (folder / library.RESOURCES_NAME).read_text(encoding="utf-8")
    assert "https://github.com/acme/audio8" in res
    idx = library.rebuild_index(lib, [{"folder_name": "Test Folder (@me)", "reel_key": "sc_x", "creator": "me",
                                       "topic": a.title_topic, "summary": "s", "tags": ["ai"], "resources": resources,
                                       "source_url": None}])
    text = idx.read_text(encoding="utf-8")
    assert "Test Folder (@me)" in text and "1 reel filed" in text
    assert not list(lib.glob(".tmp-*"))
