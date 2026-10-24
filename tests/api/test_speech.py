"""Voice commentary proxy (`/api/speech`): 503 when unconfigured, SSML/voice/headers and caching with a mocked
Speech REST endpoint. No network, no Azure credentials."""

from types import SimpleNamespace

import httpx
import pytest
from fastapi.testclient import TestClient

from fulcrum.api.app import create_app
from fulcrum.api.speech import OUTPUT_FORMAT, SpeechConfig, SpeechSynth, build_ssml, voice_for
from fulcrum.bus.base import MemoryBus

SPEECH_ENV = (
    "AZURE_SPEECH_KEY",
    "AZURE_SPEECH_REGION",
    "AZURE_SPEECH_ENDPOINT",
    "AZURE_SPEECH_RESOURCE_ID",
    "AZURE_SPEECH_VOICE_EN",
    "AZURE_SPEECH_VOICE_PT",
)
MP3 = b"ID3\x04fake-mp3-bytes"


@pytest.fixture(autouse=True)
def _no_speech_env(monkeypatch):
    for name in SPEECH_ENV:
        monkeypatch.delenv(name, raising=False)


class Recorder:
    """httpx MockTransport handler that records requests and answers with fake MP3 bytes."""

    def __init__(self, status: int = 200) -> None:
        self.requests: list[httpx.Request] = []
        self.status = status

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if self.status != 200:
            return httpx.Response(self.status, text="nope")
        return httpx.Response(200, content=MP3, headers={"content-type": "audio/mpeg"})


def make_client(synth: SpeechSynth | None) -> TestClient:
    return TestClient(create_app(MemoryBus(), in_process=False, speech=synth))


def keyed_synth(rec: Recorder, **kw) -> SpeechSynth:
    config = SpeechConfig(url="https://swedencentral.tts.speech.microsoft.com/cognitiveservices/v1", key="k123")
    return SpeechSynth(config, client=httpx.AsyncClient(transport=httpx.MockTransport(rec)), **kw)


def test_unconfigured_returns_503_and_status_says_disabled():
    with make_client(None) as c:
        r = c.post("/api/speech", json={"text": "What a strike!", "language": "en"})
        assert r.status_code == 503
        assert r.headers["content-type"].startswith("application/problem+json")
        assert c.get("/api/speech").json()["enabled"] is False
        assert c.get("/api/health").json()["speech"] is False


def test_key_auth_posts_ssml_with_voice_and_headers():
    rec = Recorder()
    with make_client(keyed_synth(rec)) as c:
        assert c.get("/api/speech").json()["enabled"] is True
        r = c.post("/api/speech", json={"text": "Rivera & co. <press> high", "language": "es"})
    assert r.status_code == 200
    assert r.headers["content-type"] == "audio/mpeg"
    assert r.content == MP3
    (req,) = rec.requests
    assert str(req.url) == "https://swedencentral.tts.speech.microsoft.com/cognitiveservices/v1"
    assert req.headers["Ocp-Apim-Subscription-Key"] == "k123"
    assert "Authorization" not in req.headers
    assert req.headers["Content-Type"] == "application/ssml+xml"
    assert req.headers["X-Microsoft-OutputFormat"] == OUTPUT_FORMAT
    assert req.headers["User-Agent"]
    body = req.content.decode()
    assert "xml:lang='es-ES'" in body and "name='es-ES-AlvaroNeural'" in body
    assert "Rivera &amp; co. &lt;press&gt; high" in body


def test_cache_hits_skip_the_upstream_call_and_are_bounded():
    rec = Recorder()
    synth = keyed_synth(rec, cache_entries=2)
    with make_client(synth) as c:
        for _ in range(3):
            assert c.post("/api/speech", json={"text": "One nil.", "language": "en"}).status_code == 200
        assert len(rec.requests) == 1  # same (voice, text) → cached
        c.post("/api/speech", json={"text": "One nil.", "language": "fr"})  # other voice → new call
        assert len(rec.requests) == 2
        c.post("/api/speech", json={"text": "Two nil.", "language": "en"})  # evicts the oldest entry
        c.post("/api/speech", json={"text": "One nil.", "language": "en"})
        assert len(rec.requests) == 4
    assert len(synth._cache) == 2


def test_upstream_failure_maps_to_502_and_bad_input_to_422():
    with make_client(keyed_synth(Recorder(status=401))) as c:
        assert c.post("/api/speech", json={"text": "Goal!", "language": "en"}).status_code == 502
        assert c.post("/api/speech", json={"text": "", "language": "en"}).status_code == 422
        assert c.post("/api/speech", json={"text": "x" * 401, "language": "en"}).status_code == 422


async def test_entra_auth_uses_aad_resource_token_and_custom_domain(monkeypatch):
    monkeypatch.setenv("AZURE_SPEECH_ENDPOINT", "https://fulcrum-speech.cognitiveservices.azure.com/")
    monkeypatch.setenv("AZURE_SPEECH_RESOURCE_ID", "/subscriptions/s/resourceGroups/g/providers/x/accounts/sp")
    config = SpeechConfig.from_env()
    assert config is not None and config.key is None
    assert config.url == "https://fulcrum-speech.cognitiveservices.azure.com/tts/cognitiveservices/v1"

    scopes: list[str] = []

    class FakeCredential:
        async def get_token(self, scope: str):
            scopes.append(scope)
            return SimpleNamespace(token="jwt", expires_on=4_000_000_000)

    rec = Recorder()
    client = httpx.AsyncClient(transport=httpx.MockTransport(rec))
    synth = SpeechSynth(config, client=client, credential=FakeCredential())
    await synth.synthesize("Line one", "de")
    await synth.synthesize("Line two", "pt-BR")
    assert synth.auth_mode == "entra"
    assert rec.requests[0].headers["Authorization"] == f"Bearer aad#{config.resource_id}#jwt"
    assert "Ocp-Apim-Subscription-Key" not in rec.requests[0].headers
    assert scopes == ["https://cognitiveservices.azure.com/.default"]  # token reused until near expiry
    assert "pt-BR-AntonioNeural" in rec.requests[1].content.decode()
    await synth.close()


def test_config_from_env_variants(monkeypatch):
    assert SpeechConfig.from_env() is None
    monkeypatch.setenv("AZURE_SPEECH_REGION", "uksouth")
    assert SpeechConfig.from_env() is None  # region alone: no credential configured
    monkeypatch.setenv("AZURE_SPEECH_KEY", "k")
    cfg = SpeechConfig.from_env()
    assert cfg is not None and cfg.url == "https://uksouth.tts.speech.microsoft.com/cognitiveservices/v1"


def test_voice_overrides_and_ssml(monkeypatch):
    assert voice_for("xx") == ("en-GB", "en-GB-RyanNeural")
    monkeypatch.setenv("AZURE_SPEECH_VOICE_EN", "en-GB-SoniaNeural")
    assert voice_for("en") == ("en-GB", "en-GB-SoniaNeural")
    ssml = build_ssml("  It's   1–0 ", "en-GB", "en-GB-SoniaNeural")
    assert "It&apos;s 1–0" in ssml and ssml.startswith("<speak version='1.0'")


def test_speech_is_rate_limited_per_client():
    from fulcrum.api.studio import RateLimiter

    with make_client(keyed_synth(Recorder())) as c:
        c.app.state.speech_limiter = RateLimiter(2, 60)
        codes = [c.post("/api/speech", json={"text": f"Line {n}.", "language": "en"}).status_code for n in range(3)]
        assert codes == [200, 200, 429]


async def test_credential_failure_is_a_speech_error(monkeypatch):
    """azure-identity errors (or the extra missing) must map to SpeechError -> 502, not a 500."""
    from fulcrum.api.speech import SpeechError

    synth = keyed_synth(Recorder())

    async def boom():
        raise RuntimeError("no credential")

    monkeypatch.setattr(synth, "_headers", boom)
    with pytest.raises(SpeechError):
        await synth.synthesize("Goal!", "en")
