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
        run_worker=False,
    )
    s.ensure_dirs()
    return s
