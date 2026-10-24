"""CLI: `python -m fulcrum.sim --home NBA --away RVR --seed 7 --speed 10 [--out match.jsonl]`.

Without `--out` the match is published to the bus selected by FULCRUM_BUS. With `--out` it is
written as JSONL of `{"topic", "data"}` records and the bus is skipped (speed is ignored).
"""

from __future__ import annotations

import argparse
import asyncio
import sys
import time

from fulcrum.contracts import MatchConfig

from .players import load_teams
from .runner import record_match, run_match


def build_parser() -> argparse.ArgumentParser:
    """Argument parser for the simulator CLI."""
    ap = argparse.ArgumentParser(prog="python -m fulcrum.sim", description="Fulcrum synthetic match simulator")
    ap.add_argument("--home", default="NBA", help="home team_id from data/teams.json")
    ap.add_argument("--away", default="RVR", help="away team_id from data/teams.json")
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--speed", type=float, default=10.0, help="sim-time multiplier; 0 = as fast as possible")
    ap.add_argument("--out", help="write JSONL of {topic,data} here instead of publishing to the bus")
    ap.add_argument("--match-id", help="default: <home>-<away>-s<seed> (lowercase)")
    ap.add_argument("--no-tracking", action="store_true", help="do not emit tracking frames")
    ap.add_argument("--tracking-hz", type=float, default=1.0)
    return ap


def main(argv: list[str] | None = None) -> int:
    """Entry point. Returns a process exit code."""
    args = build_parser().parse_args(argv)
    teams = load_teams()
    for code in (args.home, args.away):
        if code not in teams:
            print(f"unknown team {code!r}; choose from {sorted(teams)}", file=sys.stderr)
            return 2
    if args.home == args.away:
        print("home and away must differ", file=sys.stderr)
        return 2
    config = MatchConfig(
        match_id=args.match_id or f"{args.home}-{args.away}-s{args.seed}".lower(),
        home_team_id=args.home,
        away_team_id=args.away,
        seed=args.seed,
        speed=args.speed if args.speed > 0 else 10.0,
        emit_tracking=not args.no_tracking,
        tracking_hz=args.tracking_hz,
    )
    home, away = teams[args.home], teams[args.away]
    t0 = time.perf_counter()
    if args.out:
        n = record_match(config, args.out, home=home, away=away)
        print(f"wrote {n} records to {args.out} in {time.perf_counter() - t0:.2f}s", file=sys.stderr)
    else:
        h, a = asyncio.run(run_match(config, home=home, away=away, speed=args.speed))
        print(f"{config.match_id}: {args.home} {h}-{a} {args.away} ({time.perf_counter() - t0:.1f}s)", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
