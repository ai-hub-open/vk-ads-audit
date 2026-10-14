from __future__ import annotations

import json
from datetime import date

import pytest

from scripts import collect as collect_mod
from scripts.collect import STATS_CHUNK, clamp_date_to, collect
from scripts.mcp_client import ALLOWED_TOOLS, McpError


class FakeClient:
    def __init__(self, groups, banners_per_group=2, fail_tools=()):
        self.groups = groups
        self.banners_per_group = banners_per_group
        self.fail_tools = set(fail_tools)
        self.calls = []

    def _check(self, tool, args):
        assert tool in ALLOWED_TOOLS
        self.calls.append((tool, dict(args or {})))
        if tool in self.fail_tools:
            raise McpError(f"{tool}: HTTP 500", status=500)

    def paginate(self, tool, arguments=None, **_):
        self._check(tool, arguments)
        if tool == "vk_ads_campaigns_list":
            return self.groups
        if tool == "vk_ads_banners_list":
            gid = arguments["campaign_id"]
            return [{"id": gid * 100 + i, "status": "active"} for i in range(self.banners_per_group)]
        return []

    def call(self, tool, arguments=None):
        self._check(tool, arguments)
        return {"items": [], "total": {}}


def groups(n, deleted=()):
    return [{"id": i, "status": "deleted" if i in deleted else "active"} for i in range(1, n + 1)]


def tools(client, name):
    return [a for t, a in client.calls if t == name]


def test_deep_collects_everything(tmp_path):
    client = FakeClient(groups(3, deleted={3}))
    log = collect(client, tmp_path / "raw", "2026-09-01", "2026-09-30", mode="deep")
    assert log["errors"] == 0
    assert [a["campaign_id"] for a in tools(client, "vk_ads_banners_list")] == [1, 2]
    summary = tools(client, "vk_ads_statistics_summary")
    assert {a["entity"] for a in summary} == {"campaigns", "banners"}
    assert summary[0]["ids"] == "1,2"
    assert (tmp_path / "collect_log.json").is_file()
    assert (tmp_path / "raw" / "vk_ads_banners_list__001.json").is_file()


def test_banner_group_recorded_in_arguments(tmp_path):
    collect(FakeClient(groups(1)), tmp_path / "raw", "2026-09-01", "2026-09-30")
    saved = json.loads((tmp_path / "raw" / "vk_ads_banners_list__000.json").read_text(encoding="utf-8"))
    assert saved["arguments"]["campaign_id"] == 1


def test_many_groups_chunked(tmp_path):
    client = FakeClient(groups(120), banners_per_group=0)
    collect(client, tmp_path / "raw", "2026-09-01", "2026-09-30")
    day = tools(client, "vk_ads_statistics_day")
    assert len(day) == 3
    assert sum(len(a["ids"].split(",")) for a in day) == 120
    assert all(len(a["ids"].split(",")) <= STATS_CHUNK for a in day)


def test_quick_mode_skips_banners_and_summary(tmp_path):
    client = FakeClient(groups(2))
    collect(client, tmp_path / "raw", "2026-10-06", "2026-10-13", mode="quick")
    names = {t for t, _ in client.calls}
    assert "vk_ads_banners_list" not in names
    assert "vk_ads_statistics_summary" not in names
    assert "vk_ads_statistics_day" in names


def test_partial_failure_logged_and_continues(tmp_path):
    client = FakeClient(groups(2), fail_tools={"vk_ads_remarketing_pixels_list"})
    log = collect(client, tmp_path / "raw", "2026-09-01", "2026-09-30")
    assert log["errors"] == 1
    failed = [c for c in log["calls"] if not c["ok"]]
    assert failed[0]["tool"] == "vk_ads_remarketing_pixels_list"
    assert tools(client, "vk_ads_statistics_day")


def test_clamp_date_to():
    today = date(2026, 10, 14)
    assert clamp_date_to("2026-10-14", today) == "2026-10-13"
    assert clamp_date_to("2026-10-20", today) == "2026-10-13"
    assert clamp_date_to("2026-10-01", today) == "2026-10-01"


def test_cli_does_not_write_secret(tmp_path, monkeypatch):
    secret = "https://vkads-mcp.aihub.click.ru/c/TOPSECRET999999"
    monkeypatch.setenv("VK_ADS_CONNECTION_URL", secret)
    monkeypatch.setattr(collect_mod, "McpClient", lambda url, **kw: FakeClient(groups(1)))
    ws = tmp_path / "audit"
    rc = collect_mod.main(["--workspace", str(ws), "--date-from", "2026-09-01", "--date-to", "2026-09-30"])
    assert rc == 0
    for path in ws.rglob("*.json"):
        assert "TOPSECRET" not in path.read_text(encoding="utf-8")


def test_cli_without_url_exits_2(tmp_path, capsys):
    rc = collect_mod.main(["--workspace", str(tmp_path / "a"), "--date-from", "2026-09-01",
                           "--date-to", "2026-09-30"])
    assert rc == 2
    assert "путь B недоступен" in capsys.readouterr().out
