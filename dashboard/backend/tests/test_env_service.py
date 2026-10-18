from types import SimpleNamespace

from app.services import env_service


def _capture(monkeypatch, tmp_path):
    calls = []
    monkeypatch.setattr(env_service, "_project_path", lambda: tmp_path)
    monkeypatch.setattr(env_service, "_detect_project_name", lambda _s: "proj")
    monkeypatch.setattr(env_service, "_service_profiles", lambda _s: [])
    monkeypatch.setattr(env_service, "_host_project_dir", lambda: str(tmp_path))

    def run(cmd, **_kw):
        calls.append(cmd)
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(env_service.subprocess, "run", run)
    return calls


# A service whose dependency's config changed would otherwise recreate that
# dependency too (e.g. clash -> frpc-proxy).
def test_compose_up_leaves_dependencies_alone(monkeypatch, tmp_path):
    calls = _capture(monkeypatch, tmp_path)
    assert env_service.recreate_service("xray-fallback")[0]
    assert env_service.start_service("skirk")[0]
    assert len(calls) == 2
    for cmd in calls:
        assert "--no-deps" in cmd[: cmd.index("--")], cmd
