from __future__ import annotations

import json
import os
import platform
import subprocess
from pathlib import Path

SERVICE = "pku-disk-cli"
ACCOUNT = "default"


class CredentialError(RuntimeError):
    pass


def _config_path() -> Path:
    root = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))
    return root / "pku-disk" / "credentials.json"


def save_token(token: str) -> None:
    token = token.strip()
    if not token:
        raise CredentialError("Refusing to store an empty token")
    if platform.system() == "Darwin":
        subprocess.run(
            [
                "security",
                "add-generic-password",
                "-U",
                "-a",
                ACCOUNT,
                "-s",
                SERVICE,
                "-w",
                token,
            ],
            check=True,
            capture_output=True,
            text=True,
        )
        return

    path = _config_path()
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    path.write_text(json.dumps({"token": token}) + "\n", encoding="utf-8")
    path.chmod(0o600)


def load_token() -> str:
    env_token = os.environ.get("PKU_DISK_TOKEN", "").strip()
    if env_token:
        return env_token

    if platform.system() == "Darwin":
        result = subprocess.run(
            ["security", "find-generic-password", "-a", ACCOUNT, "-s", SERVICE, "-w"],
            check=False,
            capture_output=True,
            text=True,
        )
        if result.returncode == 0 and result.stdout.strip():
            return result.stdout.strip()
    else:
        path = _config_path()
        if path.exists():
            return json.loads(path.read_text(encoding="utf-8"))["token"]

    raise CredentialError("Not authenticated. Run: pku-disk auth login")


def delete_token() -> bool:
    if platform.system() == "Darwin":
        result = subprocess.run(
            ["security", "delete-generic-password", "-a", ACCOUNT, "-s", SERVICE],
            check=False,
            capture_output=True,
            text=True,
        )
        return result.returncode == 0
    path = _config_path()
    if path.exists():
        path.unlink()
        return True
    return False
