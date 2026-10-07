"""Turn the source of truth into everything the network needs.

Outputs under build/:
  topology.clab.yml            Containerlab topology (devices + cables)
  configs/bootstrap/<d>.conf   minimal config each router boots with
  configs/intended/<d>.conf    the full config each router should have
  ansible/inventory.yml        hosts for the deploy playbook
"""
from __future__ import annotations

from pathlib import Path

import yaml

from .model import Network, bgp_sessions


def _ospf_iface_lines(ifname: str) -> list[str]:
    out = [" ip ospf area 0"]
    if ifname != "lo":
        out += [" ip ospf network point-to-point", " ip ospf dead-interval minimal hello-multiplier 3"]
    return out


def bootstrap_config(net: Network, dev: str) -> str:
    d = net.devices[dev]
    out = ["frr version 10.7.1", "frr defaults traditional", f"hostname {dev}",
           "service integrated-vtysh-config", "!", "interface lo", f" ip address {d.loopback}", "!"]
    for ifname, i in d.interfaces.items():
        out += [f"interface {ifname}", f" ip address {i.ip}", "!"]
    out += ["line vty", "!"]
    return "\n".join(out) + "\n"


def intended_config(net: Network, dev: str) -> str:
    """The full config the router should be running, in `show running-config` style."""
    d = net.devices[dev]
    sessions = bgp_sessions(net, dev)
    has_ebgp = any(s["kind"] == "ebgp" for s in sessions)
    ospf_if = set(d.ospf.interfaces) if d.ospf else set()
    out: list[str] = [f"hostname {dev}"]

    # static blackholes give BGP something to originate (no real hosts behind the routers)
    if d.bgp:
        for p in d.bgp.originate:
            out.append(f"ip route {p} blackhole")

    # policy objects this device actually uses
    used_maps = sorted({s["cfg"].route_map_in for s in sessions if s["cfg"].route_map_in})
    used_pl = sorted({net.route_maps[m].match_prefix_list for m in used_maps})
    for pl in used_pl:
        for n, rule in enumerate(net.prefix_lists[pl], start=1):
            out.append(f"ip prefix-list {pl} seq {5 * n} {rule}")
    for m in used_maps:
        rm = net.route_maps[m]
        out += [f"route-map {m} permit 10", f" match ip address prefix-list {rm.match_prefix_list}"]
        out += [f" set {s}" for s in rm.set]
        out.append("exit")

    out += ["interface lo", f" ip address {d.loopback}"]
    if "lo" in ospf_if:
        out += _ospf_iface_lines("lo")
    out.append("exit")
    for ifname, i in d.interfaces.items():
        out += [f"interface {ifname}", f" ip address {i.ip}"]
        if ifname in ospf_if:
            out += _ospf_iface_lines(ifname)
        out.append("exit")

    if d.ospf:
        out += ["router ospf", f" ospf router-id {d.loopback.split('/')[0]}", "exit"]

    if d.bgp:
        out += [f"router bgp {d.asn}", f" bgp router-id {d.loopback.split('/')[0]}", " no bgp ebgp-requires-policy"]
        for s in sessions:
            out.append(f" neighbor {s['ip']} remote-as {s['asn']}")
            if s["kind"] == "ibgp":
                out.append(f" neighbor {s['ip']} update-source lo")
        out.append(" address-family ipv4 unicast")
        for p in d.bgp.originate:
            out.append(f"  network {p}")
        for a in d.bgp.aggregates:
            out.append(f"  aggregate-address {a.prefix}" + (" summary-only" if a.summary_only else ""))
        for s in sessions:
            c = s["cfg"]
            if c.route_map_in:
                out.append(f"  neighbor {s['ip']} route-map {c.route_map_in} in")
            if s["kind"] == "ibgp":
                if c.rr_client:
                    out.append(f"  neighbor {s['ip']} route-reflector-client")
                elif has_ebgp:
                    out.append(f"  neighbor {s['ip']} next-hop-self")
        out += [" exit-address-family", "exit"]
    return "\n".join(out) + "\n"


def topology(net: Network) -> str:
    nodes, links, done = {}, [], set()
    for name, d in net.devices.items():
        nodes[name] = {
            "kind": "linux", "image": net.image, "mgmt-ipv4": d.mgmt_ip, "memory": "256M",
            "exec": ["ip route del default"],
            "binds": [f"configs/bootstrap/{name}.conf:/etc/frr/frr.conf"],
        }
        for ifname, i in d.interfaces.items():
            a, b = f"{name}:{ifname}", i.peer
            if (b, a) not in done:
                done.add((a, b))
                links.append({"endpoints": [a, b]})
    doc = {"name": net.site,
           "mgmt": {"network": net.mgmt.network, "ipv4-subnet": net.mgmt.subnet},
           "topology": {"nodes": nodes, "links": links}}
    return "---\n" + yaml.safe_dump(doc, sort_keys=False)


def inventory(net: Network) -> str:
    hosts = {n: {"ansible_host": d.mgmt_ip, "role": d.role, "asn": d.asn} for n, d in net.devices.items()}
    doc = {"all": {"vars": {
        "ansible_connection": "ansible.netcommon.network_cli",
        "ansible_network_os": "frr.frr.frr",
        "ansible_user": "automation",
        "ansible_python_interpreter": "auto_silent"},
        "children": {"frr": {"hosts": hosts}}}}
    return "---\n" + yaml.safe_dump(doc, sort_keys=False)


def render_all(net: Network, out: str | Path) -> list[Path]:
    out = Path(out)
    written = []

    def w(rel: str, text: str) -> None:
        p = out / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text)
        written.append(p)

    w("topology.clab.yml", topology(net))
    w("ansible/inventory.yml", inventory(net))
    for name in net.devices:
        w(f"configs/bootstrap/{name}.conf", bootstrap_config(net, name))
        w(f"configs/intended/{name}.conf", intended_config(net, name))
    return written
