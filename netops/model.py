"""Source-of-truth model and the validation rules that reject a bad change.

`load()` parses data/network.yml. `validate()` returns a list of human-readable
problems (empty list = OK). Rules are checked all at once so one run shows
every mistake, not only the first.
"""
from __future__ import annotations

import ipaddress
from pathlib import Path
from typing import Optional

import yaml
from pydantic import BaseModel, ConfigDict, Field


class Iface(BaseModel):
    model_config = ConfigDict(extra="forbid")
    ip: str
    peer: str                       # "device:iface" on the other end of the cable


class Neighbor(BaseModel):
    model_config = ConfigDict(extra="forbid")
    via: Optional[str] = None       # eBGP over a directly connected interface
    peer: Optional[str] = None      # iBGP to another device's loopback
    route_map_in: Optional[str] = None
    rr_client: bool = False


class Aggregate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    prefix: str
    summary_only: bool = False


class Bgp(BaseModel):
    model_config = ConfigDict(extra="forbid")
    originate: list[str] = Field(default_factory=list)
    aggregates: list[Aggregate] = Field(default_factory=list)
    neighbors: list[Neighbor] = Field(default_factory=list)


class Ospf(BaseModel):
    model_config = ConfigDict(extra="forbid")
    interfaces: list[str]


class RouteMap(BaseModel):
    model_config = ConfigDict(extra="forbid")
    match_prefix_list: str
    set: list[str] = Field(default_factory=list)


class Device(BaseModel):
    model_config = ConfigDict(extra="forbid")
    role: str
    asn: int
    mgmt_ip: str
    loopback: str
    interfaces: dict[str, Iface] = Field(default_factory=dict)
    ospf: Optional[Ospf] = None
    bgp: Optional[Bgp] = None


class Mgmt(BaseModel):
    model_config = ConfigDict(extra="forbid")
    network: str
    subnet: str


class Network(BaseModel):
    model_config = ConfigDict(extra="forbid")
    site: str
    image: str
    mgmt: Mgmt
    prefix_lists: dict[str, list[str]] = Field(default_factory=dict)
    route_maps: dict[str, RouteMap] = Field(default_factory=dict)
    devices: dict[str, Device]


def load(path: str | Path) -> Network:
    return Network.model_validate(yaml.safe_load(Path(path).read_text()))


def split_endpoint(text: str) -> tuple[str, str]:
    dev, _, iface = text.partition(":")
    return dev, iface


def peer_ip(net: Network, dev: str, via: str) -> Optional[str]:
    """IP address of the device on the other end of `dev`'s interface `via`."""
    iface = net.devices[dev].interfaces.get(via)
    if not iface:
        return None
    pdev, pif = split_endpoint(iface.peer)
    other = net.devices.get(pdev, None)
    if other is None or pif not in other.interfaces:
        return None
    return other.interfaces[pif].ip.split("/")[0]


def bgp_sessions(net: Network, dev: str) -> list[dict]:
    """Resolve each BGP neighbour of `dev` to {ip, asn, kind, cfg}."""
    out = []
    d = net.devices[dev]
    for n in (d.bgp.neighbors if d.bgp else []):
        if n.via:
            ip = peer_ip(net, dev, n.via)
            pdev = split_endpoint(d.interfaces[n.via].peer)[0] if n.via in d.interfaces else None
            out.append({"ip": ip, "peer_dev": pdev, "kind": "ebgp", "cfg": n,
                        "asn": net.devices[pdev].asn if pdev in net.devices else None})
        else:
            pd = net.devices.get(n.peer)
            out.append({"ip": pd.loopback.split("/")[0] if pd else None, "peer_dev": n.peer,
                        "kind": "ibgp", "cfg": n, "asn": pd.asn if pd else None})
    return out


def expected_ospf_neighbors(net: Network, dev: str) -> int:
    d = net.devices[dev]
    if not d.ospf:
        return 0
    n = 0
    for ifname in d.ospf.interfaces:
        if ifname == "lo" or ifname not in d.interfaces:
            continue
        pdev, pif = split_endpoint(d.interfaces[ifname].peer)
        p = net.devices.get(pdev)
        if p and p.ospf and pif in p.ospf.interfaces:
            n += 1
    return n


def validate(net: Network) -> list[str]:
    errs: list[str] = []
    devs = net.devices

    # ---- addressing -------------------------------------------------------
    seen_ip: dict[str, str] = {}
    seen_mgmt: dict[str, str] = {}
    seen_rid: dict[str, str] = {}
    try:
        mgmt_net = ipaddress.ip_network(net.mgmt.subnet)
    except ValueError:
        mgmt_net = None
        errs.append(f"mgmt subnet {net.mgmt.subnet!r} is not a valid network")
    for name, d in devs.items():
        try:
            m = ipaddress.ip_address(d.mgmt_ip)
            if mgmt_net and m not in mgmt_net:
                errs.append(f"{name}: mgmt_ip {d.mgmt_ip} is outside {net.mgmt.subnet}")
            if d.mgmt_ip in seen_mgmt:
                errs.append(f"duplicate mgmt_ip {d.mgmt_ip}: {seen_mgmt[d.mgmt_ip]} and {name}")
            seen_mgmt[d.mgmt_ip] = name
        except ValueError:
            errs.append(f"{name}: mgmt_ip {d.mgmt_ip!r} is not a valid address")
        addrs = [("lo", d.loopback)] + [(i, v.ip) for i, v in d.interfaces.items()]
        for ifname, cidr in addrs:
            try:
                ip = ipaddress.ip_interface(cidr)
            except ValueError:
                errs.append(f"{name}:{ifname}: {cidr!r} is not a valid address")
                continue
            key = str(ip.ip)
            if key in seen_ip:
                errs.append(f"duplicate IP {key}: {seen_ip[key]} and {name}:{ifname}")
            seen_ip[key] = f"{name}:{ifname}"
        rid = d.loopback.split("/")[0]
        if rid in seen_rid:
            errs.append(f"duplicate router-id {rid}: {seen_rid[rid]} and {name}")
        seen_rid[rid] = name

    # ---- cables -----------------------------------------------------------
    for name, d in devs.items():
        for ifname, iface in d.interfaces.items():
            pdev, pif = split_endpoint(iface.peer)
            if pdev not in devs:
                errs.append(f"{name}:{ifname}: peer device {pdev!r} does not exist")
                continue
            back = devs[pdev].interfaces.get(pif)
            if back is None:
                errs.append(f"{name}:{ifname}: peer {iface.peer} has no such interface")
                continue
            if back.peer != f"{name}:{ifname}":
                errs.append(f"{name}:{ifname} -> {iface.peer}, but {iface.peer} points to {back.peer} (cable not reciprocal)")
                continue
            try:
                a, b = ipaddress.ip_interface(iface.ip), ipaddress.ip_interface(back.ip)
                if a.network != b.network:
                    errs.append(f"link {name}:{ifname} {iface.ip} <-> {iface.peer} {back.ip} are in different subnets")
                elif a.network.prefixlen != 31:
                    errs.append(f"link {name}:{ifname} should be a /31 (got /{a.network.prefixlen})")
            except ValueError:
                pass

    # ---- OSPF -------------------------------------------------------------
    for name, d in devs.items():
        if d.ospf:
            for ifname in d.ospf.interfaces:
                if ifname != "lo" and ifname not in d.interfaces:
                    errs.append(f"{name}: OSPF interface {ifname!r} does not exist")

    # ---- policy references ------------------------------------------------
    for rm_name, rm in net.route_maps.items():
        if rm.match_prefix_list not in net.prefix_lists:
            errs.append(f"route-map {rm_name}: prefix-list {rm.match_prefix_list!r} is not defined")

    # ---- BGP --------------------------------------------------------------
    for name, d in devs.items():
        if not d.bgp:
            continue
        for n in d.bgp.neighbors:
            if bool(n.via) == bool(n.peer):
                errs.append(f"{name}: a BGP neighbour needs exactly one of 'via' or 'peer'")
                continue
            if n.route_map_in and n.route_map_in not in net.route_maps:
                errs.append(f"{name}: route-map {n.route_map_in!r} is not defined")
            if n.via:
                if n.via not in d.interfaces:
                    errs.append(f"{name}: BGP via {n.via!r}, but that interface does not exist")
                    continue
                pdev = split_endpoint(d.interfaces[n.via].peer)[0]
                if pdev in devs and devs[pdev].asn == d.asn:
                    errs.append(f"{name}: neighbour over {n.via} ({pdev}) is in the same AS {d.asn}; use 'peer' for iBGP")
            else:
                if n.peer not in devs:
                    errs.append(f"{name}: iBGP peer {n.peer!r} does not exist")
                    continue
                if devs[n.peer].asn != d.asn:
                    errs.append(f"{name}: iBGP peer {n.peer} is AS {devs[n.peer].asn}, not {d.asn}")
                for end in (name, n.peer):
                    o = devs[end].ospf
                    if not (o and "lo" in o.interfaces):
                        errs.append(f"iBGP {name}<->{n.peer} peers on loopbacks, but {end} does not run OSPF on lo")
        # every session must exist on both ends
        for s in bgp_sessions(net, name):
            pd = s["peer_dev"]
            if pd not in devs or not devs[pd].bgp:
                if pd in devs:
                    errs.append(f"{name}: BGP neighbour {pd} has no BGP configured")
                continue
            back = [x for x in bgp_sessions(net, pd) if x["peer_dev"] == name]
            if not back:
                errs.append(f"BGP {name}->{pd} is configured but {pd} has no neighbour back to {name}")
        for p in d.bgp.originate:
            try:
                ipaddress.ip_network(p)
            except ValueError:
                errs.append(f"{name}: originated prefix {p!r} is invalid")
    return errs
