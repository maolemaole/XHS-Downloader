#!/usr/bin/env python3
import json
from getpass import getpass
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
SETTINGS = ROOT / "Volume" / "settings.json"
REQUIRED_COOKIES = {"a1", "web_session"}


def parse_cookie(cookie: str) -> dict[str, str]:
    values = {}
    for item in cookie.split(";"):
        name, separator, value = item.strip().partition("=")
        if separator and name:
            values[name] = value
    return values


def save_cookie(cookie: str, user_agent: str | None = None) -> None:
    missing = REQUIRED_COOKIES - parse_cookie(cookie).keys()
    if missing:
        names = ", ".join(sorted(missing))
        raise ValueError(f"Missing required cookies: {names}")

    # ``utf-8-sig`` accepts regular UTF-8 JSON as well as files written with
    # a BOM (for example by Windows PowerShell or some text editors).
    with SETTINGS.open("r", encoding="utf-8-sig") as file:
        settings = json.load(file)
    settings["cookie"] = cookie
    if user_agent:
        settings["user_agent"] = user_agent
    with SETTINGS.open("w", encoding="utf-8") as file:
        json.dump(settings, file, ensure_ascii=False, indent=4)
        file.write("\n")


def main() -> None:
    print("Paste a full Cookie header, or press Enter to provide individual values.")
    cookie = getpass("Cookie (input hidden): ").strip()
    if not cookie:
        a1 = getpass("a1 (input hidden): ").strip()
        web_session = getpass("web_session (input hidden): ").strip()
        web_id = getpass("webId (optional, input hidden): ").strip()
        values = {
            "a1": a1,
            "web_session": web_session,
            "webId": web_id,
        }
        cookie = "; ".join(f"{name}={value}" for name, value in values.items() if value)

    try:
        save_cookie(cookie)
    except ValueError as error:
        raise SystemExit(str(error)) from error
    print(f"Cookie saved to {SETTINGS}")


if __name__ == "__main__":
    main()
