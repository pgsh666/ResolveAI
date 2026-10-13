"""Local acceptance login; never print credentials, cookies or CSRF tokens."""
import json
import os
from pathlib import Path
from http.cookiejar import CookieJar
from urllib.parse import urlsplit
import urllib.request
from dotenv import dotenv_values


def acceptance_client(api_url, role="admin"):
    address = urlsplit(api_url)
    if address.hostname not in {"localhost", "127.0.0.1"} or address.username or address.password:
        raise ValueError("Automatic acceptance login only permits the local project API")
    client = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(CookieJar()))
    with client.open(api_url.rstrip('/') + '/auth/status', timeout=15) as response:
        enabled = json.load(response)["auth_enabled"]
    if not enabled:
        return client, ""
    private = os.environ.get("AUTH_BOOTSTRAP_USERS")
    if not private:
        private = dotenv_values(Path(__file__).parents[2] / ".env.auth", interpolate=False).get("AUTH_BOOTSTRAP_USERS")
    if not private:
        raise RuntimeError("Run scripts/bootstrap-auth.ps1 to initialize private staff credentials")
    account = next(item for item in json.loads(private) if item["role"] == role)
    body = json.dumps({"username": account["username"], "password": account["password"]}).encode()
    request = urllib.request.Request(api_url.rstrip('/') + '/auth/login', data=body, headers={"Content-Type": "application/json"})
    try:
        with client.open(request, timeout=15) as response:
            return client, json.load(response)["csrf_token"]
    except Exception:
        raise RuntimeError("Acceptance login failed; use current credentials if the initial password was changed") from None
