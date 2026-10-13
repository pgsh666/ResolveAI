"""Read-only configuration readiness checks. No keys or passwords are printed."""
import argparse
import json
from pathlib import Path
from urllib.parse import urlsplit
from sqlalchemy.engine import make_url
from app.core.config import get_settings


def checks(settings, public=False):
    result = {"authentication_enabled": settings.auth_enabled,
              "strong_signing_secret": len(settings.tool_approval_secret) >= 40 and "resolveai" not in settings.tool_approval_secret,
              "has_staff_bootstrap": any(item.get("role") == "admin" for item in settings.auth_bootstrap_users)}
    if public:
        result.update(production=settings.app_env == "production", public_guard=settings.public_deployment,
                      secure_cookies=settings.auth_cookie_secure, postgres=settings.is_postgres,
                      strong_db_password=len(make_url(settings.database_url).password or "") >= 20,
                      exact_https_origins=bool(settings.cors_origin_list) and all(
                          urlsplit(value).scheme == "https" and bool(urlsplit(value).hostname)
                          and "*" not in value and not urlsplit(value).username and not urlsplit(value).path
                          and not urlsplit(value).query and not urlsplit(value).fragment
                          for value in settings.cors_origin_list))
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--public", action="store_true")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    try:
        result = checks(get_settings(), args.public)
    except Exception:
        print(json.dumps({"passed": False, "error": "Configuration invalid; check local settings (secret values suppressed)"}))
        return 1
    report = {"profile": "public" if args.public else "local", "passed": all(result.values()), "checks": result,
              "note": "Public checks do not verify actual DNS, HTTPS issuance, infrastructure or human semantic review"}
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True); args.output.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps(report)); return 0 if report["passed"] else 1


if __name__ == "__main__": raise SystemExit(main())
