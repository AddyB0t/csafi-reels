import pytest

from csafi_bot.config import Settings


@pytest.fixture
def settings(tmp_path):
    s = Settings(
        _env_file=None,
        data_dir=tmp_path / "data",
        ai_backend="mock",
        transcribe_enabled=False,
        ocr_enabled=False,
        meta_verify_token="verify-me",
        meta_app_secret="app-secret",
        run_worker=False,
    )
    s.ensure_dirs()
    return s
