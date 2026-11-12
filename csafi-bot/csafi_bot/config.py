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

    # Meta webhook
    meta_verify_token: str = ""
    meta_app_secret: str = ""
    allowed_sender_ids: str = ""

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
    save_frames: bool = False

    # Notifications / intake
    telegram_bot_token: str = ""
    telegram_chat_id: str = ""
    telegram_intake: bool = False
    telegram_allowed_user_ids: str = ""

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
    def db_path(self) -> Path:
        return self.data_dir / "csafi.db"

    @property
    def allowed_senders(self) -> set[str]:
        return {s.strip() for s in self.allowed_sender_ids.split(",") if s.strip()}

    @property
    def telegram_allowed_users(self) -> set[str]:
        return {s.strip() for s in self.telegram_allowed_user_ids.split(",") if s.strip()}

    def ensure_dirs(self) -> None:
        for d in (self.data_dir, self.library_dir, self.work_dir, self.logs_dir):
            d.mkdir(parents=True, exist_ok=True)


@lru_cache
def get_settings() -> Settings:
    s = Settings()
    s.ensure_dirs()
    return s
