"""FULCRUM_TOPOLOGY parsing and its backwards-compatible fallbacks (FULCRUM_INPROCESS, FULCRUM_BUS)."""

import pytest

from fulcrum.topology import Topology, parse_topology, resolve_topology


@pytest.mark.parametrize(
    "env,expected",
    [
        ({}, Topology.INPROCESS),  # FULCRUM_BUS defaults to memory
        ({"FULCRUM_BUS": "memory"}, Topology.INPROCESS),
        ({"FULCRUM_BUS": "eventhubs"}, Topology.SPLIT),
        ({"FULCRUM_BUS": "eventhubs", "FULCRUM_INPROCESS": "1"}, Topology.INPROCESS),
        ({"FULCRUM_BUS": "memory", "FULCRUM_INPROCESS": "false"}, Topology.SPLIT),
        ({"FULCRUM_BUS": "eventhubs", "FULCRUM_INPROCESS": ""}, Topology.SPLIT),  # empty = unset
        ({"FULCRUM_TOPOLOGY": "hybrid", "FULCRUM_INPROCESS": "1"}, Topology.HYBRID),  # explicit wins
        ({"FULCRUM_TOPOLOGY": " Split ", "FULCRUM_BUS": "memory"}, Topology.SPLIT),
        ({"FULCRUM_TOPOLOGY": "", "FULCRUM_BUS": "eventhubs"}, Topology.SPLIT),  # azd passes "" when unset
        ({"FULCRUM_TOPOLOGY": "in-process"}, Topology.INPROCESS),
    ],
)
def test_resolve_topology(env, expected):
    assert resolve_topology(env) is expected


def test_unknown_topology_is_rejected():
    with pytest.raises(ValueError, match="split|inprocess|hybrid"):
        parse_topology("cluster")


def test_roles_per_topology():
    assert [t.api_hosts_pipeline for t in Topology] == [False, True, True]
    assert [t.api_runs_sim for t in Topology] == [False, True, False]
    assert [t.needs_shared_bus for t in Topology] == [True, False, True]
