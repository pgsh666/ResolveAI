"""Cross-platform private bootstrap for Linux/macOS clones; Windows uses ACL-aware PowerShell."""
import json
import os
from pathlib import Path
import secrets

root = Path(__file__).resolve().parents[1]
target = root / ".env.auth"


def main():
    if target.exists():
        print("Private credentials already exist; no passwords changed.")
        return 0
    if os.name == "nt":
        raise SystemExit("On Windows run scripts/bootstrap-auth.ps1 to enforce private file ACLs.")
    users = [{"username": name, "role": name, "display_name": label, "password": secrets.token_urlsafe(32)}
             for name, label in [("admin", "管理员"), ("agent", "客服坐席"), ("reviewer", "审批人")]]
    body = "# PRIVATE: do not publish or share this file\nAUTH_BOOTSTRAP_USERS='" + json.dumps(users, ensure_ascii=False) + "'\nTOOL_APPROVAL_SECRET=" + secrets.token_urlsafe(32) + "\n"
    descriptor = os.open(target, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8") as file: file.write(body)
    print("Private random staff credentials created in .env.auth; never share this file.")
    return 0


if __name__ == "__main__": raise SystemExit(main())
