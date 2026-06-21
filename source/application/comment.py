from datetime import datetime
from typing import TYPE_CHECKING

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
        )
        for comment in data.get("comments") or []:
            comment["sub_comments"] = await self._replies(
                note_id,
                comment,
                cookies,
                xsec_token,
            )
            comments.append(self._normalize(comment))
        return comments

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
                sign_format="xyw",
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
                    continue
                response.raise_for_status()
            except (HTTPError, ValueError) as request_error:
                error = repr(request_error)
                continue
            if result.get("success"):
                return result.get("data") or {}
            error = result.get("msg", "unknown error")
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
