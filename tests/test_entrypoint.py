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
