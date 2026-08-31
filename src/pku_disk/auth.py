from __future__ import annotations

import os
import queue
import shutil
import time
from pathlib import Path
from urllib.parse import urlparse

from .credentials import save_token

DEFAULT_URL = (
    "https://disk.pku.edu.cn/anyshare/zh-cn/dir/43FC0471AD1C458B91E339426F7909C4"
)


class BrowserLoginError(RuntimeError):
    pass


def browser_profile_path() -> Path:
    root = Path(os.environ.get("XDG_STATE_HOME", Path.home() / ".local" / "state"))
    return root / "pku-disk" / "browser-profile"


def _prepare_profile() -> Path:
    profile = browser_profile_path()
    profile.mkdir(parents=True, exist_ok=True, mode=0o700)
    profile.chmod(0o700)
    return profile


def forget_browser_session() -> bool:
    profile = browser_profile_path()
    if not profile.exists():
        return False
    shutil.rmtree(profile)
    return True


def _anyshare_token(request: object) -> str | None:
    url = getattr(request, "url", "")
    parsed = urlparse(url)
    is_folder_listing = parsed.path.startswith(
        "/api/efast/v1/folders/"
    ) and parsed.path.endswith("/sub_objects")
    if parsed.hostname != "disk.pku.edu.cn" or not is_folder_listing:
        return None
    headers = getattr(request, "headers", {})
    authorization = headers.get("authorization", "")
    if not authorization.lower().startswith("bearer "):
        return None
    token = authorization.split(None, 1)[1].strip()
    return token or None


def _successful_anyshare_token(response: object) -> str | None:
    status = getattr(response, "status", 0)
    if not 200 <= status < 300:
        return None
    return _anyshare_token(getattr(response, "request", None))


def browser_login(
    url: str = DEFAULT_URL, timeout: int = 300, interactive: bool = True
) -> str:
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as exc:
        raise BrowserLoginError(
            "Browser login support is not installed. Run: pipx inject pku-disk-cli playwright"
        ) from exc

    found: queue.Queue[str] = queue.Queue(maxsize=1)

    def inspect_response(response: object) -> None:
        token = _successful_anyshare_token(response)
        if token and found.empty():
            found.put(token)

    profile = browser_profile_path()
    if not interactive and not profile.exists():
        raise BrowserLoginError("No saved PKU SSO session. Run: pku-disk auth login")
    profile = _prepare_profile()

    with sync_playwright() as playwright:
        executable = Path(
            "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
        )
        kwargs = {"headless": not interactive}
        if executable.exists():
            kwargs["executable_path"] = str(executable)
        try:
            context = playwright.chromium.launch_persistent_context(profile, **kwargs)
        except Exception as exc:
            raise BrowserLoginError(
                "Could not open the dedicated PKU login browser profile. "
                "Close any other pku-disk login window and try again."
            ) from exc
        try:
            page = context.pages[0] if context.pages else context.new_page()
            context.on("response", inspect_response)
            page.goto(url, wait_until="domcontentloaded")

            deadline = time.monotonic() + timeout
            while time.monotonic() < deadline and found.empty():
                if not context.pages:
                    break
                page.wait_for_timeout(250)

            if found.empty():
                action = (
                    "Complete PKU IAAA login in the browser"
                    if interactive
                    else "Run: pku-disk auth login"
                )
                raise BrowserLoginError(
                    f"The saved PKU SSO session is unavailable or expired. {action}"
                )

            token = found.get_nowait()
            save_token(token)
            page.wait_for_timeout(500)
            return token
        finally:
            context.close()


def refresh_browser_session(timeout: int = 30) -> str:
    # PKU's SSO flow completes reliably with the installed Chrome in visible mode;
    # the same profile can hang during browser shutdown in headless mode.
    return browser_login(timeout=timeout, interactive=True)
