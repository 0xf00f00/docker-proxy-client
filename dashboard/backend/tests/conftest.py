import pytest

from app.config import settings
from app.services import store


@pytest.fixture
def state_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "state_dir", str(tmp_path))
    store.init_health()
    return tmp_path
