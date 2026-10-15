"""
mcp_client.py — клиент MCP VK Рекламы по HTTP (JSON-RPC tools/call) только на чтение.

Белый список ALLOWED_TOOLS — гарантия «аудит ничего не меняет в кабинете»:
любой другой инструмент отклоняется до сети. Адрес подключения несёт креды,
поэтому в сообщения об ошибках он не попадает.
"""
from __future__ import annotations

import http.client
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
    def __init__(self, message: str, status: Optional[int] = None, *, retryable: bool = False):
        super().__init__(message)
        self.status = status
        self.retryable = retryable


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
        # Тело не печатаем: в нём могут быть данные кабинета или адрес подключения.
        raise McpError(f"сервер ответил не JSON ({len(text.encode('utf-8'))} байт)") from None


def http_transport(url: str, payload: dict, timeout: float = 60.0) -> dict:
    req = urllib.request.Request(
        url, data=json.dumps(payload).encode("utf-8"), method="POST",
        headers={"Content-Type": "application/json", "Accept": "application/json, text/event-stream"},
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read()
            content_type = resp.headers.get("Content-Type", "")
    except urllib.error.HTTPError as e:
        raise McpError(f"HTTP {e.code}", status=e.code) from None
    except http.client.HTTPException as e:  # IncompleteRead, BadStatusLine и т. п. — не OSError
        raise McpError(f"сервер MCP оборвал ответ: {type(e).__name__}", retryable=True) from None
    except OSError as e:  # URLError, TimeoutError, ConnectionError и прочие сетевые сбои
        # Причина может содержать адрес подключения — в сообщение идёт только имя типа.
        raise McpError(f"сервер MCP недоступен: {type(e).__name__}", retryable=True) from None
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        raise McpError("сервер ответил не в UTF-8") from None
    return parse_body(content_type, text)


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
            err = detail.get("error")
            if isinstance(err, dict):
                detail = err.get("message") or json.dumps(detail, ensure_ascii=False)
            elif isinstance(err, str):
                detail = err
            else:
                detail = json.dumps(detail, ensure_ascii=False)
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
                if (e.status not in RETRY_STATUSES and not e.retryable) or attempt > self._retries:
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
        text = "".join(c.get("text", "") for c in (result.get("content") or []) if c.get("type") == "text")
        if result.get("isError"):
            raise _tool_error(tool, text)
        try:
            return json.loads(text)
        except ValueError:
            raise McpError(f"{tool}: ответ не JSON") from None

    def paginate(self, tool: str, arguments: Optional[dict] = None, *,
                 page_size: int = 250, max_pages: int = 200) -> list:
        items: list = []
        offset = 0
        for _ in range(max_pages):
            args = dict(arguments or {})
            args.update(limit=page_size, offset=offset)
            page = self.call(tool, args)
            if not isinstance(page, dict) or not isinstance(page.get("items"), list):
                raise McpError(f"{tool}: неожиданный формат страницы")
            batch = page["items"]
            items.extend(batch)
            offset += len(batch)
            count = page.get("count")
            if isinstance(count, int):
                # Сервер может обрезать limit: короткая страница ещё не конец выборки.
                if offset >= count:
                    return items
                if not batch:
                    raise McpError(f"{tool}: сервер вернул пустую страницу при offset={offset} < count={count}")
            elif len(batch) < page_size:
                return items
        raise McpError(f"{tool}: больше {max_pages} страниц — сбор остановлен")
