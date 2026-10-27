import pytest

from app.config import settings
from app.services import store
from app.services.health import scheduler, verdict
from app.services.health.collectors import containers
from tests.health.series import EDGES


@pytest.fixture
def state_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "state_dir", str(tmp_path))
    store.init_health()
    return tmp_path


@pytest.fixture(autouse=True)
def no_active_probes(monkeypatch):
    monkeypatch.setattr(scheduler, "ACTIVE_PROBES", False)


@pytest.fixture(autouse=True)
def made_up_topology(monkeypatch):
    """Tests run on invented route/edge names, so nothing can lean on the production ones."""
    for module in (scheduler, verdict, containers):
        monkeypatch.setattr(module, "PATH_EDGES", EDGES)
