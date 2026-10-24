"""`python -m fulcrum` container entrypoint: FULCRUM_SERVICE dispatch."""

import sys
import types

import pytest

import fulcrum.__main__ as entry


def test_unknown_service_exits_64(capsys: pytest.CaptureFixture[str]):
    assert entry.main("bogus") == 64
    assert "api|sim|engine|agents" in capsys.readouterr().err


@pytest.mark.parametrize("service,module", [("engine", "fulcrum.engine"), ("agents", "fulcrum.agents")])
def test_bus_services_run_their_module(monkeypatch: pytest.MonkeyPatch, service: str, module: str):
    calls = []
    monkeypatch.setattr(entry.runpy, "run_module", lambda name, **kw: calls.append((name, kw["run_name"])))
    assert entry.main(service) == 0
    assert calls == [(module, "__main__")]


@pytest.mark.parametrize(
    "service,app,port", [("api", "fulcrum.api.app:app", 8000), ("sim", "fulcrum.sim.service:app", 8001)]
)
def test_asgi_services_run_uvicorn(monkeypatch: pytest.MonkeyPatch, service: str, app: str, port: int):
    calls = []
    fake = types.SimpleNamespace(run=lambda a, **kw: calls.append((a, kw["port"])))
    monkeypatch.setitem(sys.modules, "uvicorn", fake)
    monkeypatch.delenv("PORT", raising=False)
    assert entry.main(service) == 0
    monkeypatch.setenv("PORT", "9000")
    assert entry.main(service.upper()) == 0
    assert calls == [(app, port), (app, 9000)]


@pytest.mark.parametrize("topology", ["hybrid", "inprocess"])
@pytest.mark.parametrize("service", ["engine", "agents"])
def test_bus_services_stand_by_when_the_api_hosts_them(monkeypatch: pytest.MonkeyPatch, service, topology):
    """hybrid / inprocess: the engine and agents apps must not join the api's consumer groups."""
    ran, idled = [], []
    monkeypatch.setattr(entry.runpy, "run_module", lambda name, **kw: ran.append(name))
    monkeypatch.setenv("FULCRUM_TOPOLOGY", topology)
    assert entry.main(service, idle=lambda: idled.append(True)) == 0
    assert ran == [] and idled == [True]


@pytest.mark.parametrize("env", [{"FULCRUM_TOPOLOGY": "split"}, {}, {"FULCRUM_BUS": "memory"}])
def test_bus_services_run_without_an_explicit_hosting_topology(env):
    # no explicit FULCRUM_TOPOLOGY → behave as before (the split deployment's apps have no topology set)
    assert entry.standby_reason("engine", env) is None
    assert entry.standby_reason("agents", env) is None
    assert entry.standby_reason("sim", {"FULCRUM_TOPOLOGY": "hybrid"}) is None
