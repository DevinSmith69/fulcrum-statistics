from fulcrum.agents.player_lens import PlayerLens
from fulcrum.agents.service import AgentService
from fulcrum.agents.templates import fallback_explanation, fallback_variants
from fulcrum.bus.base import MemoryBus
from fulcrum.contracts import CheckStatus, Insight, MatchConfig
from tests.agents.factories import AWAY, HOME, make_context, make_store
from tests.agents.fakes import ScriptedLLM

BRENNICK = {
    "persona": "player_lens",
    "language": "en",
    "perspective_team_id": None,
    "perspective_player_id": "NBA-10",
    "headline": "Brennick's 67' finish puts Northbridge in front",
    "body": "Brennick found the net with a 0.34 xG chance at 67'. It lifts Northbridge to 71% and caps a sharp day.",
    "commentary_line": "Brennick again, and Northbridge lead!",
}


def setup():
    ctx = make_context()
    insight = Insight(
        match_id=ctx.moment.match_id,
        moment_id=ctx.moment.moment_id,
        clock_ms=ctx.moment.clock_ms,
        explanation=fallback_explanation(ctx),
        variants=fallback_variants(ctx),
        check_status=CheckStatus.FALLBACK,
    )
    return ctx, insight


async def lens(llm, player="NBA-10"):
    ctx, insight = setup()
    return await PlayerLens(llm).variant(
        insight, ctx.moment, ctx.home, ctx.away, player, foreign_names=ctx.foreign_names
    ), llm


async def test_involved_player_gets_a_checked_lens_variant_once():
    ctx, insight = setup()
    llm = ScriptedLLM({"player_lens": BRENNICK})
    pl = PlayerLens(llm)
    v = await pl.variant(insight, ctx.moment, ctx.home, ctx.away, "NBA-10", foreign_names=ctx.foreign_names)
    assert v is not None and v.persona == "player_lens" and v.perspective_player_id == "NBA-10"
    assert v.headline == BRENNICK["headline"]
    again = await pl.variant(insight, ctx.moment, ctx.home, ctx.away, "NBA-10")
    assert again == v and len(llm.calls) == 1  # cached
    assert "Brennick (NBA-10)" in llm.calls[0]["system"] and "FOLLOWED PLAYER\nNBA-10" in llm.calls[0]["user"]


async def test_uninvolved_player_costs_no_llm_call():
    v, llm = await lens(ScriptedLLM({"player_lens": BRENNICK}), player="RVR-04")
    assert v is None and llm.calls == []


async def test_labels_are_forced_to_the_followed_player():
    wrong = {**BRENNICK, "persona": "casual", "perspective_player_id": None, "perspective_team_id": HOME}
    v, _ = await lens(ScriptedLLM({"player_lens": wrong}))
    assert (v.persona, v.perspective_player_id, v.perspective_team_id) == ("player_lens", "NBA-10", None)


async def test_invented_number_is_repaired_then_passes():
    bad = {**BRENNICK, "body": "Brennick scored with a 0.52 xG chance. It lifts Northbridge to 71%."}
    v, llm = await lens(ScriptedLLM({"player_lens": [bad, BRENNICK]}))
    assert v is not None and v.body == BRENNICK["body"]
    assert "number '0.52' is not in FACTS" in llm.calls[1]["user"]


async def test_failing_twice_raises_so_the_miss_is_not_cached():
    import pytest

    from fulcrum.agents.player_lens import PlayerLensUnavailable

    bad = {**BRENNICK, "body": "Brennick scored with a 0.52 xG chance. It lifts Northbridge to 71%."}
    llm = ScriptedLLM({"player_lens": bad})
    with pytest.raises(PlayerLensUnavailable):
        await lens(llm)
    assert len(llm.calls) == 2


async def test_service_player_lens_translates_through_the_translator():
    store = make_store()
    llm = ScriptedLLM(
        {
            "player_lens": BRENNICK,
            "translator": {
                "variants": [
                    {
                        **BRENNICK,
                        "language": "es",
                        "headline": "Brennick adelanta a Northbridge en el 67'",
                        "body": "Brennick marcó con una ocasión de 0,34 xG en el 67'. Northbridge sube al 71%.",
                    }
                ]
            },
        }
    )
    svc = AgentService(MemoryBus(), llm, league=store.league, consume_events=False)
    svc.register_match(MatchConfig(match_id="m1", home_team_id=HOME, away_team_id=AWAY))
    ctx, insight = setup()
    svc._insights[insight.insight_id] = (ctx.moment.model_copy(update={"match_id": "m1"}), insight, "1-0")
    en = await svc.player_lens(insight.insight_id, "NBA-10", "en")
    assert [v.headline for v in en] == [BRENNICK["headline"]]
    es = await svc.player_lens(insight.insight_id, "NBA-10", "es")
    assert es[0].language == "es" and es[0].perspective_player_id == "NBA-10"
    assert await svc.player_lens(insight.insight_id, "RVR-04", "en") == []
    assert await svc.player_lens("unknown", "NBA-10", "en") == []
