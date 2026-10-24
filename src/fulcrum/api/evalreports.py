"""Read-only access to the offline evaluation reports in `data/eval/` (served at `GET /api/eval/latest`).

Two kinds of JSON live there:
- **live** — `scripts/eval_live.py --json`: PASSED / REPAIRED / FALLBACK rates, latency p50/p95, tokens, recap.
- **foundry** — `scripts/eval_foundry.py`: Foundry built-in evaluator scores (groundedness, relevance,
  coherence, fluency) over the same moments, with the portal `report_url`.

The newest of each (by `generated_at`, else file time) is returned; per-item detail is left out to keep the
response small. Directory: `FULCRUM_EVAL_DIR`, else the repo's `data/eval`, else `./data/eval`.
"""

from __future__ import annotations

import json
import logging
import os
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

log = logging.getLogger("fulcrum.api.eval")

REPO_EVAL_DIR = Path(__file__).resolve().parents[3] / "data" / "eval"
HEAVY_KEYS = ("per_item", "records")


def eval_dir() -> Path:
    """Where evaluation reports are read from."""
    if env := os.getenv("FULCRUM_EVAL_DIR"):
        return Path(env)
    return REPO_EVAL_DIR if REPO_EVAL_DIR.is_dir() else Path.cwd() / "data" / "eval"


def report_kind(report: dict[str, Any]) -> str | None:
    """`"live"`, `"foundry"` or None, from the explicit `kind` or the report's shape (older files)."""
    kind = report.get("kind")
    if kind in ("live", "foundry"):
        return kind
    if "evaluators" in report and "run_id" in report:
        return "foundry"
    if "status_counts" in report and "latency_ms" in report:
        return "live"
    return None


def latest_reports(directory: Path | None = None) -> dict[str, Any]:
    """`{"live": report | None, "foundry": summary | None}` — the newest of each kind in `directory`."""
    directory = directory or eval_dir()
    best: dict[str, tuple[str, dict[str, Any]]] = {}
    for path in sorted(directory.glob("*.json")) if directory.is_dir() else []:
        try:
            report = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            log.warning("skipping unreadable eval report %s: %s", path.name, exc)
            continue
        if not isinstance(report, dict) or (kind := report_kind(report)) is None:
            continue
        stamp = str(report.get("generated_at") or "")
        if not stamp:
            stamp = datetime.fromtimestamp(path.stat().st_mtime, UTC).isoformat(timespec="seconds")
        if kind not in best or stamp >= best[kind][0]:
            slim = {k: v for k, v in report.items() if k not in HEAVY_KEYS}
            best[kind] = (stamp, {"file": path.name, "generated_at": stamp, **slim})
    return {kind: best[kind][1] if kind in best else None for kind in ("live", "foundry")}
