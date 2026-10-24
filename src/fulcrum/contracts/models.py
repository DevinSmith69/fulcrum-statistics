"""
Fulcrum shared contracts — the single source of truth for every message that
crosses a service boundary (simulator -> bus -> engine -> agents -> api -> web).

RULES
- Do NOT change a field name/type without adding an entry to docs/CONTRACT_CHANGES.md
  and regenerating schemas/ (`make schemas`).
- Additive, optional fields are fine.
- Coordinates: metres on a 105 x 68 pitch. Event coordinates are TEAM-RELATIVE:
  the acting team always attacks towards x = 105 (StatsBomb-style). Tracking
  frames use ABSOLUTE coordinates (home attacks towards x = 105 in period 1).
- Time: `clock_ms` is match time in milliseconds since kick-off of the match
  (period 2 starts at 45*60*1000 + first-half stoppage), `ts` is wall-clock UTC.
"""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from typing import Any, Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, model_validator

PITCH_LENGTH_M = 105.0
PITCH_WIDTH_M = 68.0
CONTRACT_VERSION = "1.0.0"


def _uid() -> str:
    return uuid4().hex


def _now() -> datetime:
    return datetime.now(UTC)


class _Base(BaseModel):
    model_config = ConfigDict(extra="forbid", use_enum_values=True)


# --------------------------------------------------------------------------- #
# Reference data (fictional clubs / players only — no real PL names, ever)
# --------------------------------------------------------------------------- #


class Position(StrEnum):
    GK = "GK"
    DF = "DF"
    MF = "MF"
    FW = "FW"


class PlayerRatings(_Base):
    """0-100 ratings that drive the simulator."""

    passing: int = Field(ge=0, le=100)
    finishing: int = Field(ge=0, le=100)
    pace: int = Field(ge=0, le=100)
    tackling: int = Field(ge=0, le=100)
    dribbling: int = Field(ge=0, le=100)
    goalkeeping: int = Field(default=10, ge=0, le=100)


class Player(_Base):
    player_id: str
    name: str
    short_name: str
    shirt_number: int
    position: Position
    role: str = Field(description="Formation slot, e.g. 'LCB', 'CDM', 'RW', 'ST'")
    ratings: PlayerRatings


class Team(_Base):
    team_id: str
    name: str
    short_name: str = Field(max_length=4)
    primary_colour: str = Field(description="Hex, e.g. #1E3A8A")
    secondary_colour: str
    formation: str = Field(examples=["4-3-3"])
    attack_rating: int = Field(ge=0, le=100)
    defence_rating: int = Field(ge=0, le=100)
    pre_match_goal_rate: float = Field(description="Expected goals per 90 vs an average opponent; seeds the WP model")
    players: list[Player] = Field(description="Starting XI first (11), then substitutes")


class MatchConfig(_Base):
    match_id: str = Field(default_factory=_uid)
    home_team_id: str
    away_team_id: str
    seed: int = 42
    speed: float = Field(default=10.0, gt=0, description="Sim-time multiplier. 1 = real time")
    language_default: str = "en"
    venue: str = "Fulcrum Park"
    emit_tracking: bool = True
    tracking_hz: float = Field(default=1.0, gt=0, le=10)


# --------------------------------------------------------------------------- #
# Match events (topic: match-events)
# --------------------------------------------------------------------------- #


class EventType(StrEnum):
    MATCH_START = "match_start"
    PERIOD_START = "period_start"
    PERIOD_END = "period_end"
    MATCH_END = "match_end"
    KICK_OFF = "kick_off"
    PASS = "pass"
    CARRY = "carry"
    DRIBBLE = "dribble"
    SHOT = "shot"
    GOAL = "goal"
    SAVE = "save"
    TACKLE = "tackle"
    INTERCEPTION = "interception"
    BALL_RECOVERY = "ball_recovery"
    CLEARANCE = "clearance"
    PRESSURE = "pressure"
    POSSESSION_CHANGE = "possession_change"
    FOUL = "foul"
    CARD = "card"
    CORNER = "corner"
    FREE_KICK = "free_kick"
    THROW_IN = "throw_in"
    GOAL_KICK = "goal_kick"
    OFFSIDE = "offside"
    SUBSTITUTION = "substitution"


class Outcome(StrEnum):
    SUCCESS = "success"
    FAIL = "fail"
    GOAL = "goal"
    SAVED = "saved"
    OFF_TARGET = "off_target"
    BLOCKED = "blocked"
    WOODWORK = "woodwork"
    YELLOW = "yellow"
    RED = "red"
    NA = "na"


class BodyPart(StrEnum):
    RIGHT_FOOT = "right_foot"
    LEFT_FOOT = "left_foot"
    HEAD = "head"
    OTHER = "other"


class PassAttributes(_Base):
    length_m: float
    progressive_m: float = Field(description="Metres gained towards opponent goal (can be negative)")
    height: Literal["ground", "low", "high"] = "ground"
    ball_speed_kmh: float
    expected_completion: float = Field(ge=0, le=1, description="Model p(complete) before the pass")
    difficulty: float = Field(ge=0, le=10, description="0 trivial … 10 near-impossible")
    line_breaking: bool = False
    into_box: bool = False
    is_cross: bool = False
    is_through_ball: bool = False
    recipient_id: str | None = None


class ShotAttributes(_Base):
    distance_m: float
    angle_rad: float
    body_part: BodyPart
    xg: float = Field(ge=0, le=1)
    shot_speed_kmh: float
    on_target: bool
    big_chance: bool = False
    situation: Literal["open_play", "counter", "set_piece", "penalty", "corner"] = "open_play"
    assist_player_id: str | None = None


class CardAttributes(_Base):
    colour: Literal["yellow", "red", "second_yellow"]
    reason: str = "foul"


class SubstitutionAttributes(_Base):
    player_off_id: str
    player_on_id: str


class MatchEvent(_Base):
    contract_version: str = CONTRACT_VERSION
    event_id: str = Field(default_factory=_uid)
    match_id: str
    seq: int = Field(ge=0, description="Strictly increasing per match; consumers must de-dupe on it")
    ts: datetime = Field(default_factory=_now)
    period: int = Field(ge=1, le=4)
    clock_ms: int = Field(ge=0)
    type: EventType
    team_id: str | None = None
    player_id: str | None = None
    possession_id: int | None = Field(default=None, description="Increments on every change of possession")
    x: float | None = Field(default=None, ge=0, le=PITCH_LENGTH_M)
    y: float | None = Field(default=None, ge=0, le=PITCH_WIDTH_M)
    end_x: float | None = Field(default=None, ge=0, le=PITCH_LENGTH_M)
    end_y: float | None = Field(default=None, ge=0, le=PITCH_WIDTH_M)
    outcome: Outcome = Outcome.NA
    under_pressure: bool = False
    nearest_opponent_m: float | None = None
    pass_: PassAttributes | None = Field(default=None, alias="pass")
    shot: ShotAttributes | None = None
    card: CardAttributes | None = None
    substitution: SubstitutionAttributes | None = None
    score_home: int = 0
    score_away: int = 0
    extra: dict[str, Any] = Field(default_factory=dict)

    model_config = ConfigDict(extra="forbid", use_enum_values=True, populate_by_name=True)


# --------------------------------------------------------------------------- #
# Tracking (topic: tracking-frames) — enables player speed/distance features
# --------------------------------------------------------------------------- #


class PlayerFrame(_Base):
    player_id: str
    team_id: str
    x: float
    y: float
    speed_kmh: float


class TrackingFrame(_Base):
    match_id: str
    frame_id: int
    period: int
    clock_ms: int
    ball_x: float
    ball_y: float
    ball_speed_kmh: float
    players: list[PlayerFrame]


# --------------------------------------------------------------------------- #
# Engine output: state snapshots (topic: match-state)
# --------------------------------------------------------------------------- #


class WinProbability(_Base):
    home: float = Field(ge=0, le=1)
    draw: float = Field(ge=0, le=1)
    away: float = Field(ge=0, le=1)


class TeamLiveStats(_Base):
    team_id: str
    goals: int = 0
    xg: float = 0.0
    shots: int = 0
    shots_on_target: int = 0
    passes: int = 0
    passes_completed: int = 0
    pass_accuracy: float = 0.0
    avg_pass_difficulty: float = 0.0
    possession_pct: float = 0.0
    final_third_entries: int = 0
    box_entries: int = 0
    corners: int = 0
    fouls: int = 0
    yellow_cards: int = 0
    red_cards: int = 0
    pressure_index: float = Field(0.0, description="0-100, exponentially decayed attacking threat")
    control_index: float = Field(0.0, description="0-100, sustained possession quality, last 5 min")
    max_shot_speed_kmh: float = 0.0


class PlayerLiveStats(_Base):
    player_id: str
    team_id: str
    minutes: float = 0.0
    touches: int = 0
    passes: int = 0
    passes_completed: int = 0
    pass_accuracy: float = 0.0
    avg_pass_difficulty: float = 0.0
    key_passes: int = 0
    shots: int = 0
    xg: float = 0.0
    goals: int = 0
    tackles_won: int = 0
    interceptions: int = 0
    distance_km: float = 0.0
    top_speed_kmh: float = 0.0
    sprints: int = 0
    rating: float = Field(6.0, ge=0, le=10)


class MatchPhase(StrEnum):
    CONTROL_HOME = "control_home"
    CONTROL_AWAY = "control_away"
    CHAOS = "chaos"
    BALANCED = "balanced"


class MatchState(_Base):
    match_id: str
    seq: int = Field(description="Seq of the last event applied")
    period: int
    clock_ms: int
    score_home: int
    score_away: int
    home: TeamLiveStats
    away: TeamLiveStats
    win_prob: WinProbability
    phase: MatchPhase = MatchPhase.BALANCED
    chaos_index: float = Field(0.0, description="0-100: turnover rate + short possessions + long-ball share")
    tempo_ppm: float = Field(0.0, description="Passes per minute, rolling 5 min")
    momentum: float = Field(0.0, ge=-100, le=100, description="+ = home on top, - = away on top")
    players: dict[str, PlayerLiveStats] = Field(default_factory=dict)


class WinProbPoint(_Base):
    clock_ms: int
    seq: int
    home: float
    draw: float
    away: float


# --------------------------------------------------------------------------- #
# Moments (topic: moments) — deterministic detection in the engine
# --------------------------------------------------------------------------- #


class MomentType(StrEnum):
    GOAL = "goal"
    BIG_CHANCE = "big_chance"
    WP_SWING = "wp_swing"
    PRESSURE_SHIFT = "pressure_shift"
    PHASE_CHANGE = "phase_change"
    TEMPO_CHANGE = "tempo_change"
    RED_CARD = "red_card"
    SPEED_RECORD = "speed_record"
    ELITE_PASS = "elite_pass"
    PLAYER_MILESTONE = "player_milestone"
    MOMENTUM_RUN = "momentum_run"
    HALF_TIME = "half_time"
    FULL_TIME = "full_time"


class Fact(_Base):
    """An atomic, verifiable fact. Agents may ONLY quote numbers that appear here."""

    key: str = Field(examples=["wp_home_before"])
    label: str = Field(examples=["Home win probability before"])
    value: float | int | str
    unit: str | None = Field(default=None, examples=["%", "km/h", "m", "xG"])


class Moment(_Base):
    contract_version: str = CONTRACT_VERSION
    moment_id: str = Field(default_factory=_uid)
    match_id: str
    type: MomentType
    significance: float = Field(ge=0, le=1)
    period: int
    clock_ms: int
    seq_start: int
    seq_end: int
    team_id: str | None = None
    player_ids: list[str] = Field(default_factory=list)
    wp_before: WinProbability
    wp_after: WinProbability
    facts: list[Fact] = Field(description="The fact pack — the only numbers agents may use")
    trigger: str = Field(description="Human-readable rule that fired, e.g. 'abs(dWP_home) >= 0.05 within 60s'")
    created_at: datetime = Field(default_factory=_now)


# --------------------------------------------------------------------------- #
# Insights (topic: insights) — agent output
# --------------------------------------------------------------------------- #


class Persona(StrEnum):
    ANALYST = "analyst"  # data-hungry: numbers, tactical language
    CASUAL = "casual"  # plain English, emotional, no jargon
    CLUB_FAN = "club_fan"  # partisan to the viewer's favourite club
    PLAYER_LENS = "player_lens"  # everything through the followed player


class TacticalTag(StrEnum):
    CONTROL = "control"
    CHAOS = "chaos"
    PRESSURE_SHIFT = "pressure_shift"
    RHYTHM = "rhythm"
    INDIVIDUAL_BRILLIANCE = "individual_brilliance"
    SET_PIECE = "set_piece"
    TURNING_POINT = "turning_point"


class Explanation(_Base):
    """Moment Analyst output (persona-neutral)."""

    headline: str = Field(max_length=90)
    why_it_matters: str = Field(max_length=400)
    tactical_tag: TacticalTag
    key_fact_keys: list[str] = Field(description="Fact.key values used — must exist in the moment's fact pack")


class InsightVariant(_Base):
    persona: Persona
    language: str = "en"
    perspective_team_id: str | None = Field(default=None, description="Set for club_fan variants")
    perspective_player_id: str | None = Field(default=None, description="Set for player_lens variants")
    headline: str = Field(max_length=90)
    body: str = Field(max_length=300)
    commentary_line: str = Field(max_length=160, description="One spoken-style line, <= 25 words")


class CheckStatus(StrEnum):
    PASSED = "passed"
    REPAIRED = "repaired"  # failed once, regenerated, then passed
    FALLBACK = "fallback"  # deterministic template used


class Insight(_Base):
    contract_version: str = CONTRACT_VERSION
    insight_id: str = Field(default_factory=_uid)
    match_id: str
    moment_id: str
    clock_ms: int
    explanation: Explanation
    variants: list[InsightVariant]
    check_status: CheckStatus
    check_notes: list[str] = Field(default_factory=list)
    agent_trace_id: str | None = Field(default=None, description="OpenTelemetry trace id for the agent run")
    latency_ms: int | None = None
    created_at: datetime = Field(default_factory=_now)


# --------------------------------------------------------------------------- #
# Overlay cues — machine-readable output for a broadcast rendering partner
# --------------------------------------------------------------------------- #


class OverlayTemplate(StrEnum):
    LOWER_THIRD = "lower_third"
    STAT_CARD = "stat_card"
    WP_SWING = "wp_swing"
    PLAYER_TAG = "player_tag"
    PRESSURE_BAR = "pressure_bar"
    RECAP_CARD = "recap_card"


class OverlayAnchor(StrEnum):
    TOP_LEFT = "top_left"
    TOP_RIGHT = "top_right"
    BOTTOM_LEFT = "bottom_left"
    BOTTOM_RIGHT = "bottom_right"
    BOTTOM_CENTER = "bottom_center"


class OverlayStat(_Base):
    label: str
    value: str
    unit: str | None = None


class OverlayCue(_Base):
    contract_version: str = CONTRACT_VERSION
    cue_id: str = Field(default_factory=_uid)
    match_id: str
    moment_id: str | None = None
    insight_id: str | None = None
    template: OverlayTemplate
    anchor: OverlayAnchor = OverlayAnchor.BOTTOM_LEFT
    priority: int = Field(ge=1, le=5, description="5 = interrupt everything (goal)")
    sync_clock_ms: int = Field(description="Match clock the cue is synced to")
    sync_seq: int
    show_at_clock_ms: int
    duration_ms: int = Field(ge=1000, le=30000)
    persona: Persona
    language: str = "en"
    perspective_team_id: str | None = None
    perspective_player_id: str | None = None
    team_id: str | None = None
    player_id: str | None = None
    headline: str
    body: str | None = None
    stats: list[OverlayStat] = Field(default_factory=list)


class Recap(_Base):
    match_id: str
    persona: Persona
    language: str = "en"
    title: str
    summary: str
    key_moment_ids: list[str]
    player_of_the_match_id: str | None = None
    check_status: CheckStatus


class AskRequest(_Base):
    """Body of `POST /api/matches/{id}/ask` ("Ask the studio"): a viewer's question about the live match."""

    question: str = Field(min_length=1, max_length=300)
    persona: Persona | None = Field(default=None, description="Tone of the answer; default casual")
    language: str | None = Field(default=None, description="Answer language (en/es/fr/pt/de); default en")
    favourite_team_id: str | None = Field(default=None, description="For the club_fan persona: whose side to take")


class AskAnswer(_Base):
    """The Studio Analyst's answer, grounded only in this match's data and fact-checked like every insight.

    `answerable` is false when the question can't be answered from the match data (or is off-topic): `answer`
    then politely says what can be asked instead.
    """

    match_id: str
    question: str
    answer: str = Field(max_length=400)
    answerable: bool
    check_status: CheckStatus
    cited_moment_ids: list[str] = Field(default_factory=list, description="Moments the answer relies on")
    fact_keys: list[str] = Field(default_factory=list, description="Context-pack fact keys the answer used")
    persona: Persona = Persona.CASUAL
    language: str = "en"
    latency_ms: int | None = None


class InsightsEnvelope(_Base):
    """Wrapper for the `insights` topic, which carries three message types.

    Build with `InsightsEnvelope.wrap(item)`; `kind` must match the type of `data`.
    """

    kind: Literal["insight", "cue", "recap"]
    data: Insight | OverlayCue | Recap

    @model_validator(mode="after")
    def _kind_matches_data(self) -> InsightsEnvelope:
        expected = _ENVELOPE_KINDS[type(self.data)]
        if self.kind != expected:
            raise ValueError(f"kind '{self.kind}' does not match data type {type(self.data).__name__}")
        return self

    @classmethod
    def wrap(cls, item: Insight | OverlayCue | Recap) -> InsightsEnvelope:
        """Wrap an Insight, OverlayCue or Recap with the correct `kind`."""
        return cls(kind=_ENVELOPE_KINDS[type(item)], data=item)


_ENVELOPE_KINDS: dict[type[BaseModel], str] = {Insight: "insight", OverlayCue: "cue", Recap: "recap"}


# --------------------------------------------------------------------------- #
# WebSocket protocol (api <-> web)
# --------------------------------------------------------------------------- #


class ViewerPrefs(_Base):
    persona: Persona = Persona.CASUAL
    language: str = "en"
    favourite_team_id: str | None = None
    followed_player_id: str | None = None


class ClientMessage(_Base):
    type: Literal["subscribe", "update_prefs", "ping"]
    match_id: str | None = None
    prefs: ViewerPrefs | None = None


class ServerMessage(_Base):
    type: Literal["hello", "event", "state", "moment", "insight", "cue", "recap", "match_end", "error", "pong"]
    match_id: str | None = None
    payload: dict[str, Any] = Field(default_factory=dict)
    ts: datetime = Field(default_factory=_now)


# Topic names (Event Hubs entity names / local bus topics)
TOPIC_EVENTS = "match-events"
TOPIC_TRACKING = "tracking-frames"
TOPIC_STATE = "match-state"
TOPIC_MOMENTS = "moments"
TOPIC_INSIGHTS = "insights"

ALL_MODELS: list[type[BaseModel]] = [
    Team,
    MatchConfig,
    MatchEvent,
    TrackingFrame,
    MatchState,
    WinProbPoint,
    Moment,
    Insight,
    OverlayCue,
    Recap,
    InsightsEnvelope,
    ClientMessage,
    ServerMessage,
    AskRequest,
    AskAnswer,
]
