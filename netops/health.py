"""Read live routing state and decide whether the network is healthy."""
from __future__ import annotations

from .device import Device, DeviceError
from .model import Network, bgp_sessions, expected_ospf_neighbors


def poll(net: Network, name: str, dev: Device) -> dict:
    """One health reading for one router."""
    d = net.devices[name]
    reading = {
        "up": False,
        "bgp_expected": len(bgp_sessions(net, name)),
        "bgp_established": 0,
        "bgp_prefixes": {},                      # peer ip -> prefixes received
        "ospf_expected": expected_ospf_neighbors(net, name),
        "ospf_full": 0,
    }
    try:
        if d.bgp:
            summary = dev.run_json("show bgp summary json")
            peers = (summary.get("ipv4Unicast") or {}).get("peers", {})
            for ip, p in peers.items():
                if p.get("state") == "Established":
                    reading["bgp_established"] += 1
                reading["bgp_prefixes"][ip] = p.get("pfxRcd", 0) or 0
        if d.ospf:
            nbrs = dev.run_json("show ip ospf neighbor json").get("neighbors", {})
            for entries in nbrs.values():
                for e in entries:
                    if str(e.get("nbrState", "")).startswith("Full"):
                        reading["ospf_full"] += 1
        reading["up"] = True
    except DeviceError:
        pass
    return reading


def problems(name: str, r: dict) -> dict[str, str]:
    """key -> message for everything wrong with this reading (empty dict = healthy)."""
    out: dict[str, str] = {}
    if not r["up"]:
        out[f"{name}/unreachable"] = f"{name} is not reachable over SSH"
        return out
    if r["bgp_established"] < r["bgp_expected"]:
        out[f"{name}/bgp"] = f"{name}: only {r['bgp_established']}/{r['bgp_expected']} BGP sessions are Established"
    if r["ospf_full"] < r["ospf_expected"]:
        out[f"{name}/ospf"] = f"{name}: only {r['ospf_full']}/{r['ospf_expected']} OSPF neighbours are Full"
    return out
