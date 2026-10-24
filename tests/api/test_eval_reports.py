"""GET /api/eval/latest — newest offline live report and Foundry evaluation summary from data/eval/."""

import json

from fastapi.testclient import TestClient

from fulcrum.api.app import create_app
from fulcrum.api.evalreports import REPO_EVAL_DIR, latest_reports, report_kind
from fulcrum.bus.base import MemoryBus

LIVE = {"kind": "live", "moments": 30, "status_counts": {"passed": 29}, "latency_ms": {"p50": 3000}}
FOUNDRY = {"kind": "foundry", "run_id": "r", "evaluators": {"groundedness": {"mean": 4.5}}, "per_item": [1, 2]}


def write(path, data) -> None:
    path.write_text(json.dumps(data), encoding="utf-8")


def test_report_kind_from_explicit_kind_or_shape():
    assert report_kind(LIVE) == "live" and report_kind(FOUNDRY) == "foundry"
    assert report_kind({"status_counts": {}, "latency_ms": {}}) == "live"  # pre-`kind` reports
    assert report_kind({"evaluators": {}, "run_id": "x"}) == "foundry"
    assert report_kind({"something": "else"}) is None


def test_latest_of_each_kind_without_heavy_fields(tmp_path):
    write(tmp_path / "a_live.json", {**LIVE, "generated_at": "2026-10-24T10:00:00+00:00", "moments": 10})
    write(tmp_path / "b_live.json", {**LIVE, "generated_at": "2026-10-24T12:00:00+00:00"})
    write(tmp_path / "foundry_x.json", {**FOUNDRY, "generated_at": "2026-10-24T12:30:00+00:00"})
    write(tmp_path / "other.json", ["not", "a", "report"])
    (tmp_path / "broken.json").write_text("{", encoding="utf-8")
    out = latest_reports(tmp_path)
    assert out["live"]["file"] == "b_live.json" and out["live"]["moments"] == 30
    assert out["foundry"]["file"] == "foundry_x.json" and "per_item" not in out["foundry"]
    assert out["foundry"]["evaluators"]["groundedness"]["mean"] == 4.5


def test_missing_directory_gives_nulls(tmp_path):
    assert latest_reports(tmp_path / "nope") == {"live": None, "foundry": None}


def test_endpoint_reads_eval_dir(tmp_path, monkeypatch):
    write(tmp_path / "foundry_x.json", FOUNDRY)
    monkeypatch.setenv("FULCRUM_EVAL_DIR", str(tmp_path))
    app = create_app(MemoryBus(), in_process=False)
    with TestClient(app) as c:
        r = c.get("/api/eval/latest")
    assert r.status_code == 200
    body = r.json()
    assert body["live"] is None and body["foundry"]["run_id"] == "r" and body["foundry"]["generated_at"]


def test_repo_reports_are_served():
    # The committed data/eval/ holds a live run and its Foundry evaluation (scripts/eval_*.py).
    out = latest_reports(REPO_EVAL_DIR)
    assert out["live"] is not None and out["live"]["moments"] > 0
    assert out["foundry"] is not None and "groundedness" in out["foundry"]["evaluators"]
