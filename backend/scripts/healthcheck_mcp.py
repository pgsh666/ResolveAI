"""Container healthcheck for the stateless MCP Streamable HTTP service."""

import os

import httpx


def main() -> int:
    url = os.getenv("MCP_SERVER_URL", "http://127.0.0.1:8001/mcp")
    response = httpx.post(
        url,
        headers={
            "Accept": "application/json, text/event-stream",
            "Content-Type": "application/json",
            "MCP-Protocol-Version": "2025-06-18",
        },
        json={
            "jsonrpc": "2.0",
            "id": "healthcheck",
            "method": "initialize",
            "params": {
                "protocolVersion": "2025-06-18",
                "capabilities": {},
                "clientInfo": {"name": "resolveai-healthcheck", "version": "1.0"},
            },
        },
        timeout=3,
        trust_env=False,
    )
    response.raise_for_status()
    payload = response.json()
    if payload.get("error"):
        raise RuntimeError(f"MCP initialize failed: {payload['error']}")
    print("MCP healthy")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
