from pydantic import BaseModel, Field


class TimedLine(BaseModel):
    start: float = 0
    end: float = 0
    text: str = ""


class Shot(BaseModel):
    start: float = 0
    end: float = 0
    visual: str = ""
    camera: str = ""
    on_screen_text: str = ""
    transition: str = ""


class Audio(BaseModel):
    music: str = ""
    voiceover: str = ""
    sfx: str = ""
    notes: str = ""


class CallToAction(BaseModel):
    type: str = Field("none", description="comment, follow, like, link-in-bio, dm, save, share or none")
    keyword_if_any: str = ""
    exact_text: str = ""


class FormatSpecs(BaseModel):
    captions_style: str = ""
    pacing: str = ""


class ResourceNote(BaseModel):
    name: str
    kind: str = ""
    url: str = Field("", description="Copy exactly from the provided LINKS list, or leave empty")
    how_to_use: str = ""


class FrameNote(BaseModel):
    t: float = 0
    shot_type: str = ""
    setting: str = ""
    action: str = ""


class ReelAnalysis(BaseModel):
    title_topic: str = Field(..., description="Short descriptive topic, max 60 characters")
    short_description: str = Field(..., description="4 to 8 words for the folder name")
    creator_handle: str = ""
    one_line_summary: str
    overview: str
    target_audience: str = ""
    hook: str = Field("", description="What happens in the first 3 seconds and why it works")
    timed_script: list[TimedLine] = []
    scene_shot_list: list[Shot] = []
    audio: Audio = Audio()
    caption_and_hashtags: str = ""
    call_to_action: CallToAction = CallToAction()
    format_specs: FormatSpecs = FormatSpecs()
    how_to_recreate: list[str] = []
    resources_mentioned: list[ResourceNote] = []
    tags: list[str] = []
    uncertainties: list[str] = []


class FrameDescription(BaseModel):
    shot_type: str = Field("", description="close-up, medium, wide, screen recording, overhead, etc.")
    setting: str = ""
    action: str = ""
    camera: str = Field("", description="static, handheld, pan, zoom, etc.")
