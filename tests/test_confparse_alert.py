import time

from netops import confparse
from netops.alert import Alerter

RUNNING = """\
Building configuration...

Current configuration:
!
frr version 10.7.1
hostname r1
!
interface eth1
 ip address 10.2.10.0/31
 ip ospf area 0
exit
!
router bgp 65001
 neighbor 10.2.10.1 remote-as 65100
 !
 address-family ipv4 unicast
  network 198.51.100.0/24
 exit-address-family
exit
!
end
"""


def test_normalise_drops_noise():
    out = confparse.normalise(RUNNING)
    assert "Building configuration" not in out and "frr version" not in out and "\nend" not in out
    assert "hostname r1" in out


def test_parse_keeps_parent_context():
    p = confparse.parse(RUNNING)
    assert "interface eth1 | ip ospf area 0" in p
    assert "router bgp 65001 > address-family ipv4 unicast | network 198.51.100.0/24" in p


def test_same_line_under_different_parents_is_distinct():
    a = confparse.parse("interface eth1\n ip ospf area 0\ninterface eth2\n ip ospf area 0\n")
    assert len(a) == 4


def test_order_does_not_matter():
    a = "interface eth1\n ip address 1.1.1.1/31\n ip ospf area 0\n"
    b = "interface eth1\n ip ospf area 0\n ip address 1.1.1.1/31\n"
    assert confparse.parse(a) == confparse.parse(b)


def test_missing_finds_absent_intended_line():
    intended = RUNNING + "route-map X permit 10\n"
    assert confparse.missing(intended, RUNNING) == ["route-map X permit 10"]


def test_extra_running_lines_are_not_missing():
    assert confparse.missing("hostname r1\n", RUNNING) == []


def test_changed_reports_added_and_removed():
    after = RUNNING.replace(" ip ospf area 0\n", " ip ospf cost 500\n")
    added, removed = confparse.changed(RUNNING, after)
    assert added == ["interface eth1 | ip ospf cost 500"]
    assert removed == ["interface eth1 | ip ospf area 0"]


def test_identical_dumps_do_not_differ():
    assert confparse.changed(RUNNING, RUNNING) == ([], [])


# ---- alert state machine -------------------------------------------------

def make():
    sent = []
    return Alerter(sent.append, fail_after=2), sent


def test_single_blip_does_not_alert():
    a, sent = make()
    a.update({"r1/bgp": "bgp down"})
    a.update({})
    assert sent == []


def test_alerts_after_two_in_a_row_and_only_once():
    a, sent = make()
    for _ in range(5):
        a.update({"r1/bgp": "bgp down"})
    assert sent == ["ALERT: bgp down"]


def test_resolved_message_on_recovery():
    a, sent = make()
    a.update({"r1/bgp": "bgp down"})
    a.update({"r1/bgp": "bgp down"})
    a.update({})
    assert sent == ["ALERT: bgp down", "RESOLVED: bgp down"]


def test_resolved_not_sent_if_never_alerted():
    a, sent = make()
    a.update({"k": "x"})
    a.update({})
    assert sent == []


def test_independent_problems_alert_separately():
    a, sent = make()
    for _ in range(2):
        a.update({"r1/bgp": "one", "r2/ospf": "two"})
    assert sorted(sent) == ["ALERT: one", "ALERT: two"]


def test_broken_sender_does_not_crash_the_watcher():
    def boom(_):
        raise RuntimeError("webhook down")
    a = Alerter(boom, fail_after=1)
    a.update({"k": "x"})                                    # must not raise
    assert a.sent == 0
