"""Run one recorded match through the agent team on Foundry and report quality, latency and tokens.

AI_PROMPT_DESIGN §7: PASSED / REPAIRED / FALLBACK rates, p50/p95 moment→insight latency, tokens per role,
plus the casual full-time recap. Moments are explained in match order exactly as the agents service does it
(same workflow, same per-call timeouts), fed from the engine replaying the recording, so the context each
moment sees (score, recent sequence) is what it would be live. Costs pennies on the mini deployments.

Usage (needs `az login` and FOUNDRY_* in .env):
    uv run python scripts/eval_live.py data/matches/sample_seed11.jsonl
    uv run python scripts/eval_live.py data/matches/sample_seed7.jsonl --limit 8 --json data/eval/seed7.json
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import statistics
import sys
import time
from collections import Counter, defaultdict
from pathlib import Path

from dotenv import load_dotenv

from fulcrum.agents.service import PROMPT_AGENT_ROLES, AgentService
from fulcrum.bus.base import MemoryBus
from fulcrum.contracts import MatchConfig, MatchEvent, MomentType, Persona
from fulcrum.engine.match import MatchEngine
from fulcrum.engine.replay import read_jsonl
from fulcrum.engine.teams import load_teams
from fulcrum.llm.foundry import FoundryLLM
from fulcrum.telemetry import configure_telemetry


def pct(values: list[float], q: float) -> float:
    """Nearest-rank percentile (q in 0..100); 0 for no values."""
    if not values:
        return 0.0
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, max(0, round(q / 100 * len(ordered)) - 1))]


async def run(path: Path, limit: int | None) -> dict:
    teams = load_teams()
    llm = FoundryLLM.from_env()
    service = AgentService(MemoryBus(), llm, league=list(teams.values()), consume_events=False)
    engine: MatchEngine | None = None
    contexts = []
    match_id = ""
    await llm.warm_up(PROMPT_AGENT_ROLES)
    recap_s, recap = 0.0, None
    try:
        for item in read_jsonl(path):
            if engine is None:
                engine = MatchEngine(item.match_id, teams_by_id=teams)
                match_id = item.match_id
            if not isinstance(item, MatchEvent):
                out = engine.process_frame(item)
            else:
                service.store.apply_event(item)
                out = engine.process_event(item)
                if item.type == "match_start" and engine.tracker.home_id and engine.tracker.away_id:
                    service.register_match(
                        MatchConfig(
                            match_id=match_id, home_team_id=engine.tracker.home_id, away_team_id=engine.tracker.away_id
                        )
                    )
            for moment in out.moments:
                if limit is not None and len(contexts) >= limit and moment.type != MomentType.FULL_TIME:
                    continue
                ctx = service.submit(moment)
                if ctx is None:
                    print(f"skip {moment.moment_id}: teams unknown", file=sys.stderr)
                    continue
                await service.drain()  # one at a time, in match order, like the live per-match worker
                contexts.append(ctx)
                ins = ctx.insight
                print(f"{ctx.moment.type:<17} {ins.check_status:<8} {ins.latency_ms:>5} ms  {ins.explanation.headline}")
        started = time.monotonic()
        recap = await service.recap(match_id, Persona.CASUAL, "en")  # cached if the full-time moment made it
        recap_s = time.monotonic() - started
    finally:
        await service.stop()
        await llm.aclose()
    recap_calls = [c for c in llm.calls if c.agent == "match_reporter"]
    recap_s = sum(c.seconds for c in recap_calls) or recap_s

    statuses = Counter(str(c.insight.check_status) for c in contexts)
    latencies = [c.insight.latency_ms for c in contexts]
    by_agent: dict[str, dict[str, float]] = defaultdict(lambda: defaultdict(float))
    for call in llm.calls:
        a = by_agent[call.agent]
        a["calls"] += 1
        a["failed"] += 0 if call.ok else 1
        a["input_tokens"] += call.input_tokens
        a["output_tokens"] += call.output_tokens
        a["seconds"] += call.seconds
        a[f"via_{call.via}"] += 1
    n = len(contexts) or 1
    return {
        "recording": str(path),
        "moments": len(contexts),
        "status_counts": dict(statuses),
        "status_rates_pct": {k: round(100 * v / n, 1) for k, v in statuses.items()},
        "latency_ms": {
            "p50": pct(latencies, 50),
            "p95": pct(latencies, 95),
            "mean": round(statistics.mean(latencies), 1) if latencies else 0,
        },
        "llm": {k: {kk: round(vv, 2) for kk, vv in v.items()} for k, v in by_agent.items()},
        "prompt_agents": {p.name: p.version for p in llm.published.values()},
        "tokens_total": sum(c.input_tokens + c.output_tokens for c in llm.calls),
        "recap": {
            "seconds": round(recap_s, 2),
            "status": str(recap.check_status) if recap else None,
            "title": recap.title if recap else None,
            "summary": recap.summary if recap else None,
        },
        "fallback_notes": {c.moment.moment_id: c.notes for c in contexts if str(c.insight.check_status) == "fallback"},
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("recording", type=Path, help="JSONL of {topic, data} rows (sim --out format)")
    ap.add_argument("--limit", type=int, default=None, help="explain at most N moments (+ full time)")
    ap.add_argument("--json", type=Path, default=None, help="also write the report here")
    args = ap.parse_args(argv)
    load_dotenv()
    os.environ["FULCRUM_LLM"] = "foundry"  # the deployed defaults that depend on it (e.g. the Editor fan-out)
    sys.stdout.reconfigure(encoding="utf-8")  # headlines contain arrows; the Windows console defaults to cp1252
    configure_telemetry("eval")
    report = asyncio.run(run(args.recording, args.limit))
    print(json.dumps({k: v for k, v in report.items() if k != "fallback_notes"}, indent=2))
    if report["fallback_notes"]:
        print("\nfallback reasons:")
        for mid, notes in report["fallback_notes"].items():
            print(f"  {mid}: {' | '.join(notes)[:300]}")
    if args.json:
        args.json.parent.mkdir(parents=True, exist_ok=True)
        args.json.write_text(json.dumps(report, indent=2), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
