#!/usr/bin/env python3
"""Download Xiaohongshu links sequentially with a randomized delay."""

import argparse
import random
import re
import subprocess
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
URL_PATTERN = re.compile(
    r"https?://(?:www\.)?(?:xiaohongshu\.com|xhslink\.com)/[^\s<>\"']+",
    re.IGNORECASE,
)
TRAILING_MARKDOWN = ").,，。]}"


def read_urls(path: Path) -> list[str]:
    """Read URLs from plain text or Markdown, preserving their order."""
    urls = []
    for line in path.read_text(encoding="utf-8-sig").splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        urls.extend(
            match.group().rstrip(TRAILING_MARKDOWN)
            for match in URL_PATTERN.finditer(line)
        )
    return urls


def parse_args() -> tuple[argparse.Namespace, list[str]]:
    parser = argparse.ArgumentParser(
        description=(
            "Download links from a text/Markdown file one at a time, with a "
            "random delay between downloads. Unknown options are passed to main.py."
        )
    )
    parser.add_argument("file", type=Path, help="text or Markdown file containing links")
    parser.add_argument(
        "--min-delay", type=float, default=10, help="minimum delay (default: 10)"
    )
    parser.add_argument(
        "--max-delay", type=float, default=20, help="maximum delay (default: 20)"
    )
    parser.add_argument(
        "--delay-unit",
        choices=("minutes", "seconds"),
        default="minutes",
        help="unit used by delay values (default: minutes)",
    )
    parser.add_argument(
        "--start-at",
        type=int,
        default=1,
        metavar="N",
        help="start at link N when resuming (default: 1)",
    )
    parser.add_argument("--markdown", choices=("true", "false"), default="true")
    parser.add_argument("--comments", choices=("true", "false"), default="true")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="show what would run without downloading or waiting",
    )
    return parser.parse_known_args()


def main() -> int:
    args, forwarded = parse_args()
    if not args.file.is_file():
        raise SystemExit(f"Link file not found: {args.file}")
    if args.min_delay < 0 or args.max_delay < args.min_delay:
        raise SystemExit("Delays must satisfy 0 <= --min-delay <= --max-delay")
    if args.start_at < 1:
        raise SystemExit("--start-at must be at least 1")

    urls = read_urls(args.file)
    if not urls:
        raise SystemExit(f"No Xiaohongshu links found in: {args.file}")
    if args.start_at > len(urls):
        raise SystemExit(
            f"--start-at is {args.start_at}, but the file has only {len(urls)} links"
        )

    selected = urls[args.start_at - 1 :]
    failures = 0
    print(
        f"Found {len(urls)} link(s); processing {len(selected)} "
        f"from item {args.start_at}."
    )

    for offset, url in enumerate(selected):
        number = args.start_at + offset
        command = [
            sys.executable,
            str(ROOT / "main.py"),
            "--url",
            url,
            "--markdown",
            args.markdown,
            "--comments",
            args.comments,
            *forwarded,
        ]
        print(f"\n[{number}/{len(urls)}] {url}", flush=True)
        if args.dry_run:
            print("DRY RUN:", subprocess.list2cmdline(command))
        else:
            result = subprocess.run(command, cwd=ROOT, check=False)
            if result.returncode:
                failures += 1
                print(f"Item {number} exited with code {result.returncode}; continuing.")

        if offset == len(selected) - 1:
            continue
        multiplier = 60 if args.delay_unit == "minutes" else 1
        delay = random.uniform(args.min_delay, args.max_delay) * multiplier
        next_time = datetime.now() + timedelta(seconds=delay)
        print(
            f"Waiting {delay / multiplier:.1f} {args.delay_unit}; "
            f"next item at {next_time:%Y-%m-%d %H:%M:%S}.",
            flush=True,
        )
        if not args.dry_run:
            try:
                time.sleep(delay)
            except KeyboardInterrupt:
                print(f"\nStopped. Resume later with --start-at {number + 1}.")
                return 130

    print(f"\nBatch finished: {len(selected) - failures} succeeded, {failures} failed.")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
