"""Neural voice for the overlay's co-commentary: a thin proxy to the Azure AI Speech text-to-speech REST API.

`POST /api/speech {text, language}` → `audio/mpeg`. The browser falls back to its own `speechSynthesis` when this
returns 503 (not configured), so voice commentary works offline and in tests without Azure.

Configuration (env), checked in this order:

* **Key** — `AZURE_SPEECH_KEY` + `AZURE_SPEECH_REGION` → regional endpoint
  `https://<region>.tts.speech.microsoft.com/cognitiveservices/v1`, header `Ocp-Apim-Subscription-Key`.
* **Microsoft Entra ID (managed identity, keyless)** — `AZURE_SPEECH_RESOURCE_ID` plus either
  `AZURE_SPEECH_ENDPOINT` (the resource's custom domain, `https://<name>.cognitiveservices.azure.com`; required for
  Entra auth) or `AZURE_SPEECH_REGION`. A token for `https://cognitiveservices.azure.com/.default` from
  `DefaultAzureCredential` (honours `AZURE_CLIENT_ID` = the Container Apps user-assigned identity) is sent as
  `Authorization: Bearer aad#<resource id>#<token>` (Speech REST docs, "Use Microsoft Entra authentication").
  The identity needs the "Cognitive Services Speech User" role on the Speech resource.

Optional: `AZURE_SPEECH_VOICE_<LANG>` (e.g. `AZURE_SPEECH_VOICE_EN=en-GB-SoniaNeural`) overrides a voice.
TODO(azure): no Speech resource is deployed yet — `infra/main.bicep` has one behind `enableSpeech` (default off).
"""

from __future__ import annotations

import logging
import os
import time
from collections import OrderedDict
from dataclasses import dataclass
from typing import Any
from xml.sax.saxutils import escape

import httpx

log = logging.getLogger("fulcrum.api.speech")

TOKEN_SCOPE = "https://cognitiveservices.azure.com/.default"
OUTPUT_FORMAT = "audio-24khz-48kbitrate-mono-mp3"
USER_AGENT = "fulcrum-overlay"
MAX_TEXT_CHARS = 400

# Language → (xml:lang, neural voice). Brazilian Portuguese matches the overlay's Portuguese copy ("Gol").
VOICES: dict[str, tuple[str, str]] = {
    "en": ("en-GB", "en-GB-RyanNeural"),
    "es": ("es-ES", "es-ES-AlvaroNeural"),
    "fr": ("fr-FR", "fr-FR-HenriNeural"),
    "pt": ("pt-BR", "pt-BR-AntonioNeural"),
    "de": ("de-DE", "de-DE-ConradNeural"),
}


class SpeechError(RuntimeError):
    """The Speech service refused or failed the request (mapped to 502 by the API)."""


def base_language(language: str | None) -> str:
    """'pt-BR' → 'pt'; unknown or empty → 'en'."""
    lang = (language or "en").strip().lower().split("-")[0].split("_")[0]
    return lang if lang in VOICES else "en"


def voice_for(language: str | None) -> tuple[str, str]:
    """(xml:lang, voice name) for a viewer language, honouring `AZURE_SPEECH_VOICE_<LANG>` overrides."""
    lang = base_language(language)
    locale, voice = VOICES[lang]
    override = os.getenv(f"AZURE_SPEECH_VOICE_{lang.upper()}")
    if override:
        voice = override.strip()
        locale = "-".join(voice.split("-")[:2]) or locale
    return locale, voice


def build_ssml(text: str, locale: str, voice: str) -> str:
    """SSML for one spoken line; the text is XML-escaped (insight copy can contain '&', '<' or quotes)."""
    body = escape(" ".join(text.split()), {'"': "&quot;", "'": "&apos;"})
    return (
        f"<speak version='1.0' xmlns='http://www.w3.org/2001/10/synthesis' xml:lang='{locale}'>"
        f"<voice name='{voice}'><prosody rate='+6%'>{body}</prosody></voice></speak>"
    )


@dataclass(frozen=True)
class SpeechConfig:
    """Where and how to call the Speech service."""

    url: str
    key: str | None = None
    resource_id: str | None = None

    @classmethod
    def from_env(cls) -> SpeechConfig | None:
        """Config from env, or None when Azure Speech isn't configured (the endpoint then answers 503)."""
        region = os.getenv("AZURE_SPEECH_REGION", "").strip()
        endpoint = os.getenv("AZURE_SPEECH_ENDPOINT", "").strip().rstrip("/")
        key = os.getenv("AZURE_SPEECH_KEY", "").strip()
        resource_id = os.getenv("AZURE_SPEECH_RESOURCE_ID", "").strip()
        if endpoint:
            url = f"{endpoint}/tts/cognitiveservices/v1"
        elif region:
            url = f"https://{region}.tts.speech.microsoft.com/cognitiveservices/v1"
        else:
            return None
        if key:
            return cls(url=url, key=key)
        if resource_id:
            return cls(url=url, resource_id=resource_id)
        return None


class SpeechSynth:
    """Synthesises short lines with a bounded in-memory cache keyed by (voice, text)."""

    def __init__(
        self,
        config: SpeechConfig,
        *,
        client: httpx.AsyncClient | None = None,
        credential: Any = None,
        cache_entries: int = 256,
        cache_bytes: int = 16 * 1024 * 1024,
    ) -> None:
        self.config = config
        self._client = client
        self._own_client = client is None
        self._credential = credential
        self._token: tuple[str, float] | None = None  # (token, expires_on epoch seconds)
        self._cache: OrderedDict[tuple[str, str], bytes] = OrderedDict()
        self._cache_bytes = 0
        self.cache_entries = cache_entries
        self.cache_bytes_max = cache_bytes
        self.calls = 0  # upstream requests made (tests / health)

    @classmethod
    def from_env(cls) -> SpeechSynth | None:
        """A synthesiser if Azure Speech is configured in the environment, else None."""
        config = SpeechConfig.from_env()
        return cls(config) if config else None

    @property
    def auth_mode(self) -> str:
        return "key" if self.config.key else "entra"

    async def synthesize(self, text: str, language: str | None) -> bytes:
        """MP3 bytes for `text` in the viewer's language (served from cache when the same line repeats)."""
        locale, voice = voice_for(language)
        key = (voice, " ".join(text.split()))
        cached = self._cache.get(key)
        if cached is not None:
            self._cache.move_to_end(key)
            return cached
        audio = await self._request(build_ssml(text, locale, voice))
        self._remember(key, audio)
        return audio

    async def close(self) -> None:
        """Close the HTTP client (if we own it) and the credential."""
        if self._client is not None and self._own_client:
            await self._client.aclose()
            self._client = None
        close = getattr(self._credential, "close", None)
        if close is not None:
            await close()

    # ------------------------------------------------------------------ internals
    def _remember(self, key: tuple[str, str], audio: bytes) -> None:
        if len(audio) > self.cache_bytes_max:
            return
        self._cache[key] = audio
        self._cache_bytes += len(audio)
        while len(self._cache) > self.cache_entries or self._cache_bytes > self.cache_bytes_max:
            _, old = self._cache.popitem(last=False)
            self._cache_bytes -= len(old)

    async def _headers(self) -> dict[str, str]:
        headers = {
            "Content-Type": "application/ssml+xml",
            "X-Microsoft-OutputFormat": OUTPUT_FORMAT,
            "User-Agent": USER_AGENT,
        }
        if self.config.key:
            headers["Ocp-Apim-Subscription-Key"] = self.config.key
        else:
            headers["Authorization"] = f"Bearer aad#{self.config.resource_id}#{await self._entra_token()}"
        return headers

    async def _entra_token(self) -> str:
        if self._token and self._token[1] - 120 > time.time():
            return self._token[0]
        if self._credential is None:
            from azure.identity.aio import DefaultAzureCredential  # optional extra: `uv sync --extra azure`

            self._credential = DefaultAzureCredential()  # honours AZURE_CLIENT_ID (user-assigned MI)
        tok = await self._credential.get_token(TOKEN_SCOPE)
        self._token = (tok.token, float(tok.expires_on))
        return tok.token

    async def _request(self, ssml: str) -> bytes:
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=10)
        self.calls += 1
        try:
            headers = await self._headers()
        except SpeechError:
            raise
        except Exception as exc:  # noqa: BLE001 — azure-identity errors (or a missing extra) -> 502, not 500
            raise SpeechError(f"speech auth failed: {type(exc).__name__}: {exc}") from exc
        try:
            r = await self._client.post(self.config.url, content=ssml.encode("utf-8"), headers=headers)
        except httpx.HTTPError as exc:
            raise SpeechError(f"speech service unreachable: {exc}") from exc
        if r.status_code != 200 or not r.content:
            log.warning("speech synthesis failed: %s %s", r.status_code, r.text[:200])
            raise SpeechError(f"speech service answered {r.status_code}")
        return r.content
