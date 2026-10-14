"""
mcp_client.py — клиент MCP VK Рекламы по HTTP (JSON-RPC tools/call) только на чтение.

Белый список ALLOWED_TOOLS — гарантия «аудит ничего не меняет в кабинете»:
любой другой инструмент отклоняется до сети. Адрес подключения несёт креды,
поэтому в сообщения об ошибках он не попадает.
"""
from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from typing import Callable, Optional

ALLOWED_TOOLS = frozenset({
    "vk_ads_auth_check", "vk_ads_account_info", "vk_ads_accounts_list", "vk_ads_agency_clients_list",
    "vk_ads_ad_plans_list", "vk_ads_ad_plans_get",
    "vk_ads_campaigns_list", "vk_ads_campaigns_get",
    "vk_ads_ad_groups_list", "vk_ads_ad_groups_get",
    "vk_ads_banners_list", "vk_ads_banners_get",
    "vk_ads_statistics_summary", "vk_ads_statistics_day",
    "vk_ads_remarketing_segments_list", "vk_ads_remarketing_pixels_list", "vk_ads_users_lists_list",
    "vk_ads_packages_list", "vk_ads_regions_search", "vk_ads_dictionary_get",
})
NO_ACCOUNT_ARG = frozenset({
    "vk_ads_accounts_list", "vk_ads_packages_list", "vk_ads_regions_search", "vk_ads_dictionary_get",
})
RETRY_STATUSES = frozenset({429, 500, 502, 503, 504})

Transport = Callable[[str, dict], dict]


class McpError(Exception):
    def __init__(self, message: str, status: Optional[int] = None):
        super().__init__(message)
        self.status = status


class ForbiddenToolError(McpError):
    pass


def parse_body(content_type: str, text: str) -> dict:
    if "text/event-stream" in (content_type or ""):
        data = [line[5:].strip() for line in text.splitlines() if line.startswith("data:")]
        if not data:
            raise McpError("пустой поток событий от сервера")
        text = data[-1]
    try:
        return json.loads(text)
    except ValueError:
        raise McpError(f"сервер ответил не JSON: {text[:200]}") from None


def http_transport(url: str, payload: dict, timeout: float = 60.0) -> dict:
    req = urllib.request.Request(
        url, data=json.dumps(payload).encode("utf-8"), method="POST",
        headers={"Content-Type": "application/json", "Accept": "application/json, text/event-stream"},
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return parse_body(resp.headers.get("Content-Type", ""), resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        raise McpError(f"HTTP {e.code}", status=e.code) from None
    except urllib.error.URLError as e:
        raise McpError(f"сервер MCP недоступен: {e.reason}") from None


def _tool_error(tool: str, text: str) -> McpError:
    status, message = None, text
    try:
        obj = json.loads(text)
    except ValueError:
        obj = None
    if isinstance(obj, dict):
        status = obj.get("status") if isinstance(obj.get("status"), int) else None
        detail = obj.get("detail")
        if isinstance(detail, dict):
            detail = (detail.get("error") or {}).get("message") or json.dumps(detail, ensure_ascii=False)
        message = detail or text
    if status is None and "rate_limit" in text:
        status = 429
    return McpError(f"{tool}: {message}"[:500], status=status)


class McpClient:
    def __init__(self, url: str, *, account_id=None, transport: Optional[Transport] = None,
                 sleep=time.sleep, retries: int = 3, backoff: float = 2.0):
        self._url = url
        self.account_id = account_id
        self._transport = transport or http_transport
        self._sleep = sleep
        self._retries = retries
        self._backoff = backoff
        self._next_id = 0

    def call(self, tool: str, arguments: Optional[dict] = None):
        if tool not in ALLOWED_TOOLS:
            raise ForbiddenToolError(f"{tool}: вне белого списка чтения — аудит ничего не меняет в кабинете")
        args = dict(arguments or {})
        if self.account_id and tool not in NO_ACCOUNT_ARG:
            args.setdefault("account_id", str(self.account_id))
        attempt = 0
        while True:
            try:
                return self._call_once(tool, args)
            except ForbiddenToolError:
                raise
            except McpError as e:
                attempt += 1
                if e.status not in RETRY_STATUSES or attempt > self._retries:
                    raise
                self._sleep(self._backoff * attempt)

    def _call_once(self, tool: str, args: dict):
        self._next_id += 1
        payload = {"jsonrpc": "2.0", "id": self._next_id, "method": "tools/call",
                   "params": {"name": tool, "arguments": args}}
        resp = self._transport(self._url, payload)
        if "error" in resp:
            err = resp["error"] or {}
            raise McpError(f"{tool}: JSON-RPC {err.get('code')}: {err.get('message')}")
        result = resp.get("result") or {}
        text = "".join(c.get("text", "") for c in result.get("content", []) if c.get("type") == "text")
        if result.get("isError"):
            raise _tool_error(tool, text)
        try:
            return json.loads(text)
        except ValueError:
            raise McpError(f"{tool}: ответ не JSON: {text[:200]}") from None

    def paginate(self, tool: str, arguments: Optional[dict] = None, *,
                 page_size: int = 250, max_pages: int = 200) -> list:
        items: list = []
        offset = 0
        for _ in range(max_pages):
            args = dict(arguments or {})
            args.update(limit=page_size, offset=offset)
            page = self.call(tool, args)
            batch = page.get("items", []) if isinstance(page, dict) else []
            items.extend(batch)
            offset += len(batch)
            count = page.get("count") if isinstance(page, dict) else None
            if len(batch) < page_size or (isinstance(count, int) and offset >= count):
                return items
        raise McpError(f"{tool}: больше {max_pages} страниц — сбор остановлен")
