"""Pydantic models for the AI output.

The text model answers in two passes (ContentPass, then ProductionPass). Code merges them into
ReelAnalysis and fills in the fields that can be measured (script, caption, opening line)."""
from typing import Any

from pydantic import BaseModel, Field


class TimedLine(BaseModel):
    start: float = 0
    end: float = 0
    text: str = ""


class Shot(BaseModel):
    start: float = 0
    end: float = 0
    visual: str = Field("", description="What the viewer sees: subject, setting, action, any app or object on screen")
    camera: str = Field("", description="Framing and movement, e.g. close-up static, screen recording")
    on_screen_text: str = ""
    transition: str = ""


class Audio(BaseModel):
    music: str = ""
    voiceover: str = ""
    sfx: str = ""
    notes: str = ""


class CallToAction(BaseModel):
    type: str = Field("none", description="comment, follow, like, save, share, link-in-bio, dm or none")
    keyword_if_any: str = Field("", description="The exact word viewers must comment or DM, if any")
    exact_text: str = Field("", description="The creator's request, word for word")


class FormatSpecs(BaseModel):
    captions_style: str = Field("", description="How text appears on screen: subtitles or headline, position, colour, size")
    pacing: str = Field("", description="One or two sentences based on the measured cuts and shot length")
    editing_techniques: list[str] = Field(
        [], description="Techniques visible in the material: jump cuts, screen recording, zoom-ins, B-roll, text pop-ups"
    )


class ResourceNote(BaseModel):
    name: str
    kind: str = Field("", description="repo, website, template, course, download, prompt, file or tool")
    url: str = Field("", description="Copied character for character from the LINKS list, or empty")
    how_to_use: str = ""


class ToolMention(BaseModel):
    name: str = Field(..., description="Exact name of the app, website, AI model, library, device, product, brand or person")
    what_it_is: str = ""
    how_it_is_used: str = Field("", description="What this reel does with it or says about it")
    where_seen: str = Field("", description="spoken, on screen, caption or frame")


class FrameDescription(BaseModel):
    shot_type: str = Field("", description="close-up, medium, wide, screen recording, overhead, slide, etc.")
    subject: str = Field("", description="Who or what is the main subject")
    setting: str = ""
    action: str = ""
    visible_items: str = Field(
        "", description="Apps, websites, devices, products, logos, charts or props that are clearly visible, or none"
    )
    text_style: str = Field("", description="Look of any text overlay: position, colour, size, subtitles or headline")
    camera: str = Field("", description="static, handheld, pan, zoom, etc.")


class FrameNote(FrameDescription):
    t: float = 0


class ContentPass(BaseModel):
    """What the reel says. Evidence fields come first, so the summary is written after the details."""

    key_points: list[str] = Field(..., description="Every tip, step, fact, number, claim or example, in order, one full sentence each")
    tools_and_products: list[ToolMention] = []
    call_to_action: CallToAction = CallToAction()
    resources_mentioned: list[ResourceNote] = []
    target_audience: str = ""
    overview: str = Field(..., description="3 to 5 concrete sentences: what the reel shows or teaches and the result it promises")
    one_line_summary: str = Field(..., description="The main takeaway in one sentence, at most 25 words")
    title_topic: str = Field(..., description="Short topic label, at most 60 characters, not a sentence")
    short_description: str = Field("", description="4 to 8 plain words on the format or angle, for the folder name")
    creator_handle: str = ""
    tags: list[str] = Field([], description="5 to 10 lowercase search keywords, no #")
    uncertainties: list[str] = []


class ProductionPass(BaseModel):
    """How the reel is made, and how to make one like it."""

    scene_shot_list: list[Shot] = []
    audio: Audio = Audio()
    format_specs: FormatSpecs = FormatSpecs()
    hook: str = Field("", description="What happens in the first 3 seconds and why it stops the scroll")
    why_it_works: list[str] = Field([], description="3 to 5 reasons, each tied to something specific in this reel")
    how_to_recreate: list[str] = Field([], description="6 to 12 concrete steps to make a version of this reel")
    uncertainties: list[str] = []


class ReelAnalysis(BaseModel):
    title_topic: str
    short_description: str = ""
    creator_handle: str = ""
    one_line_summary: str
    overview: str
    target_audience: str = ""
    key_points: list[str] = []
    tools_and_products: list[ToolMention] = []
    hook_line: str = ""  # measured: first words spoken
    hook_on_screen_text: str = ""  # measured: text on screen in the first 3 seconds
    hook: str = ""
    why_it_works: list[str] = []
    timed_script: list[TimedLine] = []  # measured: Whisper segments
    scene_shot_list: list[Shot] = []
    audio: Audio = Audio()
    caption_and_hashtags: str = ""  # measured: the caption as given
    call_to_action: CallToAction = CallToAction()
    format_specs: FormatSpecs = FormatSpecs()
    how_to_recreate: list[str] = []
    resources_mentioned: list[ResourceNote] = []
    tags: list[str] = []
    uncertainties: list[str] = []


def strict_schema(model: type[BaseModel]) -> dict[str, Any]:
    """JSON schema with every property required.

    Ollama turns the schema into a grammar. Optional keys can be skipped by the model, which is how
    small models end up returning almost empty answers, so every key is forced here. Validation in
    Python stays lenient through the defaults above."""
    schema = model.model_json_schema()

    def walk(node: Any) -> None:
        if isinstance(node, dict):
            if isinstance(node.get("properties"), dict):
                node["required"] = list(node["properties"])
                node["additionalProperties"] = False
            node.pop("default", None)
            for v in node.values():
                walk(v)
        elif isinstance(node, list):
            for v in node:
                walk(v)

    walk(schema)
    return schema
