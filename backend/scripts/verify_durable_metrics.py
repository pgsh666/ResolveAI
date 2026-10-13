"""Prove usage/latency ledger remains unchanged through backend + worker restart."""
import argparse
import json
from pathlib import Path
import subprocess
import time
from scripts.auth_client import acceptance_client


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--docker", default="docker")
    parser.add_argument("--output", type=Path, default=Path(__file__).parents[2] / "output/hardening/metrics-restart.json")
    args = parser.parse_args()
    api = "http://127.0.0.1:8002/api/v1"
    client, _ = acceptance_client(api)
    def metrics():
        with client.open(api + '/performance/metrics', timeout=20) as response: return json.load(response)
    def durable(value):
        return {"models": value["models"], "workflow": value["workflow"]}
    before = metrics()
    root = Path(__file__).parents[2]
    result = subprocess.run([args.docker, "compose", "-p", "resolveai-acceptance", "-f", str(root/'docker-compose.yml'),
                             "-f", str(root/'docker-compose.acceptance.yml'), "restart", "backend", "worker"], capture_output=True)
    if result.returncode: raise RuntimeError("Acceptance restart failed; no business services changed")
    deadline = time.monotonic() + 90
    while time.monotonic() < deadline:
        try: after = metrics(); break
        except Exception: time.sleep(1)
    else: raise RuntimeError("Acceptance service not ready")
    report = {"passed": durable(before) == durable(after), "before": durable(before), "after": durable(after),
              "session_survived_restart": True, "business_services_restarted": False}
    args.output.parent.mkdir(parents=True, exist_ok=True); args.output.write_text(json.dumps(report, indent=2), encoding="utf-8")
    if not report["passed"]: raise RuntimeError("Durable metrics changed unexpectedly")
    print(json.dumps({"passed": True, "ledger_calls": after["models"]["ledger_calls"], "durable_runs": after["workflow"]["durable_runs"]}))


if __name__ == "__main__": main()
