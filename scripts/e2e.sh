#!/usr/bin/env bash
# End-to-end proof against the live virtual network: health, backup, drift detection, alerting.
set -euo pipefail
PY=${PY:-.venv/bin/python}
LAB=clab-$(sed -n 's/^name: //p' build/topology.clab.yml)
fail() { echo "E2E FAIL: $*" >&2; exit 1; }
vt() { docker exec "$LAB-$1" vtysh "${@:2}"; }

echo "== 1. health: every BGP session and OSPF neighbour comes up"
for i in $(seq 1 30); do $PY -m netops verify >/tmp/netops_verify.txt && break; sleep 5; done
cat /tmp/netops_verify.txt
$PY -m netops verify >/dev/null || fail "network never became healthy"

echo "== 2. backup: configs saved as git history"
rm -rf backups
$PY -m netops backup
test "$(git -C backups log --oneline | wc -l)" -ge 1 || fail "no backup commit"

echo "== 3. drift: a clean network has none"
$PY -m netops drift || fail "clean network reported drift"

echo "== 4. drift: someone changes a router by hand -> detected"
vt r3 -c 'configure terminal' -c 'interface eth1' -c 'ip ospf cost 500' >/dev/null
if $PY -m netops drift >/tmp/netops_drift.txt; then fail "manual change was NOT detected"; fi
cat /tmp/netops_drift.txt
grep -q "ip ospf cost 500" /tmp/netops_drift.txt || fail "drift report does not show the change"

echo "== 5. revert the manual change -> clean again"
vt r3 -c 'configure terminal' -c 'interface eth1' -c 'no ip ospf cost' >/dev/null
$PY -m netops drift || fail "still drifting after revert"

echo "== 6. alerting: break a BGP session -> ALERT, fix it -> RESOLVED"
PYTHONPATH=. $PY scripts/alert_e2e.py "$LAB"
echo "E2E OK"
