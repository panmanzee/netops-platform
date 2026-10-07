"""netops: command-line entry point."""
from __future__ import annotations

import argparse
import sys

from . import backup as backup_mod
from . import health, model, render
from .alert import sender_from_env
from .device import devices_from_model
from .watch import Watcher


def _load(args):
    net = model.load(args.data)
    errs = model.validate(net)
    if errs:
        print(f"{args.data}: {len(errs)} problem(s):", file=sys.stderr)
        for e in errs:
            print(f"  - {e}", file=sys.stderr)
        raise SystemExit(1)
    return net


def cmd_validate(args) -> int:
    net = _load(args)
    n_links = sum(len(d.interfaces) for d in net.devices.values()) // 2
    print(f"OK: {len(net.devices)} devices, {n_links} links, no problems")
    return 0


def cmd_render(args) -> int:
    net = _load(args)
    files = render.render_all(net, args.out)
    print(f"wrote {len(files)} files under {args.out}/")
    return 0


def cmd_backup(args) -> int:
    net = _load(args)
    result = backup_mod.backup(devices_from_model(net), args.dir)
    for name, status in result.items():
        print(f"{name:<4} {status}")
    return 1 if "unreachable" in result.values() else 0


def cmd_drift(args) -> int:
    net = _load(args)
    report = backup_mod.check_drift(net, devices_from_model(net), args.dir)
    bad = 0
    for name, e in report.items():
        if not e["reachable"]:
            print(f"{name:<4} UNREACHABLE")
            bad += 1
        elif backup_mod.has_drift(e):
            bad += 1
            print(f"{name:<4} DRIFT")
            for l in e["missing"]:
                print(f"       missing vs source of truth: {l}")
            for l in e["added"]:
                print(f"       added since last backup:    {l}")
            for l in e["removed"]:
                print(f"       removed since last backup:  {l}")
        else:
            print(f"{name:<4} ok")
    return 1 if bad else 0


def cmd_verify(args) -> int:
    net = _load(args)
    devices = devices_from_model(net)
    bad: dict[str, str] = {}
    for name, dev in devices.items():
        r = health.poll(net, name, dev)
        probs = health.problems(name, r)
        bad.update(probs)
        state = "ok" if not probs else "PROBLEM"
        print(f"{name:<4} {state:<8} bgp {r['bgp_established']}/{r['bgp_expected']}  ospf {r['ospf_full']}/{r['ospf_expected']}")
    for msg in bad.values():
        print(f"  - {msg}")
    return 1 if bad else 0


def cmd_watch(args) -> int:
    net = _load(args)
    w = Watcher(net, devices_from_model(net), args.dir, sender_from_env(), fail_after=args.fail_after)
    w.serve(args.port, args.interval)
    return 0


def cmd_alert_test(args) -> int:
    sender_from_env()("netops: test alert, delivery works")
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="netops", description="Network automation and monitoring platform")
    p.add_argument("--data", default="data/network.yml", help="source of truth file")
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("validate", help="check the source of truth for mistakes").set_defaults(fn=cmd_validate)
    s = sub.add_parser("render", help="generate topology, configs and inventory")
    s.add_argument("--out", default="build")
    s.set_defaults(fn=cmd_render)
    s = sub.add_parser("backup", help="save every router's running config (git history)")
    s.add_argument("--dir", default="backups")
    s.set_defaults(fn=cmd_backup)
    s = sub.add_parser("drift", help="compare running configs with the source of truth and the last backup")
    s.add_argument("--dir", default="backups")
    s.set_defaults(fn=cmd_drift)
    sub.add_parser("verify", help="one-shot health check of BGP and OSPF").set_defaults(fn=cmd_verify)
    s = sub.add_parser("watch", help="poll continuously, expose metrics, send alerts")
    s.add_argument("--dir", default="backups")
    s.add_argument("--port", type=int, default=9108)
    s.add_argument("--interval", type=float, default=15)
    s.add_argument("--fail-after", type=int, default=2, help="polls in a row before alerting")
    s.set_defaults(fn=cmd_watch)
    sub.add_parser("alert-test", help="send a test alert").set_defaults(fn=cmd_alert_test)
    args = p.parse_args(argv)
    return args.fn(args)


if __name__ == "__main__":
    raise SystemExit(main())
