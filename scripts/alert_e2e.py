"""Break a BGP session on purpose and prove the watcher alerts, then resolves.

Messages are delivered to a throwaway local web server, so no real chat account is used.
"""
import json
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer

from netops import model
from netops.alert import webhook_sender
from netops.device import devices_from_model
from netops.watch import Watcher

received: list[str] = []


class Handler(BaseHTTPRequestHandler):
    def do_POST(self):
        body = self.rfile.read(int(self.headers["Content-Length"]))
        received.append(json.loads(body)["content"])
        self.send_response(204)
        self.end_headers()

    def log_message(self, *a):
        pass


server = HTTPServer(("127.0.0.1", 0), Handler)
threading.Thread(target=server.serve_forever, daemon=True).start()
lab = sys.argv[1]


def vt(router: str, *cmds: str) -> None:
    args = ["docker", "exec", f"{lab}-{router}", "vtysh"]
    for c in cmds:
        args += ["-c", c]
    subprocess.run(args, check=True, capture_output=True)


net = model.load("data/network.yml")
w = Watcher(net, devices_from_model(net), "backups",
            webhook_sender(f"http://127.0.0.1:{server.server_port}/hook"), fail_after=2)

assert not w.cycle() and not w.cycle(), "network should start healthy"
assert received == [], f"unexpected alerts on a healthy network: {received}"
print("healthy: no alerts")

vt("r2", "configure terminal", "router bgp 65100", "neighbor 10.255.3.3 shutdown")
for _ in range(20):
    w.cycle()
    if any(m.startswith("ALERT") and "BGP" in m for m in received):
        break
    time.sleep(2)
alerts = [m for m in received if m.startswith("ALERT") and "BGP" in m]
assert alerts, "no ALERT was sent for a broken BGP session"
print("ALERT sent:", alerts[0])

vt("r2", "configure terminal", "router bgp 65100", "no neighbor 10.255.3.3 shutdown")
for _ in range(40):
    w.cycle()
    if any(m.startswith("RESOLVED") and "BGP" in m for m in received):
        break
    time.sleep(3)
resolved = [m for m in received if m.startswith("RESOLVED") and "BGP" in m]
assert resolved, "no RESOLVED message after the session came back"
print("RESOLVED sent:", resolved[0])
print(f"{len(received)} message(s) delivered in total")
