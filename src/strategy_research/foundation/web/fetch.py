"""URL fetching with HTML → Markdown conversion."""

from __future__ import annotations

import json
import logging

import httpx

logger = logging.getLogger(__name__)

_DEFAULT_TIMEOUT = 10.0
_DEFAULT_MAX_CHARS = 10_000

_USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Safari/605.1.15",
    "Mozilla/5.0 (X11; Linux x86_64; rv:121.0) Gecko/20100101 Firefox/121.0",
]


def read_url(
    url: str,
    max_chars: int = _DEFAULT_MAX_CHARS,
    timeout: float = _DEFAULT_TIMEOUT,
) -> str:
    """Fetch a URL and convert HTML to Markdown.

    Args:
        url: The URL to fetch.
        max_chars: Maximum characters to return (default 10000).
        timeout: Request timeout in seconds (default 10).

    Returns:
        JSON string with fetched content.
    """
    if not url or not url.strip():
        return json.dumps({
            "status": "error",
            "error": "url is required",
        }, ensure_ascii=False)

    url = url.strip()
    if not url.startswith(("http://", "https://")):
        url = "https://" + url

    try:
        import random
        headers = {
            "User-Agent": random.choice(_USER_AGENTS),
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.9,zh-CN;q=0.8,zh;q=0.7",
        }

        response = httpx.get(
            url,
            headers=headers,
            timeout=timeout,
            follow_redirects=True,
            verify=False,
        )
        response.raise_for_status()

        content_type = response.headers.get("content-type", "")
        body = response.text

        # 非 HTML 直接返回
        if "text/html" not in content_type and "application/xhtml" not in content_type:
            truncated = len(body) > max_chars
            if truncated:
                body = body[:max_chars] + "\n... [truncated]"
            return json.dumps({
                "status": "ok",
                "url": url,
                "content_type": content_type,
                "markdown": body,
                "char_count": len(body),
                "truncated": truncated,
            }, ensure_ascii=False)

        # HTML → Markdown
        try:
            from markdownify import markdownify
            markdown = markdownify(body, strip=["script", "style", "nav", "footer"])
            # 清理多余空行
            lines = markdown.splitlines()
            cleaned = []
            prev_blank = False
            for line in lines:
                is_blank = not line.strip()
                if is_blank and prev_blank:
                    continue
                cleaned.append(line)
                prev_blank = is_blank
            markdown = "\n".join(cleaned)
        except ImportError:
            # markdownify 不可用，返回原始 HTML
            markdown = body

        truncated = len(markdown) > max_chars
        if truncated:
            markdown = markdown[:max_chars] + "\n... [truncated]"

        return json.dumps({
            "status": "ok",
            "url": url,
            "content_type": content_type,
            "markdown": markdown,
            "char_count": len(markdown),
            "truncated": truncated,
        }, ensure_ascii=False)

    except httpx.TimeoutException:
        return json.dumps({
            "status": "error",
            "error": f"request timed out after {timeout}s",
            "url": url,
        }, ensure_ascii=False)
    except httpx.HTTPStatusError as exc:
        return json.dumps({
            "status": "error",
            "error": f"HTTP {exc.response.status_code}: {exc.response.reason_phrase}",
            "url": url,
        }, ensure_ascii=False)
    except Exception as exc:
        logger.warning("read_url failed for %r: %s", url, exc)
        return json.dumps({
            "status": "error",
            "error": f"fetch failed: {exc}",
            "url": url,
        }, ensure_ascii=False)
