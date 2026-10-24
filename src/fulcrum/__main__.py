"""`python -m fulcrum` — the container entrypoint. Runs one service, chosen by `FULCRUM_SERVICE`:

    api    → uvicorn fulcrum.api.app:app     on $PORT (default 8000), REST + WebSocket
    sim    → uvicorn fulcrum.sim.service:app on $PORT (default 8001), internal match trigger
    engine → python -m fulcrum.engine        (bus consumer)
    agents → python -m fulcrum.agents        (bus consumer)

Single-container fallback: `FULCRUM_SERVICE=api` with `FULCRUM_BUS=memory` (or `FULCRUM_INPROCESS=1`) runs the
simulator, engine and agents inside the API process.
"""

from __future__ import annotations

import os
import runpy
import sys

ASGI_APPS = {"api": ("fulcrum.api.app:app", 8000), "sim": ("fulcrum.sim.service:app", 8001)}
MODULES = {"engine": "fulcrum.engine", "agents": "fulcrum.agents"}
SERVICES = (*ASGI_APPS, *MODULES)


def main(service: str | None = None) -> int:
    """Run the selected service until it exits. Returns a process exit code (64 for an unknown service)."""
    service = (service or os.getenv("FULCRUM_SERVICE", "api")).strip().lower()
    if service in ASGI_APPS:
        import uvicorn

        app, default_port = ASGI_APPS[service]
        port = int(os.getenv("PORT", str(default_port)))
        uvicorn.run(app, host="0.0.0.0", port=port, proxy_headers=True, forwarded_allow_ips="*")
        return 0
    if service in MODULES:
        runpy.run_module(MODULES[service], run_name="__main__", alter_sys=True)
        return 0
    print(f"FULCRUM_SERVICE must be one of {'|'.join(SERVICES)} (got {service!r})", file=sys.stderr)
    return 64


if __name__ == "__main__":
    raise SystemExit(main())
