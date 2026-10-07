"""Every rule that should reject a bad change to the source of truth."""
import copy
from pathlib import Path

import pytest
import yaml

from netops import model

DATA = Path(__file__).parent.parent / "data" / "network.yml"


@pytest.fixture
def raw():
    return yaml.safe_load(DATA.read_text())


def errors(raw):
    return model.validate(model.Network.model_validate(raw))


def test_shipped_data_is_valid():
    assert model.validate(model.load(DATA)) == []


def test_duplicate_interface_ip(raw):
    raw["devices"]["r5"]["interfaces"]["eth1"]["ip"] = "10.2.14.0/31"      # same as r4's end
    assert any("duplicate IP 10.2.14.0" in e for e in errors(raw))


def test_duplicate_loopback_is_also_duplicate_router_id(raw):
    raw["devices"]["r5"]["loopback"] = "10.255.1.1/32"
    e = errors(raw)
    assert any("duplicate IP 10.255.1.1" in x for x in e)
    assert any("duplicate router-id" in x for x in e)


def test_duplicate_mgmt_ip(raw):
    raw["devices"]["r2"]["mgmt_ip"] = raw["devices"]["r1"]["mgmt_ip"]
    assert any("duplicate mgmt_ip" in e for e in errors(raw))


def test_mgmt_ip_outside_subnet(raw):
    raw["devices"]["r2"]["mgmt_ip"] = "192.168.9.9"
    assert any("outside 172.30.30.0/24" in e for e in errors(raw))


def test_cable_must_be_reciprocal(raw):
    raw["devices"]["r2"]["interfaces"]["eth1"]["peer"] = "r5:eth1"
    assert any("not reciprocal" in e for e in errors(raw))


def test_peer_device_must_exist(raw):
    raw["devices"]["r1"]["interfaces"]["eth1"]["peer"] = "r99:eth1"
    assert any("r99" in e and "does not exist" in e for e in errors(raw))


def test_link_ends_must_share_a_subnet(raw):
    raw["devices"]["r2"]["interfaces"]["eth1"]["ip"] = "10.2.77.1/31"
    assert any("different subnets" in e for e in errors(raw))


def test_links_must_be_slash_31(raw):
    raw["devices"]["r1"]["interfaces"]["eth1"]["ip"] = "10.2.10.1/30"
    raw["devices"]["r2"]["interfaces"]["eth1"]["ip"] = "10.2.10.2/30"
    assert any("/31" in e for e in errors(raw))


def test_invalid_address(raw):
    raw["devices"]["r1"]["loopback"] = "10.255.1.999/32"
    assert any("not a valid address" in e for e in errors(raw))


def test_unknown_route_map(raw):
    raw["devices"]["r2"]["bgp"]["neighbors"][0]["route_map_in"] = "NOPE"
    assert any("route-map 'NOPE' is not defined" in e for e in errors(raw))


def test_route_map_with_missing_prefix_list(raw):
    raw["route_maps"]["FROM-CUST-B"]["match_prefix_list"] = "GONE"
    assert any("prefix-list 'GONE' is not defined" in e for e in errors(raw))


def test_ibgp_peer_in_other_as(raw):
    raw["devices"]["r2"]["bgp"]["neighbors"][1] = {"peer": "r1"}
    assert any("not 65100" in e for e in errors(raw))


def test_ebgp_over_link_to_same_as(raw):
    raw["devices"]["r5"]["asn"] = 65001
    raw["devices"]["r1"]["interfaces"]["eth1"]["peer"] = "r2:eth1"
    # r4 (AS 65100) is still different, but make r2 same as r1 to trigger the rule
    raw["devices"]["r2"]["asn"] = 65001
    assert any("same AS" in e for e in errors(raw))


def test_bgp_neighbour_must_exist_on_both_ends(raw):
    raw["devices"]["r3"]["bgp"]["neighbors"] = [{"peer": "r4", "rr_client": True}]   # r2 still points at r3
    assert any("r3 has no neighbour back to r2" in e or "no neighbour back" in e for e in errors(raw))


def test_ibgp_on_loopbacks_needs_ospf_on_lo(raw):
    raw["devices"]["r3"]["ospf"]["interfaces"] = ["eth1", "eth2"]
    assert any("does not run OSPF on lo" in e for e in errors(raw))


def test_ospf_interface_must_exist(raw):
    raw["devices"]["r2"]["ospf"]["interfaces"].append("eth9")
    assert any("OSPF interface 'eth9'" in e for e in errors(raw))


def test_neighbour_needs_via_or_peer(raw):
    raw["devices"]["r1"]["bgp"]["neighbors"][0] = {}
    assert any("exactly one of" in e for e in errors(raw))


def test_all_mistakes_are_reported_together(raw):
    raw["devices"]["r2"]["mgmt_ip"] = raw["devices"]["r1"]["mgmt_ip"]
    raw["devices"]["r5"]["loopback"] = "10.255.1.1/32"
    assert len(errors(raw)) >= 3


def test_unknown_field_is_rejected(raw):
    raw["devices"]["r1"]["colour"] = "blue"
    with pytest.raises(Exception):
        model.Network.model_validate(raw)


def test_expected_neighbour_counts():
    net = model.load(DATA)
    assert [model.expected_ospf_neighbors(net, d) for d in ("r1", "r2", "r3", "r4", "r5")] == [0, 1, 2, 1, 0]
    assert [len(model.bgp_sessions(net, d)) for d in ("r1", "r2", "r3", "r4", "r5")] == [2, 2, 2, 3, 1]
