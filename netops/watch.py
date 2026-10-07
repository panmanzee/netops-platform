"""The long-running watcher: polls every router, exposes Prometheus metrics, sends alerts."""
from __future__ import annotations

import time
from pathlib import Path

from prometheus_client import CollectorRegistry, Counter, Gauge, start_http_server

from . import backup, health
from .alert import Alerter, Sender
from .device import Device
from .model import Network


class Watcher:
    def __init__(self, net: Network, devices: dict[str, Device], backup_dir: str | Path, send: Sender,
                 fail_after: int = 2):
        self.net, self.devices, self.backup_dir = net, devices, Path(backup_dir)
        self.alerter = Alerter(send, fail_after)
        self.registry = CollectorRegistry()
        g = lambda n, h, labels=("device",): Gauge(n, h, labels, registry=self.registry)  # noqa: E731
        self.up = g("netops_device_up", "1 if the router answers over SSH")
        self.bgp_exp = g("netops_bgp_sessions_expected", "BGP sessions the source of truth defines")
        self.bgp_est = g("netops_bgp_sessions_established", "BGP sessions currently Established")
        self.bgp_pfx = g("netops_bgp_prefixes_received", "Prefixes received from a BGP peer", ("device", "peer"))
        self.ospf_exp = g("netops_ospf_neighbors_expected", "OSPF neighbours the source of truth defines")
        self.ospf_full = g("netops_ospf_neighbors_full", "OSPF neighbours currently Full")
        self.missing = g("netops_config_missing_lines", "Intended config lines the router does not have")
        self.changed = g("netops_config_changed_lines", "Config lines changed since the last backup")
        self.backup_age = g("netops_last_backup_age_seconds", "Age of the newest config backup")
        self.poll_seconds = Gauge("netops_poll_duration_seconds", "Duration of the last full poll", registry=self.registry)
        self.alerts = Counter("netops_alerts_sent", "Alert messages delivered", registry=self.registry)

    def cycle(self) -> dict[str, str]:
        """One full poll of the network. Returns the problems found."""
        t0 = time.time()
        found: dict[str, str] = {}
        drift = backup.check_drift(self.net, self.devices, self.backup_dir)
        for name, dev in self.devices.items():
            r = health.poll(self.net, name, dev)
            self.up.labels(name).set(1 if r["up"] else 0)
            self.bgp_exp.labels(name).set(r["bgp_expected"])
            self.bgp_est.labels(name).set(r["bgp_established"])
            self.ospf_exp.labels(name).set(r["ospf_expected"])
            self.ospf_full.labels(name).set(r["ospf_full"])
            for peer, n in r["bgp_prefixes"].items():
                self.bgp_pfx.labels(name, peer).set(n)
            found.update(health.problems(name, r))
            d = drift[name]
            if d["reachable"]:
                self.missing.labels(name).set(len(d["missing"]))
                self.changed.labels(name).set(len(d["added"]) + len(d["removed"]))
                if backup.has_drift(d):
                    parts = []
                    if d["missing"]:
                        parts.append(f"{len(d['missing'])} intended line(s) missing")
                    if d["added"] or d["removed"]:
                        parts.append(f"{len(d['added'])} added / {len(d['removed'])} removed since last backup")
                    found[f"{name}/drift"] = f"{name}: config drift ({'; '.join(parts)})"
            f = self.backup_dir / f"{name}.conf"
            if f.exists():
                self.backup_age.labels(name).set(time.time() - f.stat().st_mtime)
        before = self.alerter.sent
        self.alerter.update(found)
        self.alerts.inc(self.alerter.sent - before)
        self.poll_seconds.set(time.time() - t0)
        return found

    def serve(self, port: int, interval: float) -> None:
        start_http_server(port, registry=self.registry)
        print(f"netops watch: metrics on :{port}/metrics, polling every {interval:.0f}s", flush=True)
        while True:
            self.cycle()
            time.sleep(interval)
