from pathlib import Path

import yaml

from netops import confparse, model, render

DATA = Path(__file__).parent.parent / "data" / "network.yml"
NET = model.load(DATA)


def test_intended_config_for_route_reflector():
    cfg = render.intended_config(NET, "r3")
    assert "neighbor 10.255.2.2 route-reflector-client" in cfg
    assert "neighbor 10.255.4.4 route-reflector-client" in cfg
    assert "next-hop-self" not in cfg                      # the reflector is not an edge


def test_edges_use_next_hop_self_and_policy():
    cfg = render.intended_config(NET, "r4")
    assert "neighbor 10.255.3.3 next-hop-self" in cfg
    assert "neighbor 10.2.11.0 route-map FROM-CUST-A-PRIMARY in" in cfg
    assert "set local-preference 200" in cfg
    assert "aggregate-address 203.0.113.0/24 summary-only" in cfg


def test_backup_edge_has_lower_local_preference():
    assert "set local-preference 100" in render.intended_config(NET, "r2")


def test_customers_get_blackhole_routes_and_networks():
    cfg = render.intended_config(NET, "r1")
    assert "ip route 198.51.100.0/24 blackhole" in cfg
    assert "network 198.51.100.0/24" in cfg
    assert "route-map" not in cfg                          # customers need no policy


def test_only_used_policy_is_rendered():
    assert "CUST-B" not in render.intended_config(NET, "r2")


def test_ospf_only_where_defined():
    assert "router ospf" not in render.intended_config(NET, "r1")
    assert "router ospf" in render.intended_config(NET, "r3")


def test_topology_has_every_device_and_each_cable_once():
    topo = yaml.safe_load(render.topology(NET))
    assert set(topo["topology"]["nodes"]) == {"r1", "r2", "r3", "r4", "r5"}
    assert len(topo["topology"]["links"]) == 5
    assert topo["mgmt"]["ipv4-subnet"] == "172.30.30.0/24"


def test_bootstrap_has_only_addresses():
    cfg = render.bootstrap_config(NET, "r2")
    assert "ip address 10.2.10.1/31" in cfg and "router bgp" not in cfg


def test_inventory_lists_all_hosts():
    inv = yaml.safe_load(render.inventory(NET))
    hosts = inv["all"]["children"]["frr"]["hosts"]
    assert hosts["r3"]["ansible_host"] == "172.30.30.13"


def test_render_all_writes_files(tmp_path):
    files = render.render_all(NET, tmp_path)
    assert len(files) == 2 + 2 * 5
    assert (tmp_path / "configs/intended/r4.conf").read_text().startswith("hostname r4")


def test_rendering_is_deterministic():
    assert render.intended_config(NET, "r4") == render.intended_config(NET, "r4")


def test_intended_config_is_a_subset_of_itself():
    for d in NET.devices:
        assert confparse.missing(render.intended_config(NET, d), render.intended_config(NET, d)) == []
