"""Score the agents' real output with Foundry's built-in quality evaluators (AI_PROMPT_DESIGN §7).

Input is the per-moment JSONL that `scripts/eval_live.py --json ...` writes next to its report
(`<report>.records.jsonl`: query = task + moment, context = the moment's MOMENT/FACTS/ROSTER/RECENT/SCORE
sections, response = the explanation + four persona variants). Each record becomes an Agent Framework
`EvalItem`; `agent_framework.foundry.FoundryEvals` creates an evaluation in the Foundry project and runs
`builtin.groundedness` (response vs. context), `builtin.relevance`, `builtin.coherence` and `builtin.fluency`
with the `fast` deployment as the LLM judge. The run shows up on the portal's Evaluations page
(`report_url`); the summary (per-evaluator mean / min / pass rate, split by PASSED / REPAIRED / FALLBACK,
lowest-scoring moments) is written to data/eval/.

Usage (needs `az login` and FOUNDRY_PROJECT_ENDPOINT in .env):
    uv run python scripts/eval_foundry.py data/eval/sample_seed11.records.jsonl
    uv run python scripts/eval_foundry.py data/eval/sample_seed11.records.jsonl --evaluators groundedness relevance
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

from fulcrum.agents.evaluation import QUALITY_EVALUATORS, summarise_eval


def read_records(path: Path) -> list[dict[str, Any]]:
    """Records from an eval_live JSONL, skipping blank lines."""
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


async def evaluate(records: list[dict[str, Any]], evaluators: list[str], judge: str, name: str) -> Any:
    """Submit the records to Foundry and wait for the evaluation run."""
    from agent_framework import EvalItem, Message
    from agent_framework.foundry import FoundryEvals
    from azure.ai.projects.aio import AIProjectClient
    from azure.identity.aio import DefaultAzureCredential

    items = [
        EvalItem(
            conversation=[Message("user", [r["query"]]), Message("assistant", [r["response"]])],
            context=r["context"],
        )
        for r in records
    ]
    async with (
        DefaultAzureCredential() as credential,
        AIProjectClient(endpoint=os.environ["FOUNDRY_PROJECT_ENDPOINT"], credential=credential) as project,
    ):
        evals = FoundryEvals(project_client=project, model=judge, evaluators=evaluators, timeout=900.0)
        return await evals.evaluate(items, eval_name=name)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("records", type=Path, help="JSONL written by scripts/eval_live.py (<report>.records.jsonl)")
    ap.add_argument("--evaluators", nargs="+", default=list(QUALITY_EVALUATORS), help="built-in evaluator names")
    ap.add_argument("--judge", default=None, help="judge deployment (default FOUNDRY_MODEL_DEPLOYMENT or 'fast')")
    ap.add_argument("--limit", type=int, default=None, help="evaluate only the first N records")
    ap.add_argument("--out", type=Path, default=None, help="summary JSON (default data/eval/foundry_<stem>.json)")
    ap.add_argument("--env", type=Path, default=None, help=".env file to load (default: search upwards)")
    args = ap.parse_args(argv)
    load_dotenv(args.env)
    sys.stdout.reconfigure(encoding="utf-8")
    if not os.getenv("FOUNDRY_PROJECT_ENDPOINT"):
        print("FOUNDRY_PROJECT_ENDPOINT is not set (see docs/AZURE_SETUP.md)", file=sys.stderr)
        return 2

    records = read_records(args.records)[: args.limit]
    judge = args.judge or os.getenv("FOUNDRY_MODEL_DEPLOYMENT", "fast")
    stem = args.records.name.removesuffix(".records.jsonl").removesuffix(".jsonl")
    name = f"fulcrum-insights-{stem}"
    print(f"evaluating {len(records)} moments with {', '.join(args.evaluators)} (judge: {judge}) ...")
    results = asyncio.run(evaluate(records, args.evaluators, judge, name))

    summary = {
        "kind": "foundry",
        "generated_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "records": args.records.as_posix(),
        "eval_name": name,
        **summarise_eval(results, records, evaluators=args.evaluators, judge=judge),
    }
    out = args.out or Path("data/eval") / f"foundry_{stem}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8")
    print(json.dumps({k: v for k, v in summary.items() if k not in ("per_item", "lowest")}, indent=2))
    print(f"\nsummary -> {out}")
    return 0 if summary["status"] == "completed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
