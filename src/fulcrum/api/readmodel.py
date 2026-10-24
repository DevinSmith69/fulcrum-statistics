"""Per-match in-memory read models (ring buffers) that back REST and the WebSocket catch-up snapshot.

Persistence (Cosmos DB) is a stretch goal; everything here is process-local and bounded.
"""

from __future__ import annotations

from collections import OrderedDict, deque
from dataclasses import dataclass, field
from typing import Any

from fulcrum.contracts import (
    EventType,
    Insight,
    MatchConfig,
    MatchEvent,
    MatchState,
    Moment,
    OverlayCue,
    Recap,
    Team,
    WinProbPoint,
)

MAX_EVENTS = 20_000  # a simulated match is ~1,400–2,000 events
MAX_WINPROB = 20_000
MAX_MOMENTS = 500
MAX_INSIGHTS = 500
MAX_CUES = 5_000


@dataclass
class MatchReadModel:
    """Everything the API knows about one match."""

    match_id: str
    config: MatchConfig | None = None
    home: Team | None = None
    away: Team | None = None
    status: str = "scheduled"  # scheduled | live | finished | error
    error: str | None = None
    events: deque[MatchEvent] = field(default_factory=lambda: deque(maxlen=MAX_EVENTS))
    state: MatchState | None = None
    winprob: deque[WinProbPoint] = field(default_factory=lambda: deque(maxlen=MAX_WINPROB))
    moments: deque[Moment] = field(default_factory=lambda: deque(maxlen=MAX_MOMENTS))
    insights: OrderedDict[str, Insight] = field(default_factory=OrderedDict)
    cues: deque[OverlayCue] = field(default_factory=lambda: deque(maxlen=MAX_CUES))
    recaps: dict[tuple[str, str], Recap] = field(default_factory=dict)
    last_seq: int = -1
    clock_ms: int = 0
    score_home: int = 0
    score_away: int = 0
    _moment_ids: set[str] = field(default_factory=set)
    _cue_ids: set[str] = field(default_factory=set)

    # ------------------------------------------------------------------ writers (return False on duplicates)

    def add_event(self, ev: MatchEvent) -> bool:
        """Append an event; de-duplicated on seq (consumers must be idempotent)."""
        if ev.seq <= self.last_seq:
            return False
        self.last_seq = ev.seq
        self.events.append(ev)
        self.clock_ms = max(self.clock_ms, ev.clock_ms)
        self.score_home = max(self.score_home, ev.score_home)
        self.score_away = max(self.score_away, ev.score_away)
        if ev.type in (EventType.MATCH_START, EventType.PERIOD_START, EventType.KICK_OFF) and self.status in (
            "scheduled",
            "error",
        ):
            self.status = "live"
        if ev.type == EventType.MATCH_END:
            self.status = "finished"
        return True

    def set_state(self, state: MatchState) -> bool:
        """Keep the latest state and append a WP point (states arrive ≤ 1 Hz sim time)."""
        if self.state is not None and state.seq < self.state.seq:
            return False
        self.state = state
        self.clock_ms = max(self.clock_ms, state.clock_ms)
        # max(): the event feed runs ahead of the engine, so a lagging state must not undo a goal
        self.score_home = max(self.score_home, state.score_home)
        self.score_away = max(self.score_away, state.score_away)
        wp = state.win_prob
        if not self.winprob or self.winprob[-1].seq != state.seq:
            self.winprob.append(
                WinProbPoint(clock_ms=state.clock_ms, seq=state.seq, home=wp.home, draw=wp.draw, away=wp.away)
            )
        if self.status == "scheduled":
            self.status = "live"
        return True

    def add_moment(self, moment: Moment) -> bool:
        if moment.moment_id in self._moment_ids:
            return False
        self._moment_ids.add(moment.moment_id)
        self.moments.append(moment)
        return True

    def add_insight(self, insight: Insight) -> bool:
        """Insert or replace (a later copy may carry more variants, e.g. translations)."""
        new = insight.insight_id not in self.insights
        self.insights[insight.insight_id] = insight
        while len(self.insights) > MAX_INSIGHTS:
            self.insights.popitem(last=False)
        return new

    def add_cue(self, cue: OverlayCue) -> bool:
        if cue.cue_id in self._cue_ids:
            return False
        self._cue_ids.add(cue.cue_id)
        self.cues.append(cue)
        return True

    def add_recap(self, recap: Recap) -> None:
        self.recaps[(str(recap.persona), recap.language)] = recap

    # ------------------------------------------------------------------ readers

    def events_since(self, since_seq: int) -> list[MatchEvent]:
        return [e for e in self.events if e.seq > since_seq]

    def team_for(self, team_id: str | None) -> Team | None:
        return next((t for t in (self.home, self.away) if t and t.team_id == team_id), None)

    def player_name(self, player_id: str | None) -> str:
        for team in (self.home, self.away):
            for p in team.players if team else []:
                if p.player_id == player_id:
                    return p.short_name
        return player_id or ""

    def summary(self) -> dict[str, Any]:
        """Row for `GET /api/matches`."""
        return {
            "match_id": self.match_id,
            "home_team_id": self.home.team_id if self.home else (self.config.home_team_id if self.config else None),
            "away_team_id": self.away.team_id if self.away else (self.config.away_team_id if self.config else None),
            "status": self.status,
            "clock_ms": self.clock_ms,
            "score": {"home": self.score_home, "away": self.score_away},
        }


class Registry:
    """match_id → read model, plus the team reference data."""

    def __init__(self, teams: dict[str, Team]) -> None:
        self.teams = teams
        self.matches: dict[str, MatchReadModel] = {}

    def create(self, config: MatchConfig) -> MatchReadModel:
        rm = self.matches.get(config.match_id) or MatchReadModel(config.match_id)
        rm.config = config
        rm.home = self.teams.get(config.home_team_id)
        rm.away = self.teams.get(config.away_team_id)
        self.matches[config.match_id] = rm
        return rm

    def get(self, match_id: str) -> MatchReadModel | None:
        return self.matches.get(match_id)

    def get_or_create(self, match_id: str) -> MatchReadModel:
        """Matches started outside this process (e.g. the sim Container App) appear on their first message."""
        if match_id not in self.matches:
            self.matches[match_id] = MatchReadModel(match_id)
        return self.matches[match_id]

    def learn_teams(self, rm: MatchReadModel, ev: MatchEvent) -> None:
        """Fill home/away from `match_start.extra` when the match was not created via POST."""
        if rm.home and rm.away:
            return
        h, a = ev.extra.get("home_team_id"), ev.extra.get("away_team_id")
        if h and a:
            rm.home = rm.home or self.teams.get(str(h))
            rm.away = rm.away or self.teams.get(str(a))
