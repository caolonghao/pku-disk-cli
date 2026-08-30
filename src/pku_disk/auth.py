from __future__ import annotations

import queue
import tempfile
import time
from pathlib import Path
from urllib.parse import urlparse

from .credentials import save_token

DEFAULT_URL = (
    "https://disk.pku.edu.cn/anyshare/zh-cn/dir/43FC0471AD1C458B91E339426F7909C4"
)


class BrowserLoginError(RuntimeError):
    pass


def _anyshare_token(request: object) -> str | None:
    url = getattr(request, "url", "")
    parsed = urlparse(url)
    if parsed.hostname != "disk.pku.edu.cn" or not parsed.path.startswith("/api/"):
        return None
    headers = getattr(request, "headers", {})
    authorization = headers.get("authorization", "")
    if not authorization.lower().startswith("bearer "):
        return None
    token = authorization.split(None, 1)[1].strip()
    return token or None


def browser_login(url: str = DEFAULT_URL, timeout: int = 300) -> None:
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as exc:
        raise BrowserLoginError(
            "Browser login support is not installed. Run: pipx inject pku-disk-cli playwright"
        ) from exc

    found: queue.Queue[str] = queue.Queue(maxsize=1)

    def inspect_request(request: object) -> None:
        token = _anyshare_token(request)
        if token and found.empty():
            found.put(token)

    with (
        tempfile.TemporaryDirectory(prefix="pku-disk-login-") as profile,
        sync_playwright() as playwright,
    ):
        executable = Path(
            "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
        )
        kwargs = {"headless": False}
        if executable.exists():
            kwargs["executable_path"] = str(executable)
        context = playwright.chromium.launch_persistent_context(profile, **kwargs)
        page = context.pages[0] if context.pages else context.new_page()
        page.on("request", inspect_request)
        page.goto(url, wait_until="domcontentloaded")

        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline and found.empty():
            if not context.pages:
                break
            page.wait_for_timeout(250)

        if found.empty():
            context.close()
            raise BrowserLoginError(
                "Login timed out or the browser was closed before authentication completed"
            )

        save_token(found.get_nowait())
        context.close()
