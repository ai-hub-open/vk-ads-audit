from __future__ import annotations

import json
import urllib.error

import pytest

from scripts.mcp_client import (ALLOWED_TOOLS, ForbiddenToolError, McpClient, McpError,
                                http_transport, parse_body)

SECRET_URL = "https://vkads-mcp.aihub.click.ru/c/SECRET0123456789"

# Все инструменты записи vk-ads-mcp 0.11.0 (tools/list от 2026-10-14).
WRITE_TOOLS = [
    "vk_ads_token_revoke", "vk_ads_ad_plans_create", "vk_ads_ad_plans_update", "vk_ads_ad_plans_delete",
    "vk_ads_campaigns_create", "vk_ads_campaigns_update", "vk_ads_campaigns_set_status",
    "vk_ads_campaigns_delete", "vk_ads_ad_groups_create", "vk_ads_ad_groups_update",
    "vk_ads_ad_groups_delete", "vk_ads_banners_update", "vk_ads_banners_remoderate",
    "vk_ads_banners_delete", "vk_ads_content_upload_image", "vk_ads_content_upload_video",
    "vk_ads_remarketing_segments_create", "vk_ads_remarketing_segments_update",
    "vk_ads_remarketing_segments_delete", "vk_ads_remarketing_pixels_create",
    "vk_ads_remarketing_pixels_delete", "vk_ads_users_lists_create", "vk_ads_users_lists_upload_items",
    "vk_ads_users_lists_delete", "vk_ads_agency_clients_create",
]


def ok(obj):
    return {"jsonrpc": "2.0", "id": 1, "result": {"content": [{"type": "text", "text": json.dumps(obj)}]}}


def tool_error(obj_or_text):
    text = obj_or_text if isinstance(obj_or_text, str) else json.dumps(obj_or_text)
    return {"jsonrpc": "2.0", "id": 1, "result": {"content": [{"type": "text", "text": text}], "isError": True}}


class FakeTransport:
    def __init__(self, *responses):
        self.responses = list(responses)
        self.payloads = []

    def __call__(self, url, payload):
        self.payloads.append(payload)
        r = self.responses.pop(0)
        if isinstance(r, Exception):
            raise r
        return r


def test_allowed_and_write_disjoint():
    assert not (ALLOWED_TOOLS & set(WRITE_TOOLS))


@pytest.mark.parametrize("tool", WRITE_TOOLS)
def test_write_tool_refused_before_network(tool):
    transport = FakeTransport()
    client = McpClient(SECRET_URL, transport=transport)
    with pytest.raises(ForbiddenToolError):
        client.call(tool, {})
    assert transport.payloads == []


def test_account_id_injected_where_supported():
    transport = FakeTransport(ok({"items": []}), ok({"accounts": []}))
    client = McpClient(SECRET_URL, account_id=652819, transport=transport)
    client.call("vk_ads_campaigns_list", {"fields": "id"})
    client.call("vk_ads_accounts_list")
    assert transport.payloads[0]["params"]["arguments"]["account_id"] == "652819"
    assert "account_id" not in transport.payloads[1]["params"]["arguments"]


def test_rate_limit_retried():
    sleeps = []
    transport = FakeTransport(tool_error({"error": True, "status": 429, "detail": "rate_limit_exceeded"}),
                              ok({"ok": True}))
    client = McpClient(SECRET_URL, transport=transport, sleep=sleeps.append)
    assert client.call("vk_ads_auth_check") == {"ok": True}
    assert sleeps == [2.0]


def test_validation_error_not_retried():
    transport = FakeTransport(tool_error({"error": True, "status": 400,
                                          "detail": {"error": {"message": "Validation failed"}}}))
    client = McpClient(SECRET_URL, transport=transport, sleep=lambda s: None)
    with pytest.raises(McpError) as exc:
        client.call("vk_ads_statistics_summary", {"entity": "campaigns", "ids": "1"})
    assert exc.value.status == 400
    assert "Validation failed" in str(exc.value)
    assert len(transport.payloads) == 1


def test_plain_text_error():
    transport = FakeTransport(tool_error("Инструмент 'x' не найден"))
    with pytest.raises(McpError, match="не найден"):
        McpClient(SECRET_URL, transport=transport).call("vk_ads_auth_check")


def test_paginate_collects_all_pages():
    pages = [ok({"count": 600, "items": [{"id": i} for i in range(s, min(s + 250, 600))]})
             for s in (0, 250, 500)]
    transport = FakeTransport(*pages)
    items = McpClient(SECRET_URL, transport=transport).paginate("vk_ads_banners_list", {"fields": "id"})
    assert [i["id"] for i in items] == list(range(600))
    assert [p["params"]["arguments"]["offset"] for p in transport.payloads] == [0, 250, 500]


def test_paginate_without_count_stops_on_short_page():
    transport = FakeTransport(ok({"items": [{"id": 1}, {"id": 2}]}))
    items = McpClient(SECRET_URL, transport=transport).paginate("vk_ads_remarketing_pixels_list")
    assert len(items) == 2 and len(transport.payloads) == 1


def test_paginate_clamped_page_size_keeps_paging_until_count():
    pages = [ok({"count": 250, "items": [{"id": i} for i in range(s, min(s + 100, 250))]})
             for s in (0, 100, 200)]
    transport = FakeTransport(*pages)
    items = McpClient(SECRET_URL, transport=transport).paginate("vk_ads_banners_list")
    assert [i["id"] for i in items] == list(range(250))
    assert len(transport.payloads) == 3


def test_paginate_empty_page_before_count_raises():
    transport = FakeTransport(ok({"count": 250, "items": [{"id": 1}]}), ok({"count": 250, "items": []}))
    with pytest.raises(McpError, match="пустую страницу при offset=1 < count=250"):
        McpClient(SECRET_URL, transport=transport).paginate("vk_ads_banners_list")


@pytest.mark.parametrize("page", [[1, 2], {"count": 2}, {"items": "x"}])
def test_paginate_unexpected_shape_raises(page):
    transport = FakeTransport(ok(page))
    with pytest.raises(McpError, match="неожиданный формат страницы"):
        McpClient(SECRET_URL, transport=transport).paginate("vk_ads_banners_list")


EXPECTED_ALLOWED = {
    "vk_ads_auth_check", "vk_ads_account_info", "vk_ads_accounts_list", "vk_ads_agency_clients_list",
    "vk_ads_ad_plans_list", "vk_ads_ad_plans_get", "vk_ads_campaigns_list", "vk_ads_campaigns_get",
    "vk_ads_ad_groups_list", "vk_ads_ad_groups_get", "vk_ads_banners_list", "vk_ads_banners_get",
    "vk_ads_statistics_summary", "vk_ads_statistics_day", "vk_ads_remarketing_segments_list",
    "vk_ads_remarketing_pixels_list", "vk_ads_users_lists_list", "vk_ads_packages_list",
    "vk_ads_regions_search", "vk_ads_dictionary_get",
}


def test_allowed_tools_pinned():
    assert ALLOWED_TOOLS == EXPECTED_ALLOWED


class _FakeResp:
    def __init__(self, body: bytes, ctype: str = "application/json"):
        self.headers = {"Content-Type": ctype}
        self._body = body

    def read(self):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def _raise_from_urlopen(monkeypatch, exc):
    def fake(req, timeout=None):
        raise exc
    monkeypatch.setattr("scripts.mcp_client.urllib.request.urlopen", fake)


def test_http_error_does_not_leak_url(monkeypatch):
    _raise_from_urlopen(monkeypatch, urllib.error.HTTPError(SECRET_URL, 401, "Unauthorized", {}, None))
    with pytest.raises(McpError) as exc:
        http_transport(SECRET_URL, {})
    assert exc.value.status == 401
    assert "SECRET" not in str(exc.value)


def test_urlerror_does_not_leak_url_and_is_retryable(monkeypatch):
    _raise_from_urlopen(monkeypatch, urllib.error.URLError("boom " + SECRET_URL))
    with pytest.raises(McpError) as exc:
        http_transport(SECRET_URL, {})
    assert exc.value.retryable is True
    assert "SECRET" not in str(exc.value)


def test_timeout_does_not_leak_url_and_is_retryable(monkeypatch):
    _raise_from_urlopen(monkeypatch, TimeoutError("read " + SECRET_URL))
    with pytest.raises(McpError) as exc:
        http_transport(SECRET_URL, {})
    assert exc.value.retryable is True
    assert "SECRET" not in str(exc.value)


def test_bad_utf8_body_is_mcp_error(monkeypatch):
    monkeypatch.setattr("scripts.mcp_client.urllib.request.urlopen",
                        lambda req, timeout=None: _FakeResp(b"\xff\xfe"))
    with pytest.raises(McpError, match="UTF-8"):
        http_transport(SECRET_URL, {})


def test_retryable_transport_error_retried_once():
    sleeps = []
    transport = FakeTransport(McpError("сервер MCP недоступен: TimeoutError", retryable=True),
                              ok({"ok": True}))
    client = McpClient(SECRET_URL, transport=transport, sleep=sleeps.append)
    assert client.call("vk_ads_auth_check") == {"ok": True}
    assert sleeps == [2.0]


def test_retryable_exhausted_raises_after_retries():
    sleeps = []
    transport = FakeTransport(*[McpError("сервер MCP недоступен: TimeoutError", retryable=True)
                                 for _ in range(4)])
    client = McpClient(SECRET_URL, transport=transport, sleep=sleeps.append)
    with pytest.raises(McpError):
        client.call("vk_ads_auth_check")
    assert len(transport.payloads) == 4
    assert sleeps == [2.0, 4.0, 6.0]


def test_tool_error_detail_error_as_string():
    transport = FakeTransport(tool_error({"error": True, "status": 500, "detail": {"error": "boom"}}))
    client = McpClient(SECRET_URL, transport=transport, retries=0, sleep=lambda s: None)
    with pytest.raises(McpError) as exc:
        client.call("vk_ads_auth_check")
    assert "boom" in str(exc.value)
    assert exc.value.status == 500


def test_null_content_is_mcp_error_not_crash():
    transport = FakeTransport({"jsonrpc": "2.0", "id": 1, "result": {"content": None}})
    with pytest.raises(McpError):
        McpClient(SECRET_URL, transport=transport).call("vk_ads_auth_check")


def test_parse_body_sse():
    text = 'event: message\ndata: {"jsonrpc":"2.0","id":1,"result":{}}\n\n'
    assert parse_body("text/event-stream", text)["id"] == 1


def test_parse_body_json():
    assert parse_body("application/json; charset=utf-8", '{"id": 2}') == {"id": 2}


def test_http_exception_is_retryable_mcp_error(monkeypatch):
    import http.client
    _raise_from_urlopen(monkeypatch, http.client.BadStatusLine("garbage " + SECRET_URL))
    with pytest.raises(McpError) as exc:
        http_transport(SECRET_URL, {})
    assert exc.value.retryable is True
    assert "сервер MCP оборвал ответ: BadStatusLine" == str(exc.value)
    assert "SECRET" not in str(exc.value)


def test_incomplete_read_during_body_is_mcp_error(monkeypatch):
    import http.client

    class Broken:
        headers = {"Content-Type": "application/json"}

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def read(self):
            raise http.client.IncompleteRead(b"abc")

    monkeypatch.setattr("scripts.mcp_client.urllib.request.urlopen", lambda req, timeout=None: Broken())
    with pytest.raises(McpError, match="оборвал ответ: IncompleteRead") as exc:
        http_transport(SECRET_URL, {})
    assert exc.value.retryable is True


def test_non_json_body_not_echoed():
    with pytest.raises(McpError) as exc:
        parse_body("text/html", "<html>SECRET token=abc</html>")
    assert "SECRET" not in str(exc.value) and "байт" in str(exc.value)


def test_non_json_tool_text_not_echoed():
    transport = FakeTransport({"jsonrpc": "2.0", "id": 1,
                               "result": {"content": [{"type": "text", "text": "SECRET not json"}]}})
    with pytest.raises(McpError) as exc:
        McpClient(SECRET_URL, transport=transport).call("vk_ads_auth_check")
    assert str(exc.value) == "vk_ads_auth_check: ответ не JSON"
