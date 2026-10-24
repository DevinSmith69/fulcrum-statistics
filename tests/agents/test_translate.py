import asyncio

from fulcrum.agents.orchestrator import OrchestratorConfig
from fulcrum.agents.templates import fallback_explanation, fallback_variants
from fulcrum.agents.translate import Translator
from fulcrum.contracts import CheckStatus, Insight
from fulcrum.llm import FakeLLM
from tests.agents.factories import make_context
from tests.agents.fakes import ScriptedLLM


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


def spanish(insight, body="¡Brennick marca! 0,34 xG y ahora un 71% de opciones de ganar.", language="es"):
    return {
        "variants": [
            {**v.model_dump(mode="json"), "language": language, "headline": "¡Gol de Brennick!", "body": body}
            for v in insight.variants
        ]
    }


async def test_translation_passes_checks_with_decimal_commas_and_is_cached():
    ctx, insight = setup()
    llm = ScriptedLLM({"translator": spanish(insight)})
    tr = Translator(llm)
    out = await tr.translate(insight, ctx.moment, ctx.home, ctx.away, "ES", foreign_names=ctx.foreign_names)
    assert [v.language for v in out] == ["es"] * 4 and out[0].headline == "¡Gol de Brennick!"
    assert [(v.persona, v.perspective_team_id) for v in out] == [
        (v.persona, v.perspective_team_id) for v in insight.variants
    ]
    again = await tr.translate(insight, ctx.moment, ctx.home, ctx.away, "es")
    assert again == out and len(llm.calls) == 1
    assert tr.cached(insight.insight_id, "es") == out
    assert "Spanish" in llm.calls[0]["system"] and "0.34" in llm.calls[0]["user"]


async def test_translation_language_is_forced_to_target():
    ctx, insight = setup()
    tr = Translator(ScriptedLLM({"translator": spanish(insight, language="en")}))
    out = await tr.translate(insight, ctx.moment, ctx.home, ctx.away, "es")
    assert {v.language for v in out} == {"es"}


async def test_english_requests_return_source_without_llm():
    ctx, insight = setup()
    llm = ScriptedLLM({})
    out = await Translator(llm).translate(insight, ctx.moment, ctx.home, ctx.away, "en")
    assert out == insight.variants and llm.calls == []


async def test_translation_with_altered_number_is_repaired():
    ctx, insight = setup()
    bad = spanish(insight, body="¡Brennick marca! 0,43 xG.")
    llm = ScriptedLLM({"translator": [bad, spanish(insight)]})
    out = await Translator(llm).translate(insight, ctx.moment, ctx.home, ctx.away, "es")
    assert out[0].language == "es" and len(llm.calls) == 2
    assert "number '0,43' is not in FACTS" in llm.calls[1]["user"]


async def test_translation_with_betting_words_falls_back_to_english_uncached():
    ctx, insight = setup()
    bad = spanish(insight, body="Las apuestas cambian: 71%.")
    llm = ScriptedLLM({"translator": bad})
    tr = Translator(llm)
    out = await tr.translate(insight, ctx.moment, ctx.home, ctx.away, "es")
    assert out == insight.variants and len(llm.calls) == 2
    assert "banned betting/market term 'apuestas'" in llm.calls[1]["user"]
    assert tr.cached(insight.insight_id, "es") is None


async def test_translation_reordered_variants_fail_structure_check():
    ctx, insight = setup()
    swapped = spanish(insight)
    swapped["variants"].reverse()
    llm = ScriptedLLM({"translator": [swapped, spanish(insight)]})
    out = await Translator(llm).translate(insight, ctx.moment, ctx.home, ctx.away, "es")
    assert out[0].persona == "analyst" and "same order as source" in llm.calls[1]["user"]


async def test_translation_llm_errors_and_timeouts_fall_back():
    ctx, insight = setup()
    out = await Translator(FakeLLM(fail_times=5)).translate(insight, ctx.moment, ctx.home, ctx.away, "fr")
    assert out == insight.variants
    started = []

    async def count(user):
        started.append(user)

    slow = ScriptedLLM({"translator": spanish(insight)}, delays={"translator": 1.0}, hooks={"translator": count})
    out = await Translator(slow, OrchestratorConfig(call_timeout_s=0.05)).translate(
        insight, ctx.moment, ctx.home, ctx.away, "es"
    )
    assert out == insight.variants and len(started) == 1  # a timeout is not retried


async def test_concurrent_requests_share_one_call():
    ctx, insight = setup()
    llm = ScriptedLLM({"translator": spanish(insight)}, delays={"translator": 0.02})
    tr = Translator(llm)
    results = await asyncio.gather(*(tr.translate(insight, ctx.moment, ctx.home, ctx.away, "es") for _ in range(5)))
    assert len(llm.calls) == 1 and all(r == results[0] for r in results)


async def test_translated_cues_are_built_and_cached():
    ctx, insight = setup()
    tr = Translator(ScriptedLLM({"translator": spanish(insight)}))
    cues = await tr.cues(insight, ctx.moment, ctx.home, ctx.away, "es")
    assert cues and {c.language for c in cues} == {"es"}
    assert await tr.cues(insight, ctx.moment, ctx.home, ctx.away, "es") is cues


async def test_english_phase_words_get_one_repair_but_never_force_english():
    ctx, insight = setup()
    leaky = spanish(insight, body="El partido pasa de balanced a control de Northbridge. ¡Brennick marca! 0,34 xG.")
    clean = spanish(insight)
    llm = ScriptedLLM({"translator": [leaky, clean]})
    out = await Translator(llm).translate(insight, ctx.moment, ctx.home, ctx.away, "es")
    assert out[0].headline == "¡Gol de Brennick!" and "balanced" not in out[0].body
    assert "'balanced' is English" in llm.calls[1]["user"]
    # still leaky on the repair: accept it rather than fall back to English
    llm2 = ScriptedLLM({"translator": leaky})
    out2 = await Translator(llm2).translate(insight, ctx.moment, ctx.home, ctx.away, "es")
    assert out2[0].language == "es" and len(llm2.calls) == 2
