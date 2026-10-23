import pytest

from fulcrum.agents.cues import CUE_MAP, build_cues, show_at
from fulcrum.agents.templates import fallback_explanation, fallback_variants
from fulcrum.contracts import CheckStatus, Insight, InsightVariant, MomentType
from tests.agents.factories import AWAY, HOME, make_context, make_moment


def insight_for(mtype: MomentType, latency_ms: int | None = 1200, **moment_overrides):
    ctx = make_context(make_moment(mtype, **moment_overrides))
    insight = Insight(
        match_id=ctx.moment.match_id,
        moment_id=ctx.moment.moment_id,
        clock_ms=ctx.moment.clock_ms,
        explanation=fallback_explanation(ctx),
        variants=fallback_variants(ctx),
        check_status=CheckStatus.FALLBACK,
        latency_ms=latency_ms,
    )
    return ctx, insight


def by_template(cues, persona=None):
    out: dict[str, list] = {}
    for c in cues:
        if persona is None or c.persona == persona:
            out.setdefault(c.template, []).append(c)
    return out


def test_goal_maps_to_lower_third_and_wp_swing_for_every_persona():
    ctx, insight = insight_for(MomentType.GOAL)
    cues = build_cues(insight, ctx.moment, ctx.home, ctx.away)
    groups = by_template(cues)
    assert len(groups["lower_third"]) == 4 and len(groups["wp_swing"]) == 4
    lt = groups["lower_third"][0]
    assert (lt.priority, lt.duration_ms, lt.anchor) == (5, 8000, "bottom_left")
    wp = groups["wp_swing"][0]
    assert (wp.priority, wp.duration_ms) == (4, 6000)
    assert [s.value for s in wp.stats] == ["48", "71", "23"]
    assert wp.headline == "Win probability · Northbridge Athletic"
    personas = {(c.persona, c.perspective_team_id) for c in groups["lower_third"]}
    assert personas == {("analyst", None), ("casual", None), ("club_fan", HOME), ("club_fan", AWAY)}
    # copy comes straight from the matching variant
    for c in groups["lower_third"]:
        v = next(
            v for v in insight.variants if (v.persona, v.perspective_team_id) == (c.persona, c.perspective_team_id)
        )
        assert (c.headline, c.body) == (v.headline, v.body)


def test_goal_player_tags_for_involved_players():
    ctx, insight = insight_for(MomentType.GOAL)
    tags = by_template(build_cues(insight, ctx.moment, ctx.home, ctx.away))["player_tag"]
    names = {p.player_id: p.name for p in ctx.home.players}
    assert [(t.persona, t.perspective_player_id, t.player_id, t.headline) for t in tags] == [
        ("player_lens", "NBA-10", "NBA-10", names["NBA-10"]),
        ("player_lens", "NBA-09", "NBA-09", names["NBA-09"]),
    ]
    casual = next(v for v in insight.variants if v.persona == "casual")
    assert all(t.body == casual.headline and t.template == "player_tag" for t in tags)


def test_sync_fields_and_show_at_rounding():
    ctx, insight = insight_for(MomentType.GOAL, latency_ms=1200)
    for c in build_cues(insight, ctx.moment, ctx.home, ctx.away):
        assert c.sync_clock_ms == ctx.moment.clock_ms and c.sync_seq == ctx.moment.seq_end
        assert c.show_at_clock_ms == ctx.moment.clock_ms + 2000
        assert c.match_id == insight.match_id and c.insight_id == insight.insight_id
        assert c.moment_id == ctx.moment.moment_id and c.team_id == HOME
    assert show_at(ctx.moment, None) == ctx.moment.clock_ms
    assert show_at(ctx.moment, 1000) == ctx.moment.clock_ms + 1000
    assert show_at(ctx.moment, 1) == ctx.moment.clock_ms + 1000


@pytest.mark.parametrize(
    "mtype, template",
    [
        (MomentType.WP_SWING, "wp_swing"),
        (MomentType.PRESSURE_SHIFT, "pressure_bar"),
        (MomentType.MOMENTUM_RUN, "pressure_bar"),
        (MomentType.PHASE_CHANGE, "pressure_bar"),
        (MomentType.ELITE_PASS, "stat_card"),
        (MomentType.SPEED_RECORD, "stat_card"),
        (MomentType.PLAYER_MILESTONE, "stat_card"),
        (MomentType.FULL_TIME, "recap_card"),
    ],
)
def test_contract_mapping(mtype, template):
    ctx, insight = insight_for(mtype)
    groups = by_template(build_cues(insight, ctx.moment, ctx.home, ctx.away))
    assert len(groups[template]) == 4
    assert set(groups) <= {template, "player_tag"}


@pytest.mark.parametrize("mtype", list(MomentType))
def test_every_type_produces_valid_cues_with_at_most_four_stats(mtype):
    ctx, insight = insight_for(mtype)
    cues = build_cues(insight, ctx.moment, ctx.home, ctx.away)
    assert mtype in CUE_MAP
    assert len([c for c in cues if c.persona != "player_lens"]) == 4 * len(CUE_MAP[mtype])
    for c in cues:
        assert len(c.stats) <= 4 and len(c.headline) <= 90 and 1 <= c.priority <= 5
        assert all(s.value for s in c.stats)


def test_wp_swing_stats_follow_the_moment_team():
    ctx, insight = insight_for(MomentType.WP_SWING)  # team RVR
    wp = by_template(build_cues(insight, ctx.moment, ctx.home, ctx.away))["wp_swing"][0]
    assert [(s.value, s.unit) for s in wp.stats] == [("22", "%"), ("33", "%"), ("11", "%")]
    assert wp.priority == 4


def test_stats_fall_back_to_explanation_key_facts_when_preferred_missing():
    base = make_moment(MomentType.ELITE_PASS)
    facts = [f for f in base.facts if f.key not in ("difficulty", "length_m")]
    ctx, insight = insight_for(MomentType.ELITE_PASS, facts=facts)
    card = by_template(build_cues(insight, ctx.moment, ctx.home, ctx.away))["stat_card"][0]
    assert [s.label for s in card.stats] == ["Expected completion", "Metres gained"]


def test_translated_variants_make_translated_cues():
    ctx, insight = insight_for(MomentType.GOAL)
    es = [InsightVariant(**{**v.model_dump(), "language": "es", "headline": "¡Gol!"}) for v in insight.variants]
    cues = build_cues(insight, ctx.moment, ctx.home, ctx.away, variants=es)
    assert {c.language for c in cues} == {"es"}
    wp = by_template(cues)["wp_swing"][0]
    assert wp.headline.startswith("Probabilidad de victoria")
