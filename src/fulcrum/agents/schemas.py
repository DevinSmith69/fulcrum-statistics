"""Agent-local schemas: LLM output wrappers and the shared workflow state.

These are NOT cross-service contracts (those live in `fulcrum.contracts`). They describe what each LLM
agent returns (`EditorOutput`, `ReviewOutput`, …) and the typed state the explain-moment workflow passes
between steps (`MomentContext`). In the Microsoft Agent Framework port, `MomentContext` becomes the
workflow's shared state and each step becomes an executor that reads and writes it.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, PrivateAttr

from fulcrum.contracts import (
    CheckStatus,
    Explanation,
    Fact,
    Insight,
    InsightVariant,
    Moment,
    OverlayCue,
    Team,
)


class EditorOutput(BaseModel):
    """Persona Editor output: exactly four variants (analyst, casual, club_fan home, club_fan away)."""

    model_config = ConfigDict(extra="forbid")

    variants: list[InsightVariant]


class TranslationOutput(BaseModel):
    """Translator output: the source variants, translated, in the same order."""

    model_config = ConfigDict(extra="forbid")

    variants: list[InsightVariant]


class ReviewOutput(BaseModel):
    """LLM reviewer verdict for big moments (significance >= 0.6, `FULCRUM_LLM_REVIEW=1`)."""

    model_config = ConfigDict(extra="forbid")

    supported: bool
    issues: list[str] = Field(default_factory=list)


class RecapOutput(BaseModel):
    """Match Reporter output. Turned into a `fulcrum.contracts.Recap` after fact-checking."""

    model_config = ConfigDict(extra="forbid")

    title: str = Field(max_length=80)
    summary: str = Field(max_length=1200)
    key_moment_ids: list[str] = Field(default_factory=list)
    player_of_the_match_id: str | None = None
    social_posts: list[str] = Field(default_factory=list)


class StudioAnswerOutput(BaseModel):
    """Studio Analyst output ("Ask the studio"). Turned into a `fulcrum.contracts.AskAnswer` after fact-checking."""

    model_config = ConfigDict(extra="forbid")

    answer: str = Field(max_length=400, description="1-3 complete sentences in the requested language")
    cited_moment_ids: list[str] = Field(default_factory=list, description="MOMENT ids the answer relies on")
    fact_keys: list[str] = Field(default_factory=list, description="FACT keys the answer used")
    answerable: bool = Field(True, description="False if the CONTEXT PACK cannot answer it, or it is off-topic")


class GraphicStatOutput(BaseModel):
    """One stat tile the Graphics Producer wants: a pack fact key plus a short on-screen label."""

    model_config = ConfigDict(extra="forbid")

    fact_key: str
    label: str = Field(max_length=40)


class GraphicOutput(BaseModel):
    """Graphics Producer output. Turned into a `fulcrum.contracts.OverlayCue` after fact-checking.

    Stat values are never written by the model: they are filled from the context pack by `fact_key`.
    """

    model_config = ConfigDict(extra="forbid")

    doable: bool = Field(True, description="False if the CONTEXT PACK can't support it, or it is off-topic")
    template: Literal["lower_third", "stat_card", "wp_swing", "pressure_bar", "player_tag"] = "lower_third"
    headline: str = Field("", max_length=120)
    body: str = Field("", max_length=300)
    stats: list[GraphicStatOutput] = Field(default_factory=list)
    team_id: str | None = None
    player_id: str | None = None
    fact_keys: list[str] = Field(default_factory=list)


class CoachPoint(BaseModel):
    """One point of the Coach's view: a sentence and the coach_ facts it rests on."""

    model_config = ConfigDict(extra="forbid")

    text: str = Field(max_length=300, description="One sentence, at most 200 characters")
    fact_keys: list[str] = Field(default_factory=list, description="The coach_ FACT keys this point states")


class CoachOutput(BaseModel):
    """Coach output ("Coach's view"). Turned into a `CoachView` after fact-checking."""

    model_config = ConfigDict(extra="forbid")

    points: list[CoachPoint] = Field(description="2 or 3 points, in the order the move happened")
    takeaway: str = Field(max_length=300, description="One constructive, conditional sentence")


class CoachView(BaseModel):
    """`GET /api/matches/{id}/insights/{insight_id}/coach` (API_CONTRACT §2): the Coach's view of a goal or big
    chance, from the defending team's side.

    Not a bus message, so it lives here rather than in `fulcrum.contracts`. `check_status="fallback"` means no
    checked LLM copy is available (no agents, LLM failure, or two failed fact-checks): `points` is empty,
    `takeaway` null, and clients show `facts` (the moment's coach_ facts) as a plain list.
    """

    model_config = ConfigDict(extra="forbid")

    match_id: str
    insight_id: str
    moment_id: str
    moment_type: str
    defending_team_id: str | None = None
    language: str = "en"
    check_status: CheckStatus
    points: list[CoachPoint] = Field(default_factory=list)
    takeaway: str | None = None
    facts: list[Fact] = Field(default_factory=list, description="The coach_ facts the view is based on")
    latency_ms: int | None = None


class MomentContext(BaseModel):
    """Shared, typed state for one run of the explain-moment workflow.

    Every step reads what it needs and writes its result here; routing between steps is decided only
    from this state (see `orchestrator.EDGES`). Input fields are set once by the caller; output fields
    are filled as the workflow advances.
    """

    model_config = ConfigDict(extra="forbid")

    # --- inputs ---------------------------------------------------------------------------------- #
    moment: Moment
    home: Team
    away: Team
    score: str = Field(description="Current score as 'home-away', e.g. '2-1'")
    recent_sequence: list[str] = Field(default_factory=list, description="Compact lines, oldest first")
    foreign_names: list[str] = Field(
        default_factory=list, description="League team/player names NOT in this match (must not appear)"
    )

    # --- workflow bookkeeping -------------------------------------------------------------------- #
    attempt: int = 1
    max_attempts: int = 2
    issues: list[str] = Field(default_factory=list, description="Issues from the latest failed check")
    explanation_failed: bool = Field(False, description="True if the latest issues include the explanation")
    error: str | None = Field(None, description="Last LLM error/timeout message, cleared on success")
    timed_out: bool = False
    notes: list[str] = Field(default_factory=list, description="Audit trail → Insight.check_notes")
    trace: list[str] = Field(default_factory=list, description="Step names in execution order")

    # --- outputs --------------------------------------------------------------------------------- #
    explanation: Explanation | None = None
    variants: list[InsightVariant] = Field(default_factory=list)
    status: CheckStatus | None = None
    latency_ms: int | None = None
    insight: Insight | None = None
    cues: list[OverlayCue] = Field(default_factory=list)

    _cache: dict[str, Any] = PrivateAttr(default_factory=dict)

    @property
    def can_retry(self) -> bool:
        """True while another attempt is allowed (timeouts never retry: the latency budget is spent)."""
        return self.attempt < self.max_attempts and not self.timed_out

    def team_name(self, team_id: str | None) -> str | None:
        """Return the display name for `team_id` if it plays in this match."""
        for team in (self.home, self.away):
            if team.team_id == team_id:
                return team.name
        return None
