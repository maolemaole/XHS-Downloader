#!/usr/bin/env python3
import argparse
from pathlib import Path
from time import monotonic, sleep

try:
    from playwright.sync_api import Error as PlaywrightError
    from playwright.sync_api import sync_playwright
except ImportError as error:
    raise SystemExit(
        "Playwright is not installed. Run this script with:\n"
        "uv run --with playwright python scripts/update_cookie_playwright.py"
    ) from error

from set_cookie import SETTINGS, save_cookie


PROFILE = Path(__file__).resolve().parent.parent / "Volume" / "browser-profile"
REQUIRED = {"a1", "web_session"}


def cookie_header(cookies: list[dict]) -> str:
    values = {
        cookie["name"]: cookie["value"]
        for cookie in cookies
        if "xiaohongshu.com" in cookie.get("domain", "xiaohongshu.com")
    }
    if not REQUIRED <= values.keys():
        return ""
    names = [
        *sorted(REQUIRED),
        *sorted(values.keys() - REQUIRED),
    ]
    return "; ".join(f"{name}={values[name]}" for name in names)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Log in with a dedicated Chrome profile and update settings.json."
    )
    parser.add_argument(
        "--timeout",
        type=int,
        default=300,
        help="seconds to wait for login (default: 300)",
    )
    args = parser.parse_args()

    PROFILE.mkdir(parents=True, exist_ok=True)
    print("Opening Chrome with the XHS-Downloader browser profile...")
    print("Log in to Xiaohongshu if requested. Cookie values will not be displayed.")

    try:
        with sync_playwright() as playwright:
            context = playwright.chromium.launch_persistent_context(
                str(PROFILE),
                channel="chrome",
                headless=False,
                no_viewport=True,
            )
            page = context.pages[0] if context.pages else context.new_page()
            page.goto("https://www.xiaohongshu.com/", wait_until="domcontentloaded")
            deadline = monotonic() + args.timeout
            while monotonic() < deadline:
                header = cookie_header(context.cookies())
                if header:
                    save_cookie(header)
                    print(f"Cookie updated in {SETTINGS}")
                    context.close()
                    return
                sleep(1)
            context.close()
    except PlaywrightError as error:
        raise SystemExit(f"Browser automation failed: {error}") from error

    raise SystemExit(
        "Timed out waiting for a1 and web_session; settings were not changed."
    )


if __name__ == "__main__":
    main()
