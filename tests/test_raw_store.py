from __future__ import annotations

import json

import pytest

from scripts.raw_store import items_of, main, read_calls, write_call


def test_roundtrip_and_parts(tmp_path):
    raw = tmp_path / "raw"
    write_call(raw, "vk_ads_campaigns_list", {"fields": "id"}, {"items": [{"id": 1}]})
    write_call(raw, "vk_ads_campaigns_list", {"fields": "id"}, {"items": [{"id": 2}]})
    calls = read_calls(raw, "vk_ads_campaigns_list")
    assert [c["response"]["items"][0]["id"] for c in calls] == [1, 2]
    assert (raw / "vk_ads_campaigns_list__001.json").is_file()


def test_items_of_with_filter(tmp_path):
    raw = tmp_path / "raw"
    write_call(raw, "vk_ads_statistics_summary", {"entity": "campaigns"}, {"items": [{"id": 1}]})
    write_call(raw, "vk_ads_statistics_summary", {"entity": "banners"}, {"items": [{"id": 9}]})
    got = items_of(raw, "vk_ads_statistics_summary", where=lambda a: a.get("entity") == "banners")
    assert got == [{"id": 9}]


def test_missing_tool_is_empty(tmp_path):
    assert read_calls(tmp_path / "raw", "vk_ads_banners_list") == []


def test_malformed_raw_names_file(tmp_path):
    raw = tmp_path / "raw"
    raw.mkdir()
    (raw / "vk_ads_banners_list__000.json").write_text("{not json", encoding="utf-8")
    with pytest.raises(ValueError, match="vk_ads_banners_list__000.json"):
        read_calls(raw, "vk_ads_banners_list")


def test_missing_keys_names_file(tmp_path):
    raw = tmp_path / "raw"
    raw.mkdir()
    (raw / "vk_ads_banners_list__000.json").write_text(json.dumps({"items": []}), encoding="utf-8")
    with pytest.raises(ValueError, match="response"):
        read_calls(raw, "vk_ads_banners_list")


def test_check_cli(tmp_path, capsys):
    write_call(tmp_path / "raw", "vk_ads_auth_check", {}, {"ok": True})
    assert main(["check", "--workspace", str(tmp_path)]) == 0
    (tmp_path / "raw" / "vk_ads_banners_list__000.json").write_text("[]", encoding="utf-8")
    assert main(["check", "--workspace", str(tmp_path)]) == 3
