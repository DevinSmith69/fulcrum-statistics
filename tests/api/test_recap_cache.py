"""A recap that fell back to the template must never replace a checked recap (review finding, 24 Oct)."""

from fulcrum.api.hub import Hub
from fulcrum.api.readmodel import MatchReadModel, Registry
from fulcrum.api.translation import Translator
from fulcrum.bus.base import MemoryBus
from fulcrum.contracts import CheckStatus, Persona, Recap, ViewerPrefs


def _recap(persona="casual", language="en", status=CheckStatus.PASSED, title="Good recap"):
    return Recap(
        match_id="m",
        persona=persona,
        language=language,
        title=title,
        summary="s.",
        key_moment_ids=[],
        check_status=status,
    )


class FallbackAgents:
    """The reporter timed out: the agents hand back their English template recap."""

    async def recap(self, match_id, persona, language):
        return _recap(persona=str(persona), language="en", status=CheckStatus.FALLBACK, title="Template")


def test_read_model_keeps_a_checked_recap_over_a_fallback():
    rm = MatchReadModel("m")
    rm.add_recap(_recap())
    rm.add_recap(_recap(status=CheckStatus.FALLBACK, title="Template"))
    assert rm.recaps[("casual", "en")].title == "Good recap"
    rm.add_recap(_recap(status=CheckStatus.REPAIRED, title="Better"))  # a checked one still replaces
    assert rm.recaps[("casual", "en")].title == "Better"


async def test_failed_lazy_recap_does_not_overwrite_the_published_one():
    rm = MatchReadModel("m")
    rm.add_recap(_recap())
    hub = Hub(MemoryBus(), Registry({}), Translator(FallbackAgents()))
    got = await hub.best_recap(rm, ViewerPrefs(persona=Persona.CASUAL, language="es"))
    assert got.title == "Good recap"  # nearest checked recap, not the template
    assert rm.recaps[("casual", "en")].title == "Good recap"
    assert ("casual", "es") not in rm.recaps  # nothing cached, so a later request retries
