"""infra/: the Bicep `topology` param stays in step with `fulcrum.topology` and the azd parameter mapping."""

import json
import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from fulcrum.topology import Topology

INFRA = Path(__file__).resolve().parents[1] / "infra"


def test_topology_param_matches_the_python_values():
    bicep = (INFRA / "main.bicep").read_text(encoding="utf-8")
    allowed = re.search(r"@allowed\((\[[^\]]*\])\)\s*@description\([^\n]*\)\s*param topology string = ''", bicep)
    assert allowed, "param topology with @allowed([...]) not found"
    values = re.findall(r"'([^']*)'", allowed.group(1))
    assert values == ["", *(t.value for t in Topology)]


def test_azd_maps_fulcrum_topology_with_an_empty_default():
    params = json.loads((INFRA / "main.parameters.json").read_text(encoding="utf-8"))["parameters"]
    # empty → Bicep falls back to inProcess (FULCRUM_INPROCESS), i.e. the behaviour before topologies existed
    assert params["topology"]["value"] == "${FULCRUM_TOPOLOGY=}"
    assert params["inProcess"]["value"] == "${FULCRUM_INPROCESS=false}"


@pytest.mark.skipif(
    os.getenv("FULCRUM_TEST_BICEP") != "1" or shutil.which("az") is None,
    reason="set FULCRUM_TEST_BICEP=1 (needs the az CLI with Bicep) to compile infra/main.bicep",
)
def test_bicep_builds(tmp_path):
    out = tmp_path / "main.json"
    r = subprocess.run(
        ["az", "bicep", "build", "--file", str(INFRA / "main.bicep"), "--outfile", str(out)],
        capture_output=True,
        text=True,
        shell=os.name == "nt",  # az is az.cmd on Windows
    )
    assert r.returncode == 0, r.stderr
    variables = json.loads(out.read_text(encoding="utf-8"))["variables"]
    assert "effectiveTopology" in variables and "apiHostsPipeline" in variables
