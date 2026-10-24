"""`python -m fulcrum` — the container entrypoint. Runs one service, chosen by `FULCRUM_SERVICE`:

    api    → uvicorn fulcrum.api.app:app     on $PORT (default 8000), REST + WebSocket
    sim    → uvicorn fulcrum.sim.service:app on $PORT (default 8001), internal match trigger
    engine → python -m fulcrum.engine        (bus consumer)
    agents → python -m fulcrum.agents        (bus consumer)

Topologies (`fulcrum.topology`, FULCRUM_TOPOLOGY): in `inprocess` the api runs sim + engine + agents on the memory
bus; in `hybrid` the api runs engine + agents on Event Hubs while the sim is its own service.

When FULCRUM_TOPOLOGY is set explicitly and says the api hosts the engine and agents, `engine` / `agents` stand by
instead of consuming: they would otherwise join the same consumer groups as the api and take half the partitions.
(Container Apps keeps a replica of an app without ingress or scale rules running even at minReplicas 0.)
"""

from __future__ import annotations

import logging
import os
import runpy
import sys
import time
from collections.abc import Callable

from fulcrum.topology import parse_topology

ASGI_APPS = {"api": ("fulcrum.api.app:app", 8000), "sim": ("fulcrum.sim.service:app", 8001)}
MODULES = {"engine": "fulcrum.engine", "agents": "fulcrum.agents"}
SERVICES = (*ASGI_APPS, *MODULES)

log = logging.getLogger("fulcrum")


def standby_reason(service: str, env: dict[str, str] | None = None) -> str | None:
    """Why `service` must not consume the bus under the explicit FULCRUM_TOPOLOGY, or None if it should run."""
    env = dict(os.environ) if env is None else env
    raw = (env.get("FULCRUM_TOPOLOGY") or "").strip()
    if service not in MODULES or not raw:
        return None
    topology = parse_topology(raw)
    if topology.api_hosts_pipeline:
        return f"FULCRUM_TOPOLOGY={topology}: the api process runs the {service} (same consumer groups)"
    return None


def _idle_forever() -> None:  # pragma: no cover - blocks by design
    while True:
        time.sleep(3600)


def main(service: str | None = None, *, idle: Callable[[], None] = _idle_forever) -> int:
    """Run the selected service until it exits. Returns a process exit code (64 for an unknown service)."""
    service = (service or os.getenv("FULCRUM_SERVICE", "api")).strip().lower()
    if service in ASGI_APPS:
        import uvicorn

        app, default_port = ASGI_APPS[service]
        port = int(os.getenv("PORT", str(default_port)))
        uvicorn.run(app, host="0.0.0.0", port=port, proxy_headers=True, forwarded_allow_ips="*")
        return 0
    if service in MODULES:
        if (reason := standby_reason(service)) is not None:
            logging.basicConfig(level=logging.INFO)
            log.warning("%s standing by, not consuming the bus: %s", service, reason)
            idle()
            return 0
        runpy.run_module(MODULES[service], run_name="__main__", alter_sys=True)
        return 0
    print(f"FULCRUM_SERVICE must be one of {'|'.join(SERVICES)} (got {service!r})", file=sys.stderr)
    return 64


if __name__ == "__main__":
    raise SystemExit(main())
