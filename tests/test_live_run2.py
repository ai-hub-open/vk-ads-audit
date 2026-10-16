"""Второй живой прогон (16.10.2026): кабинет без живых групп (все 56 удалены), сводка без дат, metrics=base,
второй режим ставки, or_status, авто-UTM без ключа — «не подтверждено», а не «выключено»."""
from __future__ import annotations

from scripts._workspace import SKILL_ROOT
from scripts.checks import run_checks
from scripts.collect import collect
from scripts.diagnose import diagnose, load_thresholds
from scripts.normalize import build_dataset, stats_from
from tests.helpers import make_banner, make_ds, make_group, stats
from tests.test_collect import FakeClient, SpendClient, groups, tools
from tests.test_live_run1 import D, day_rows
from tests.test_normalize import _active, _stat_call, base, cabinet

TH = load_thresholds()
SKILL = (SKILL_ROOT / "SKILL.md").read_text(encoding="utf-8")


def _ref(name):
    return (SKILL_ROOT / "references" / name).read_text(encoding="utf-8")


def res(ds):
    return diagnose(ds, TH)


def found(ds, rule):
    return [f for f in res(ds)["findings"] if f["rule"] == rule]


def checklist(ds):
    r = res(ds)
    return run_checks(ds, TH, r["findings"], r["evaluated"])


def row(cl, cid):
    return next(c for b in cl["blocks"] for c in b["checks"] if c["id"] == cid)


# ---- 5–6. metrics=base для дней, сводка без дат и с base,uniques ----

def test_summary_called_without_dates_and_with_base_uniques(tmp_path):
    client = SpendClient(groups(2))
    collect(client, tmp_path / "raw", "2026-09-01", "2026-09-30")
    summary = tools(client, "vk_ads_statistics_summary")
    assert summary and all("date_from" not in a and "date_to" not in a for a in summary)   # MCP с датами отказывает
    assert {a["metrics"] for a in summary} == {"base,uniques"}
    day = tools(client, "vk_ads_statistics_day")
    assert {a["entity"] for a in day} == {"users", "campaigns", "banners"}
    assert all(a["metrics"] == "base" and a["date_from"] == "2026-09-01" and a["date_to"] == "2026-09-30" for a in day)


def test_quick_mode_uses_base_metrics(tmp_path):
    client = FakeClient(groups(1))
    collect(client, tmp_path / "raw", "2026-10-06", "2026-10-13", mode="quick")
    assert {a["metrics"] for a in tools(client, "vk_ads_statistics_day")} == {"base"}


def test_base_only_block_gives_unknown_reach_not_zero():
    s = stats_from({"base": {"spent": "100", "shows": 1000, "clicks": 10, "goals": 1, "ctr": 1.0}}, None)
    assert s["reach"] is None and s["frequency"] is None
    assert s["spent"] == 100.0 and s["conversions"] == 1


def test_daily_base_rows_without_uniques_aggregate_to_unknown_reach(tmp_path):
    raw = tmp_path / "raw"
    rows = [{"date": "2026-09-01", "base": {"spent": "100", "shows": 1000, "clicks": 10, "goals": 1, "ctr": 1.0}}]
    cabinet(raw, [_active(1, created="2026-08-01 00:00:00")], daily={1: rows})
    g = build_dataset(raw, *D)["groups"][0]
    assert g["stats"]["spent"] == 100.0 and g["stats"]["reach"] is None and g["stats"]["frequency"] is None


def _summary_no_dates(raw, gid, shows, freq=2.0):
    from scripts.raw_store import write_call
    write_call(raw, "vk_ads_statistics_summary", {"entity": "campaigns", "ids": str(gid), "metrics": "base,uniques"},
               {"items": [{"id": gid, "total": {**base(spent="300", shows=shows, clicks=30),
                                                "uniques": {"reach": 1500, "frequency": freq}}}]})


def test_summary_without_dates_accepted_and_lifetime_rule_kept(tmp_path):
    new, old = tmp_path / "new", tmp_path / "old"
    for raw, created in ((new, "2026-09-01 10:00:00"), (old, "2026-08-01 10:00:00")):
        cabinet(raw, [_active(1, created=created)])
        _summary_no_dates(raw, 1, 3000)
        _stat_call(raw, "vk_ads_statistics_day", "campaigns", [1], [{"id": 1, "rows": day_rows(3)}])
    g_new = build_dataset(new, *D)["groups"][0]
    g_old = build_dataset(old, *D)["groups"][0]
    assert g_new["stats"]["frequency"] == 2.0 and g_new["stats"]["reach"] == 1500     # жизнь целиком в периоде
    assert g_old["stats"]["frequency"] is None and g_old["stats"]["spent"] == 300.0    # старше периода — не годится


def test_docs_say_summary_is_all_time_and_dates_not_passed():
    tools_doc = _ref("mcp-read-tools.md")
    for text in (SKILL, tools_doc):
        assert "баг MCP" not in text and "игнорирует" not in text
    assert "даты не передавай" in SKILL and "только за всё время" in tools_doc
    assert "metrics=base,uniques" in tools_doc and "metrics=base`" in tools_doc


# ---- 4. неудача, после которой тот же вызов прошёл, — не ограничение ----

def _fail(tool, args, err="HTTP 502"):
    return {"tool": tool, "arguments": args, "ok": False, "error": f"{tool}: {err}"}


def _not_got(lines, tool):
    return [x for x in lines if x.startswith(f"Не удалось получить {tool}")]


def test_failure_followed_by_same_successful_call_in_log_is_hidden(tmp_path):
    from scripts.render_report import limitations
    raw = tmp_path / "raw"
    cabinet(raw, [_active(1)], daily={1: day_rows(2)})
    args = {"entity": "campaigns", "ids": "1", "date_from": D[0], "date_to": D[1], "metrics": "base"}
    log = {"calls": [_fail("vk_ads_statistics_day", args),
                     {"tool": "vk_ads_statistics_day", "arguments": dict(args), "ok": True, "error": None}]}
    assert _not_got(limitations(build_dataset(raw, *D), log), "vk_ads_statistics_day") == []


def test_failure_recovered_by_raw_file_is_hidden_and_other_args_stay(tmp_path):
    from scripts.raw_store import mark_recovered, write_call
    from scripts.render_report import limitations
    raw = tmp_path / "raw"
    cabinet(raw, [_active(1)], daily={1: day_rows(2)})          # список групп лежит в raw/ с paginated=True
    write_call(raw, "vk_ads_banners_list", {"campaign_id": 1, "fields": "id,name"}, {"items": []})
    log = {"calls": [_fail("vk_ads_campaigns_list", {}),                                    # путь A: повтор удался
                     _fail("vk_ads_banners_list", {"campaign_id": "1", "fields": "id,name"}),  # тот же id строкой
                     _fail("vk_ads_banners_list", {"campaign_id": 2, "fields": "id,name"})]}   # другой вызов — сбой
    marked = mark_recovered(log, raw)
    assert [c.get("recovered", False) for c in marked["calls"]] == [True, True, False]
    lines = limitations(build_dataset(raw, *D), marked)
    assert _not_got(lines, "vk_ads_campaigns_list") == []
    assert len(_not_got(lines, "vk_ads_banners_list")) == 1
    assert log["calls"][0].get("recovered") is None                 # исходный журнал не меняется


def test_render_cli_reads_raw_to_hide_recovered_failures(tmp_path):
    import json
    from scripts.render_report import main as render_main
    ws = tmp_path / "ws"
    cabinet(ws / "raw", [_active(1)], daily={1: day_rows(2)})
    ds = build_dataset(ws / "raw", *D)
    (ws / "dataset.json").write_text(json.dumps(ds, ensure_ascii=False), encoding="utf-8")
    (ws / "findings.json").write_text(json.dumps(res(ds), ensure_ascii=False), encoding="utf-8")
    (ws / "collect_log.json").write_text(json.dumps({"calls": [_fail("vk_ads_ad_plans_list", {})]}),
                                         encoding="utf-8")
    assert render_main(["--workspace", str(ws), "--slug", "t"]) == 0
    assert "Не удалось получить vk_ads_ad_plans_list" not in (ws / "АУДИТ_t.html").read_text(encoding="utf-8")


def test_no_access_then_success_is_not_no_access(tmp_path):
    from scripts.normalize import main as norm_main
    import json
    from scripts.raw_store import write_call
    ws = tmp_path / "ws"
    cabinet(ws / "raw", [_active(1)], daily={1: day_rows(2)})
    write_call(ws / "raw", "vk_ads_users_lists_list", {}, {"items": []})
    (ws / "collect_log.json").write_text(json.dumps({"calls": [
        {**_fail("vk_ads_users_lists_list", {}, "429"), "no_access": True}]}), encoding="utf-8")
    assert norm_main(["--workspace", str(ws), "--date-from", D[0], "--date-to", D[1]]) == 0
    ds = json.loads((ws / "dataset.json").read_text(encoding="utf-8"))
    assert ds["coverage"]["users_lists_no_access"] is False


# ---- 9. авто-UTM: нет ключа enable_utm и шаблона — «не подтверждено» (None), а не «выключено» ----

SITE = {"p": {"url": "https://shop.ru/catalog"}}
SITE_UTM = {"p": {"url": "https://shop.ru/catalog?utm_source=vk"}}


def test_auto_utm_absent_key_is_unknown_not_false(tmp_path):
    raw = tmp_path / "raw"
    cabinet(raw, [_active(1, enable_utm=True), _active(2, utm="utm_source=vk_ads&utm_campaign={{campaign_id}}"),
                  _active(3), _active(4, utm=None), _active(5, enable_utm=False)])
    g = {x["id"]: x["auto_utm"] for x in build_dataset(raw, *D)["groups"]}
    assert g == {1: True, 2: True, 3: None, 4: None, 5: False}   # явный false — однозначен, отсутствие ключа — нет


def _utm_cabinet(raw, url):
    cabinet(raw, [_active(1, enable_utm=True), _active(2)], daily={1: day_rows(2), 2: day_rows(2)},
            banners_by_group={1: [{"id": 11, "status": "active", "urls": SITE}],
                              2: [{"id": 21, "status": "active", "urls": url}]})
    return build_dataset(raw, *D)


def test_utm_missing_silent_and_a3_unknown_for_unconfirmed_group(tmp_path):
    from scripts.render_report import limitations
    ds = _utm_cabinet(tmp_path / "raw", SITE)
    assert found(ds, "utm_missing") == []
    cl = checklist(ds)
    a3 = row(cl, "A3")
    assert a3["status"] == "unknown" and "не подтверждена API" in a3["reason"]
    lines = limitations(ds, None, cl)
    assert lines.count("Авто-UTM у 1 группы не подтверждена API — проверьте в кабинете.") == 1
    assert not any("«UTM-метки на объявлениях»" in x for x in lines)      # без дубля «Не проверено …»


def test_unconfirmed_group_with_utm_in_link_is_fine(tmp_path):
    from scripts.render_report import limitations
    ds = _utm_cabinet(tmp_path / "raw", SITE_UTM)
    cl = checklist(ds)
    assert found(ds, "utm_missing") == [] and row(cl, "A3")["status"] == "pass"
    assert not any("Авто-UTM" in x for x in limitations(ds, None, cl))


def test_explicit_false_still_fires_even_with_unconfirmed_neighbours(tmp_path):
    from scripts.render_report import limitations
    raw = tmp_path / "raw"
    cabinet(raw, [_active(1, enable_utm=False), _active(2), _active(3)],
            banners_by_group={1: [{"id": 11, "status": "active", "urls": SITE}],
                              2: [{"id": 21, "status": "active", "urls": SITE}],
                              3: [{"id": 31, "status": "active", "urls": SITE}]})
    ds = build_dataset(raw, *D)
    f = found(ds, "utm_missing")
    assert f and f[0]["evidence"]["examples"] == [11]
    cl = checklist(ds)
    assert row(cl, "A3")["status"] == "fail"
    assert "Авто-UTM у 2 групп не подтверждена API — проверьте в кабинете." in limitations(ds, None, cl)


def test_utm_docs_updated():
    rules, tools_doc = _ref("diagnostic-rules.md"), _ref("mcp-read-tools.md")
    assert "не подтверждена API" in rules and "нет ключа = выключено" not in tools_doc
    assert "Авто-UTM у N групп не подтверждена API" in _ref("limitations.md")
