import pytest

from app.config import settings
from app.services import store
from app.services.health import scheduler


@pytest.fixture
def state_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "state_dir", str(tmp_path))
    store.init_health()
    return tmp_path


@pytest.fixture(autouse=True)
def no_active_probes(monkeypatch):
    monkeypatch.setattr(scheduler, "ACTIVE_PROBES", False)
