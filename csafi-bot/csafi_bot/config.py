from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # Paths
    data_dir: Path = Path("./data")

    # Dashboard / server
    host: str = "127.0.0.1"
    port: int = 8765
    dashboard_token: str = ""
    run_worker: bool = True

    max_upload_mb: int = Field(500, ge=10, le=4096)

    # AI
    ai_backend: Literal["auto", "ollama", "mock"] = "auto"
    ollama_url: str = "http://127.0.0.1:11434"
    vision_model: str = "qwen2.5vl:7b"
    text_model: str = "qwen3:8b"
    ollama_timeout_s: int = 600
    text_num_ctx: int = 12288

    # Media
    whisper_model: str = "small"
    whisper_device: Literal["auto", "cuda", "cpu"] = "auto"
    whisper_language: str = ""
    transcribe_enabled: bool = True
    ocr_enabled: bool = True
    vision_enabled: bool = True
    max_frames: int = Field(14, ge=3, le=32)
    frame_width: int = Field(768, ge=256, le=1920)
    ocr_interval_s: float = Field(1.0, ge=0.25, le=10)
    ocr_max_frames: int = Field(45, ge=4, le=120)
    ocr_frame_width: int = Field(1080, ge=480, le=2160)
    save_frames: bool = False

    # Optional Drive mirror
    rclone_remote: str = ""

    @field_validator("data_dir")
    @classmethod
    def _expand(cls, v: Path) -> Path:
        return v.expanduser().resolve()

    @property
    def library_dir(self) -> Path:
        return self.data_dir / "library"

    @property
    def work_dir(self) -> Path:
        return self.data_dir / "work"

    @property
    def logs_dir(self) -> Path:
        return self.data_dir / "logs"

    @property
    def uploads_dir(self) -> Path:
        return self.data_dir / "uploads"

    @property
    def thumbs_dir(self) -> Path:
        return self.data_dir / "thumbs"

    @property
    def db_path(self) -> Path:
        return self.data_dir / "csafi.db"

    def ensure_dirs(self) -> None:
        for d in (self.data_dir, self.library_dir, self.work_dir, self.logs_dir, self.uploads_dir, self.thumbs_dir):
            d.mkdir(parents=True, exist_ok=True)


@lru_cache
def get_settings() -> Settings:
    s = Settings()
    s.ensure_dirs()
    return s
