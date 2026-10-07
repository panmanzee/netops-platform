"""Generate monitoring/grafana/dashboards/netops.json (dashboard as code).

Run: python monitoring/make_dashboard.py
"""
import json
from pathlib import Path

DS = {"type": "prometheus", "uid": "prom"}


def target(expr, legend="", ref="A"):
    return {"datasource": DS, "expr": expr, "legendFormat": legend, "refId": ref}


def stat(pid, title, expr, x, y, w=4, h=4, unit="none", steps=None, mappings=None, decimals=None):
    steps = steps or [{"color": "green", "value": None}]
    field = {"unit": unit, "thresholds": {"mode": "absolute", "steps": steps}, "mappings": mappings or []}
    if decimals is not None:
        field["decimals"] = decimals
    return {"id": pid, "type": "stat", "title": title, "datasource": DS, "gridPos": {"x": x, "y": y, "w": w, "h": h},
            "targets": [target(expr)], "fieldConfig": {"defaults": field, "overrides": []},
            "options": {"reduceOptions": {"calcs": ["lastNotNull"]}, "colorMode": "background", "graphMode": "none"}}


def series(pid, title, expr, legend, x, y, w=12, h=8, unit="none", ymax=None):
    field = {"unit": unit, "custom": {"lineWidth": 2, "fillOpacity": 10, "drawStyle": "line", "lineInterpolation": "stepAfter"}}
    if ymax is not None:
        field["max"] = ymax
    field["min"] = 0
    return {"id": pid, "type": "timeseries", "title": title, "datasource": DS, "gridPos": {"x": x, "y": y, "w": w, "h": h},
            "targets": [target(expr, legend)], "fieldConfig": {"defaults": field, "overrides": []},
            "options": {"legend": {"displayMode": "list", "placement": "bottom"}, "tooltip": {"mode": "multi"}}}


GOOD_BAD = [{"color": "red", "value": None}, {"color": "green", "value": 1}]
ZERO_GOOD = [{"color": "green", "value": None}, {"color": "red", "value": 1}]

panels = [
    stat(1, "Routers up", "sum(netops_device_up)", 0, 0, steps=[{"color": "red", "value": None}, {"color": "green", "value": 5}]),
    stat(2, "BGP down", "sum(netops_bgp_sessions_expected) - sum(netops_bgp_sessions_established)", 4, 0, steps=ZERO_GOOD),
    stat(3, "OSPF down", "sum(netops_ospf_neighbors_expected) - sum(netops_ospf_neighbors_full)", 8, 0, steps=ZERO_GOOD),
    stat(4, "Config drift", "count((netops_config_missing_lines + netops_config_changed_lines) > 0) or vector(0)", 12, 0, steps=ZERO_GOOD),
    stat(5, "Alerts sent", "sum(netops_alerts_sent_total)", 16, 0, steps=[{"color": "blue", "value": None}]),
    stat(6, "Poll time", "netops_poll_duration_seconds", 20, 0, unit="s", decimals=1, steps=[{"color": "green", "value": None}, {"color": "orange", "value": 10}]),
    series(7, "BGP sessions Established (per router)", "netops_bgp_sessions_established", "{{device}}", 0, 4, ymax=4),
    series(8, "OSPF neighbours Full (per router)", "netops_ospf_neighbors_full", "{{device}}", 12, 4, ymax=3),
    series(9, "Prefixes received per BGP peer", "netops_bgp_prefixes_received", "{{device}} <- {{peer}}", 0, 12),
    series(10, "Config lines changed since last backup", "netops_config_changed_lines + netops_config_missing_lines", "{{device}}", 12, 12),
    series(11, "Age of newest config backup", "netops_last_backup_age_seconds", "{{device}}", 0, 20, unit="s"),
]

dash = {"uid": "netops", "title": "Network overview", "tags": ["netops"], "timezone": "browser", "schemaVersion": 39,
        "version": 1, "refresh": "10s", "time": {"from": "now-15m", "to": "now"}, "panels": panels,
        "annotations": {"list": []}, "templating": {"list": []}}

out = Path(__file__).parent / "grafana" / "dashboards" / "netops.json"
out.write_text(json.dumps(dash, indent=2) + "\n")
print("wrote", out)
