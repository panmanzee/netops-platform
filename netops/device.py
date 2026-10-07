"""Talk to the routers over SSH (key auth, the vtysh login shell)."""
from __future__ import annotations

import json
import os
from dataclasses import dataclass

import paramiko


class DeviceError(Exception):
    pass


@dataclass
class Device:
    name: str
    host: str
    user: str = "automation"
    key_file: str = os.environ.get("NETOPS_SSH_KEY", "ansible/.ssh/id_ed25519")
    timeout: float = 8.0

    def run(self, command: str) -> str:
        """Run one vtysh command and return its output."""
        client = paramiko.SSHClient()
        client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        try:
            client.connect(self.host, username=self.user, key_filename=self.key_file,
                           timeout=self.timeout, banner_timeout=self.timeout, auth_timeout=self.timeout,
                           look_for_keys=False, allow_agent=False)
            _, stdout, _ = client.exec_command(command, timeout=self.timeout)
            return stdout.read().decode(errors="replace")
        except Exception as exc:                       # network, auth, timeout: all "device not reachable"
            raise DeviceError(f"{self.name} ({self.host}): {exc}") from exc
        finally:
            client.close()

    def run_json(self, command: str) -> dict:
        text = self.run(command).strip()
        try:
            return json.loads(text) if text else {}
        except json.JSONDecodeError as exc:
            raise DeviceError(f"{self.name}: '{command}' did not return JSON: {text[:80]!r}") from exc

    def running_config(self) -> str:
        return self.run("show running-config")


def devices_from_model(net) -> dict[str, Device]:
    return {n: Device(n, d.mgmt_ip) for n, d in net.devices.items()}
