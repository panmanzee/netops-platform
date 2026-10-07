"""Backup, drift and health logic against fake routers (no network, no containers)."""
import subprocess
from pathlib import Path

from netops import backup, health, model
from netops.device import Device, DeviceError
from netops.render import intended_config

DATA = Path(__file__).parent.parent / "data" / "network.yml"
NET = model.load(DATA)


class Fake(Device):
    """A router that answers from canned data instead of SSH."""

    def __init__(self, name, config="", up=True, json_answers=None):
        super().__init__(name, "0.0.0.0")
        self.config, self.up, self.answers = config, up, json_answers or {}

    def run(self, command):
        if not self.up:
            raise DeviceError(f"{self.name}: unreachable")
        return self.config if command == "show running-config" else ""

    def run_json(self, command):
        if not self.up:
            raise DeviceError(f"{self.name}: unreachable")
        return self.answers.get(command, {})


def good(name):
    return Fake(name, intended_config(NET, name))


def test_first_backup_is_new_then_unchanged(tmp_path):
    devs = {"r1": good("r1")}
    assert backup.backup(devs, tmp_path) == {"r1": "new"}
    assert backup.backup(devs, tmp_path) == {"r1": "unchanged"}
    assert subprocess.run(["git", "-C", str(tmp_path), "log", "--oneline"], capture_output=True, text=True).stdout.count("\n") == 1


def test_changed_config_makes_a_second_commit(tmp_path):
    devs = {"r1": good("r1")}
    backup.backup(devs, tmp_path)
    devs["r1"].config += "ip route 9.9.9.0/24 blackhole\n"
    assert backup.backup(devs, tmp_path) == {"r1": "changed"}
    log = subprocess.run(["git", "-C", str(tmp_path), "log", "--oneline"], capture_output=True, text=True).stdout
    assert log.count("\n") == 2


def test_unreachable_router_keeps_its_old_backup(tmp_path):
    devs = {"r1": good("r1")}
    backup.backup(devs, tmp_path)
    before = (tmp_path / "r1.conf").read_text()
    devs["r1"].up = False
    assert backup.backup(devs, tmp_path) == {"r1": "unreachable"}
    assert (tmp_path / "r1.conf").read_text() == before


def test_empty_answer_never_overwrites_a_good_backup(tmp_path):
    devs = {"r1": good("r1")}
    backup.backup(devs, tmp_path)
    devs["r1"].config = ""
    assert backup.backup(devs, tmp_path) == {"r1": "unreachable"}
    assert "hostname r1" in (tmp_path / "r1.conf").read_text()


def test_clean_router_has_no_drift(tmp_path):
    devs = {"r4": good("r4")}
    backup.backup(devs, tmp_path)
    rep = backup.check_drift(NET, devs, tmp_path)
    assert not backup.has_drift(rep["r4"])


def test_manual_change_is_detected(tmp_path):
    devs = {"r4": good("r4")}
    backup.backup(devs, tmp_path)
    devs["r4"].config = devs["r4"].config.replace("interface eth2\n", "interface eth2\n ip ospf cost 500\n")
    rep = backup.check_drift(NET, devs, tmp_path)["r4"]
    assert backup.has_drift(rep)
    assert "interface eth2 | ip ospf cost 500" in rep["added"]


def test_missing_intended_line_is_detected(tmp_path):
    devs = {"r4": Fake("r4", intended_config(NET, "r4").replace(" no bgp ebgp-requires-policy\n", ""))}
    rep = backup.check_drift(NET, devs, tmp_path)["r4"]
    assert any("ebgp-requires-policy" in m for m in rep["missing"])


def test_drift_check_marks_unreachable(tmp_path):
    rep = backup.check_drift(NET, {"r1": Fake("r1", up=False)}, tmp_path)
    assert rep["r1"]["reachable"] is False


# ---- health ----------------------------------------------------------------

BGP_OK = {"ipv4Unicast": {"peers": {"10.2.10.1": {"state": "Established", "pfxRcd": 1},
                                    "10.2.11.1": {"state": "Established", "pfxRcd": 2}}}}


def test_healthy_customer_router():
    dev = Fake("r1", json_answers={"show bgp summary json": BGP_OK})
    r = health.poll(NET, "r1", dev)
    assert r["bgp_established"] == 2 and r["bgp_prefixes"]["10.2.11.1"] == 2
    assert health.problems("r1", r) == {}


def test_idle_session_is_a_problem():
    bad = {"ipv4Unicast": {"peers": {"10.2.10.1": {"state": "Established"}, "10.2.11.1": {"state": "Active"}}}}
    r = health.poll(NET, "r1", Fake("r1", json_answers={"show bgp summary json": bad}))
    assert "r1/bgp" in health.problems("r1", r)


def test_missing_ospf_neighbour_is_a_problem():
    ospf = {"neighbors": {"10.255.3.3": [{"nbrState": "Full/-"}]}}                       # r3 expects 2
    dev = Fake("r3", json_answers={"show ip ospf neighbor json": ospf,
                                   "show bgp summary json": {"ipv4Unicast": {"peers": {
                                       "10.255.2.2": {"state": "Established"}, "10.255.4.4": {"state": "Established"}}}}})
    r = health.poll(NET, "r3", dev)
    assert r["ospf_full"] == 1
    assert list(health.problems("r3", r)) == ["r3/ospf"]


def test_unreachable_router_reports_a_single_clear_problem():
    r = health.poll(NET, "r2", Fake("r2", up=False))
    assert list(health.problems("r2", r)) == ["r2/unreachable"]
