"""Print the moment timeline for a recorded match, for tuning thresholds in `fulcrum/engine/params.py`.

Usage:
    uv run python scripts/print_moments.py tests/fixtures/handmade_nba_rvr.jsonl
    uv run python scripts/print_moments.py data/matches/sample_seed7.jsonl --all-facts --min-sig 0.3
"""

from __future__ import annotations

import argparse
import sys
from collections import Counter

from fulcrum.engine.replay import run_file

# Facts shown inline per moment type (the rest with --all-facts).
KEY_FACTS: dict[str, list[str]] = {
    "goal": ["scorer", "assist", "xg", "score_after", "wp_scorer_before", "wp_scorer_after", "wp_scorer_delta"],
    "big_chance": ["shooter", "xg", "outcome", "distance_m", "team_xg_total"],
    "wp_swing": ["wp_home_before", "wp_home_after", "wp_home_delta", "wp_away_delta", "cause_event"],
    "pressure_shift": ["shift_kind", "pressure_index_home_delta", "pressure_index_away_delta", "shots_last_5"],
    "phase_change": ["phase_before", "phase_after", "chaos_index", "control_index_home", "control_index_away"],
    "tempo_change": ["tempo_ppm", "tempo_match_avg", "tempo_delta_pct"],
    "red_card": ["player", "card", "wp_home_delta", "wp_away_delta"],
    "speed_record": ["player", "record_type", "speed_kmh", "previous_match_high"],
    "elite_pass": ["passer", "recipient", "difficulty", "expected_completion_pct", "progressive_m"],
    "player_milestone": ["player", "milestone", "value"],
    "momentum_run": ["team", "shots_last_5", "opp_shots_last_5", "xg_last_5", "final_third_possessions_in_row"],
    "half_time": ["xg_home", "xg_away", "possession_home", "wp_home", "wp_draw", "wp_away", "top_player_1"],
    "full_time": ["xg_home", "xg_away", "possession_home", "top_player_1", "top_player_1_rating"],
}
BASE = {"minute", "score", "home_team", "away_team"}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("fixture", help="JSONL of {topic, data} rows (WS1 --out format)")
    ap.add_argument("--min-sig", type=float, default=0.0, help="hide moments below this significance")
    ap.add_argument("--all-facts", action="store_true", help="print the full fact pack for each moment")
    args = ap.parse_args(argv)

    engine = run_file(args.fixture)
    moments = [m for m in engine.moments if m.significance >= args.min_sig]
    for m in moments:
        facts = {f.key: f for f in m.facts}
        keys = [k for k in facts if k not in BASE] if args.all_facts else KEY_FACTS.get(m.type, [])
        shown = ", ".join(
            f"{k}={facts[k].value}{facts[k].unit or ''}" if facts[k].unit in ("%", "m") else f"{k}={facts[k].value}"
            for k in keys
            if k in facts
        )
        team = m.team_id or "-"
        print(
            f"{facts['minute'].value:>7} {facts['score'].value:>5}  {m.type:<17} "
            f"{m.significance:4.2f} {team:<4} {shown}"
        )

    state = engine.snapshot()
    counts = Counter(m.type for m in moments)
    print("-" * 100)
    print(f"{len(moments)} moments (target 20-35): " + ", ".join(f"{t}={n}" for t, n in sorted(counts.items())))
    print(
        f"final {state.score_home}-{state.score_away} | xG {state.home.xg:.2f}-{state.away.xg:.2f} | "
        f"shots {state.home.shots}-{state.away.shots} | passes {state.home.passes}-{state.away.passes} | "
        f"possession {state.home.possession_pct:.0f}%-{state.away.possession_pct:.0f}%"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
