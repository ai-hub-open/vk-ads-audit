"""Третий живой прогон (16.10.2026): большой боевой кабинет на MCP 0.12.0 — группы в остановленных кампаниях,
бюджеты, изменённые после периода, слои по relations сегментов, объявления по выборке (путь A), конверсии по «Результату»,
гейты, не прячущие находки, кнопка в textblocks, охват без смысла, человеческие подписи в отчёте.
Все данные синтетические: id, названия и суммы — не из кабинета клиента."""
from __future__ import annotations

from scripts._workspace import SKILL_ROOT
from scripts.checks import run_checks
from scripts.diagnose import diagnose, load_thresholds
from scripts.normalize import build_dataset
from tests.helpers import make_banner, make_ds, make_group, stats
from tests.test_live_run1 import D, day_rows
from tests.test_normalize import _active, cabinet

TH = load_thresholds()


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


def daily(n=30, spent=100.0, shows=1000, clicks=10, conv=1, start=1):
    return [{"date": f"2026-09-{d:02d}", **stats(spent, shows, clicks, conv)} for d in range(start, start + n)]


# ---- 2.1. Сработавшая находка — всегда ❌: гейт unknown / na заменяет только ✅ ----

def test_unknown_gate_does_not_hide_fired_stale_creatives():
    old = {"age_days": 400, "created": "2025-08-01 00:00:00"}
    g1 = make_group(1, stats=stats(5000, 50000, 500, 50))
    g2 = make_group(2, stats=stats(4000, 40000, 400, 40))
    bs = [make_banner(11, 1, **old), make_banner(12, 1, **old), make_banner(21, 2), make_banner(22, 2, **old)]
    ds = make_ds([g1, g2], bs)                     # у объявления 21 нет даты создания — гейт «unknown»
    assert found(ds, "stale_creatives")
    assert row(checklist(ds), "E4")["status"] == "fail"


def test_partial_gate_does_not_hide_fired_no_cta_text():
    g1, g2 = make_group(1), make_group(2)
    bs = [make_banner(11, 1, text="Лучшие витамины"), make_banner(12, 1, text="Лучшие витамины")]
    ds = make_ds([g1, g2], bs, coverage={"path": "A", "banners": True, "banner_groups": [1], "summary": True,
                                         "stats": True, "pixels": True, "metrika": False})
    assert found(ds, "no_cta_text")
    assert row(checklist(ds), "E6")["status"] == "fail"


def test_gate_still_replaces_pass_when_nothing_fired():
    g1, g2 = make_group(1), make_group(2)
    bs = [make_banner(11, 1, text="Запишитесь сегодня"), make_banner(12, 1, text="Узнайте цену")]
    ds = make_ds([g1, g2], bs, coverage={"path": "A", "banners": True, "banner_groups": [1], "summary": True,
                                         "stats": True, "pixels": True, "metrika": False})
    assert found(ds, "no_cta_text") == []
    assert row(checklist(ds), "E6")["status"] == "unknown"


# ---- 1.1. Группа в остановленной кампании — на паузе, а не активна ----

PLANS = [{"id": 7, "name": "Кампания идёт", "status": "active", "budget_day_rub": None, "budget_total_rub": None,
          "has_budget": None},
         {"id": 8, "name": "Кампания стоит", "status": "blocked", "budget_day_rub": None, "budget_total_rub": None,
          "has_budget": None}]


def test_normalize_marks_group_of_stopped_plan(tmp_path):
    raw = tmp_path / "raw"
    from scripts.raw_store import write_call
    cabinet(raw, [_active(1, ad_plan_id=7), _active(2, ad_plan_id=8), _active(3, ad_plan_id=7,
                                                                           issues=[{"code": "AD_PLAN_STOPPED"}])])
    write_call(raw, "vk_ads_ad_plans_list", {"paginated": True, "page": 2},
               {"items": [{"id": 8, "name": "Стоит", "status": "blocked"}]})
    by = {g["id"]: g for g in build_dataset(raw, *D)["groups"]}
    from scripts.features import is_active_group
    assert by[2]["plan_status"] == "blocked"
    assert [is_active_group(by[i], TH["active_statuses"]) for i in (1, 2, 3)] == [True, False, False]


def test_ad_plan_stopped_is_not_blocking_issue():
    assert "AD_PLAN_STOPPED" not in TH["blocking_issue_codes"]
    g = make_group(1, issues=["AD_PLAN_STOPPED"])
    ds = make_ds([g])
    assert found(ds, "blocking_issues") == []
    assert row(checklist(ds), "D4")["status"] == "na"      # активных групп нет — пауза кампании не проблема


def test_no_money_still_blocking():
    ds = make_ds([make_group(1, issues=["NO_MONEY"])])
    assert [f["evidence"]["issues"] for f in found(ds, "blocking_issues")] == [["NO_MONEY"]]


def test_duplicates_only_among_really_running_groups():
    same = make_group(1)["targeting_key"]
    gs = [make_group(1), make_group(2, targeting_key=same, ad_plan_id=8, plan_status="blocked"),
          make_group(3, targeting_key=same, issues=["AD_PLAN_STOPPED"])]
    assert found(make_ds(gs, plans=PLANS), "duplicate_targeting") == []
    gs.append(make_group(4, targeting_key=same))
    assert [f["evidence"]["groups"] for f in found(make_ds(gs, plans=PLANS), "duplicate_targeting")] == [[1, 4]]


def _leader(gid=1, **kw):
    kw.setdefault("budget_day_rub", 1000.0)
    return make_group(gid, stats=stats(30000, 300000, 3000, 300), daily=daily(30, 1000.0, 10000, 100, 10), **kw)


def _cheap_ref():
    return make_group(9, stats=stats(30000, 300000, 3000, 30), daily=daily(30, 1000.0, 10000, 100, 1),
                      budget_day_rub=5000.0)


def test_scale_leader_skips_group_of_stopped_plan():
    ds = make_ds([_leader(1, plan_status="blocked", ad_plan_id=8), _cheap_ref()], plans=PLANS)
    assert found(ds, "scale_leader") == []
    ds = make_ds([_leader(1), _cheap_ref()], plans=PLANS)
    assert [f["entity"]["id"] for f in found(ds, "scale_leader")] == [1]


def test_quick_flags_skip_group_of_stopped_plan():
    from scripts.quick import quick_summary
    rows = [{"date": f"2026-09-{d:02d}", **stats(100, 1000, 10, 1)} for d in range(1, 8)]
    g = make_group(1, daily=rows + [{"date": "2026-09-08", **stats(0, 0, 0, 0)}], issues=["AD_PLAN_STOPPED"])
    out = quick_summary(make_ds([g, make_group(2, daily=rows + [{"date": "2026-09-08", **stats(50, 500, 5, 1)}])]),
                        "2026-09-08")
    assert [x["flags"] for x in out["groups"]] == [[], []]


def test_report_shows_paused_by_plan_humanly():
    from scripts.render_report import render
    ds = make_ds([make_group(1, issues=["AD_PLAN_STOPPED"]), make_group(2), make_group(3, status="blocked")])
    html = render(ds, {**res(ds), "checklist": checklist(ds)})
    assert "на паузе (кампания остановлена)" in html and "активна" in html
    assert "<td>active</td>" not in html and "<td>blocked</td>" not in html


# ---- 1.2. Лидер и бюджет, изменённый после периода ----

def _changed_cabinet(raw, group_upd, plan_upd):
    from scripts.raw_store import write_call
    write_call(raw, "vk_ads_ad_plans_list", {"paginated": True},
               {"items": [{"id": 7, "name": "План", "status": "active", "updated": plan_upd}]})
    write_call(raw, "vk_ads_campaigns_list", {"paginated": True},
               {"items": [_active(1, ad_plan_id=7, updated=group_upd), _active(2, ad_plan_id=7,
                                                                             updated="2026-08-01 10:00:00")]})


def test_normalize_flags_changes_after_period(tmp_path):
    for i, (gu, pu, want) in enumerate([("2026-10-01 09:00:00", "2026-10-02 10:00:00", True),   # группа позже конца
                                       ("2026-09-20 09:00:00", "2026-10-01 00:00:01", True),   # кампания позже конца
                                       ("2026-09-30 23:59:59", "2026-09-30 10:00:00", False)]):
        raw = tmp_path / str(i) / "raw"
        _changed_cabinet(raw, gu, pu)
        ds = build_dataset(raw, *D)
        g1 = ds["groups"][0]
        assert g1["updated"] == gu and ds["plans"][0]["updated"] == pu
        assert g1["changed_after_period"] is want and ds["changed_after_period"] is want, (gu, pu)


def test_scale_leader_budget_cut_after_period_is_not_plus_50():
    lead = _leader(1, changed_after_period=True, budget_day_rub=500.0)        # расход 1000 ₽ в день, бюджет 500
    ds = make_ds([lead, _cheap_ref()])
    assert found(ds, "scale_leader") == []
    f = found(ds, "leader_budget_changed")
    assert len(f) == 1 and f[0]["level"] == "white" and f[0]["effect_rub"] == 0
    assert f[0]["title"] == "Бюджет менялся после периода: сейчас 500 ₽ при расходе 1 000 ₽ в день — проверьте актуальность"
    assert "50 %" not in f[0]["action"] and "упирается" not in f[0]["title"]


def test_scale_leader_unchanged_or_budget_not_cut_stays_green():
    for g in (_leader(1, changed_after_period=False, budget_day_rub=900.0),      # не меняли — как было
              _leader(1, changed_after_period=True, budget_day_rub=1050.0)):     # меняли, но бюджет не ниже расхода
        ds = make_ds([g, _cheap_ref()])
        assert [f["level"] for f in found(ds, "scale_leader")] == ["green"]
        assert found(ds, "leader_budget_changed") == []


def test_report_warns_when_cabinet_changed_after_period():
    from scripts.render_report import render
    text = "Кабинет менялся после конца периода: статусы и бюджеты показаны на дату сбора"
    ds = make_ds([make_group(1)], changed_after_period=True)
    assert text in render(ds, {**res(ds), "checklist": checklist(ds)})
    ds = make_ds([make_group(1)])
    assert text not in render(ds, {**res(ds), "checklist": checklist(ds)})
