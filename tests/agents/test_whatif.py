"""What-if facts (MODEL_SPEC §3.1) in fallback copy and overlay cues."""

from fulcrum.agents.cues import WHATIF_STAT_LABEL, build_cues
from fulcrum.agents.factcheck import check_insight_copy
from fulcrum.agents.templates import fallback_explanation, fallback_variants
from fulcrum.contracts import MomentType
from tests.agents.factories import AWAY, HOME, F, make_context, make_moment
from tests.agents.test_cues import insight_for
from tests.agents.test_templates import sheet_for


def _without_whatif(mtype: MomentType):
    base = make_moment(mtype)
    return make_moment(mtype, facts=[f for f in base.facts if not f.key.startswith("whatif_")])


def test_big_chance_fallback_uses_whatif_facts():
    ctx = make_context(make_moment(MomentType.BIG_CHANCE))
    explanation, variants = fallback_explanation(ctx), fallback_variants(ctx)
    assert check_insight_copy(explanation, variants, sheet_for(ctx), home_id=HOME, away_id=AWAY).passed
    assert explanation.why_it_matters == (
        "Had Draycott's 0.41 xG chance gone in, it would be 0-1 and Riverside Rovers' win probability would be "
        "52% instead of 31%."
    )
    assert {"whatif_goal_win_pct", "whatif_win_pct_now", "whatif_goal_score"} <= set(explanation.key_fact_keys)
    for v in variants:
        assert "52%" in v.body, (v.persona, v.body)


def test_big_chance_without_whatif_keeps_the_original_copy():
    ctx = make_context(_without_whatif(MomentType.BIG_CHANCE))
    explanation, variants = fallback_explanation(ctx), fallback_variants(ctx)
    assert check_insight_copy(explanation, variants, sheet_for(ctx), home_id=HOME, away_id=AWAY).passed
    assert "gone in" not in explanation.why_it_matters
    assert all("gone in" not in v.body for v in variants)


def test_wp_swing_from_own_shot_mentions_the_whatif():
    base = make_moment(MomentType.WP_SWING)  # team = RVR (away)
    facts = [f for f in base.facts if f.key != "cause_event"] + [
        F("cause_event", "shot", label="Event that tipped the swing"),
        F("whatif_win_pct_now", 33, "%", "Riverside Rovers win probability now"),
        F("whatif_goal_win_pct", 58, "%", "Riverside Rovers win probability if it had gone in"),
    ]
    ctx = make_context(make_moment(MomentType.WP_SWING, facts=facts))
    explanation = fallback_explanation(ctx)
    assert explanation.why_it_matters.endswith("had it gone in, it would be 58%.")
    assert check_insight_copy(explanation, fallback_variants(ctx), sheet_for(ctx), home_id=HOME, away_id=AWAY).passed


def test_big_chance_stat_card_shows_the_whatif_second_with_a_short_label():
    ctx, insight = insight_for(MomentType.BIG_CHANCE)
    cues = [c for c in build_cues(insight, ctx.moment, ctx.home, ctx.away) if c.template == "stat_card"]
    assert cues
    for c in cues:
        assert [s.value for s in c.stats][:2] == ["0.41", "52"]
        assert c.stats[1].label == WHATIF_STAT_LABEL and c.stats[1].unit == "%"
        assert len(c.stats) == 4


def test_big_chance_stat_card_without_whatif_is_unchanged():
    moment = _without_whatif(MomentType.BIG_CHANCE)
    ctx, insight = insight_for(MomentType.BIG_CHANCE, facts=moment.facts)
    c = next(c for c in build_cues(insight, ctx.moment, ctx.home, ctx.away) if c.template == "stat_card")
    assert [s.value for s in c.stats][:3] == ["0.41", "9.5", "0.87"]
