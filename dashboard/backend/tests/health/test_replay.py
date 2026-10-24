import json
from pathlib import Path

from app.services.health.model import GOOD, SLOW
from tests.health.series import changes, edge, run, sample, states

FIXTURE = json.loads((Path(__file__).parent / "fixtures" / "edge_degradation.json").read_text())["minutes"]
ONSET = 60  # failures begin one hour into the recording
FLAGGED_BY = 110


def series(edges_for):
    return [sample(i, edges=edges_for(*pair)) for i, pair in enumerate(FIXTURE)]


def test_a_real_multi_hour_edge_degradation_on_the_active_edge_is_flagged_and_stays_flagged():
    r = run(series(lambda hello, fails: {"edge-a": edge(hello, fails)}))
    s = states(r)
    assert set(s[:ONSET]) == {GOOD}
    first = s.index(SLOW)
    assert ONSET < first < FLAGGED_BY
    assert s[first:350].count(SLOW) >= 0.75 * (350 - first)
    assert len(changes(r)) <= 14
    assert s[-1] == GOOD


def test_the_same_degradation_on_the_backup_edge_never_changes_the_verdict():
    r = run(series(lambda hello, fails: {"edge-b": edge(hello, fails)}))
    assert set(states(r)) == {GOOD}
    evidence = r.tracker.verdict(r.minutes[ONSET + 200].ts)["evidence"]
    assert any(e["signal"] == "edge" and "backup" in e["text"] for e in evidence)
