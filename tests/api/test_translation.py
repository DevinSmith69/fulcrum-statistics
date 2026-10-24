"""The API's translation facade against a fake agents service (the WS3 on-demand hooks)."""

from fulcrum.api.translation import Translator
from fulcrum.contracts import (
    CheckStatus,
    Explanation,
    Insight,
    InsightVariant,
    OverlayCue,
    OverlayTemplate,
    Persona,
    Recap,
)


def _variant(persona, lang="en", team=None):
    return InsightVariant(
        persona=persona,
        language=lang,
        perspective_team_id=team,
        headline=f"{persona}-{lang}",
        body="b",
        commentary_line="c",
    )


INSIGHT = Insight(
    match_id="m",
    moment_id="mo",
    clock_ms=0,
    explanation=Explanation(headline="h", why_it_matters="w", tactical_tag="control", key_fact_keys=[]),
    variants=[_variant(Persona.CASUAL), _variant(Persona.CLUB_FAN, team="NBA")],
    check_status=CheckStatus.PASSED,
)
CUE = OverlayCue(
    match_id="m",
    moment_id="mo",
    insight_id=INSIGHT.insight_id,
    template=OverlayTemplate.STAT_CARD,
    priority=3,
    sync_clock_ms=0,
    sync_seq=0,
    show_at_clock_ms=1000,
    duration_ms=5000,
    persona=Persona.CASUAL,
    headline="casual-en cue",
)


class FakeAgents:
    def __init__(self, fail: bool = False) -> None:
        self.fail = fail
        self.calls: list[tuple] = []

    async def translate(self, insight_id, language):
        self.calls.append(("translate", insight_id, language))
        if self.fail:
            raise RuntimeError("llm down")
        return [_variant(Persona.CASUAL, language), _variant(Persona.CLUB_FAN, language, team="NBA")]

    async def translate_cues(self, insight_id, language):
        self.calls.append(("translate_cues", insight_id, language))
        return [CUE.model_copy(update={"language": language, "headline": f"casual-{language} cue"})]

    async def recap(self, match_id, persona, language):
        self.calls.append(("recap", match_id, str(persona), language))
        return Recap(
            match_id=match_id,
            persona=persona,
            language=language,
            title=f"{persona}-{language}",
            summary="s",
            key_moment_ids=[],
            check_status=CheckStatus.PASSED,
        )


async def test_without_agents_serves_english():
    tr = Translator()
    out = await tr.variants(INSIGHT, INSIGHT.variants[:1], "es")
    assert out[0].headline == "casual-en"
    assert (await tr.cue(CUE, "es")).headline == "casual-en cue"
    assert await tr.recap("m", Persona.CASUAL, "es") is None


async def test_with_agents_translates_slot_by_slot_and_caches():
    agents = FakeAgents()
    tr = Translator(agents)
    fan = await tr.variants(INSIGHT, [INSIGHT.variants[1]], "es")
    assert (fan[0].headline, fan[0].perspective_team_id) == ("club_fan-es", "NBA")
    casual = await tr.variants(INSIGHT, [INSIGHT.variants[0]], "es")
    assert casual[0].headline == "casual-es"
    assert [c[0] for c in agents.calls] == ["translate"]  # second lookup served from cache
    assert (await tr.cue(CUE, "fr")).headline == "casual-fr cue"
    assert (await tr.recap("m", Persona.ANALYST, "de")).title == "analyst-de"


async def test_agent_failure_falls_back_and_is_not_cached():
    agents = FakeAgents(fail=True)
    tr = Translator(agents)
    assert (await tr.variants(INSIGHT, INSIGHT.variants[:1], "pt"))[0].headline == "casual-en"
    agents.fail = False
    assert (await tr.variants(INSIGHT, INSIGHT.variants[:1], "pt"))[0].headline == "casual-pt"


class LensAgents:
    """Only NBA-10 is in the moment; counts calls."""

    def __init__(self, fail=False):
        self.calls, self.fail = [], fail

    async def player_lens(self, insight_id, player_id, language):
        self.calls.append((insight_id, player_id, language))
        if self.fail:
            raise RuntimeError("boom")
        if player_id != "NBA-10":
            return []
        v = _variant(Persona.PLAYER_LENS, lang=language)
        return [v.model_copy(update={"perspective_player_id": player_id, "headline": f"lens-{language}"})]


def _lens_prefs(player, language="en"):
    from fulcrum.contracts import ViewerPrefs

    return ViewerPrefs(persona=Persona.PLAYER_LENS, language=language, followed_player_id=player)


async def test_player_lens_viewer_gets_the_lensed_variant_and_misses_are_cached():
    from fulcrum.api.personalise import personalise_insight

    agents = LensAgents()
    tr = Translator(agents)
    out = await personalise_insight(INSIGHT, _lens_prefs("NBA-10", "es"), tr)
    assert [v.headline for v in out.variants] == ["lens-es"]
    # not involved: casual variant, and the miss is cached (one agent call however often it is asked)
    for _ in range(3):
        out = await personalise_insight(INSIGHT, _lens_prefs("RVR-04"), tr)
        assert [v.headline for v in out.variants] == ["casual-en"]
    assert [c[1] for c in agents.calls] == ["NBA-10", "RVR-04"]


async def test_player_lens_falls_back_to_casual_when_agents_fail_or_cache_only():
    from fulcrum.api.personalise import personalise_insight

    tr = Translator(LensAgents(fail=True))
    out = await personalise_insight(INSIGHT, _lens_prefs("NBA-10"), tr)
    assert [v.headline for v in out.variants] == ["casual-en"]
    out = await personalise_insight(INSIGHT, _lens_prefs("NBA-10"), Translator(LensAgents()), translate=False)
    assert [v.headline for v in out.variants] == ["casual-en"]  # cache-only snapshot never calls the LLM
