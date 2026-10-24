"""Calibrate the simulator against MODEL_SPEC §1.9.

    uv run python scripts/calibrate_sim.py [--seeds 200] [--workers N] [--markdown]

Runs `--seeds` matches at speed 0 cycling through every ordered pairing of the four clubs, plus
`--seeds` NBA v HLC matches (home/away alternating) for the stronger-side win rate, then prints
mean ± sd per metric against its target. Tracking frames are not materialised (positions are still
simulated, so the event stream is identical), which keeps a full run to a few seconds per core.
Exit code 1 if any metric is out of range.
"""

from __future__ import annotations

import argparse
import itertools
import statistics
import sys
from concurrent.futures import ProcessPoolExecutor
from dataclasses import dataclass

from fulcrum.contracts import EventType, MatchConfig, MatchEvent, Outcome, Position
from fulcrum.sim.engine import MatchSimulator
from fulcrum.sim.players import load_teams


@dataclass(frozen=True)
class Target:
    key: str
    label: str
    lo: float
    hi: float
    fmt: str = "{:.0f}"
    on_mean: bool = True  # the target applies to the mean (all of §1.9 is stated per-match means)


TARGETS = [
    Target("events", "Events (excl. tracking)", 1400, 2000),
    Target("passes", "Passes", 800, 1000),
    Target("pass_completion", "Pass completion %", 78, 86, "{:.1f}"),
    Target("shots", "Shots", 20, 30, "{:.1f}"),
    Target("on_target_pct", "Shots on target %", 30, 40, "{:.1f}"),
    Target("xg", "Total xG", 2.4, 3.2, "{:.2f}"),
    Target("goals", "Goals", 2.4, 3.1, "{:.2f}"),
    Target("stronger_wins_pct", "Stronger side (NBA v HLC) wins %", 50, 60, "{:.1f}"),
    Target("corners", "Corners", 8, 12, "{:.1f}"),
    Target("fouls", "Fouls", 18, 26, "{:.1f}"),
    Target("yellows", "Yellow cards", 2.5, 4.5, "{:.2f}"),
]
INFO = [  # §1.8 tracking calibration, reported but not part of the §1.9 acceptance table
    Target("outfield_km", "Outfield distance km/90 (per player)", 9.5, 12.0, "{:.2f}"),
    Target("gk_km", "GK distance km/90", 4.0, 6.0, "{:.2f}"),
    Target("top_speed", "Outfield top speed km/h (per player)", 27, 35, "{:.1f}"),
    Target("reds", "Red cards (info)", 0, 1, "{:.2f}"),
    Target("subs", "Substitutions (info, both teams)", 6, 10, "{:.1f}"),
]


def run_one(job: tuple[str, str, int]) -> dict[str, float]:
    """Simulate one match and return its §1.9 metrics."""
    home_id, away_id, seed = job
    teams = load_teams()
    cfg = MatchConfig(match_id=f"cal-{home_id}-{away_id}-{seed}", home_team_id=home_id, away_team_id=away_id, seed=seed)
    cfg = cfg.model_copy(update={"emit_tracking": False})
    sim = MatchSimulator(cfg, teams[home_id], teams[away_id])
    events: list[MatchEvent] = [e for e in sim.run() if isinstance(e, MatchEvent)]
    passes = [e for e in events if e.type == EventType.PASS]
    shots = [e for e in events if e.type == EventType.SHOT]
    on_target = [s for s in shots if s.shot and s.shot.on_target]
    cards = [e for e in events if e.type == EventType.CARD]
    totals = sim.tracking.totals()
    players = {p.player_id: p for t in (teams[home_id], teams[away_id]) for p in t.players}
    full_90 = {pid for pid in totals if pid not in {e.substitution.player_off_id for e in events if e.substitution}}
    full_90 -= {pid for pid in totals if pid in {e.substitution.player_on_id for e in events if e.substitution}}
    match_min = events[-1].clock_ms / 60000
    scale = 90.0 / match_min
    outfield = [totals[p][0] / 1000 * scale for p in full_90 if players[p].position != Position.GK]
    gks = [totals[p][0] / 1000 * scale for p in full_90 if players[p].position == Position.GK]
    tops = [totals[p][1] for p in full_90 if players[p].position != Position.GK]
    return {
        "home": home_id,
        "away": away_id,
        "score_home": sim.score[0],
        "score_away": sim.score[1],
        "events": len(events),
        "passes": len(passes),
        "pass_completion": 100 * sum(e.outcome == Outcome.SUCCESS for e in passes) / max(len(passes), 1),
        "shots": len(shots),
        "on_target_pct": 100 * len(on_target) / max(len(shots), 1),
        "xg": sum(s.shot.xg for s in shots if s.shot),
        "goals": sum(sim.score),
        "corners": sum(e.type == EventType.CORNER for e in events),
        "fouls": sum(e.type == EventType.FOUL for e in events),
        "yellows": sum(1 for c in cards if c.card and c.card.colour == "yellow"),
        "reds": sum(1 for c in cards if c.card and c.card.colour != "yellow"),
        "subs": sum(e.type == EventType.SUBSTITUTION for e in events),
        "outfield_km": statistics.fmean(outfield) if outfield else 0.0,
        "gk_km": statistics.fmean(gks) if gks else 0.0,
        "top_speed": statistics.fmean(tops),
        "top_speed_min": min(tops),
        "top_speed_max": max(tops),
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--seeds", type=int, default=200)
    ap.add_argument("--workers", type=int, default=None)
    ap.add_argument("--markdown", action="store_true", help="print a markdown table (for the README)")
    args = ap.parse_args(argv)
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    team_ids = sorted(load_teams())
    pairs = list(itertools.permutations(team_ids, 2))
    league = [(*pairs[i % len(pairs)], 1000 + i) for i in range(args.seeds)]
    strong = [(("NBA", "HLC") if i % 2 == 0 else ("HLC", "NBA")) + (5000 + i,) for i in range(args.seeds)]

    with ProcessPoolExecutor(max_workers=args.workers) as ex:
        league_res = list(ex.map(run_one, league, chunksize=4))
        strong_res = list(ex.map(run_one, strong, chunksize=4))

    series: dict[str, list[float]] = {k: [r[k] for r in league_res] for k in league_res[0] if k not in ("home", "away")}
    wins = [
        100.0 * ((r["score_home"] > r["score_away"]) if r["home"] == "NBA" else (r["score_away"] > r["score_home"]))
        for r in strong_res
    ]
    series["stronger_wins_pct"] = wins
    draws = sum(r["score_home"] == r["score_away"] for r in strong_res) / len(strong_res) * 100

    ok_all = True
    rows = []
    for t in TARGETS + INFO:
        vals = series[t.key]
        mean, sd = statistics.fmean(vals), statistics.pstdev(vals)
        ok = t.lo <= mean <= t.hi
        if t in TARGETS:
            ok_all &= ok
        rows.append((t, mean, sd, ok))

    title = f"Calibration: {args.seeds} seeds (league rotation) + {args.seeds} NBA v HLC"
    if args.markdown:
        print(f"_{title}_\n")
        print("| Metric (per match, both teams) | Mean ± sd | Target | |")
        print("|---|---|---|---|")
        for t, mean, sd, ok in rows:
            m = f"{t.fmt.format(mean)} ± {t.fmt.format(sd)}"
            print(f"| {t.label} | {m} | {t.fmt.format(t.lo)} – {t.fmt.format(t.hi)} | {'✅' if ok else '❌'} |")
    else:
        print(title)
        print(f"{'Metric':<40} {'mean ± sd':>20} {'target':>16}")
        for t, mean, sd, ok in rows:
            m = f"{t.fmt.format(mean)} ± {t.fmt.format(sd)}"
            rng = f"{t.fmt.format(t.lo)} – {t.fmt.format(t.hi)}"
            print(f"{t.label:<40} {m:>20} {rng:>16}  {'OK' if ok else 'OUT'}")
    print(
        f"\nNBA v HLC draws {draws:.1f}% · per-player top speed range "
        f"{min(series['top_speed_min']):.1f}–{max(series['top_speed_max']):.1f} km/h"
    )
    return 0 if ok_all else 1


if __name__ == "__main__":
    sys.exit(main())
