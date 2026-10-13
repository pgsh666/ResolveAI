from __future__ import annotations

import argparse
import json
import time
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path

from app.services.performance import latency_summary
CLIENT = None


def request_once(url: str, timeout: float) -> dict:
    started = time.perf_counter()
    status = 0
    error = None
    server_ms = None
    try:
        with (CLIENT.open if CLIENT else urllib.request.urlopen)(url, timeout=timeout) as response:
            status = response.status
            response.read()
            header = response.headers.get("X-Response-Time-Ms")
            server_ms = float(header) if header else None
    except urllib.error.HTTPError as exc:
        status = exc.code
        error = str(exc)
    except Exception as exc:
        error = f"{type(exc).__name__}: {exc}"
    return {
        "status": status,
        "duration_ms": round((time.perf_counter() - started) * 1000, 3),
        "server_duration_ms": server_ms,
        "error": error,
    }


def run_level(url: str, concurrency: int, requests: int, timeout: float) -> dict:
    started = time.perf_counter()
    with ThreadPoolExecutor(max_workers=concurrency) as executor:
        futures = [executor.submit(request_once, url, timeout) for _ in range(requests)]
        results = [future.result() for future in as_completed(futures)]
    elapsed = time.perf_counter() - started
    successful = [item for item in results if 200 <= item["status"] < 300]
    latencies = [item["duration_ms"] for item in successful]
    server_latencies = [item["server_duration_ms"] for item in successful if item["server_duration_ms"] is not None]
    return {
        "concurrency": concurrency,
        "requests": requests,
        "successful": len(successful),
        "failed": len(results) - len(successful),
        "throughput_rps": round(len(successful) / elapsed, 2) if elapsed else 0.0,
        "client_latency": latency_summary(latencies),
        "server_latency": latency_summary(server_latencies),
        "errors": [item["error"] for item in results if item["error"]][:5],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="ResolveAI 5/10/20 concurrency benchmark")
    parser.add_argument("--base-url", default="http://localhost:8000")
    parser.add_argument("--levels", default="5,10,20")
    parser.add_argument("--requests-per-level", type=int, default=30)
    parser.add_argument("--timeout", type=float, default=15.0)
    parser.add_argument("--output-dir", default="evaluation/reports/performance")
    args = parser.parse_args()
    from scripts.auth_client import acceptance_client
    global CLIENT
    CLIENT, _ = acceptance_client(args.base_url.rstrip('/') + '/api/v1')

    levels = [int(item.strip()) for item in args.levels.split(",") if item.strip()]
    query = urllib.parse.urlencode({"q": "退款审批与风控规范"})
    url = f"{args.base_url.rstrip('/')}/api/v1/knowledge/search?{query}"
    warmup = request_once(url, args.timeout)
    if not 200 <= warmup["status"] < 300:
        print(f"Warm-up failed: {warmup}")
        return 1

    results = [
        run_level(url, level, max(args.requests_per_level, level), args.timeout)
        for level in levels
    ]
    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "target": f"{args.base_url.rstrip('/')}/api/v1/knowledge/search",
        "levels": results,
    }
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    json_path = output_dir / "performance-latest.json"
    md_path = output_dir / "performance-latest.md"
    json_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    lines = [
        "# ResolveAI 并发性能报告",
        "",
        "| 并发 | 请求 | 成功 | 失败 | 吞吐量 RPS | 客户端 P50 | 客户端 P95 | 服务端 P95 |",
        "| ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    for item in results:
        lines.append(
            f"| {item['concurrency']} | {item['requests']} | {item['successful']} | "
            f"{item['failed']} | {item['throughput_rps']} | {item['client_latency']['p50_ms']} ms | "
            f"{item['client_latency']['p95_ms']} ms | {item['server_latency']['p95_ms']} ms |"
        )
    md_path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    print(json.dumps(report, ensure_ascii=False, indent=2))
    print(f"JSON report: {json_path.resolve()}")
    print(f"Markdown report: {md_path.resolve()}")
    return 1 if any(item["failed"] for item in results) else 0


if __name__ == "__main__":
    raise SystemExit(main())
