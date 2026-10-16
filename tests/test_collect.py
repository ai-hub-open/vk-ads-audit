from __future__ import annotations

import json
from datetime import date, timedelta

import pytest

from scripts import collect as collect_mod
from scripts.collect import (BANNER_FIELDS_BASE, GROUP_FIELDS_BASE, STATS_CHUNK, DateLimitError, RawNotEmptyError,
                             clamp_date_to, collect, history_start, total_spend)
from scripts.mcp_client import ALLOWED_TOOLS, ForbiddenToolError, McpError

URL = "https://vkads-mcp.aihub.click.ru/c/TOKEN123456789"


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


class ForbidStats(FakeClient):
    def call(self, tool, arguments=None):
        if tool == "vk_ads_statistics_day":
            self._check(tool, arguments)
            raise ForbiddenToolError(f"{tool}: вне белого списка чтения")
        return super().call(tool, arguments)


class FailOneGroup(FakeClient):
    def paginate(self, tool, arguments=None, **kw):
        if tool == "vk_ads_banners_list" and arguments["campaign_id"] == 2:
            self._check(tool, arguments)
            raise McpError(f"{tool}: HTTP 500", status=500)
        return super().paginate(tool, arguments, **kw)


class LeakyClient(FakeClient):
    """Ошибка, которая несёт адрес подключения: сбор обязан его скрыть."""

    def __init__(self, url):
        super().__init__(groups(1))
        self.url = url

    def call(self, tool, arguments=None):
        if tool == "vk_ads_statistics_day":
            self._check(tool, arguments)
            raise McpError(f"{tool}: сбой при обращении к {self.url}")
        return super().call(tool, arguments)


def groups(n, deleted=()):
    return [{"id": i, "status": "deleted" if i in deleted else "active"} for i in range(1, n + 1)]


def tools(client, name):
    return [a for t, a in client.calls if t == name]


def read_log(ws):
    return json.loads((ws / "collect_log.json").read_text(encoding="utf-8"))


def test_deep_collects_everything(tmp_path):
    client = FakeClient(groups(3, deleted={3}))
    log = collect(client, tmp_path / "raw", "2026-09-01", "2026-09-30", mode="deep")
    assert log["errors"] == 0
    assert [a["campaign_id"] for a in tools(client, "vk_ads_banners_list")] == [1, 2]
    summary = tools(client, "vk_ads_statistics_summary")
    assert {a["entity"] for a in summary} == {"campaigns"}      # сводка объявлений не нужна: итоги — сумма дней
    assert summary[0]["ids"] == "1,2"
    day = tools(client, "vk_ads_statistics_day")
    assert {a["entity"] for a in day} == {"campaigns", "banners"}
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


def test_second_run_into_same_raw_refused(tmp_path):
    collect(FakeClient(groups(1)), tmp_path / "raw", "2026-09-01", "2026-09-30")
    with pytest.raises(RawNotEmptyError):
        collect(FakeClient(groups(1)), tmp_path / "raw", "2026-09-01", "2026-09-30")


def test_cli_second_run_returns_4(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("VK_ADS_CONNECTION_URL", URL)
    monkeypatch.setattr(collect_mod, "McpClient", lambda url, **kw: FakeClient(groups(1)))
    argv = ["--workspace", str(tmp_path / "ws"), "--date-from", "2026-09-01", "--date-to", "2026-09-30"]
    assert collect_mod.main(argv) == 0
    assert collect_mod.main(argv) == 4
    assert "уже содержит файлы" in capsys.readouterr().out


def test_forbidden_tool_propagates_and_log_written(tmp_path):
    with pytest.raises(ForbiddenToolError):
        collect(ForbidStats(groups(1)), tmp_path / "raw", "2026-09-01", "2026-09-30")
    assert read_log(tmp_path)["aborted"].startswith("ForbiddenToolError")


def test_item_without_id_aborts_with_log(tmp_path):
    with pytest.raises(KeyError):
        collect(FakeClient([{"status": "active"}]), tmp_path / "raw", "2026-09-01", "2026-09-30")
    assert read_log(tmp_path)["aborted"].startswith("KeyError")


def test_failed_campaigns_list_is_critical(tmp_path):
    log = collect(FakeClient(groups(2), fail_tools={"vk_ads_campaigns_list"}),
                  tmp_path / "raw", "2026-09-01", "2026-09-30")
    assert log["critical"] is True
    entry = [c for c in log["calls"] if c["tool"] == "vk_ads_campaigns_list"][0]
    assert entry["ok"] is False


def test_cli_critical_returns_3(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("VK_ADS_CONNECTION_URL", URL)
    monkeypatch.setattr(collect_mod, "McpClient",
                        lambda url, **kw: FakeClient(groups(2), fail_tools={"vk_ads_campaigns_list"}))
    rc = collect_mod.main(["--workspace", str(tmp_path / "ws"), "--date-from", "2026-09-01",
                           "--date-to", "2026-09-30"])
    assert rc == 3
    assert "опись кабинета не собрана" in capsys.readouterr().out


def test_failed_banners_of_one_group_continues(tmp_path, monkeypatch):
    monkeypatch.setenv("VK_ADS_CONNECTION_URL", URL)
    client = FailOneGroup(groups(2))
    monkeypatch.setattr(collect_mod, "McpClient", lambda url, **kw: client)
    rc = collect_mod.main(["--workspace", str(tmp_path / "ws"), "--date-from", "2026-09-01",
                           "--date-to", "2026-09-30"])
    assert rc == 0
    assert [a["campaign_id"] for a in tools(client, "vk_ads_banners_list")] == [1, 2, 2]  # группа 2: и повтор с базой
    summary = [a for a in tools(client, "vk_ads_statistics_day") if a["entity"] == "banners"]
    assert summary[0]["ids"] == "100,101"
    log = read_log(tmp_path / "ws")
    assert log["errors"] == 2  # расширенные поля и повтор с базовыми — оба вызова группы 2 упали
    assert log["critical"] is False


class RejectsExtendedFields(FakeClient):
    """Сервер отвергает расширенный список fields (имена, которых он не знает)."""

    def paginate(self, tool, arguments=None, **kw):
        if tool == "vk_ads_campaigns_list" and arguments["fields"] != GROUP_FIELDS_BASE:
            self._check(tool, arguments)
            raise McpError(f"{tool}: HTTP 400 unknown field", status=400)
        if tool == "vk_ads_banners_list" and arguments["fields"] != BANNER_FIELDS_BASE:
            self._check(tool, arguments)
            raise McpError(f"{tool}: HTTP 400 unknown field", status=400)
        return super().paginate(tool, arguments, **kw)


def test_extended_fields_requested_first_single_call(tmp_path):
    client = FakeClient(groups(2))
    log = collect(client, tmp_path / "raw", "2026-09-01", "2026-09-30")
    assert log["errors"] == 0
    g = tools(client, "vk_ads_campaigns_list")
    assert len(g) == 1 and "enable_utm" in g[0]["fields"] and g[0]["fields"] != GROUP_FIELDS_BASE
    assert "auto_utm" not in g[0]["fields"].split(",")        # живой прогон: 400 unknown_resource_fields
    assert "utm" in g[0]["fields"].split(",")
    b = tools(client, "vk_ads_banners_list")
    assert len(b) == 2 and all("call_to_action" in a["fields"] for a in b)
    for a in b:                                                # живой прогон: erid / ord_token / ord_erid → 400
        names = a["fields"].split(",")
        assert "ord_marker" in names and not {"erid", "ord_token", "ord_erid"} & set(names)


def test_extended_fields_rejected_retries_with_base(tmp_path):
    client = RejectsExtendedFields(groups(2))
    log = collect(client, tmp_path / "raw", "2026-09-01", "2026-09-30")
    assert log["critical"] is False
    g = tools(client, "vk_ads_campaigns_list")
    assert [a["fields"] == GROUP_FIELDS_BASE for a in g] == [False, True]
    b = tools(client, "vk_ads_banners_list")
    assert [a["fields"] == BANNER_FIELDS_BASE for a in b] == [False, True, True]  # после отказа база сразу
    assert log["errors"] == 2  # упавшие расширенные вызовы остаются в журнале
    assert (tmp_path / "raw" / "vk_ads_campaigns_list__000.json").is_file()
    summary = [a for a in tools(client, "vk_ads_statistics_day") if a["entity"] == "banners"]
    assert summary[0]["ids"] == "100,101,200,201"


def test_bad_mode_rejected(tmp_path):
    with pytest.raises(ValueError):
        collect(FakeClient(groups(1)), tmp_path / "raw", "2026-09-01", "2026-09-30", mode="full")


def test_cli_bad_date_format_exit_2(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("VK_ADS_CONNECTION_URL", URL)
    monkeypatch.setattr(collect_mod, "McpClient", lambda url, **kw: FakeClient(groups(1)))
    rc = collect_mod.main(["--workspace", str(tmp_path / "ws"), "--date-from", "2026-13-01",
                           "--date-to", "2026-09-30"])
    assert rc == 2
    assert "ГГГГ-ММ-ДД" in capsys.readouterr().out


def test_cli_empty_period_after_clamp_exit_2(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("VK_ADS_CONNECTION_URL", URL)
    monkeypatch.setattr(collect_mod, "McpClient", lambda url, **kw: FakeClient(groups(1)))
    rc = collect_mod.main(["--workspace", str(tmp_path / "ws"), "--date-from", "2030-01-01",
                           "--date-to", "2030-02-01"])
    assert rc == 2
    assert "период пуст" in capsys.readouterr().out


def test_cli_does_not_write_secret(tmp_path, monkeypatch, capsys):
    secret = "https://vkads-mcp.aihub.click.ru/c/TOPSECRET999999"
    monkeypatch.setenv("VK_ADS_CONNECTION_URL", secret)
    seen = []

    def factory(url, **kw):
        seen.append(url)
        return LeakyClient(url)

    monkeypatch.setattr(collect_mod, "McpClient", factory)
    ws = tmp_path / "audit"
    rc = collect_mod.main(["--workspace", str(ws), "--date-from", "2026-09-01", "--date-to", "2026-09-30"])
    assert rc == 0
    assert seen == [secret]
    assert read_log(ws)["errors"] == 2          # дни по группам и дни по объявлениям
    assert "TOPSECRET" not in capsys.readouterr().out
    files = [p for p in ws.rglob("*") if p.is_file()]
    assert files
    for path in files:
        assert "TOPSECRET" not in path.read_text(encoding="utf-8")


def test_cli_without_url_exits_2(tmp_path, capsys):
    rc = collect_mod.main(["--workspace", str(tmp_path / "a"), "--date-from", "2026-09-01",
                           "--date-to", "2026-09-30"])
    assert rc == 2
    assert "путь B недоступен" in capsys.readouterr().out


# ---- предполётка расхода, глубина истории, нет прав на списки, архивные группы ----

class SpendClient(FakeClient):
    """account_info отдаёт id пользователя; statistics_day(users) — расход за период."""

    def __init__(self, groups, spent="1500.00", **kw):
        super().__init__(groups, **kw)
        self.spent = spent

    def call(self, tool, arguments=None):
        if tool == "vk_ads_account_info":
            self._check(tool, arguments)
            return {"id": 77, "username": "acc", "currency": "RUB"}
        if tool == "vk_ads_statistics_day" and (arguments or {}).get("entity") == "users":
            self._check(tool, arguments)
            base = {"shows": 0, "spent": self.spent}
            return {"items": [{"id": 77, "total": {"base": base}, "rows": [{"date": "2026-09-01", "base": base}]}],
                    "total": {"base": base}}
        return super().call(tool, arguments)


def test_preflight_zero_spend_stops_before_collecting(tmp_path):
    client = SpendClient(groups(3), spent="0")
    log = collect(client, tmp_path / "raw", "2026-09-01", "2026-09-30")
    assert log["no_spend"] is True
    assert [t for t, _ in client.calls] == ["vk_ads_account_info", "vk_ads_statistics_day"]
    users = tools(client, "vk_ads_statistics_day")[0]
    assert users["entity"] == "users" and users["ids"] == "77"
    assert users["date_from"] == "2026-09-01" and users["date_to"] == "2026-09-30"
    assert not (tmp_path / "raw").exists() or not any((tmp_path / "raw").iterdir())
    assert (tmp_path / "collect_log.json").is_file()


def test_preflight_with_spend_continues_and_keeps_raw_clean(tmp_path):
    client = SpendClient(groups(2))
    log = collect(client, tmp_path / "raw", "2026-09-01", "2026-09-30")
    assert log["no_spend"] is False and log["errors"] == 0
    assert tools(client, "vk_ads_campaigns_list")
    assert not list((tmp_path / "raw").glob("vk_ads_account_info*"))           # предполётка не пишет в raw/
    assert not [p for p in (tmp_path / "raw").glob("vk_ads_statistics_day*")
                if json.loads(p.read_text(encoding="utf-8"))["arguments"]["entity"] == "users"]


def test_preflight_skipped_when_user_id_unknown(tmp_path):
    client = FakeClient(groups(1))                       # account_info без id
    log = collect(client, tmp_path / "raw", "2026-09-01", "2026-09-30")
    assert log["no_spend"] is False and tools(client, "vk_ads_campaigns_list")
    assert not [a for a in tools(client, "vk_ads_statistics_day") if a["entity"] == "users"]


def test_preflight_failure_does_not_stop_collection(tmp_path):
    class Broken(SpendClient):
        def call(self, tool, arguments=None):
            if tool == "vk_ads_statistics_day" and (arguments or {}).get("entity") == "users":
                self._check(tool, arguments)
                raise McpError("vk_ads_statistics_day: HTTP 500", status=500)
            return super().call(tool, arguments)

    client = Broken(groups(1))
    log = collect(client, tmp_path / "raw", "2026-09-01", "2026-09-30")
    assert log["no_spend"] is False and log["errors"] == 0 and tools(client, "vk_ads_campaigns_list")
    assert any(c.get("preflight") and not c["ok"] for c in log["calls"])


def test_total_spend_shapes():
    assert total_spend({"items": [{"id": 1, "rows": [{"base": {"spent": "10.5"}}, {"base": {"spent": "4,5"}}]}]}) == 15.0
    assert total_spend({"items": [{"id": 1, "total": {"base": {"spent": "7"}}}]}) == 7.0
    assert total_spend({"items": [], "total": {"base": {"spent": "3"}}}) == 3.0
    assert total_spend({"items": []}) is None and total_spend(None) is None


def test_cli_zero_spend_exit_5(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("VK_ADS_CONNECTION_URL", URL)
    monkeypatch.setattr(collect_mod, "McpClient", lambda url, **kw: SpendClient(groups(1), spent="0"))
    rc = collect_mod.main(["--workspace", str(tmp_path / "ws"), "--date-from", "2026-09-01",
                           "--date-to", "2026-09-30"])
    out = capsys.readouterr().out
    assert rc == 5 and "расход в кабинете 0" in out and "другой период" in out


def test_period_older_than_history_refused_before_any_call(tmp_path):
    today = date(2026, 10, 16)
    client = FakeClient(groups(1))
    old = (history_start(today) - timedelta(days=1)).isoformat()
    with pytest.raises(DateLimitError) as exc:
        collect(client, tmp_path / "raw", old, "2026-09-30", today=today)
    assert exc.value.min_date == history_start(today) == date(2025, 10, 16)
    assert "2025-10-16" in str(exc.value) and client.calls == []
    collect(client, tmp_path / "raw", history_start(today).isoformat(), "2026-09-30", today=today)  # граница годна


def test_server_wrong_date_explained_not_raw(tmp_path):
    class WrongDate(FakeClient):
        def call(self, tool, arguments=None):
            self._check(tool, arguments)
            raise McpError("vk_ads_statistics_day: 400 WRONG_DATE: minimal allowed date is '2025-8-11'", status=400)

    with pytest.raises(DateLimitError) as exc:
        collect(WrongDate(groups(1)), tmp_path / "raw", "2026-09-01", "2026-09-30")
    assert exc.value.min_date == date(2025, 8, 11) and exc.value.from_server
    entry = [c for c in read_log(tmp_path)["calls"] if not c["ok"]][0]
    assert "2025-08-11" in entry["error"] and "WRONG_DATE" not in entry["error"]


def test_cli_old_period_exit_2_with_message(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("VK_ADS_CONNECTION_URL", URL)
    monkeypatch.setattr(collect_mod, "McpClient", lambda url, **kw: FakeClient(groups(1)))
    rc = collect_mod.main(["--workspace", str(tmp_path / "ws"), "--date-from", "2020-01-01",
                           "--date-to", "2020-02-01"])
    out = capsys.readouterr().out
    assert rc == 2 and "История статистики VK доступна с" in out


class NoAccessLists(FakeClient):
    def paginate(self, tool, arguments=None, **kw):
        if tool == "vk_ads_users_lists_list":
            self._check(tool, arguments)
            raise McpError("vk_ads_users_lists_list: нет доступа (лимит запросов 0)", status=429, no_access=True)
        return super().paginate(tool, arguments, **kw)


def test_no_access_list_flagged_in_log(tmp_path):
    log = collect(NoAccessLists(groups(1)), tmp_path / "raw", "2026-09-01", "2026-09-30")
    entry = [c for c in log["calls"] if c["tool"] == "vk_ads_users_lists_list"][0]
    assert entry["ok"] is False and entry["no_access"] is True
    assert log["critical"] is False
    assert not (tmp_path / "raw" / "vk_ads_users_lists_list__000.json").exists()


def test_archived_groups_skip_banners_but_keep_statistics(tmp_path):
    gs = [{"id": 1, "status": "active"}, {"id": 2, "status": "active", "issues": [{"code": "ARCHIVED"}]},
          {"id": 3, "status": "deleted"}]
    client = FakeClient(gs, banners_per_group=1)
    collect(client, tmp_path / "raw", "2026-09-01", "2026-09-30")
    assert [a["campaign_id"] for a in tools(client, "vk_ads_banners_list")] == [1]
    day = [a for a in tools(client, "vk_ads_statistics_day") if a["entity"] == "campaigns"]
    assert day[0]["ids"] == "1,2"                       # расход архивной группы остаётся в итогах


MCP_REFUSAL = ("Ошибка: Статистика по дням хранится 366 дней: доступен период с 2025-10-16 по 2026-10-16 (по Москве). "
               "date_from 2024-11-01 раньше начала истории — за такие дни ВК вернул бы нули вместо данных. "
               "Укажите date_from не раньше 2025-10-16.")


def test_history_is_366_days_window_inclusive_of_today():
    assert history_start(date(2026, 10, 16)) == date(2025, 10, 16)


def test_mcp_refusal_message_parsed_into_min_date(tmp_path):
    class Refuses(FakeClient):
        def call(self, tool, arguments=None):
            self._check(tool, arguments)
            raise McpError("vk_ads_statistics_day: " + MCP_REFUSAL, status=400)

    with pytest.raises(DateLimitError) as exc:
        collect(Refuses(groups(1)), tmp_path / "raw", "2026-09-01", "2026-09-30")
    assert exc.value.min_date == date(2025, 10, 16) and exc.value.from_server
    entry = [c for c in read_log(tmp_path)["calls"] if not c["ok"]][0]
    assert "2025-10-16" in entry["error"] and "нули вместо данных" not in entry["error"]


def test_preflight_reads_total_base_spent_string_from_live_shape(tmp_path):
    live = {"items": [{"id": 13983692,
                       "total": {"base": {"shows": 0, "clicks": 0, "goals": 0, "spent": "0", "not_ad_spent": "0",
                                          "cpm": "0", "cpc": "0", "cpa": "0", "ctr": 0, "cr": 0, "vk": {}}},
                       "rows": [{"date": "2026-10-10", "base": {"spent": "0", "shows": 0}}]}],
            "total": {"base": {"spent": "0"}}}
    assert total_spend(live) == 0.0
    live["items"][0]["total"]["base"]["spent"] = "1234.50"
    live["items"][0]["rows"][0]["base"]["spent"] = "0"      # total приоритетнее строк
    assert total_spend(live) == 1234.5


def test_user_id_from_top_level_id():
    from scripts.collect import _user_id
    assert _user_id({"id": 13983692, "username": "u"}) == 13983692
