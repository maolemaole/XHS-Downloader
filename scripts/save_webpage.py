#!/usr/bin/env python3
"""Save ordinary web pages as bilingual Markdown with local images."""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import mimetypes
import re
import sys
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from urllib.parse import unquote, urljoin, urlparse

import httpx
from lxml import html
from lxml.html import HtmlElement
from playwright.async_api import BrowserContext, TimeoutError as PlaywrightTimeout
from playwright.async_api import async_playwright


ROOT = Path(__file__).resolve().parent.parent
DEFAULT_OUTPUT = ROOT / "Volume" / "Webpages"
URL_RE = re.compile(r"https?://[^\s<>\"']+", re.IGNORECASE)
TRAILING_URL_PUNCTUATION = ").,，。]}"
SPACE_RE = re.compile(r"[ \t]+")
BLANK_LINES_RE = re.compile(r"\n{3,}")
INVALID_FILENAME_RE = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
TRANSLATABLE_RE = re.compile(r"[A-Za-z\u00c0-\u024f]")
MARKDOWN_PROTECTED_RE = re.compile(
    r"`[^`]*`|\((?:https?://)[^)]+\)|https?://[^\s)>]+", re.IGNORECASE
)
IMAGE_ATTRIBUTES = ("src", "data-src", "data-original", "data-lazy-src")
REMOVE_XPATH = (
    ".//script | .//style | .//noscript | .//template | .//svg | "
    ".//canvas | .//iframe | .//form | .//button | .//input | "
    ".//select | .//textarea | .//dialog"
)


@dataclass
class SavedImage:
    source_url: str
    relative_path: str
    alt: str


@dataclass
class PageResult:
    source_url: str
    final_url: str
    title: str
    root: HtmlElement
    page_dir: Path
    images: dict[str, SavedImage] = field(default_factory=dict)


def safe_filename(value: str, fallback: str = "webpage", limit: int = 90) -> str:
    value = html.fromstring(f"<span>{value}</span>").text_content()
    value = SPACE_RE.sub(" ", INVALID_FILENAME_RE.sub("_", value)).strip(" ._")
    return (value[:limit].rstrip(" ._") or fallback)


def unique_directory(parent: Path, name: str) -> Path:
    candidate = parent / name
    number = 2
    while candidate.exists():
        candidate = parent / f"{name}_{number}"
        number += 1
    candidate.mkdir(parents=True)
    return candidate


def read_urls(value: str) -> list[str]:
    path = Path(value)
    if path.is_file():
        text = path.read_text(encoding="utf-8-sig")
        urls = [
            match.group().rstrip(TRAILING_URL_PUNCTUATION)
            for match in URL_RE.finditer(text)
        ]
    elif URL_RE.fullmatch(value.strip()):
        urls = [value.strip()]
    else:
        raise SystemExit(f"Not a URL or a readable URL-list file: {value}")
    return list(dict.fromkeys(urls))


def parse_srcset(value: str) -> str:
    candidates = []
    for item in value.split(","):
        parts = item.strip().split()
        if not parts:
            continue
        score = 1.0
        if len(parts) > 1:
            descriptor = parts[-1].lower()
            try:
                score = float(descriptor[:-1])
            except (ValueError, IndexError):
                score = 1.0
        candidates.append((score, parts[0]))
    return max(candidates, default=(0.0, ""))[1]


def image_url(node: HtmlElement, base_url: str) -> str:
    for attribute in IMAGE_ATTRIBUTES:
        value = node.get(attribute)
        if value and not value.startswith(("data:", "blob:", "javascript:")):
            return urljoin(base_url, value)
    srcset = node.get("srcset") or node.get("data-srcset") or ""
    value = parse_srcset(srcset)
    return urljoin(base_url, value) if value else ""


def choose_content(document: HtmlElement) -> HtmlElement:
    selectors = (
        "//article",
        "//main",
        "//*[@role='main']",
        "//*[contains(concat(' ', normalize-space(@class), ' '), ' article-body ')]",
        "//*[contains(concat(' ', normalize-space(@class), ' '), ' post-content ')]",
        "//*[contains(concat(' ', normalize-space(@class), ' '), ' entry-content ')]",
    )
    candidates: list[HtmlElement] = []
    for xpath in selectors:
        candidates.extend(document.xpath(xpath))
    if candidates:
        return max(candidates, key=lambda node: len(node.text_content().strip()))
    bodies = document.xpath("//body")
    return bodies[0] if bodies else document


def clean_content(root: HtmlElement) -> None:
    for node in list(root.xpath(REMOVE_XPATH)):
        node.drop_tree()
    for node in list(root.xpath(".//*")):
        marker = " ".join(
            filter(
                None,
                (
                    node.get("id", ""),
                    node.get("class", ""),
                    node.get("role", ""),
                    node.get("aria-label", ""),
                ),
            )
        ).lower()
        if any(
            word in marker
            for word in (
                "cookie",
                "consent",
                "newsletter",
                "subscribe",
                "advertisement",
                "social-share",
                "share-button",
            )
        ):
            node.drop_tree()


async def scroll_page(page) -> None:
    previous = 0
    for _ in range(12):
        height = await page.evaluate("document.body.scrollHeight")
        await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
        await page.wait_for_timeout(450)
        if height == previous:
            break
        previous = height
    await page.evaluate("window.scrollTo(0, 0)")


async def fetch_page(
    context: BrowserContext, url: str, output_root: Path, timeout_ms: int
) -> PageResult:
    page = await context.new_page()
    try:
        await page.goto(url, wait_until="domcontentloaded", timeout=timeout_ms)
        try:
            await page.wait_for_load_state("networkidle", timeout=min(timeout_ms, 8000))
        except PlaywrightTimeout:
            pass
        await scroll_page(page)
        markup = await page.content()
        final_url = page.url
        browser_title = (await page.title()).strip()
    finally:
        await page.close()

    document = html.fromstring(markup, base_url=final_url)
    titles = document.xpath(
        "//meta[@property='og:title']/@content | //h1[1]//text() | //title/text()"
    )
    title = browser_title or " ".join(part.strip() for part in titles if part.strip())
    title = SPACE_RE.sub(" ", title).strip() or urlparse(final_url).netloc
    page_dir = unique_directory(output_root, safe_filename(title))
    root = choose_content(document)
    clean_content(root)
    return PageResult(url, final_url, title, root, page_dir)


def extension_for(url: str, content_type: str) -> str:
    suffix = Path(unquote(urlparse(url).path)).suffix.lower()
    if re.fullmatch(r"\.[a-z0-9]{2,5}", suffix):
        return ".jpg" if suffix == ".jpeg" else suffix
    content_type = content_type.split(";", 1)[0].strip().lower()
    guessed = mimetypes.guess_extension(content_type) or ".img"
    return ".jpg" if guessed in (".jpe", ".jpeg") else guessed


async def save_images(result: PageResult, context: BrowserContext) -> None:
    assets = result.page_dir / "images"
    nodes_by_url: dict[str, list[HtmlElement]] = {}
    for node in result.root.xpath(".//img"):
        url = image_url(node, result.final_url)
        if url:
            nodes_by_url.setdefault(url, []).append(node)

    total = len(nodes_by_url)
    if not total:
        print("  Images: none found", flush=True)
        return

    print(f"  Images: downloading {total} file(s)...", flush=True)
    assets.mkdir(exist_ok=True)
    semaphore = asyncio.Semaphore(6)
    completed = 0

    async def download(
        index: int, url: str, related_nodes: list[HtmlElement]
    ) -> None:
        nonlocal completed
        try:
            async with semaphore:
                response = await context.request.get(url, timeout=15_000)
                if not response.ok:
                    print(f"    Skipped HTTP {response.status}: {url[:100]}")
                    return
                body = await response.body()
                content_type = response.headers.get("content-type", "")
                if not body or (
                    content_type
                    and not content_type.lower().startswith(
                        ("image/", "application/octet")
                    )
                ):
                    return

            stem = safe_filename(
                Path(unquote(urlparse(url).path)).stem,
                fallback=f"image_{index:03d}",
                limit=55,
            )
            digest = hashlib.sha1(url.encode()).hexdigest()[:8]
            filename = f"{stem}_{digest}{extension_for(url, content_type)}"
            path = assets / filename
            path.write_bytes(body)
            alt = (
                SPACE_RE.sub(" ", related_nodes[0].get("alt", "")).strip() or stem
            )
            saved = SavedImage(url, f"images/{filename}", alt)
            result.images[url] = saved
            for node in related_nodes:
                node.set("data-local-image", saved.relative_path)
        except Exception as error:
            print(f"    Skipped ({error}): {url[:100]}")
        finally:
            completed += 1
            if completed == total or completed % 5 == 0:
                print(f"    Image progress: {completed}/{total}", flush=True)

    await asyncio.gather(
        *(
            download(index, url, related_nodes)
            for index, (url, related_nodes) in enumerate(nodes_by_url.items(), 1)
        )
    )
    print(f"  Images: saved {len(result.images)}/{total}", flush=True)


def text_value(value: str | None) -> str:
    return SPACE_RE.sub(" ", value or "")


def render_inline(node: HtmlElement) -> str:
    tag = (node.tag or "").lower() if isinstance(node.tag, str) else ""
    before = text_value(node.text)
    children = "".join(render_inline(child) for child in node)
    content = before + children

    if tag == "br":
        rendered = "  \n"
    elif tag in ("strong", "b") and content.strip():
        rendered = f"**{content.strip()}**"
    elif tag in ("em", "i") and content.strip():
        rendered = f"*{content.strip()}*"
    elif tag == "code" and node.getparent() is not None and node.getparent().tag != "pre":
        rendered = f"`{content.strip().replace('`', '\\`')}`"
    elif tag == "a":
        href = urljoin(node.base_url or "", node.get("href", ""))
        label = content.strip() or href
        rendered = f"[{label}]({href})" if href.startswith(("http://", "https://")) else label
    elif tag == "img":
        local = node.get("data-local-image")
        alt = text_value(node.get("alt")).strip() or "image"
        rendered = f"![{alt}]({local})" if local else ""
    else:
        rendered = content
    return rendered + text_value(node.tail)


def render_table(node: HtmlElement) -> str:
    rows = []
    for row in node.xpath(".//tr"):
        cells = [
            " ".join(cell.text_content().split()).replace("|", r"\|")
            for cell in row.xpath("./th | ./td")
        ]
        if cells:
            rows.append(cells)
    if not rows:
        return ""
    width = max(map(len, rows))
    rows = [row + [""] * (width - len(row)) for row in rows]
    lines = ["| " + " | ".join(row) + " |" for row in rows]
    lines.insert(1, "| " + " | ".join("---" for _ in range(width)) + " |")
    return "\n".join(lines)


def render_block(node: HtmlElement, list_depth: int = 0) -> str:
    tag = (node.tag or "").lower() if isinstance(node.tag, str) else ""
    if tag in ("h1", "h2", "h3", "h4", "h5", "h6"):
        return f"{'#' * int(tag[1])} {render_inline(node).strip()}\n\n"
    if tag == "p":
        content = render_inline(node).strip()
        return f"{content}\n\n" if content else ""
    if tag == "pre":
        content = node.text_content().strip("\n")
        return f"```\n{content}\n```\n\n" if content else ""
    if tag == "blockquote":
        content = render_children(node, list_depth).strip()
        return "\n".join(f"> {line}" for line in content.splitlines()) + "\n\n"
    if tag == "table":
        content = render_table(node)
        return f"{content}\n\n" if content else ""
    if tag in ("ul", "ol"):
        lines = []
        ordered = tag == "ol"
        for index, item in enumerate(node.xpath("./li"), 1):
            content = render_children(item, list_depth + 1).strip()
            prefix = f"{index}. " if ordered else "- "
            indent = "  " * list_depth
            lines.append(indent + prefix + content.replace("\n", "\n" + indent + "  "))
        return "\n".join(lines) + "\n\n" if lines else ""
    if tag == "img":
        content = render_inline(node).strip()
        return f"{content}\n\n" if content else ""
    if tag in ("a", "strong", "b", "em", "i", "code", "span", "small", "mark"):
        return render_inline(node)
    if tag in ("figure", "picture"):
        content = render_children(node, list_depth).strip()
        return f"{content}\n\n" if content else ""
    return render_children(node, list_depth)


def render_children(node: HtmlElement, list_depth: int = 0) -> str:
    pieces = []
    if node.text and node.text.strip():
        pieces.append(text_value(node.text))
    for child in node:
        pieces.append(render_block(child, list_depth))
        if child.tail and child.tail.strip():
            pieces.append(text_value(child.tail))
    return "".join(pieces)


def to_markdown(root: HtmlElement) -> str:
    markdown = render_block(root).strip()
    markdown = re.sub(r" *\n", "\n", markdown)
    return BLANK_LINES_RE.sub("\n\n", markdown)


def split_for_translation(text: str, limit: int = 3800) -> list[str]:
    if len(text) <= limit:
        return [text]
    pieces = re.split(r"(?<=[.!?。！？])\s+", text)
    chunks: list[str] = []
    current = ""
    for piece in pieces:
        if len(piece) > limit:
            if current:
                chunks.append(current)
                current = ""
            chunks.extend(piece[i : i + limit] for i in range(0, len(piece), limit))
        elif len(current) + len(piece) + 1 > limit:
            chunks.append(current)
            current = piece
        else:
            current = f"{current} {piece}".strip()
    if current:
        chunks.append(current)
    return chunks


async def translate_text(client: httpx.AsyncClient, text: str) -> str:
    if not text.strip() or not TRANSLATABLE_RE.search(text):
        return text
    translated = []
    for chunk in split_for_translation(text):
        response = await client.get(
            "https://translate.googleapis.com/translate_a/single",
            params={
                "client": "gtx",
                "sl": "auto",
                "tl": "zh-CN",
                "dt": "t",
                "q": chunk,
            },
            timeout=20,
        )
        response.raise_for_status()
        payload = response.json()
        translated.append("".join(item[0] for item in payload[0] if item[0]))
    return "".join(translated)


async def translate_line(client: httpx.AsyncClient, content: str) -> str:
    translated_parts = []
    position = 0
    for match in MARKDOWN_PROTECTED_RE.finditer(content):
        translated_parts.append(
            await translate_text(client, content[position : match.start()])
        )
        translated_parts.append(match.group())
        position = match.end()
    translated_parts.append(await translate_text(client, content[position:]))
    return "".join(translated_parts)


async def translate_markdown(markdown: str) -> str | None:
    lines = markdown.splitlines()
    output = list(lines)
    work: list[tuple[int, str, str]] = []
    in_code_block = False
    for index, line in enumerate(lines):
        if line.lstrip().startswith("```"):
            in_code_block = not in_code_block
            continue
        if in_code_block or not line.strip() or line.lstrip().startswith("!["):
            continue
        prefix_match = re.match(
            r"^(\s*(?:#{1,6}\s+|>\s+|[-*+]\s+|\d+\.\s+))?(.*)$", line
        )
        prefix, content = prefix_match.groups()
        if TRANSLATABLE_RE.search(content):
            work.append((index, prefix or "", content))

    total = len(work)
    if not total:
        print("  Translation: no English text found", flush=True)
        return "\n".join(output)

    print(
        f"  Translation: checking service, then translating {total} text block(s)...",
        flush=True,
    )
    limits = httpx.Limits(max_connections=6, max_keepalive_connections=6)
    async with httpx.AsyncClient(follow_redirects=True, limits=limits) as client:
        first_index, first_prefix, first_content = work[0]
        try:
            first_translation = await translate_line(client, first_content)
        except Exception as error:
            print(
                "  Translation service is unavailable; saving the original text "
                f"without a Chinese section. ({error})",
                flush=True,
            )
            return None
        output[first_index] = first_prefix + first_translation
        completed = 1
        print(f"    Translation progress: {completed}/{total}", flush=True)
        semaphore = asyncio.Semaphore(5)

        async def translate_one(index: int, prefix: str, content: str) -> None:
            nonlocal completed
            try:
                async with semaphore:
                    translated = await translate_line(client, content)
            except Exception as error:
                print(f"    Translation warning ({error}); kept one original block.")
                translated = content
            output[index] = prefix + translated
            completed += 1
            if completed == total or completed % 5 == 0:
                print(
                    f"    Translation progress: {completed}/{total}",
                    flush=True,
                )

        await asyncio.gather(
            *(
                translate_one(index, prefix, content)
                for index, prefix, content in work[1:]
            )
        )
    print("  Translation: complete", flush=True)
    return "\n".join(output)


def write_markdown(
    result: PageResult, original: str, chinese: str | None
) -> Path:
    path = result.page_dir / f"{safe_filename(result.title)}.md"
    metadata = (
        f"# {result.title}\n\n"
        f"- Source: {result.final_url}\n"
        f"- Saved: {datetime.now().astimezone().isoformat(timespec='seconds')}\n"
        f"- Images: {len(result.images)}\n\n"
    )
    sections = f"## Original text\n\n{original}\n"
    if chinese is not None:
        sections += f"\n---\n\n## 中文翻译\n\n{chinese}\n"
    path.write_text(metadata + sections, encoding="utf-8")
    return path


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Save ordinary web pages as Markdown with original text, Chinese "
            "translation, and local images."
        )
    )
    parser.add_argument("input", help="one URL, or a .txt/.md file containing URLs")
    parser.add_argument(
        "-o",
        "--output",
        type=Path,
        default=DEFAULT_OUTPUT,
        help=f"output folder (default: {DEFAULT_OUTPUT})",
    )
    parser.add_argument(
        "--no-translate",
        action="store_true",
        help="save original text only; do not contact Google Translate",
    )
    parser.add_argument(
        "--headed",
        action="store_true",
        help="show the browser window (useful for pages needing manual interaction)",
    )
    parser.add_argument(
        "--timeout",
        type=int,
        default=45,
        metavar="SECONDS",
        help="page-load timeout (default: 45)",
    )
    return parser.parse_args()


async def async_main(args: argparse.Namespace) -> int:
    urls = read_urls(args.input)
    if not urls:
        raise SystemExit("No http:// or https:// URLs were found.")
    args.output.mkdir(parents=True, exist_ok=True)
    failures = 0
    async with async_playwright() as playwright:
        try:
            browser = await playwright.chromium.launch(headless=not args.headed)
        except Exception as error:
            raise SystemExit(
                "Chromium is not installed. Run:\n"
                "  uv run playwright install chromium\n\n"
                f"Original error: {error}"
            ) from error
        context = await browser.new_context(
            viewport={"width": 1440, "height": 1000},
            locale="en-US",
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 Chrome/131 Safari/537.36"
            ),
        )
        for number, url in enumerate(urls, 1):
            print(
                f"[{number}/{len(urls)}] Opening {url}\n"
                f"  Loading page (timeout: {args.timeout} seconds)...",
                flush=True,
            )
            try:
                result = await fetch_page(
                    context, url, args.output.resolve(), args.timeout * 1000
                )
                print(f"  Loaded: {result.title}", flush=True)
                await save_images(result, context)
                print("  Markdown: extracting readable page text...", flush=True)
                original = to_markdown(result.root)
                if not original.strip():
                    raise RuntimeError("No readable page content was found")
                chinese = (
                    None if args.no_translate else await translate_markdown(original)
                )
                print("  Markdown: writing file...", flush=True)
                path = write_markdown(result, original, chinese)
                print(f"  Saved: {path}", flush=True)
            except Exception as error:
                failures += 1
                print(f"  Failed: {error}", file=sys.stderr)
        await context.close()
        await browser.close()
    print(f"Finished: {len(urls) - failures} saved, {failures} failed.")
    return 1 if failures else 0


def main() -> int:
    args = parse_args()
    if args.timeout <= 0:
        raise SystemExit("--timeout must be greater than zero")
    return asyncio.run(async_main(args))


if __name__ == "__main__":
    raise SystemExit(main())
