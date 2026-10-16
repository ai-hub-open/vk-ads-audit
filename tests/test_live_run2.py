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
