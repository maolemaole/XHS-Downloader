from asyncio import TimeoutError as AsyncTimeoutError
from asyncio import create_task, get_running_loop, wait_for
from datetime import datetime
from typing import TYPE_CHECKING
from urllib.parse import urlencode

from httpx import HTTPError
from xhshow import Xhshow

from ..module import WARNING, logging, sleep_time

if TYPE_CHECKING:
    from ..module import Manager


class Comment:
    PAGE_URL = "https://edith.xiaohongshu.com/api/sns/web/v2/comment/page"
    REPLY_URL = "https://edith.xiaohongshu.com/api/sns/web/v2/comment/sub/page"
    encipher = Xhshow()

    def __init__(self, manager: "Manager"):
        self.client = manager.request_client
        self.headers = manager.blank_headers
        self.print = manager.print
        self.root = manager.root
        self.enabled = manager.comments
        self.retry = manager.retry

    async def run(self, note_id: str, xsec_token: str = "") -> list[dict]:
        if not self.enabled:
            return []
        cookies = self._signing_cookies(self.client.cookies.jar)
        if not cookies.get("a1") or not cookies.get("web_session"):
            logging(
                self.print,
                "Downloading comments requires a Cookie containing a1 and web_session",
                WARNING,
            )
            return []

        comments = []
        params = {
            "note_id": note_id,
            "cursor": "",
            "top_comment_id": "",
            "image_formats": "jpg,webp,avif",
        }
        if xsec_token:
            params["xsec_token"] = xsec_token
        data = await self._request(
            self.PAGE_URL,
            params,
            cookies,
            sign_format="xyw",
            log_failure=False,
        )
        browser_fallback = not data
        if not data:
            data = await self._browser_request(note_id, xsec_token)
        for comment in data.get("comments") or []:
            if not browser_fallback:
                comment["sub_comments"] = await self._replies(
                    note_id,
                    comment,
                    cookies,
                    xsec_token,
                )
            comments.append(self._normalize(comment))
        return comments

    async def _browser_request(self, note_id: str, xsec_token: str) -> dict:
        try:
            from playwright.async_api import Error as PlaywrightError
            from playwright.async_api import TimeoutError as PlaywrightTimeoutError
            from playwright.async_api import async_playwright
        except ImportError:
            logging(
                self.print,
                "Browser comment fallback requires Playwright",
                WARNING,
            )
            return {}

        query = {
            "source": "webshare",
            "xhsshare": "pc_web",
            "xsec_source": "pc_share",
        }
        if xsec_token:
            query["xsec_token"] = xsec_token
        url = (
            f"https://www.xiaohongshu.com/discovery/item/{note_id}?"
            f"{urlencode(query)}"
        )
        profile = self.root.joinpath("browser-profile")
        profile.mkdir(exist_ok=True)

        try:
            async with async_playwright() as playwright:
                context = await playwright.chromium.launch_persistent_context(
                    str(profile),
                    channel="chrome",
                    headless=False,
                    no_viewport=True,
                )
                page = context.pages[0] if context.pages else await context.new_page()
                try:
                    result_future = get_running_loop().create_future()

                    async def capture(response) -> None:
                        if self.PAGE_URL not in response.url:
                            return
                        try:
                            candidate = await response.json()
                        except (PlaywrightError, ValueError):
                            return
                        if (
                            candidate.get("success")
                            and (candidate.get("data") or {}).get("comments")
                            and not result_future.done()
                        ):
                            result_future.set_result(candidate)

                    page.on("response", lambda response: create_task(capture(response)))
                    await page.goto(url, wait_until="domcontentloaded")
                    for _ in range(6):
                        if result_future.done():
                            break
                        await page.mouse.wheel(0, 1_000)
                        await page.wait_for_timeout(1_000)
                    result = await wait_for(result_future, timeout=30)
                finally:
                    await context.close()
        except (
            AsyncTimeoutError,
            PlaywrightError,
            PlaywrightTimeoutError,
            ValueError,
        ) as error:
            logging(
                self.print,
                f"Failed to capture comments from browser: {error}",
                WARNING,
            )
            return {}

        if result.get("success"):
            return result.get("data") or {}
        logging(
            self.print,
            f"Failed to capture comments from browser: {result.get('msg', 'unknown error')}",
            WARNING,
        )
        return {}

    @classmethod
    def _signing_cookies(cls, cookie_jar) -> dict[str, str]:
        cookies = {}
        for cookie in sorted(cookie_jar, key=lambda item: len(item.domain or "")):
            cookies[cookie.name] = cookie.value
        return cookies

    async def _replies(
        self,
        note_id: str,
        comment: dict,
        cookies: dict,
        xsec_token: str,
    ) -> list[dict]:
        replies = list(comment.get("sub_comments") or [])
        cursor = comment.get("sub_comment_cursor") or ""
        count = str(comment.get("sub_comment_count") or "0").rstrip("+")
        expected = int(count) if count.isdigit() else len(replies)
        has_more = (
            comment.get("sub_comment_has_more", False) and len(replies) < expected
        )
        while has_more:
            params = {
                "note_id": note_id,
                "root_comment_id": comment.get("id", ""),
                "num": 10,
                "cursor": cursor,
                "image_formats": "jpg,webp,avif",
            }
            if xsec_token:
                params["xsec_token"] = xsec_token
            data = await self._request(
                self.REPLY_URL,
                params,
                cookies,
            )
            if not data:
                break
            replies.extend(data.get("comments") or [])
            has_more = data.get("has_more", False)
            if not (cursor := data.get("cursor")):
                break
        return replies

    async def _request(
        self,
        url: str,
        params: dict,
        cookies: dict,
        sign_format: str = "xys",
        log_failure: bool = True,
    ) -> dict:
        error = None
        for _ in range(self.retry + 1):
            headers = (
                self.encipher.sign_headers_get(
                    uri=url,
                    cookies=cookies,
                    params=params,
                    sign_format=sign_format,
                )
                | self.headers
            )
            try:
                response = await self.client.get(url, params=params, headers=headers)
                await sleep_time()
                result = response.json()
                if response.status_code == 461:
                    error = "status 461 (request rejected by risk control)"
                    break
                response.raise_for_status()
            except (HTTPError, ValueError) as request_error:
                error = repr(request_error)
                response = getattr(request_error, "response", None)
                if response is not None and response.status_code in {406, 461}:
                    break
                continue
            if result.get("success"):
                return result.get("data") or {}
            error = result.get("msg", "unknown error")
        if log_failure:
            logging(self.print, f"Failed to download comments: {error}", WARNING)
        return {}

    @classmethod
    def _normalize(cls, comment: dict) -> dict:
        user = comment.get("user_info") or {}
        timestamp = comment.get("create_time")
        return {
            "id": comment.get("id", ""),
            "author": user.get("nickname", ""),
            "author_id": user.get("user_id", ""),
            "content": comment.get("content", ""),
            "created": cls._format_time(timestamp),
            "likes": comment.get("like_count", 0),
            "location": comment.get("ip_location", ""),
            "pictures": [
                picture.get("url_default") or picture.get("url_pre")
                for picture in comment.get("pictures") or []
                if picture.get("url_default") or picture.get("url_pre")
            ],
            "replies": [
                cls._normalize(reply) for reply in comment.get("sub_comments") or []
            ],
        }

    @staticmethod
    def _format_time(timestamp) -> str:
        if not timestamp:
            return ""
        return datetime.fromtimestamp(int(timestamp) / 1000).strftime(
            "%Y-%m-%d %H:%M:%S"
        )
