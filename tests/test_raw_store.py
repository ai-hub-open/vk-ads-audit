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
    out = capsys.readouterr().out
    assert "✗" not in out
    assert "файлов: 1, с ошибками: 0" in out
    (tmp_path / "raw" / "vk_ads_banners_list__000.json").write_text("[]", encoding="utf-8")
    assert main(["check", "--workspace", str(tmp_path)]) == 3
    out = capsys.readouterr().out
    assert "✗ vk_ads_banners_list__000.json" in out
    assert "файлов: 2, с ошибками: 1" in out


def _raw_with(tmp_path, name, text):
    raw = tmp_path / "raw"
    raw.mkdir(exist_ok=True)
    (raw / name).write_text(text, encoding="utf-8")
    return raw


def test_parts_sorted_numerically(tmp_path):
    raw = tmp_path / "raw"
    for part in (1, 2, 10):
        write_call(raw, "x", {}, {"items": [{"id": part}]}, part=part)
    calls = read_calls(raw, "x")
    assert [c["response"]["items"][0]["id"] for c in calls] == [1, 2, 10]


def test_non_integer_part_names_file(tmp_path):
    raw = _raw_with(tmp_path, "x__abc.json", json.dumps({"tool": "x", "arguments": {}, "response": {}}))
    with pytest.raises(ValueError, match="x__abc.json"):
        read_calls(raw, "x")


def test_next_part_after_gap_is_max_plus_one(tmp_path):
    raw = tmp_path / "raw"
    write_call(raw, "x", {}, {"items": []}, part=0)
    write_call(raw, "x", {}, {"items": []}, part=2)
    path = write_call(raw, "x", {}, {"items": []})
    assert path.name == "x__003.json"


def test_explicit_part_refuses_overwrite(tmp_path):
    raw = tmp_path / "raw"
    write_call(raw, "x", {}, {"items": [{"id": 1}]}, part=0)
    with pytest.raises(FileExistsError, match="x__000.json"):
        write_call(raw, "x", {}, {"items": [{"id": 2}]}, part=0)
    assert read_calls(raw, "x")[0]["response"]["items"] == [{"id": 1}]


def test_null_arguments_names_file(tmp_path):
    raw = _raw_with(tmp_path, "x__000.json", json.dumps({"tool": "x", "arguments": None, "response": {}}))
    with pytest.raises(ValueError, match="x__000.json.*arguments"):
        read_calls(raw, "x")


def test_tool_mismatch_names_both(tmp_path):
    raw = _raw_with(tmp_path, "x__000.json", json.dumps({"tool": "y", "arguments": {}, "response": {}}))
    with pytest.raises(ValueError, match="x__000.json.*'y'.*'x'"):
        read_calls(raw, "x")


def test_items_as_dict_names_file(tmp_path):
    raw = _raw_with(tmp_path, "x__000.json",
                    json.dumps({"tool": "x", "arguments": {}, "response": {"items": {"id": 1}}}))
    with pytest.raises(ValueError, match="x__000.json"):
        items_of(raw, "x")


def test_response_as_list_names_file(tmp_path):
    raw = _raw_with(tmp_path, "x__000.json", json.dumps({"tool": "x", "arguments": {}, "response": [1, 2]}))
    with pytest.raises(ValueError, match="x__000.json"):
        items_of(raw, "x")


def test_response_without_items_is_empty(tmp_path):
    raw = tmp_path / "raw"
    write_call(raw, "vk_ads_auth_check", {}, {"ok": True})
    assert items_of(raw, "vk_ads_auth_check") == []


def test_missing_keys_message_lists_actual_missing(tmp_path):
    raw = _raw_with(tmp_path, "x__000.json", json.dumps({"tool": "x", "response": {}}))
    with pytest.raises(ValueError, match="нет ключей arguments"):
        read_calls(raw, "x")


def test_utf8_bom_is_accepted(tmp_path):
    raw = tmp_path / "raw"
    raw.mkdir()
    (raw / "x__000.json").write_bytes(b"\xef\xbb\xbf" + json.dumps(
        {"tool": "x", "arguments": {}, "response": {"items": [{"id": 7}]}}).encode("utf-8"))
    assert items_of(raw, "x") == [{"id": 7}]


def test_check_missing_raw_dir_exits_3(tmp_path, capsys):
    assert main(["check", "--workspace", str(tmp_path)]) == 3
    assert "✗" in capsys.readouterr().out


def test_check_nonmatching_name_is_error(tmp_path, capsys):
    write_call(tmp_path / "raw", "vk_ads_auth_check", {}, {"ok": True})
    (tmp_path / "raw" / "notes.txt").write_text("hi", encoding="utf-8")
    assert main(["check", "--workspace", str(tmp_path)]) == 3
    out = capsys.readouterr().out
    assert "✗ notes.txt" in out
    assert "файлов: 2, с ошибками: 1" in out
