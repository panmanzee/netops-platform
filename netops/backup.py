"""Config backups (kept as git history) and drift detection."""
from __future__ import annotations

import subprocess
from pathlib import Path

from . import confparse
from .device import Device, DeviceError
from .model import Network
from .render import intended_config

_GIT = ["git", "-c", "user.name=netops", "-c", "user.email=netops@localhost"]


def _git(root: Path, *args: str) -> str:
    return subprocess.run([*_GIT, "-C", str(root), *args], capture_output=True, text=True, check=True).stdout


def latest(root: Path, name: str) -> str | None:
    f = root / f"{name}.conf"
    return f.read_text() if f.exists() else None


def backup(devices: dict[str, Device], root: str | Path) -> dict[str, str]:
    """Save each router's running config. Returns name -> new | changed | unchanged | unreachable."""
    root = Path(root)
    root.mkdir(parents=True, exist_ok=True)
    if not (root / ".git").exists():
        _git(root, "init", "-q")
    result: dict[str, str] = {}
    for name, dev in devices.items():
        try:
            text = confparse.normalise(dev.running_config())
        except DeviceError:
            result[name] = "unreachable"
            continue
        if "hostname" not in text:                      # an empty / error answer must never overwrite a good backup
            result[name] = "unreachable"
            continue
        old = latest(root, name)
        (root / f"{name}.conf").write_text(text)
        result[name] = "unchanged" if old == text else ("new" if old is None else "changed")
    _git(root, "add", "-A")
    if _git(root, "status", "--porcelain").strip():
        summary = ", ".join(f"{n} {s}" for n, s in result.items() if s in ("new", "changed"))
        _git(root, "commit", "-q", "-m", f"backup: {summary}")
    return result


def check_drift(net: Network, devices: dict[str, Device], root: str | Path) -> dict[str, dict]:
    """For each router: lines missing vs the source of truth, and lines changed since the last backup."""
    root = Path(root)
    report: dict[str, dict] = {}
    for name, dev in devices.items():
        entry = {"reachable": True, "missing": [], "added": [], "removed": [], "has_backup": False}
        try:
            running = confparse.normalise(dev.running_config())
        except DeviceError:
            entry["reachable"] = False
            report[name] = entry
            continue
        entry["missing"] = confparse.missing(intended_config(net, name), running)
        golden = latest(root, name)
        if golden is not None:
            entry["has_backup"] = True
            entry["added"], entry["removed"] = confparse.changed(golden, running)
        report[name] = entry
    return report


def has_drift(entry: dict) -> bool:
    return bool(entry["missing"] or entry["added"] or entry["removed"])
