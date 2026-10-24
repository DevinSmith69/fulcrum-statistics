"""Producer console, agent side: edit re-checks and the Graphics Producer (offline, scripted LLM)."""

import pytest

from fulcrum.agents.context import foreign_names, load_teams
from fulcrum.agents.producer import (
    EDIT_NOTE,
    REFUSE_ERROR,
    REFUSE_OFF_TOPIC,
    REFUSE_UNDOABLE,
    REFUSE_UNVERIFIED,
    GraphicsProducer,
    apply_edit,
    check_graphic,
    graphics_pack,
    graphics_prompt,
    insight_sheet,
    named_players,
    screen_request,
)
from fulcrum.agents.schemas import GraphicOutput
from fulcrum.agents.service import PROMPT_AGENT_ROLES, AgentService
from fulcrum.agents.studio import build_pack
from fulcrum.agents.templates import fallback_explanation, fallback_variants
from fulcrum.bus.base import MemoryBus
from fulcrum.contracts import (
    CheckStatus,
    Insight,
    InsightVariant,
    MatchConfig,
    MomentType,
    WinProbPoint,
)
from tests.agents.factories import AWAY, HOME, MATCH_ID, league, make_context, make_moment, team
from tests.agents.fakes import ScriptedLLM
from tests.agents.test_studio import history, state

# --------------------------------------------------------------------------- edits


def goal_insight():
    moment = make_moment(MomentType.GOAL)
    ctx = make_context(moment)
    insight = Insight(
        match_id=moment.match_id,
        moment_id=moment.moment_id,
        clock_ms=moment.clock_ms,
        explanation=fallback_explanation(ctx),
        variants=fallback_variants(ctx),
        check_status=CheckStatus.PASSED,
    )
    sheet = insight_sheet(moment, score=ctx.score, foreign=ctx.foreign_names)
    return insight, sheet


def casual(insight: Insight, **update) -> InsightVariant:
    v = next(v for v in insight.variants if v.persona == "casual")
    return v.model_copy(update=update)


def test_edit_with_fact_pack_numbers_passes_and_is_marked():
    insight, sheet = goal_insight()
    edit = casual(insight, headline="Brennick makes it 1-0 on 67'", body="A 0.34 xG chance, finished. Huge moment.")
    edited, result = apply_edit(insight, [edit], sheet, home_id=HOME, away_id=AWAY)
    assert result.passed and edited is not None
    assert casual(edited).headline == "Brennick makes it 1-0 on 67'"
    assert edited.check_notes[-1] == EDIT_NOTE
    assert len(edited.variants) == len(insight.variants)


def test_edit_with_an_invented_number_is_rejected():
    insight, sheet = goal_insight()
    edit = casual(insight, body="Brennick's 25-yard rocket makes it 1-0. What a strike.")
    edited, result = apply_edit(insight, [edit], sheet, home_id=HOME, away_id=AWAY)
    assert edited is None
    assert any("'25' is not in FACTS" in m for m in result.messages)


def test_edit_rejects_other_matches_names_betting_and_overlong_copy():
    insight, sheet = goal_insight()
    for body in (
        "Brennick scores, and Harlow Cross will be watching.",
        "Brennick scores and the odds shorten.",
        "Brennick scores. " + "Huge. " * 60,
    ):
        edited, result = apply_edit(insight, [casual(insight, body=body)], sheet, home_id=HOME, away_id=AWAY)
        assert edited is None and result.issues, body


def test_edit_for_a_missing_variant_is_rejected():
    insight, sheet = goal_insight()
    stray = casual(insight).model_copy(update={"persona": "player_lens", "perspective_player_id": "NBA-10"})
    edited, result = apply_edit(insight, [stray], sheet, home_id=HOME, away_id=AWAY)
    assert edited is None and result.issues[0].code == "variants"


# --------------------------------------------------------------------------- the graphics pack


def winprob():
    return [
        WinProbPoint(clock_ms=0, seq=0, home=0.45, draw=0.3, away=0.25),
        WinProbPoint(clock_ms=50 * 60_000, seq=500, home=0.41, draw=0.31, away=0.28),
        WinProbPoint(clock_ms=67 * 60_000, seq=900, home=0.712, draw=0.18, away=0.108),
    ]


def gpack(request="stat card on Brennick's shooting", wp=None):
    home, away = team(HOME), team(AWAY)
    base = build_pack(
        MATCH_ID,
        home,
        away,
        state=state(),
        history=history(),
        score="1-0",
        foreign_names=foreign_names(home, away, league()),
    )
    return graphics_pack(base, request, state=state(), winprob=winprob() if wp is None else wp)


def test_pack_adds_the_named_players_stats_and_earlier_win_probability():
    p = gpack("stat card on Brennick and Aldane")
    facts = {f.key: f.value for f in p.facts}
    assert facts["focus_1"] == "Aldane (NBA)" and facts["focus_2"] == "Brennick (NBA)"
    assert facts["focus_2_xg"] == 0.51 and facts["focus_2_shots"] == 3 and facts["focus_1_pass_accuracy"] == 92
    assert facts["wp_home_earlier"] == 41 and facts["wp_away_earlier"] == 28  # 15 minutes before 68'
    assert "win probability at 51'" in next(f.label for f in p.facts if f.key == "wp_home_earlier")
    prompt = graphics_prompt(p, "stat card on Brennick")
    for section in ("REQUEST (from the producer)", "FACTS (the match so far)", "focus_2_xg", "ROSTER", "NBA-10"):
        assert section in prompt


def test_named_players_matches_short_and_full_names():
    names = [p.player_id for p, _ in named_players("brennick's passing vs Gael Draycott", team(HOME), team(AWAY))]
    assert names == ["NBA-10", "RVR-09"]
    assert named_players("pressure bar now", team(HOME), team(AWAY)) == []


def test_screen_request_refuses_betting_real_clubs_and_other_teams():
    p = gpack()
    assert screen_request("graphic with the odds for a home win", p) == REFUSE_OFF_TOPIC
    assert screen_request("lower third on Arsenal", p) == REFUSE_OFF_TOPIC
    reason = screen_request("lower third: Harlow Cross's comeback", p)
    assert reason is not None and reason.startswith("Harlow Cross isn't in this match")
    assert screen_request("stat card on Brennick's passing", p) is None


# --------------------------------------------------------------------------- the agent

STAT_CARD = {
    "template": "stat_card",
    "headline": "Brennick: the main threat",
    "body": "Brennick has the 0.51 xG that matters most tonight.",
    "stats": [{"fact_key": "focus_1_xg", "label": "xG"}, {"fact_key": "focus_1_shots", "label": "Shots"}],
    "team_id": HOME,
    "player_id": "NBA-10",
    "fact_keys": ["focus_1_xg", "focus_1_shots"],
}


async def test_valid_graphic_passes_and_becomes_a_producer_cue():
    llm = ScriptedLLM({"graphics_producer": STAT_CARD})
    prop = await GraphicsProducer(llm).propose(gpack(), "stat card on Brennick's shooting", seq=900)
    assert prop.ok and prop.check_status == CheckStatus.PASSED and prop.refusal is None
    cue = prop.cue
    assert cue is not None and cue.origin == "producer" and cue.template == "stat_card"
    assert cue.anchor == "bottom_right" and cue.persona == "casual" and cue.language == "en"
    assert [(s.label, s.value, s.unit) for s in cue.stats] == [("xG", "0.51", "xG"), ("Shots", "3", None)]
    assert cue.show_at_clock_ms == state().clock_ms and cue.sync_seq == 900
    assert cue.insight_id is None and cue.moment_id is None and cue.player_id == "NBA-10"
    assert prop.fact_keys == ["focus_1_xg", "focus_1_shots"]
    assert llm.agents_called() == ["graphics_producer"]


async def test_invented_number_is_repaired():
    bad = {**STAT_CARD, "headline": "Brennick: 37 touches, 0.51 xG"}
    llm = ScriptedLLM({"graphics_producer": [bad, STAT_CARD]})
    prop = await GraphicsProducer(llm).propose(gpack(), "stat card on Brennick")
    assert prop.ok and prop.check_status == CheckStatus.REPAIRED
    assert "'37' is not in FACTS" in llm.calls[1]["user"]  # the repair prompt carries the issue


async def test_failing_twice_is_refused_with_the_issues():
    bad = {**STAT_CARD, "stats": [{"fact_key": "focus_1_dribbles", "label": "Dribbles"}]}
    llm = ScriptedLLM({"graphics_producer": bad})
    prop = await GraphicsProducer(llm).propose(gpack(), "stat card on Brennick")
    assert not prop.ok and prop.cue is None and prop.refusal == REFUSE_UNVERIFIED
    assert prop.check_status == CheckStatus.FALLBACK and any("focus_1_dribbles" in i for i in prop.issues)
    assert len(llm.calls) == 2


async def test_betting_request_is_refused_without_the_llm():
    llm = ScriptedLLM({"graphics_producer": STAT_CARD})
    prop = await GraphicsProducer(llm).propose(gpack(), "show the betting odds for Brennick to score")
    assert not prop.ok and prop.refusal == REFUSE_OFF_TOPIC and llm.calls == []


async def test_undoable_request_and_llm_errors_are_refused():
    llm = ScriptedLLM({"graphics_producer": {"doable": False}})
    prop = await GraphicsProducer(llm).propose(gpack(), "the weather forecast")
    assert prop.refusal == REFUSE_UNDOABLE
    prop = await GraphicsProducer(ScriptedLLM({"graphics_producer": STAT_CARD}, fail_times=2)).propose(gpack(), "x")
    assert prop.refusal == REFUSE_ERROR and not prop.ok


async def test_win_probability_graphic_uses_earlier_then_current():
    out = {
        "template": "wp_swing",
        "headline": "Northbridge Athletic: favourites now",
        "stats": [
            {"fact_key": "wp_home_earlier", "label": "Earlier"},
            {"fact_key": "wp_home", "label": "Now"},
        ],
        "team_id": HOME,
    }
    prop = await GraphicsProducer(ScriptedLLM({"graphics_producer": out})).propose(gpack(), "win probability graphic")
    assert prop.ok and [s.value for s in prop.cue.stats] == ["41", "71"] and prop.cue.anchor == "top_right"


@pytest.mark.parametrize(
    "out, code",
    [
        (
            {
                "template": "wp_swing",
                "headline": "Swing",
                "team_id": HOME,
                "stats": [{"fact_key": "home_xg", "label": "xG"}],
            },
            "template",
        ),
        ({"template": "player_tag", "headline": "", "body": "On fire."}, "template"),
        ({"template": "stat_card", "headline": "Brennick", "stats": []}, "template"),
        ({"template": "lower_third", "headline": "NBA-10 is flying"}, "id_in_text"),
        ({"template": "lower_third", "headline": "Northbridge Athletic lead 2-0"}, "unknown_score"),
        ({"template": "lower_third", "headline": "Riverside Rovers pressing", "team_id": "HLC"}, "unknown_team"),
        (
            {"template": "stat_card", "headline": "Brennick", "stats": [{"fact_key": "top_rated_1", "label": "Top"}]},
            "not_numeric",
        ),
        ({"template": "lower_third", "headline": "Brennick", "body": "He has been everywhere and"}, "cut_off"),
    ],
)
def test_check_graphic_rules(out, code):
    issues = check_graphic(GraphicOutput.model_validate(out), gpack())
    assert code in {i.code for i in issues}, issues


async def test_player_tag_uses_the_roster_name():
    out = {"template": "player_tag", "headline": "whatever", "body": "Brennick leads the line.", "player_id": "NBA-10"}
    prop = await GraphicsProducer(ScriptedLLM({"graphics_producer": out})).propose(gpack(), "player tag Brennick")
    assert prop.ok and prop.cue.headline == "Iker Brennick" and prop.cue.team_id == HOME


def test_refusal_copy_is_fact_check_clean():
    p = gpack()
    for text in (REFUSE_OFF_TOPIC, REFUSE_UNDOABLE, REFUSE_UNVERIFIED, REFUSE_ERROR):
        assert p.sheet.check_text(text, "refusal") == []


# --------------------------------------------------------------------------- service


def test_graphics_producer_is_a_published_prompt_agent():
    roles = {name: system for name, system, _, _ in PROMPT_AGENT_ROLES}
    assert "graphics_producer" in roles and "{language}" not in roles["graphics_producer"]


async def test_service_graphic_and_update_insight():
    llm = ScriptedLLM({"graphics_producer": STAT_CARD})
    svc = AgentService(MemoryBus(), llm, league=load_teams(), consume_events=False)
    svc.register_match(MatchConfig(match_id=MATCH_ID, home_team_id=HOME, away_team_id=AWAY))
    prop = await svc.graphic(MATCH_ID, "stat card on Brennick's shooting", state=state(), winprob=winprob())
    assert prop.ok and prop.cue.sync_seq == state().seq
    unknown = await svc.graphic("nope", "stat card on Brennick")
    assert not unknown.ok and unknown.refusal

    insight, _ = goal_insight()
    assert svc.update_insight(insight) is False  # never published by this service


def test_pressure_bar_headline_must_credit_the_side_that_leads():
    """Live run: "Marsh Dock United pressing harder" over a 73-30 bar for Harlow Cross passed every number check."""
    from fulcrum.agents.producer import check_graphic

    st = state()
    st = st.model_copy(
        update={
            "home": st.home.model_copy(update={"pressure_index": 73.0}),
            "away": st.away.model_copy(update={"pressure_index": 30.0}),
        }
    )
    home_t, away_t = team(HOME), team(AWAY)
    base = build_pack(MATCH_ID, home_t, away_t, state=st, history=history(), score="1-0")
    p = graphics_pack(base, "pressure bar now", state=st, winprob=winprob())
    leader, trailer = home_t, away_t
    stats = [
        {"fact_key": "home_pressure_index", "label": "Pressure"},
        {"fact_key": "away_pressure_index", "label": "Pressure"},
    ]
    wrong = GraphicOutput(template="pressure_bar", headline=f"{trailer.name} pressing harder", stats=stats)
    right = GraphicOutput(template="pressure_bar", headline=f"{leader.name} pressing harder", stats=stats)
    assert [i.code for i in check_graphic(wrong, p)] == ["direction"]
    assert check_graphic(right, p) == []
