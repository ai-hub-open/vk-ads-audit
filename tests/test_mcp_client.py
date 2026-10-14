from __future__ import annotations

import json

import pytest

from scripts.mcp_client import (ALLOWED_TOOLS, ForbiddenToolError, McpClient, McpError,
                                parse_body)

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


def test_error_does_not_leak_url():
    transport = FakeTransport(McpError("HTTP 401", status=401))
    with pytest.raises(McpError) as exc:
        McpClient(SECRET_URL, transport=transport).call("vk_ads_auth_check")
    assert "SECRET" not in str(exc.value)


def test_parse_body_sse():
    text = 'event: message\ndata: {"jsonrpc":"2.0","id":1,"result":{}}\n\n'
    assert parse_body("text/event-stream", text)["id"] == 1


def test_parse_body_json():
    assert parse_body("application/json; charset=utf-8", '{"id": 2}') == {"id": 2}
