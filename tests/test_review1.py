"""Независимое ревью fix/live-run-1: ложные ❌/✅/«0 ₽» — по одному тесту на пункт."""
from __future__ import annotations

import json

import pytest

from scripts import collect as collect_mod
from scripts.checks import run_checks
from scripts.collect import collect, total_spend
from scripts.diagnose import diagnose, load_thresholds
from scripts.mcp_client import McpError
from scripts.normalize import build_dataset
from scripts.raw_store import import_response, main as raw_main, write_call
from scripts.render_report import limitations
from tests.helpers import make_banner, make_ds, make_group, stats
from tests.test_collect import URL, FakeClient, SpendClient, groups, read_log, tools
from tests.test_live_run1 import D, day_rows
from tests.test_normalize import _active, _stat_call, base, cabinet

TH = load_thresholds()


def res(ds):
    return diagnose(ds, TH)


def found(ds, rule):
    return [f for f in res(ds)["findings"] if f["rule"] == rule]


def checklist(ds):
    r = res(ds)
    return run_checks(ds, TH, r["findings"], r["evaluated"])


def status(cl, cid):
    return next(c for b in cl["blocks"] for c in b["checks"] if c["id"] == cid)


# ---- 1. сводка (всё время) не подменяет итоги периода, если дневные вызовы были ----

def test_summary_fallback_only_when_no_daily_calls_at_all(tmp_path):
    raw = tmp_path / "raw"
    cabinet(raw, [_active(1), _active(2)])
    huge = base(spent="99999", shows=999999, clicks=9999)
    _stat_call(raw, "vk_ads_statistics_summary", "campaigns", [1, 2],
               [{"id": 1, "total": huge}, {"id": 2, "total": huge}])
    _stat_call(raw, "vk_ads_statistics_day", "campaigns", [1], [{"id": 1, "rows": day_rows(2)}])   # пачка группы 2 упала
    ds = build_dataset(raw, *D)
    g1, g2 = ds["groups"]
    assert g1["stats"]["spent"] == 200.0
    assert g2["stats_missing"] is True and g2["stats"]["spent"] == 0.0
    assert ds["totals"]["spent"] == 200.0
    assert any("Статистика не получена для 1 групп" in n for n in ds["notes"])


def test_banner_summary_not_used_when_banner_daily_calls_exist(tmp_path):
    raw = tmp_path / "raw"
    cabinet(raw, [_active(1)], banners_by_group={1: [{"id": 11, "status": "active"}, {"id": 12, "status": "active"}]},
            daily={1: day_rows(2)}, banner_daily={11: day_rows(2)},
            banner_summary={12: base(spent="99999", shows=999999, clicks=9999)})
    by = {b["id"]: b for b in build_dataset(raw, *D)["banners"]}
    assert by[11]["stats"]["spent"] == 200.0 and by[12]["stats"] is None


# ---- 2. F3 ----

def _g(i, freq, shows=20000, **kw):
    return make_group(i, stats=stats(900, shows, 100, 1, frequency=freq),
                      daily=[{"date": f"2026-09-{d:02d}", **stats(30, 700, 3, 0)} for d in range(1, 31)], **kw)


def test_f3_unknown_when_a_big_group_has_unknown_frequency():
    ds = make_ds([_g(1, 1.5), _g(2, None)])
    assert status(checklist(ds), "F3")["status"] == "unknown"


def test_f3_na_when_only_small_groups():
    # Ревью fix/live-run-2: групп с достаточными показами нет — проверять нечего («—» na), а не «нет данных».
    ds = make_ds([_g(1, 1.5, shows=500)])
    c = status(checklist(ds), "F3")
    assert (c["status"], c["reason"]) == ("na", "нечего проверять: нет групп с достаточными показами")


def test_f3_pass_when_every_eligible_group_known():
    ds = make_ds([_g(1, 1.5), _g(2, 1.2), _g(3, None, shows=500)])
    assert status(checklist(ds), "F3")["status"] == "pass"


def test_f3_fail_stays_when_burnout_fired_despite_unknown_others():
    ds = make_ds([_g(1, 6.0), _g(2, None)])
    assert status(checklist(ds), "F3")["status"] == "fail"


def test_f3_unknown_when_some_group_stats_missing():
    ds = make_ds([_g(1, 1.5), _g(2, None, stats_missing=True)])
    assert status(checklist(ds), "F3")["status"] == "unknown"


# ---- 3. E5 ----

def test_e5_unknown_with_unreadable_image_ratio_among_equal_images():
    bs = [make_banner(11, 1, ratios=["1:1"], content_ids=[1]), make_banner(12, 1, ratios=["1:1"], content_ids=[2]),
          make_banner(13, 1, ratios=[], has_video=False, content_ids=[3], ratios_unknown=True)]
    assert status(checklist(make_ds([make_group(1)], bs)), "E5")["status"] == "unknown"


def test_e5_not_fail_with_unknown_ratio_video_among_images():
    bs = [make_banner(11, 1, ratios=["1:1"], content_ids=[1]), make_banner(12, 1, ratios=["1:1"], content_ids=[2]),
          make_banner(13, 1, has_video=True, content_ids=[3], ratios_unknown=True)]
    assert status(checklist(make_ds([make_group(1)], bs)), "E5")["status"] == "pass"


def test_e5_unknown_when_collapsed_but_some_ratio_unknown():
    bs = [make_banner(11, 1, has_video=True, content_ids=[1], ratios_unknown=True),
          make_banner(12, 1, has_video=True, content_ids=[2], ratios_unknown=True)]
    assert status(checklist(make_ds([make_group(1)], bs)), "E5")["status"] == "unknown"


def test_e5_still_fails_when_all_known_and_equal():
    bs = [make_banner(11, 1, ratios=["1:1"], content_ids=[1]), make_banner(12, 1, ratios=["1:1"], content_ids=[2])]
    assert status(checklist(make_ds([make_group(1)], bs)), "E5")["status"] == "fail"


# ---- 4. объявления нежилых групп не попадают в правила ----

@pytest.mark.parametrize("gstatus,issues", [("active", ["ARCHIVED"]), ("deleted", [])])
def test_banners_of_non_live_groups_are_ignored(gstatus, issues):
    dead = make_group(2, status=gstatus, issues=issues)
    live = make_group(1)
    bs = [make_banner(11, 1, erid="X", moderation_status="allowed", content_ids=[1], ratios=["1:1"]),
          make_banner(21, 2, erid="", moderation_status="banned", urls=["https://site.ru/"],
                      created="2026-09-01 00:00:00")]
    ds = make_ds([live, dead], bs)
    for rule in ("rejected_banners", "ord_missing", "utm_missing", "moderation_stuck"):
        assert found(ds, rule) == [], rule
    cl = checklist(ds)
    assert status(cl, "E8")["status"] != "fail" and status(cl, "A2")["status"] != "fail"


def test_one_liveness_helper():
    from scripts import diagnose, normalize
    from scripts.features import is_live_group
    assert diagnose.is_live_group is is_live_group and normalize._is_live is is_live_group
    assert not is_live_group({"status": "active", "issues": ["ARCHIVED"]})
    assert not is_live_group({"status": "deleted", "issues": []}) and is_live_group({"status": "active"})


def test_quick_ignores_archived():
    from scripts.quick import quick_summary
    arch = make_group(1, issues=["ARCHIVED"], daily=[{"date": "2026-10-13", **stats(500, 1000, 10, 0)}])
    act = make_group(2, daily=[{"date": "2026-10-13", **stats(100, 1000, 10, 1)}])
    assert quick_summary(make_ds([arch, act]), "2026-10-13")["today"]["spent"] == 100.0


# ---- 5. raw_store import строже ----

def _src(tmp_path, obj, name="r.json"):
    p = tmp_path / name
    p.write_text(json.dumps(obj, ensure_ascii=False), encoding="utf-8")
    return p


def _imp(tmp_path, obj, tool="vk_ads_campaigns_list", args=None):
    return import_response(tmp_path / "raw", tool, args or {}, _src(tmp_path, obj))


@pytest.mark.parametrize("obj", [
    {"error": True, "status": 429, "detail": "x"},
    {"tool": "vk_ads_campaigns_list", "arguments": {}, "response": {"items": []}},
    {"count": 3},                                                  # у списка нет items
    {"items": {"id": 1}},                                          # items не список
])
def test_import_refuses_loose_responses_for_list_tools(tmp_path, obj):
    with pytest.raises(ValueError):
        _imp(tmp_path, obj)


def test_import_requires_items_for_statistics_but_not_for_info(tmp_path):
    with pytest.raises(ValueError, match="items"):
        _imp(tmp_path, {"total": {}}, tool="vk_ads_statistics_day")
    assert _imp(tmp_path, {"id": 5, "username": "u"}, tool="vk_ads_account_info")


def test_import_warns_on_partial_unpaginated_list(tmp_path, capsys):
    _imp(tmp_path, {"count": 56, "items": [{"id": 1}] * 10}, args={"limit": 10})
    err = capsys.readouterr().err
    assert "56" in err and "10" in err and "offset" in err
    _imp(tmp_path, {"count": 1, "items": [{"id": 1}]}, args={})
    assert capsys.readouterr().err == ""
    _imp(tmp_path, {"count": 56, "items": [{"id": 1}] * 10}, args={"offset": 10})   # не первая страница — не ругаемся
    assert capsys.readouterr().err == ""


def test_import_arguments_and_file_mutually_exclusive(tmp_path):
    src = _src(tmp_path, {"items": []})
    argf = _src(tmp_path, {}, "a.json")
    with pytest.raises(SystemExit):
        raw_main(["import", "--workspace", str(tmp_path / "ws"), "--tool", "vk_ads_campaigns_list",
                  "--arguments", "{}", "--arguments-file", str(argf), "--from", str(src)])


# ---- 6. предполётка: нечитаемое ≠ 0 ₽ ----

@pytest.mark.parametrize("resp", [{}, {"items": []}, {"items": [{"id": 1, "total": {"base": {"spent": "abc"}}}]},
                                  None, [], {"items": ["x"]}])
def test_total_spend_none_when_unreadable(resp):
    assert total_spend(resp) is None


def test_total_spend_positive_zero_is_zero():
    assert total_spend({"items": [{"id": 1, "total": {"base": {"spent": "0"}}}]}) == 0.0
    assert total_spend({"items": [{"id": 1, "rows": [{"base": {"spent": "0"}}]}]}) == 0.0


def test_unreadable_preflight_response_does_not_stop_collection(tmp_path):
    class Odd(SpendClient):
        def call(self, tool, arguments=None):
            if tool == "vk_ads_statistics_day" and (arguments or {}).get("entity") == "users":
                self._check(tool, arguments)
                return {"items": []}
            return super().call(tool, arguments)

    client = Odd(groups(1))
    log = collect(client, tmp_path / "raw", "2026-09-01", "2026-09-30")
    assert log["no_spend"] is False and tools(client, "vk_ads_campaigns_list")
    assert log["preflight"].startswith("пропущена")


# ---- 7. UTM группы ----

def test_group_with_utm_template_is_tagged(tmp_path):
    raw = tmp_path / "raw"
    tpl = "utm_source=vk_ads&utm_medium=cpa&utm_campaign={{campaign_id}}&utm_term={{gender}}_{{age}}"
    cabinet(raw, [_active(1, utm=tpl), _active(2, enable_utm=True, utm=None), _active(3, utm=None),
                  _active(4, enable_utm=False, utm="")])
    g = {x["id"]: x["auto_utm"] for x in build_dataset(raw, *D)["groups"]}
    # Второй живой прогон: отсутствие ключа enable_utm «выключено» не означает (не подтверждено) — 3 теперь None;
    # явный enable_utm false с пустым шаблоном (4) — по-прежнему False.
    assert g == {1: True, 2: True, 3: None, 4: False}


def test_utm_unknown_when_no_enable_utm_anywhere_and_no_template(tmp_path):
    raw = tmp_path / "raw"
    cabinet(raw, [_active(1, utm=None)])
    assert build_dataset(raw, *D)["groups"][0]["auto_utm"] is None


def test_utm_missing_silent_for_template_group(tmp_path):
    raw = tmp_path / "raw"
    cabinet(raw, [_active(1, utm="utm_source=vk"), _active(2, enable_utm=False)],
            banners_by_group={1: [{"id": 11, "status": "active", "urls": {"p": {"url": "https://s.ru/"}}}],
                              2: [{"id": 21, "status": "active", "urls": {"p": {"url": "https://s.ru/"}}}]})
    ds = build_dataset(raw, *D)
    bad = found(ds, "utm_missing")
    assert bad and bad[0]["evidence"]["without_utm"] == 1 and bad[0]["evidence"]["examples"] == [21]


# ---- минорное ----

def test_no_access_line_only_for_users_lists(tmp_path):
    raw = tmp_path / "raw"
    cabinet(raw, [_active(1)], daily={1: day_rows(2)})
    log = {"calls": [{"tool": "vk_ads_remarketing_pixels_list", "arguments": {}, "ok": False,
                      "error": "429 limits 0", "no_access": True}]}
    ds = build_dataset(raw, *D, collect_log=log)
    lines = limitations(ds, log, None)
    assert any(x.startswith("Не удалось получить vk_ads_remarketing_pixels_list") for x in lines)
    assert not any("Нет доступа к базам клиентов" in x for x in lines)


def test_failed_preflight_not_in_client_limitations(tmp_path):
    class Broken(SpendClient):
        def call(self, tool, arguments=None):
            if tool == "vk_ads_account_info":
                self._check(tool, arguments)
                raise McpError("vk_ads_account_info: HTTP 500", status=500)
            return super().call(tool, arguments)

    log = collect(Broken(groups(1)), tmp_path / "raw", "2026-09-01", "2026-09-30")
    assert log["errors"] == 0
    raw = tmp_path / "raw"
    ds = build_dataset(raw, *D)
    assert not any("vk_ads_account_info" in x for x in limitations(ds, log, None))


def test_e7_unknown_reason_wording():
    ds = make_ds([make_group(1)], [make_banner(11, 1), make_banner(12, 1)])
    assert status(checklist(ds), "E7")["reason"] == "кнопка не задана в объявлениях (call_to_action пустой)"


def test_quick_zero_spend_wording(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("VK_ADS_CONNECTION_URL", URL)
    monkeypatch.setattr(collect_mod, "McpClient", lambda url, **kw: SpendClient(groups(1), spent="0"))
    rc = collect_mod.main(["--workspace", str(tmp_path / "ws"), "--mode", "quick", "--date-from", "2026-09-01",
                           "--date-to", "2026-09-30"])
    assert rc == 5 and "кабинет не откручивал" in capsys.readouterr().out
