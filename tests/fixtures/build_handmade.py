"""Build `tests/fixtures/handmade_nba_rvr.jsonl` — a hand-scripted, contract-valid match for engine/API tests.

Run: `uv run python tests/fixtures/build_handmade.py` (deterministic; the output is committed).

Script (NBA home v RVR away, football minutes):
   1'  NBA 14-pass sequence ending in a speculative shot          (long pass sequence)
   4'  scrappy spell: six turnovers in two minutes                (turnovers)
   8'  RVR elite pass Orrell → Ironside, big chance saved, corner  (elite_pass, big_chance)
  15'  NBA goal: Loxley → Brennick                                 (goal)
  27'  Aldane 118 km/h shot off target                             (speed_record, shot)
  32'  tracking burst, Fenwick-Shaw sprints at 34.6 km/h            (speed_record, sprint)
  36'  Yardley (RVR) booked
  45+1 half time
  55'  Yardley second yellow → red                                 (red_card)
  60'–66' NBA siege: shots with no reply                           (momentum_run)
  78'  RVR equaliser: Draycott → Ironside                          (goal)
  85'  Brennick misses a 0.55 xG chance                             (big_chance)
  90+3 full time, 1-1
Everything in between is deterministic filler possession play.
"""

from __future__ import annotations

import json
import math
import random
from pathlib import Path

from fulcrum.contracts import (
    TOPIC_EVENTS,
    TOPIC_TRACKING,
    BodyPart,
    CardAttributes,
    EventType,
    MatchEvent,
    Outcome,
    PassAttributes,
    PlayerFrame,
    ShotAttributes,
    TrackingFrame,
)

MATCH_ID = "fixture-nba-rvr"
HOME, AWAY = "NBA", "RVR"
OUT = Path(__file__).with_name("handmade_nba_rvr.jsonl")
H1_STOPPAGE_MS = 90_000
P2_START_MS = 45 * 60_000 + H1_STOPPAGE_MS
E = EventType


def pid(team: str, n: int) -> str:
    return f"{team}-{n:02d}"


def other(team: str) -> str:
    return AWAY if team == HOME else HOME


class Builder:
    def __init__(self) -> None:
        self.rows: list[dict] = []
        self.seq = 0
        self.clock = 0
        self.period = 1
        self.poss_id = 0
        self.poss_team: str | None = None
        self.score = {HOME: 0, AWAY: 0}
        self.frame_id = 0
        self.rng = random.Random(7)

    # -- time
    def at(self, minute: float) -> None:
        """Jump forward to a football minute (never backwards)."""
        base = 0 if self.period == 1 else P2_START_MS - 45 * 60_000
        self.clock = max(self.clock, int(base + minute * 60_000))

    def minute(self) -> float:
        base = 0 if self.period == 1 else P2_START_MS - 45 * 60_000
        return (self.clock - base) / 60_000

    # -- raw emit
    def ev(self, type_: EventType, team: str | None = None, player: str | None = None, dt: float = 0.0, **kw) -> None:
        self.clock += int(dt * 1000)
        if team and type_ in (
            E.PASS,
            E.CARRY,
            E.DRIBBLE,
            E.SHOT,
            E.KICK_OFF,
            E.GOAL_KICK,
            E.THROW_IN,
            E.CORNER,
            E.FREE_KICK,
            E.INTERCEPTION,
            E.BALL_RECOVERY,
            E.POSSESSION_CHANGE,
            E.CLEARANCE,
        ):
            if team != self.poss_team:
                self.poss_id += 1
                self.poss_team = team
        model = MatchEvent(
            match_id=MATCH_ID,
            seq=self.seq,
            period=self.period,
            clock_ms=self.clock,
            type=type_,
            team_id=team,
            player_id=player,
            possession_id=self.poss_id or None,
            score_home=self.score[HOME],
            score_away=self.score[AWAY],
            ts="2026-10-24T15:00:00Z",
            event_id=f"ev{self.seq:05d}",
            **kw,
        )
        self.rows.append({"topic": TOPIC_EVENTS, "data": model.model_dump(mode="json", by_alias=True)})
        self.seq += 1

    # -- football actions (team-relative coordinates)
    def pass_(self, team, passer, recipient, x, y, ex, ey, ok=True, difficulty=None, dt=3.0, **attrs):
        length = math.hypot(ex - x, ey - y)
        progressive = ex - x
        if difficulty is None:
            difficulty = round(min(8.0, 0.5 + 0.06 * length + 0.05 * max(progressive, 0)), 1)
        pa = PassAttributes(
            length_m=round(length, 1),
            progressive_m=round(progressive, 1),
            ball_speed_kmh=round(45 + 0.6 * length),
            expected_completion=round(1 - difficulty / 10, 2),
            difficulty=difficulty,
            recipient_id=recipient,
            **attrs,
        )
        self.ev(
            E.PASS,
            team,
            passer,
            dt,
            x=x,
            y=y,
            end_x=ex,
            end_y=ey,
            outcome=Outcome.SUCCESS if ok else Outcome.FAIL,
            **{"pass": pa},
        )

    def shot(self, team, player, x, y, xg, speed, outcome, assist=None, body=BodyPart.RIGHT_FOOT, dt=2.0):
        dist = math.hypot(105 - x, 34 - y)
        on_target = outcome in (Outcome.GOAL, Outcome.SAVED)
        sa = ShotAttributes(
            distance_m=round(dist, 1),
            angle_rad=0.4,
            body_part=body,
            xg=xg,
            shot_speed_kmh=speed,
            on_target=on_target,
            big_chance=xg >= 0.30,
            assist_player_id=assist,
        )
        self.ev(E.SHOT, team, player, dt, x=x, y=y, end_x=105, end_y=34, outcome=outcome, shot=sa)

    def goal(self, team, scorer):
        self.score[team] += 1
        self.ev(E.GOAL, team, scorer, 0.5, x=100, y=34, outcome=Outcome.GOAL)

    def lose(self, team, x, y, dt=1.5):
        """Opponent wins the ball at team-relative (x, y) of `team` → opponent frame (105-x, 68-y)."""
        opp = other(team)
        self.ev(
            E.INTERCEPTION, opp, pid(opp, self.rng.choice([3, 4, 6])), dt, x=105 - x, y=68 - y, outcome=Outcome.SUCCESS
        )

    def possession(self, team: str, n: int, x0: float = 25.0, gain: float = 5.0, lost: bool = True) -> None:
        """Filler: `n` short completed passes moving up the pitch, then the ball is lost."""
        players = [2, 3, 4, 5, 6, 7, 8, 9, 10, 11]
        x, y = x0, 20 + self.rng.random() * 28
        holder = self.rng.choice(players)
        for _ in range(n):
            nxt = self.rng.choice([p for p in players if p != holder])
            ex = min(68.0, x + gain * self.rng.random())
            ey = min(64.0, max(4.0, y + self.rng.uniform(-12, 12)))
            self.pass_(
                team,
                pid(team, holder),
                pid(team, nxt),
                round(x, 1),
                round(y, 1),
                round(ex, 1),
                round(ey, 1),
                dt=2.5 + self.rng.random() * 2,
            )
            x, y, holder = ex, ey, nxt
        if lost:
            self.pass_(
                team,
                pid(team, holder),
                pid(team, 9),
                round(x, 1),
                round(y, 1),
                min(80.0, x + 18),
                round(y, 1),
                ok=False,
                dt=3.0,
            )
            self.lose(team, min(80.0, x + 12), y)

    def fill(self, until_minute: float, first: str = HOME) -> None:
        """Alternate filler possessions until the clock reaches `until_minute`."""
        team = first
        while self.minute() < until_minute - 0.6:
            self.possession(team, n=self.rng.randint(3, 7))
            team = other(team)
            self.clock += 4000

    def frame(self, sprinter: str | None = None, sprint_kmh: float = 0.0) -> None:
        players = []
        for team, side in ((HOME, 1), (AWAY, -1)):
            for n in range(1, 12):
                p = pid(team, n)
                ax = 52.5 - side * (45 - 8 * ((n - 1) // 4 + (n > 1)))
                ay = 6 + 6 * ((n * 7) % 10)
                speed = sprint_kmh if p == sprinter else 8.0 + (n % 5)
                drift = (self.frame_id % 60) * (speed / 3.6) * 0.1
                players.append(
                    PlayerFrame(
                        player_id=p, team_id=team, x=round(min(104.0, ax + drift), 2), y=round(ay, 2), speed_kmh=speed
                    )
                )
        fr = TrackingFrame(
            match_id=MATCH_ID,
            frame_id=self.frame_id,
            period=self.period,
            clock_ms=self.clock,
            ball_x=52.5,
            ball_y=34.0,
            ball_speed_kmh=30.0,
            players=players,
        )
        self.rows.append({"topic": TOPIC_TRACKING, "data": fr.model_dump(mode="json")})
        self.frame_id += 1


def build() -> list[dict]:
    b = Builder()
    extra = {"home_team_id": HOME, "away_team_id": AWAY}
    b.ev(E.MATCH_START, extra=extra)
    b.ev(E.PERIOD_START)
    b.ev(E.KICK_OFF, HOME, pid(HOME, 10), x=52.5, y=34)

    # 1' — long NBA sequence, 14 completed passes, ending in a speculative shot
    b.possession(HOME, n=14, x0=30, gain=3.5, lost=False)
    b.pass_(HOME, pid(HOME, 8), pid(HOME, 10), 70, 30, 82, 34)
    b.shot(HOME, pid(HOME, 10), 82, 34, 0.06, 92, Outcome.OFF_TARGET)
    b.ev(E.GOAL_KICK, AWAY, pid(AWAY, 1), dt=15, x=5.5, y=34)

    # 4' — scrappy spell: six quick turnovers
    b.at(4)
    team = AWAY
    for _ in range(6):
        b.pass_(team, pid(team, 6), pid(team, 9), 45, 30, 75, 30, ok=False, dt=3)
        b.lose(team, 60, 30)
        team = other(team)
    b.fill(8, first=HOME)

    # 8' — RVR elite pass then a big chance, saved, corner
    b.at(8)
    b.pass_(AWAY, pid(AWAY, 6), pid(AWAY, 8), 40, 34, 55, 30)
    b.pass_(AWAY, pid(AWAY, 8), pid(AWAY, 10), 55, 30, 86, 38, difficulty=7.8, line_breaking=True, is_through_ball=True)
    b.shot(AWAY, pid(AWAY, 10), 94, 36, 0.42, 101, Outcome.SAVED, assist=pid(AWAY, 8))
    b.ev(E.SAVE, HOME, pid(HOME, 1), 0.5, x=2, y=34, outcome=Outcome.SUCCESS)
    b.ev(E.CORNER, AWAY, pid(AWAY, 9), 25, x=105, y=0.5)
    b.pass_(AWAY, pid(AWAY, 9), pid(AWAY, 3), 105, 0.5, 98, 34, ok=False, is_cross=True, height="high")
    b.ev(E.CLEARANCE, HOME, pid(HOME, 3), 1, x=8, y=34, end_x=40, end_y=30)
    b.fill(15, first=HOME)

    # 15' — NBA goal, Loxley → Brennick
    b.at(15)
    b.pass_(HOME, pid(HOME, 6), pid(HOME, 8), 45, 34, 62, 28)
    b.ev(E.CARRY, HOME, pid(HOME, 8), 3, x=62, y=28, end_x=78, end_y=30, outcome=Outcome.SUCCESS)
    b.pass_(HOME, pid(HOME, 8), pid(HOME, 10), 78, 30, 95, 35, into_box=True, is_through_ball=True)
    b.shot(HOME, pid(HOME, 10), 95, 35, 0.35, 104, Outcome.GOAL, assist=pid(HOME, 8))
    b.goal(HOME, pid(HOME, 10))
    b.ev(E.KICK_OFF, AWAY, pid(AWAY, 10), 75, x=52.5, y=34)
    b.fill(27, first=AWAY)

    # 27' — Aldane rocket off target
    b.at(27)
    b.pass_(HOME, pid(HOME, 7), pid(HOME, 9), 50, 50, 76, 52)
    b.shot(HOME, pid(HOME, 9), 80, 48, 0.05, 118, Outcome.OFF_TARGET)
    b.ev(E.GOAL_KICK, AWAY, pid(AWAY, 1), 18, x=5.5, y=34)
    b.fill(32, first=AWAY)

    # 32' — one minute of 1 Hz tracking; Fenwick-Shaw sprints
    b.at(32)
    for i in range(60):
        b.frame(sprinter=pid(HOME, 11) if 20 <= i < 30 else None, sprint_kmh=34.6)
        b.clock += 1000
    b.fill(36, first=HOME)

    # 36' — Yardley booked
    b.at(36)
    b.ev(E.FOUL, AWAY, pid(AWAY, 3), x=40, y=30)
    b.ev(E.CARD, AWAY, pid(AWAY, 3), 20, outcome=Outcome.YELLOW, card=CardAttributes(colour="yellow"))
    b.ev(E.FREE_KICK, HOME, pid(HOME, 6), 30, x=65, y=38)
    b.fill(46, first=HOME)

    b.at(45 + H1_STOPPAGE_MS / 60_000)
    b.ev(E.PERIOD_END)

    # second half
    b.period, b.clock = 2, P2_START_MS
    b.ev(E.PERIOD_START)
    b.ev(E.KICK_OFF, AWAY, pid(AWAY, 10), x=52.5, y=34)
    b.fill(55, first=AWAY)

    # 55' — Yardley second yellow
    b.at(55)
    b.ev(E.FOUL, AWAY, pid(AWAY, 3), x=30, y=20)
    b.ev(E.CARD, AWAY, pid(AWAY, 3), 25, outcome=Outcome.RED, card=CardAttributes(colour="second_yellow"))
    b.ev(E.FREE_KICK, HOME, pid(HOME, 4), 30, x=72, y=48)
    b.fill(60, first=HOME)

    # 60'–66' — NBA siege: four shots, no reply
    for k, (minute, xg) in enumerate([(60, 0.12), (61.5, 0.18), (63, 0.09), (64.5, 0.22)]):
        b.at(minute)
        b.pass_(HOME, pid(HOME, 7), pid(HOME, 9), 60, 40, 82, 45)
        b.shot(HOME, pid(HOME, 9 + k % 3), 86, 40, xg, 95, Outcome.SAVED if k % 2 else Outcome.BLOCKED)
        b.ev(E.CORNER, HOME, pid(HOME, 9), 20, x=105, y=67.5)
        b.pass_(HOME, pid(HOME, 9), pid(HOME, 3), 105, 67.5, 97, 34, ok=False, is_cross=True, height="high")
        b.lose(HOME, 97, 34)
    b.fill(78, first=AWAY)

    # 78' — RVR equaliser
    b.at(78)
    b.pass_(AWAY, pid(AWAY, 7), pid(AWAY, 9), 50, 55, 74, 58)
    b.pass_(AWAY, pid(AWAY, 9), pid(AWAY, 10), 74, 58, 96, 32, into_box=True, is_cross=True)
    b.shot(AWAY, pid(AWAY, 10), 96, 32, 0.28, 88, Outcome.GOAL, assist=pid(AWAY, 9), body=BodyPart.HEAD)
    b.goal(AWAY, pid(AWAY, 10))
    b.ev(E.KICK_OFF, HOME, pid(HOME, 10), 70, x=52.5, y=34)
    b.fill(85, first=HOME)

    # 85' — Brennick misses a sitter
    b.at(85)
    b.pass_(HOME, pid(HOME, 11), pid(HOME, 10), 80, 10, 99, 36, into_box=True, is_cross=True)
    b.shot(HOME, pid(HOME, 10), 99, 36, 0.55, 96, Outcome.OFF_TARGET)
    b.ev(E.GOAL_KICK, AWAY, pid(AWAY, 1), 20, x=5.5, y=34)
    b.fill(93, first=AWAY)

    b.at(93)
    b.ev(E.PERIOD_END)
    b.ev(E.MATCH_END, dt=1)
    return b.rows


def main() -> None:
    rows = build()
    with OUT.open("w", encoding="utf-8", newline="\n") as fh:
        for row in rows:
            fh.write(json.dumps(row, separators=(",", ":")) + "\n")
    n_ev = sum(r["topic"] == TOPIC_EVENTS for r in rows)
    print(f"wrote {OUT} ({n_ev} events, {len(rows) - n_ev} frames)")


if __name__ == "__main__":
    main()
