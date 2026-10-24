"""Fulcrum API gateway (API_CONTRACT §2–3): REST under `/api`, WebSocket at `/ws`.

    uvicorn fulcrum.api.app:app --port 8000          (module-level `app`)
    create_app(bus=MemoryBus(), run_sim=False)       (tests)

The hub always consumes the bus. What else runs in this process depends on the topology (`fulcrum.topology`):
inprocess (the default with FULCRUM_BUS=memory) runs sim + engine + agents here (see `pipeline.py`); hybrid runs
engine + agents here and forwards `POST /api/matches` to the sim service; split only forwards.
"""

from __future__ import annotations

import asyncio
import hmac
import logging
import os
from contextlib import asynccontextmanager
from typing import Any

import httpx
from dotenv import load_dotenv
from fastapi import APIRouter, FastAPI, HTTPException, Query, Request, WebSocket, WebSocketDisconnect
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel, Field, ValidationError
from starlette.exceptions import HTTPException as StarletteHTTPException

from fulcrum.bus import Bus, get_bus
from fulcrum.contracts import (
    AskAnswer,
    AskRequest,
    ClientMessage,
    GraphicProposal,
    GraphicRequest,
    HeldItem,
    Insight,
    InsightVariant,
    MatchConfig,
    MatchEvent,
    MatchState,
    Moment,
    OverlayCue,
    Persona,
    ProducerDecision,
    ProducerEdit,
    ProducerSettings,
    ProducerState,
    Recap,
    Team,
    WinProbPoint,
)
from fulcrum.engine.service import EngineService
from fulcrum.engine.teams import load_teams
from fulcrum.sim.control import ControlError, ControlRequest, MatchControl, apply
from fulcrum.telemetry import configure_telemetry
from fulcrum.topology import Topology, parse_topology, resolve_topology

from . import personalise as ps
from .evalreports import latest_reports
from .hub import Connection, Hub
from .pipeline import Pipeline
from .producer import DeskError, ProducerDesk
from .readmodel import MatchReadModel, Registry
from .speech import MAX_TEXT_CHARS, SpeechError, SpeechSynth, voice_for
from .studio import DEFAULT_SPEECH_RATE, RateLimiter, client_key
from .translation import Translator

log = logging.getLogger("fulcrum.api")
DEFAULT_ORIGINS = "http://localhost:5173,http://127.0.0.1:5173"


class TranslateRequest(BaseModel):
    insight_id: str
    language: str


class SpeechRequest(BaseModel):
    text: str = Field(min_length=1, max_length=MAX_TEXT_CHARS)
    language: str = "en"


def _env_flag(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    return default if raw is None else raw.strip().lower() in ("1", "true", "yes", "on")


def problem(status: int, title: str, detail: Any = None, **extra: Any) -> JSONResponse:
    """RFC 7807 problem response."""
    body = {"type": "about:blank", "title": title, "status": status, "detail": detail, **extra}
    return JSONResponse(body, status_code=status, media_type="application/problem+json")


async def start_on_sim(app: FastAPI, sim_url: str, config: MatchConfig) -> None:
    """Ask the sim service (`fulcrum.sim.service`, `POST /matches`) to run `config`; 502 if it can't."""
    client: httpx.AsyncClient | None = getattr(app.state, "sim_client", None)
    try:
        if client is not None:
            r = await client.post(f"{sim_url.rstrip('/')}/matches", json=config.model_dump(mode="json"))
        else:
            async with httpx.AsyncClient(timeout=10) as c:
                r = await c.post(f"{sim_url.rstrip('/')}/matches", json=config.model_dump(mode="json"))
    except httpx.HTTPError as exc:
        raise HTTPException(502, f"sim service unreachable: {exc}") from exc
    if r.status_code >= 400:
        raise HTTPException(502, f"sim refused the match ({r.status_code}): {r.text[:200]}")


def _remote_sim_url(app: FastAPI) -> str | None:
    """The sim service URL when matches are simulated out of this process (split / hybrid), else None."""
    topology = getattr(app.state, "topology", None)
    if topology is None or topology.api_runs_sim:
        return None
    return os.getenv("FULCRUM_SIM_URL") or None


async def _sim_request(app: FastAPI, method: str, path: str, body: Any = None) -> httpx.Response:
    url = f"{_remote_sim_url(app) or ''}".rstrip("/") + path
    client: httpx.AsyncClient | None = getattr(app.state, "sim_client", None)
    if client is not None:
        return await client.request(method, url, json=body)
    async with httpx.AsyncClient(timeout=10) as c:
        return await c.request(method, url, json=body)


async def _sim_control(app: FastAPI, method: str, match_id: str, body: Any = None) -> dict[str, Any]:
    """Forward a demo-director call to the sim service; remember a running skip so moments get passed on."""
    try:
        r = await _sim_request(app, method, f"/matches/{match_id}/control", body)
    except httpx.HTTPError as exc:
        raise HTTPException(502, f"sim service unreachable: {exc}") from exc
    if r.status_code >= 400:
        detail = r.json().get("detail") if "json" in r.headers.get("content-type", "") else r.text[:200]
        raise HTTPException(r.status_code if r.status_code in (404, 409, 422) else 502, detail)
    snap = r.json()
    _track_skip(app, match_id, snap)
    return snap


def _track_skip(app: FastAPI, match_id: str, snap: dict[str, Any]) -> None:
    skips: set[str] = app.state.remote_skips
    (skips.add if snap.get("skipping") else skips.discard)(match_id)


async def _forward_moment(app: FastAPI, moment: Moment) -> None:
    """While a remote skip runs, the sim service (which has no engine) learns about each moment from us."""
    try:
        r = await _sim_request(app, "POST", f"/matches/{moment.match_id}/moment", moment.model_dump(mode="json"))
        if r.status_code < 400:
            _track_skip(app, moment.match_id, r.json())
    except httpx.HTTPError as exc:
        log.warning("could not pass moment %s to the sim service: %s", moment.moment_id, exc)


DEFAULT_GRAPHIC_RATE = "10/30"  # graphic requests per producer client (LLM spend)


def _agents_learn_edit(translator: Translator, insight: Insight) -> None:
    """Tell the in-process agents about a producer edit, so translations start from the edited copy."""
    update = getattr(translator.agents, "update_insight", None)
    if update is not None:
        update(insight)


def _check_producer_key(request: Request) -> None:
    """Shared-secret gate for the producer console: `X-Producer-Key` must equal FULCRUM_PRODUCER_KEY when set."""
    expected = os.getenv("FULCRUM_PRODUCER_KEY")
    if not expected:
        return  # a demo: open unless a key is configured
    given = request.headers.get("x-producer-key", "")
    if not hmac.compare_digest(given.encode(), expected.encode()):
        raise HTTPException(401, "producer key required (X-Producer-Key header)")


_BG: set[asyncio.Task[Any]] = set()


def _spawn_bg(coro: Any) -> None:
    task = asyncio.create_task(coro)
    _BG.add(task)
    task.add_done_callback(_BG.discard)


def create_app(
    bus: Bus | None = None,
    *,
    teams: dict[str, Team] | None = None,
    in_process: bool | None = None,
    topology: Topology | str | None = None,
    run_sim: bool | None = None,
    run_agents: bool | None = None,
    speech: SpeechSynth | None = None,
) -> FastAPI:
    """Build the app.

    `topology` defaults to `resolve_topology()` (FULCRUM_TOPOLOGY, else FULCRUM_INPROCESS, else FULCRUM_BUS);
    `in_process=True|False` is the older spelling of inprocess|split. `run_sim` / `run_agents` default to
    FULCRUM_RUN_SIM / FULCRUM_RUN_AGENTS (default on); hybrid never runs the sim here. Tests switch them off and
    publish onto the bus themselves. `speech` defaults to `SpeechSynth.from_env()` (None = Azure Speech not
    configured, `POST /api/speech` answers 503).
    """
    if topology is not None:
        topo: Topology | None = parse_topology(topology)
    elif in_process is not None:
        topo = Topology.INPROCESS if in_process else Topology.SPLIT
    else:
        topo = None  # resolved from the environment at start-up

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        configure_telemetry("api")  # no-op unless APPLICATIONINSIGHTS_CONNECTION_STRING is set
        the_bus = bus or get_bus()
        registry = Registry(teams if teams is not None else load_teams())
        translator = Translator()
        hub = Hub(the_bus, registry, translator)
        desk = ProducerDesk(registry, hub.deliver, on_edit=lambda i: _agents_learn_edit(translator, i))
        hub.gate = desk.intercept  # editorial gate: holds insights + cues only while a producer asks for approval
        hub.start()
        desk_task = asyncio.create_task(desk.run(), name="producer-desk")
        the_topology = topo or resolve_topology()
        _warn_misconfigured(the_topology, the_bus)
        pipeline = None
        if the_topology.api_hosts_pipeline:
            sim = the_topology.api_runs_sim and (run_sim if run_sim is not None else _env_flag("FULCRUM_RUN_SIM", True))
            agents = run_agents if run_agents is not None else _env_flag("FULCRUM_RUN_AGENTS", True)
            engine = EngineService(the_bus, teams=registry.teams)
            pipeline = Pipeline(the_bus, registry, engine, translator, run_sim=sim, run_agents=agents)
            hub.moment_listeners.append(pipeline.on_moment)
        app.state.topology = the_topology
        app.state.remote_skips = set()
        if not the_topology.api_runs_sim and os.getenv("FULCRUM_SIM_URL"):
            # demo director, sim elsewhere: pass moments on while a skip runs (ends the skip there)
            hub.moment_listeners.append(
                lambda m: _spawn_bg(_forward_moment(app, m)) if m.match_id in app.state.remote_skips else None
            )
        app.state.bus, app.state.registry, app.state.hub = the_bus, registry, hub
        app.state.translator, app.state.pipeline = translator, pipeline
        app.state.speech = speech if speech is not None else SpeechSynth.from_env()
        app.state.ask_limiter = RateLimiter.from_env()
        app.state.ask_global = RateLimiter(60, 60)  # all clients together: a ceiling on LLM spend
        app.state.speech_limiter = RateLimiter.from_env("FULCRUM_SPEECH_RATE", DEFAULT_SPEECH_RATE)
        app.state.speech_global = RateLimiter(600, 60)
        app.state.producer = desk
        app.state.graphic_limiter = RateLimiter.from_env("FULCRUM_GRAPHIC_RATE", DEFAULT_GRAPHIC_RATE)
        if pipeline is not None:
            if not the_topology.api_runs_sim:
                # hybrid: events come from another process (and from sim autostarts nobody POSTed), so the engine
                # joins its consumer groups now rather than racing the first match's kick-off.
                pipeline.engine.start()
            pipeline.prestart()  # agents warm up now, not on the first match
        try:
            yield
        finally:
            if pipeline is not None:
                await pipeline.stop()
            desk_task.cancel()
            await asyncio.gather(desk_task, return_exceptions=True)
            await hub.stop()
            if app.state.speech is not None:
                await app.state.speech.close()

    app = FastAPI(title="Fulcrum API", version="0.1.0", lifespan=lifespan)
    origins = [o.strip() for o in os.getenv("FULCRUM_ALLOWED_ORIGINS", DEFAULT_ORIGINS).split(",") if o.strip()]
    app.add_middleware(CORSMiddleware, allow_origins=origins, allow_methods=["*"], allow_headers=["*"])

    @app.exception_handler(StarletteHTTPException)
    async def _http_problem(_: Request, exc: StarletteHTTPException) -> JSONResponse:
        titles = {
            400: "Bad Request",
            401: "Unauthorized",
            404: "Not Found",
            409: "Conflict",
            422: "Unprocessable Entity",
            429: "Too Many Requests",
            501: "Not Implemented",
            502: "Bad Gateway",
            503: "Service Unavailable",
        }
        resp = problem(exc.status_code, titles.get(exc.status_code, "Error"), exc.detail)
        resp.headers.update(getattr(exc, "headers", None) or {})
        return resp

    @app.exception_handler(RequestValidationError)
    async def _validation_problem(_: Request, exc: RequestValidationError) -> JSONResponse:
        return problem(422, "Unprocessable Entity", "request validation failed", errors=_jsonable(exc.errors()))

    app.include_router(_router())

    @app.websocket("/ws")
    async def ws_endpoint(ws: WebSocket) -> None:
        await _serve_ws(ws, ws.app.state.hub)

    return app


def _warn_misconfigured(topology: Topology, bus: Bus) -> None:
    """Log (don't fail) topologies that cannot work as configured, so a misconfigured container still serves."""
    from fulcrum.bus.base import MemoryBus

    if topology.needs_shared_bus and isinstance(bus, MemoryBus) and os.getenv("FULCRUM_SIM_URL"):
        log.warning("topology %s with the memory bus: the sim service's events will not reach this process", topology)
    if not topology.api_runs_sim and not os.getenv("FULCRUM_SIM_URL"):
        log.warning("topology %s without FULCRUM_SIM_URL: POST /api/matches cannot start a simulated match", topology)
    log.info("api topology: %s", topology)


def _jsonable(errors: Any) -> Any:
    return [
        {k: (v if isinstance(v, (str, int, float, list, type(None))) else str(v)) for k, v in e.items()} for e in errors
    ]


# --------------------------------------------------------------------------- REST


def _router() -> APIRouter:
    r = APIRouter(prefix="/api")

    def registry(request: Request) -> Registry:
        return request.app.state.registry

    def match(request: Request, match_id: str) -> MatchReadModel:
        rm = registry(request).get(match_id)
        if rm is None:
            raise HTTPException(404, f"unknown match '{match_id}'")
        return rm

    @r.get("/health")
    async def health(request: Request) -> dict[str, Any]:
        pipeline: Pipeline | None = request.app.state.pipeline
        topology: Topology = request.app.state.topology
        if topology.api_runs_sim:
            sim = "simulator" if pipeline is not None and pipeline.sim_available else "replay"
        else:
            sim = "service" if os.getenv("FULCRUM_SIM_URL") else None
        return {
            "status": "ok",
            "bus": os.getenv("FULCRUM_BUS", "memory"),
            "llm": os.getenv("FULCRUM_LLM", "fake"),
            "topology": topology.value,
            "in_process": topology is Topology.INPROCESS,
            "sim": sim,
            # True only once the agents service is actually running (it starts with the first match);
            # agents_state says why not otherwise (e.g. "failed" with agents_error = a missing Foundry setting).
            "agents": None if pipeline is None else pipeline.agents_state == "running",
            "agents_state": None if pipeline is None else pipeline.agents_state,
            "agents_error": None if pipeline is None else pipeline.agents_error,
            "speech": request.app.state.speech is not None,
            # Demo director (speed / pause / next moment) needs the simulator running in this process.
            "match_controls": (pipeline is not None and pipeline.run_sim) or _remote_sim_url(request.app) is not None,
        }

    @r.get("/eval/latest")
    async def eval_latest() -> dict[str, Any]:
        # Offline reports (scripts/eval_live.py, scripts/eval_foundry.py); small files, read per request.
        return await asyncio.to_thread(latest_reports)

    @r.get("/teams", response_model=list[Team])
    async def teams(request: Request) -> list[Team]:
        return list(registry(request).teams.values())

    @r.post("/matches", status_code=201)
    async def create_match(request: Request, config: MatchConfig) -> dict[str, str]:
        reg = registry(request)
        for team_id in (config.home_team_id, config.away_team_id):
            if team_id not in reg.teams:
                raise HTTPException(422, f"unknown team '{team_id}'")
        if config.home_team_id == config.away_team_id:
            raise HTTPException(422, "home and away must be different teams")
        if reg.get(config.match_id) is not None:
            raise HTTPException(409, f"match '{config.match_id}' already exists")
        reg.create(config)
        pipeline: Pipeline | None = request.app.state.pipeline
        if pipeline is not None:
            # inprocess: also starts the sim here. hybrid: engine + agents learn the teams before the first event.
            await pipeline.start_match(config)
        if not request.app.state.topology.api_runs_sim and (sim_url := os.getenv("FULCRUM_SIM_URL")):
            # split / hybrid (Azure): the sim Container App runs the match and publishes to Event Hubs.
            try:
                await start_on_sim(request.app, sim_url, config)
            except Exception:
                reg.matches.pop(config.match_id, None)  # let the client retry with the same match_id
                raise
        return {"match_id": config.match_id}

    @r.get("/matches")
    async def list_matches(request: Request) -> list[dict[str, Any]]:
        return [rm.summary() for rm in registry(request).matches.values()]

    @r.get("/matches/{match_id}/state", response_model=MatchState)
    async def get_state(request: Request, match_id: str) -> MatchState:
        rm = match(request, match_id)
        if rm.state is None:
            raise HTTPException(404, "no state yet — the match has not started")
        return rm.state

    @r.get("/matches/{match_id}/events", response_model=list[MatchEvent], response_model_by_alias=True)
    async def get_events(request: Request, match_id: str, since_seq: int = -1) -> list[MatchEvent]:
        return match(request, match_id).events_since(since_seq)

    @r.get("/matches/{match_id}/winprob", response_model=list[WinProbPoint])
    async def get_winprob(request: Request, match_id: str) -> list[WinProbPoint]:
        return list(match(request, match_id).winprob)

    @r.get("/matches/{match_id}/moments", response_model=list[Moment])
    async def get_moments(request: Request, match_id: str) -> list[Moment]:
        return list(match(request, match_id).moments)

    @r.get("/matches/{match_id}/insights", response_model=list[Insight])
    async def get_insights(
        request: Request,
        match_id: str,
        persona: Persona | None = None,
        language: str | None = None,
        favourite_team_id: str | None = None,
        followed_player_id: str | None = None,
    ) -> list[Insight]:
        rm = match(request, match_id)
        prefs = ps.personalise_query(persona, language, favourite_team_id, followed_player_id)
        if prefs is None:
            return list(rm.insights.values())
        tr = request.app.state.translator
        # Snapshot first: new insights keep arriving while we await translations (iterating the live dict raised
        # "OrderedDict mutated during iteration" mid-match). Translations run concurrently.
        items = list(rm.insights.values())
        return list(await asyncio.gather(*(ps.personalise_insight(i, prefs, tr) for i in items)))

    @r.get("/matches/{match_id}/overlay-cues", response_model=list[OverlayCue])
    async def get_cues(
        request: Request,
        match_id: str,
        persona: Persona | None = None,
        language: str | None = None,
        since_clock_ms: int = Query(-1),
        favourite_team_id: str | None = None,
        followed_player_id: str | None = None,
    ) -> list[OverlayCue]:
        rm = match(request, match_id)
        cues = [c for c in rm.cues if c.show_at_clock_ms > since_clock_ms]
        prefs = ps.personalise_query(persona, language, favourite_team_id, followed_player_id)
        if prefs is None:
            return cues
        tr = request.app.state.translator
        return [await tr.cue(c, prefs.language) for c in ps.select_cues(cues, prefs)]

    @r.get("/matches/{match_id}/recap", response_model=Recap)
    async def get_recap(
        request: Request, match_id: str, persona: Persona = Persona.CASUAL, language: str = "en"
    ) -> Recap:
        rm = match(request, match_id)
        if rm.status != "finished":
            raise HTTPException(404, "recap is available after full time")
        prefs = ps.personalise_query(persona, language, None, None)
        best = await request.app.state.hub.best_recap(rm, prefs) if prefs else None
        if best is None:
            raise HTTPException(404, "recap not ready yet")
        return best

    def local_control(request: Request, match_id: str) -> MatchControl | None:
        match(request, match_id)  # 404 first
        pipeline: Pipeline | None = request.app.state.pipeline
        return pipeline.control(match_id) if pipeline is not None else None

    def no_controls() -> HTTPException:
        return HTTPException(
            501, "match controls need the simulator in this process or a sim service (FULCRUM_SIM_URL)"
        )

    @r.get("/matches/{match_id}/control")
    async def get_control(request: Request, match_id: str) -> dict[str, Any]:
        """Demo director: the match's live pacing (speed, paused, skipping)."""
        if (ctl := local_control(request, match_id)) is not None:
            return ctl.snapshot()
        if _remote_sim_url(request.app) is None:
            raise no_controls()
        return await _sim_control(request.app, "GET", match_id)

    @r.post("/matches/{match_id}/control")
    async def post_control(request: Request, match_id: str, body: ControlRequest) -> dict[str, Any]:
        """Demo director: set speed, pause / resume, or run fast to the next significant moment."""
        if (ctl := local_control(request, match_id)) is not None:
            try:
                return apply(ctl, body)
            except ControlError as exc:
                raise HTTPException(exc.status, str(exc)) from exc
        if _remote_sim_url(request.app) is None:
            raise no_controls()
        return await _sim_control(request.app, "POST", match_id, body.model_dump(mode="json"))

    @r.post("/matches/{match_id}/ask", response_model=AskAnswer)
    async def ask(request: Request, match_id: str, body: AskRequest) -> AskAnswer:
        """Ask the studio: the Studio Analyst answers from this match's data only (fact-checked, cached)."""
        rm = match(request, match_id)
        agents = request.app.state.translator.agents
        if agents is None or getattr(agents, "ask", None) is None:
            raise HTTPException(
                503, "the agents service is not running, so the studio can't answer questions right now"
            )
        client = client_key(request.headers, request.client.host if request.client else None)
        wait = request.app.state.ask_limiter.allow(client) or request.app.state.ask_global.allow("*")
        if wait is not None:
            raise HTTPException(
                429,
                "too many questions: wait a few seconds and ask again",
                headers={"Retry-After": str(max(1, round(wait)))},
            )
        return await agents.ask(
            match_id,
            body.question,
            persona=body.persona or Persona.CASUAL,
            language=body.language or "en",
            favourite_team_id=body.favourite_team_id,
            state=rm.state,
        )

    @r.post("/matches/{match_id}/translate", response_model=list[InsightVariant])
    async def translate(request: Request, match_id: str, body: TranslateRequest) -> list[InsightVariant]:
        rm = match(request, match_id)
        insight = rm.insights.get(body.insight_id)
        if insight is None:
            raise HTTPException(404, f"unknown insight '{body.insight_id}'")
        native = [v for v in insight.variants if v.language == body.language]
        if native:
            return native
        english = [v for v in insight.variants if v.language == "en"] or insight.variants
        return await request.app.state.translator.variants(insight, english, body.language)

    @r.get("/speech")
    async def speech_status(request: Request) -> dict[str, Any]:
        """Whether neural voice is available, so the overlay can pick its speech engine without a failed POST."""
        synth: SpeechSynth | None = request.app.state.speech
        voices = {lang: voice_for(lang)[1] for lang in ("en", "es", "fr", "pt", "de")}
        return {"enabled": synth is not None, "auth": synth.auth_mode if synth else None, "voices": voices}

    @r.post("/speech", response_class=Response)
    async def speech(request: Request, body: SpeechRequest) -> Response:
        """One commentary line as MP3 (Azure AI Speech neural voice); 503 when not configured."""
        synth: SpeechSynth | None = request.app.state.speech
        if synth is None:
            raise HTTPException(503, "Azure Speech is not configured; use the browser's speech synthesis")
        client = client_key(request.headers, request.client.host if request.client else None)
        wait = request.app.state.speech_limiter.allow(client) or request.app.state.speech_global.allow("*")
        if wait is not None:
            raise HTTPException(429, "too many speech requests", headers={"Retry-After": str(int(wait) + 1)})
        try:
            audio = await synth.synthesize(body.text, body.language)
        except SpeechError as exc:
            raise HTTPException(502, str(exc)) from exc
        return Response(audio, media_type="audio/mpeg", headers={"Cache-Control": "private, max-age=3600"})

    _producer_routes(r, match)
    return r


def _producer_routes(r: APIRouter, match: Any) -> None:
    """Producer console: editorial gate + overlay requests in plain language (API_CONTRACT §2)."""

    def desk(request: Request, match_id: str) -> ProducerDesk:
        _check_producer_key(request)
        match(request, match_id)  # 404 first
        return request.app.state.producer

    def graphics_agents(request: Request) -> Any:
        agents = request.app.state.translator.agents
        return agents if getattr(agents, "graphic", None) is not None else None

    def decided(fn: Any, *args: Any) -> ProducerDecision:
        try:
            return fn(*args)
        except DeskError as exc:
            raise HTTPException(exc.status, str(exc)) from exc

    @r.get("/matches/{match_id}/producer", response_model=ProducerState)
    async def producer_state(request: Request, match_id: str) -> ProducerState:
        """The gate settings, the held queue (oldest first) and the decision log (newest first)."""
        d = desk(request, match_id)
        return d.state(match_id, graphics_available=graphics_agents(request) is not None)

    @r.post("/matches/{match_id}/producer/settings", response_model=ProducerState)
    async def producer_settings(request: Request, match_id: str, body: ProducerSettings) -> ProducerState:
        """Switch approval on/off (off releases everything held) and set auto-approve seconds (0 = off)."""
        d = desk(request, match_id)
        d.configure(match_id, approval_required=body.approval_required, auto_approve_s=body.auto_approve_s)
        return d.state(match_id, graphics_available=graphics_agents(request) is not None)

    @r.get("/matches/{match_id}/producer/held", response_model=list[HeldItem])
    async def producer_held(request: Request, match_id: str) -> list[HeldItem]:
        return desk(request, match_id).held(match_id)

    @r.post("/matches/{match_id}/producer/held/{insight_id}/approve", response_model=ProducerDecision)
    async def producer_approve(request: Request, match_id: str, insight_id: str) -> ProducerDecision:
        return decided(desk(request, match_id).approve, match_id, insight_id)

    @r.post("/matches/{match_id}/producer/held/{insight_id}/kill", response_model=ProducerDecision)
    async def producer_kill(request: Request, match_id: str, insight_id: str) -> ProducerDecision:
        return decided(desk(request, match_id).kill, match_id, insight_id)

    @r.post("/matches/{match_id}/producer/held/{insight_id}/edit", response_model=ProducerDecision)
    async def producer_edit(request: Request, match_id: str, insight_id: str, body: ProducerEdit) -> Any:
        """Edited copy is fact-checked against the moment's facts; 422 with `issues` if it fails (still held)."""
        decision = decided(desk(request, match_id).edit, match_id, insight_id, body.variants)
        if not decision.ok:
            return problem(422, "Unprocessable Entity", "the edit failed the fact-check", issues=decision.issues)
        return decision

    @r.post("/matches/{match_id}/producer/graphics", response_model=GraphicProposal)
    async def producer_graphic(request: Request, match_id: str, body: GraphicRequest) -> GraphicProposal:
        """The Graphics Producer turns a plain-language request into a fact-checked OverlayCue to preview."""
        d = desk(request, match_id)
        rm = match(request, match_id)
        agents = graphics_agents(request)
        if agents is None:
            raise HTTPException(503, "the agents service is not running, so the graphics desk can't take requests")
        client = client_key(request.headers, request.client.host if request.client else None)
        wait = request.app.state.graphic_limiter.allow(client) or request.app.state.ask_global.allow("*")
        if wait is not None:
            raise HTTPException(429, "too many graphic requests", headers={"Retry-After": str(max(1, round(wait)))})
        proposal = await agents.graphic(match_id, body.request, state=rm.state, winprob=list(rm.winprob))
        d.remember(proposal)
        return proposal

    @r.post("/matches/{match_id}/producer/graphics/{proposal_id}/air", response_model=OverlayCue)
    async def producer_air(request: Request, match_id: str, proposal_id: str) -> OverlayCue:
        """Put a previewed graphic on air: published to every viewer now (persona-neutral, `origin="producer"`)."""
        d = desk(request, match_id)
        rm = match(request, match_id)
        clock = rm.state.clock_ms if rm.state is not None else rm.clock_ms
        seq = rm.state.seq if rm.state is not None else max(rm.last_seq, 0)
        try:
            return d.air(match_id, proposal_id, clock_ms=clock, seq=seq)
        except DeskError as exc:
            raise HTTPException(exc.status, str(exc)) from exc


# --------------------------------------------------------------------------- WebSocket


async def _serve_ws(ws: WebSocket, hub: Hub) -> None:
    """API_CONTRACT §3: subscribe / update_prefs / ping in; hello / event / state / … out."""
    await ws.accept()
    conn = Connection(ws, hub)
    sender = asyncio.create_task(_run_sender(conn), name="ws-sender")
    try:
        while True:
            raw = await ws.receive_text()
            try:
                msg = ClientMessage.model_validate_json(raw)
            except ValidationError as exc:
                conn.push("error", {"code": "bad_request", "message": str(exc.errors()[0]["msg"])})
                continue
            if msg.type == "ping":
                conn.push("pong", {})
            elif msg.type == "subscribe":
                if not msg.match_id or hub.registry.get(msg.match_id) is None:
                    conn.push("error", {"code": "unknown_match", "message": f"unknown match '{msg.match_id}'"})
                    continue
                if msg.prefs is not None:
                    conn.prefs = msg.prefs
                conn.reset_prefs_state()
                hub.subscribe(conn, msg.match_id)
                conn.push("hello", None)
            elif msg.type == "update_prefs":
                if msg.prefs is None:
                    conn.push("error", {"code": "bad_request", "message": "update_prefs requires prefs"})
                    continue
                conn.prefs = msg.prefs
                conn.reset_prefs_state()
                if conn.match_id:
                    conn.push("hello", None)  # re-send the catch-up snapshot in the new persona/language
    except WebSocketDisconnect:
        pass
    finally:
        hub.unsubscribe(conn)
        sender.cancel()
        await asyncio.gather(sender, return_exceptions=True)


async def _run_sender(conn: Connection) -> None:
    try:
        await conn.sender()
    except (WebSocketDisconnect, RuntimeError):
        pass  # socket closed underneath us
    except Exception:
        log.exception("websocket sender crashed")


# `uvicorn fulcrum.api.app:app` (make dev) reads .env like the other services do; variables already set in the
# environment win. Tests set FULCRUM_DOTENV=0 (tests/conftest.py) so a developer's .env never leaks into them.
if os.getenv("FULCRUM_DOTENV", "1") == "1":
    load_dotenv()
app = create_app()
