import re

import pytest

from fulcrum.agents.factcheck import FactSheet, check_insight_copy, check_variant
from fulcrum.agents.templates import (
    GENERIC,
    GENERIC_WHY,
    TEMPLATES,
    WHY,
    build_slots,
    fallback_explanation,
    fallback_variants,
    render,
)
from fulcrum.contracts import InsightVariant, MomentType
from tests.agents.factories import AWAY, HOME, F, make_context, make_moment

ALL_TYPES = list(MomentType)


def sheet_for(ctx) -> FactSheet:
    return FactSheet.build(
        ctx.moment.facts, score=ctx.score, clock_ms=ctx.moment.clock_ms, foreign_names=ctx.foreign_names
    )


def perspective(key: str, team_id: str | None) -> str | None:
    if key in ("analyst", "casual"):
        return None
    if key == "fan_for":
        return team_id
    if key == "fan_against":
        return AWAY if team_id == HOME else HOME
    return HOME  # fan_neutral / generic fan


@pytest.mark.parametrize("mtype", ALL_TYPES)
def test_every_moment_type_has_templates_for_core_personas(mtype):
    groups = TEMPLATES[mtype]
    assert {"analyst", "casual"} <= groups.keys()
    assert "fan_neutral" in groups or {"fan_for", "fan_against"} <= groups.keys()
    assert WHY[mtype]


RUN_TRIGGERS = [
    ">= 3 shots in 5 min with 0 opponent shots",
    ">= 5 consecutive possessions reaching the final third",
]


def _contexts(mtype):
    """One context per real trigger for momentum runs (their templates are trigger-gated), else one."""
    if mtype == MomentType.MOMENTUM_RUN:
        return [make_context(make_moment(mtype, trigger=t)) for t in RUN_TRIGGERS]
    return [make_context(make_moment(mtype))]


@pytest.mark.parametrize("mtype", ALL_TYPES)
def test_every_template_renders_and_passes_factcheck_on_synthetic_facts(mtype):
    contexts = _contexts(mtype)
    for key, templates in TEMPLATES[mtype].items():
        for i, template in enumerate(templates):
            rendered = []
            for ctx in contexts:
                slots = build_slots(ctx.moment, ctx.home, ctx.away, ctx.score, perspective(key, ctx.moment.team_id))
                if (out := render(template, slots)) is not None:
                    rendered.append((ctx, out))
            assert rendered, f"{mtype}/{key}[{i}] did not render: missing {template.fields - slots.values.keys()}"
            for ctx, (headline, body, line) in rendered:
                v = InsightVariant(persona="analyst", headline=headline, body=body, commentary_line=line)
                assert check_variant(v, sheet_for(ctx)) == [], f"{mtype}/{key}[{i}]: {(headline, body, line)}"


@pytest.mark.parametrize(
    ("trigger", "expect", "avoid"),
    [(RUN_TRIGGERS[0], "shots", "possessions"), (RUN_TRIGGERS[1], "final third", "shots")],
)
def test_momentum_run_copy_matches_its_trigger(trigger, expect, avoid):
    """A territory run with 0 shots used to read "Harlow Cross on a run: 0 shots to 0"."""
    base = make_moment(MomentType.MOMENTUM_RUN)
    zero_shots = {"shots_last_5": 0, "opp_shots_last_5": 0, "xg_last_5": 0.0}
    facts = [f.model_copy(update={"value": zero_shots[f.key]}) if f.key in zero_shots else f for f in base.facts]
    ctx = make_context(make_moment(MomentType.MOMENTUM_RUN, trigger=trigger, facts=facts))
    explanation = fallback_explanation(ctx)
    analyst = next(v for v in fallback_variants(ctx) if v.persona == "analyst")
    text = " ".join([explanation.headline, explanation.why_it_matters, analyst.headline, analyst.body])
    assert expect in text
    if trigger == RUN_TRIGGERS[1]:
        assert "0 shots" not in text


@pytest.mark.parametrize("mtype", ALL_TYPES)
def test_fallback_copy_passes_full_factcheck(mtype):
    ctx = make_context(make_moment(mtype))
    explanation, variants = fallback_explanation(ctx), fallback_variants(ctx)
    result = check_insight_copy(explanation, variants, sheet_for(ctx), home_id=HOME, away_id=AWAY)
    assert result.passed, result.messages
    assert set(explanation.key_fact_keys) <= {f.key for f in ctx.moment.facts}
    assert explanation.key_fact_keys, "fallback explanation should cite the facts it used"


@pytest.mark.parametrize("mtype", ALL_TYPES)
@pytest.mark.parametrize("team_id", [HOME, AWAY, None])
def test_fallback_with_only_common_facts_uses_generic_and_passes(mtype, team_id):
    base = make_moment(mtype)
    common = [f for f in base.facts if f.key in ("minute", "score", "home_team", "away_team")]
    ctx = make_context(make_moment(mtype, facts=common, team_id=team_id))
    result = check_insight_copy(
        fallback_explanation(ctx), fallback_variants(ctx), sheet_for(ctx), home_id=HOME, away_id=AWAY
    )
    assert result.passed, result.messages


@pytest.mark.parametrize("mtype", ALL_TYPES)
def test_fallback_with_no_facts_at_all_passes(mtype):
    ctx = make_context(make_moment(mtype, facts=[]))
    result = check_insight_copy(
        fallback_explanation(ctx), fallback_variants(ctx), sheet_for(ctx), home_id=HOME, away_id=AWAY
    )
    assert result.passed, result.messages


def test_template_text_has_no_literal_digits():
    texts = []
    for groups in [*TEMPLATES.values(), GENERIC]:
        for templates in groups.values():
            texts += [part for t in templates for part in (t.headline, t.body, t.line)]
    texts += [w.why for ws in [*WHY.values(), GENERIC_WHY] for w in ws]
    for text in texts:
        literal = re.sub(r"\{[^}]*\}", "", text)
        assert not re.search(r"\d", literal), text


def test_side_relative_aliases_follow_the_moment_team():
    ctx = make_context(make_moment(MomentType.WP_SWING))  # team = RVR (away)
    slots = build_slots(ctx.moment, ctx.home, ctx.away, ctx.score, perspective_team_id=HOME)
    assert slots.values["team"] == "Riverside Rovers" and slots.values["opp"] == "Northbridge Athletic"
    assert slots.values["wp_team_after"] == "33" and slots.values["wp_opp_after"] == "40"
    assert slots.values["wp_us_after"] == "40" and slots.values["wp_them_after"] == "33"
    assert slots.values["wp_home_delta_abs"] == "12" and slots.sources["wp_team_after"] == "wp_away_after"


def test_club_fan_variants_are_partisan_and_correctly_oriented():
    ctx = make_context(make_moment(MomentType.GOAL))  # NBA score
    variants = {v.perspective_team_id: v for v in fallback_variants(ctx) if v.persona == "club_fan"}
    assert variants[HOME].headline.startswith("YES!")
    assert "Riverside Rovers need a response" not in variants[HOME].body
    assert "Northbridge Athletic" in variants[AWAY].body and "Gutting" in variants[AWAY].body


def test_player_id_fact_values_render_as_short_names():
    moment = make_moment(MomentType.GOAL)
    facts = [F("scorer", "NBA-10") if f.key == "scorer" else f for f in moment.facts]
    ctx = make_context(make_moment(MomentType.GOAL, facts=facts))
    assert fallback_variants(ctx)[0].headline.startswith("Brennick scores")


def test_phase_descriptions_are_readable():
    ctx = make_context(make_moment(MomentType.PHASE_CHANGE))
    analyst = fallback_variants(ctx)[0]
    assert analyst.headline == "Phase change: balance → Northbridge Athletic in control"


def test_casual_bodies_quote_at_most_one_fact_number():
    for mtype in ALL_TYPES:
        ctx = make_context(make_moment(mtype))
        casual = fallback_variants(ctx)[1]
        nums = re.findall(r"\d+(?:[.,]\d+)?", re.sub(r"\b\d-\d\b", "", casual.body))
        assert len(nums) <= 1, (mtype, casual.body)


def test_possessive_team_names():
    ctx = make_context(make_moment(MomentType.WP_SWING))  # team = Riverside Rovers
    assert "Riverside Rovers' win probability" in fallback_explanation(ctx).why_it_matters


def _copy_passes(ctx) -> list[str]:
    return check_insight_copy(
        fallback_explanation(ctx),
        fallback_variants(ctx),
        sheet_for(ctx),
        home_id=ctx.home.team_id,
        away_id=ctx.away.team_id,
    ).messages


def test_tiny_float_is_not_rendered_in_scientific_notation():
    base = make_moment(MomentType.GOAL)
    facts = [F("xg", 5e-05, "xG", "Shot xG") if f.key == "xg" else f for f in base.facts]
    ctx = make_context(make_moment(MomentType.GOAL, facts=facts))
    assert _copy_passes(ctx) == []
    assert "0.00005 xG" in fallback_variants(ctx)[0].body


@pytest.mark.parametrize("value", ["Ullmann interception", "Arsenal-style press", "a betting-slip moment"])
def test_bad_string_facts_fall_through_to_clean_templates(value):
    base = make_moment(MomentType.WP_SWING)
    facts = [F("cause_event", value) if f.key == "cause_event" else f for f in base.facts]
    ctx = make_context(make_moment(MomentType.WP_SWING, facts=facts))
    assert _copy_passes(ctx) == []
    assert all(value not in v.body for v in fallback_variants(ctx))


@pytest.mark.parametrize("mtype", ALL_TYPES)
def test_fallback_passes_for_every_team_pairing(mtype):
    from fulcrum.agents.context import MatchContextStore
    from fulcrum.contracts import MatchConfig
    from tests.agents.factories import league

    teams = league()
    for home in teams:
        for away in teams:
            if home.team_id == away.team_id:
                continue
            store = MatchContextStore(teams)
            store.register_match(MatchConfig(match_id="m", home_team_id=home.team_id, away_team_id=away.team_id))
            base = make_moment(mtype, match_id="m")
            team_id = {HOME: home.team_id, AWAY: away.team_id}.get(base.team_id)
            facts = [f for f in base.facts if f.key not in ("home_team", "away_team")]
            ctx = store.build_context(make_moment(mtype, match_id="m", team_id=team_id, facts=facts))
            assert _copy_passes(ctx) == [], (home.team_id, away.team_id)
