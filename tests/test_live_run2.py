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


# ---- 7. second_price_mean — ручная ставка, не автостратегия ----

def _manual(i=1, **kw):
    return make_group(i, autobidding_mode="second_price_mean", **kw)


def test_second_price_mean_is_manual_bid_in_dictionary():
    from scripts.diagnose import _is_auto, _is_manual, bidding_label
    g = _manual()
    assert "second_price_mean" in TH["manual_bidding_modes"] and not _is_auto(g, TH) and _is_manual(g, TH)
    assert bidding_label("second_price_mean", TH) == "ручная ставка"
    assert bidding_label("max_goals", TH) == "автостратегия"
    assert bidding_label("something_new", TH) is None and bidding_label(None, TH) is None


def test_auto_strategy_rules_skip_manual_bid():
    daily = [{"date": f"2026-09-{d:02d}", **stats(100, 1000, 10, 0)} for d in range(1, 31)]
    g = _manual(stats=stats(3000, 30000, 300, 2), daily=daily, budget_day_rub=400.0)
    ds = make_ds([g])
    assert found(ds, "learning_starved") == []
    assert [f["level"] for f in found(ds, "low_budget")] == ["white"]          # 300–500 ₽ — гигиена, не «обучение»
    assert row(checklist(ds), "D3")["status"] == "na"                          # автостратегий нет — нечего проверять


def test_manual_bid_first_week_gives_no_new_orange():
    w7 = stats(200, 2000, 20, 1)
    young = [{"date": f"2026-09-{d:02d}", **stats(100, 1000, 10, 0)} for d in range(25, 31)]
    g = _manual(max_price_rub=80.0, created="2026-09-25 00:00:00", daily=young, stats=stats(600, 6000, 60, 0),
                windows={"7": w7, "14": None, "30": None})
    ds = make_ds([g])
    assert found(ds, "cap_too_early") == []                 # ручная ставка ≠ «предельная цена» на старте
    d_rules = {"low_budget", "cap_too_early", "learning_starved", "blocking_issues"}
    assert not [f for f in res(ds)["findings"] if f["rule"] in d_rules and f["level"] == "orange"]


def test_report_names_bidding_mode_humanly():
    from scripts.render_report import render
    ds = make_ds([_manual(1), make_group(2, autobidding_mode="max_goals")])
    html = render(ds, {**res(ds), "checklist": checklist(ds)})
    assert "ручная ставка" in html and "автостратегия" in html and "second_price_mean" not in html


def test_bidding_docs():
    rules, tools_doc = _ref("diagnostic-rules.md"), _ref("mcp-read-tools.md")
    assert "`second_price_mean`" in rules and "ручная ставка" in rules and "`manual_bidding_modes`" in rules
    assert "second_price_mean" in tools_doc and "значение ручного режима неизвестно" not in tools_doc


# ---- 8. or_status (статус ОРД) собираем рядом с ord_marker — справочно, без новых ❌ ----

def test_banner_fields_include_or_status(tmp_path):
    from scripts.collect import BANNER_FIELDS
    names = BANNER_FIELDS.split(",")
    assert "or_status" in names and "ord_marker" in names and not {"erid", "ord_token", "ord_erid"} & set(names)
    client = FakeClient(groups(1))
    collect(client, tmp_path / "raw", "2026-09-01", "2026-09-30")
    assert all("or_status" in a["fields"].split(",") for a in tools(client, "vk_ads_banners_list"))


def test_or_status_normalized_as_reference(tmp_path):
    raw = tmp_path / "raw"
    cabinet(raw, [_active(1)], banners_by_group={1: [
        {"id": 11, "status": "active", "ord_marker": "2Vtzqx1", "or_status": "registered"},
        {"id": 12, "status": "active", "ord_marker": "2Vtzqx2"}]})
    by = {b["id"]: b for b in build_dataset(raw, *D)["banners"]}
    assert by[11]["ord_status"] == "registered" and by[12]["ord_status"] is None


def test_or_status_unknown_when_key_never_seen(tmp_path):
    raw = tmp_path / "raw"
    cabinet(raw, [_active(1)], banners_by_group={1: [{"id": 11, "status": "active", "ord_marker": "X"}]})
    assert build_dataset(raw, *D)["banners"][0]["ord_status"] is None


def test_or_status_does_not_change_a2():
    bs = [make_banner(11, 1, erid="2Vtzqx1", ord_status="not_registered"),
          make_banner(12, 1, erid="2Vtzqx2", ord_status="unknown_value")]
    ds = make_ds([make_group(1)], bs)
    assert found(ds, "ord_missing") == [] and row(checklist(ds), "A2")["status"] == "pass"


def test_or_status_shown_in_report_as_reference():
    from scripts.render_report import render
    bs = [make_banner(11, 1, erid="A", ord_status="registered"), make_banner(12, 1, erid="B", ord_status="registered"),
          make_banner(13, 1, erid="C", ord_status="pending")]
    ds = make_ds([make_group(1)], bs)
    html = render(ds, {**res(ds), "checklist": checklist(ds)})
    assert "Справочно" in html and "or_status" in html and "registered — 2" in html and "pending — 1" in html
    plain = make_ds([make_group(1)], [make_banner(11, 1, erid="A")])
    assert "or_status" not in render(plain, {**res(plain), "checklist": checklist(plain)})


def test_or_status_documented():
    tools_doc, rules = _ref("mcp-read-tools.md"), _ref("diagnostic-rules.md")
    assert "`or_status`" in tools_doc and "ORD_STATUS_KEYS" in tools_doc and "`or_status`" in rules


# ---- 2. неиспользуемые сегменты — одна сводная карточка, а не по карточке на сегмент ----

def _dead(i, segs, status="deleted"):
    return make_group(i, status=status, segment_ids=list(segs))


def test_unused_segments_single_summary_for_cabinet_without_live_groups():
    segs = [{"id": 100 + i, "name": f"s{i}"} for i in range(24)]
    groups_ = [_dead(1, [100, 101, 102]), _dead(2, range(103, 110)),
               _dead(3, range(110, 120), status="active") | {"issues": ["ARCHIVED"]}]
    ds = make_ds(groups_, segments=segs)
    fs = found(ds, "unused_segments")
    assert len(fs) == 1
    f = fs[0]
    assert f["level"] == "white" and f["entity"]["type"] == "account" and f["check"] is None
    assert f["title"] == "24 сегмента не используются ни в одной живой группе"
    assert f["evidence"]["segments_unused"] == 24 and f["evidence"]["only_in_deleted_or_archived_groups"] == 20
    assert f["evidence"]["examples"] == [100, 101, 102, 103, 104]


def test_unused_segments_counts_only_unused_and_plural():
    g = make_group(1, segment_ids=[5], excluded_segment_ids=[6])
    ds = make_ds([g], segments=[{"id": 5, "name": "a"}, {"id": 6, "name": "минус"}, {"id": 7, "name": "никому"}])
    f = found(ds, "unused_segments")
    assert len(f) == 1 and f[0]["title"] == "1 сегмент не используется ни в одной живой группе"
    assert f[0]["evidence"] == {"segments_unused": 1, "only_in_deleted_or_archived_groups": 0, "examples": [7]}
    used = make_ds([make_group(1, segment_ids=[5])], segments=[{"id": 5, "name": "a"}])
    assert found(used, "unused_segments") == []


# ---- 1. кабинет без живых групп: «— нечего проверять», а не ложные «поле не получено» ----

NO_ACTIVE = "нечего проверять: нет активных групп"


class DeadCabinet(FakeClient):
    """Второй живой прогон: все группы удалены, кампании и сегменты остались, базы старые."""

    def paginate(self, tool, arguments=None, **kw):
        if tool == "vk_ads_ad_plans_list":
            self._check(tool, arguments)
            return [{"id": 7, "name": "Кампания 1", "status": "active", "budget_limit_day": 0}]
        if tool == "vk_ads_remarketing_segments_list":
            self._check(tool, arguments)
            return [{"id": 500 + i, "name": f"Посетители {i}"} for i in range(4)]
        if tool == "vk_ads_users_lists_list":
            self._check(tool, arguments)
            return [{"id": 9, "name": "Клиенты", "updated": "2025-01-01 00:00:00"}]
        if tool == "vk_ads_remarketing_pixels_list":
            self._check(tool, arguments)
            return [{"id": 1, "name": "px"}]
        return super().paginate(tool, arguments, **kw)


def _dead_dataset(tmp_path):
    gs = [{"id": i, "name": f"Группа {i}", "status": "deleted", "ad_plan_id": 7, "objective": "site_conversions",
           "targetings": {"geo": {"regions": [1]}, "segments": [500, 501]}, "issues": [{"code": "ARCHIVED"}]}
          for i in range(1, 4)]
    client = DeadCabinet(gs)
    log = collect(client, tmp_path / "raw", "2026-09-01", "2026-09-30")
    assert not tools(client, "vk_ads_banners_list") and not tools(client, "vk_ads_statistics_day")
    return build_dataset(tmp_path / "raw", *D, collect_log=log), log


def test_dead_cabinet_every_check_is_nothing_to_check(tmp_path):
    ds, _ = _dead_dataset(tmp_path)
    cl = checklist(ds)
    rows = [c for b in cl["blocks"] for c in b["checks"]]
    assert {c["status"] for c in rows} == {"na"}, [(c["id"], c["status"], c["reason"]) for c in rows
                                                   if c["status"] != "na"]
    for c in rows:
        if c["id"] != "C13":                                         # LAL-сегментов нет — своя причина
            assert "нет активных групп" in c["reason"], (c["id"], c["reason"])
    assert row(cl, "F5")["reason"] == NO_ACTIVE


def test_dead_cabinet_total_score_hidden(tmp_path):
    ds, _ = _dead_dataset(tmp_path)
    cl = checklist(ds)
    assert cl["total"] is None and cl["idle"] == "кабинет не откручивает: нет живых групп"


def test_dead_cabinet_no_false_findings_or_limitations(tmp_path):
    from scripts.render_report import limitations, render
    ds, log = _dead_dataset(tmp_path)
    r = res(ds)
    assert {f["rule"] for f in r["findings"]} == {"unused_segments"}       # нет «база устарела» и «названия»
    cl = checklist(ds)
    lines = limitations(ds, log, cl)
    for bad in ("Не проверено", "Объявления не собирались", "Статистика за период не собрана",
                "объявления разобраны только"):
        assert not any(bad in x for x in lines), bad
    html = render(ds, {**r, "checklist": cl})
    assert "кабинет не откручивает" in html and "общий балл" not in html


def test_diagnose_cli_prints_idle_instead_of_score(tmp_path, capsys):
    import json
    from scripts.diagnose import main as diag_main
    ws = tmp_path / "ws"
    ds, _ = _dead_dataset(ws)
    (ws / "dataset.json").write_text(json.dumps(ds, ensure_ascii=False), encoding="utf-8")
    assert diag_main(["--workspace", str(ws)]) == 0
    out = capsys.readouterr().out
    assert "кабинет не откручивает" in out and "балл" not in out


def test_paused_only_groups_checks_say_no_active_groups():
    g = make_group(1, status="blocked", features={"keys": ["geo"]})
    cl = checklist(make_ds([g], [make_banner(11, 1, status="blocked")]))
    for cid in ("A1", "A2", "A3", "B2", "C1", "C10", "C11", "C15", "C16", "D1", "D3", "E1", "E3", "F4", "G1"):
        assert (row(cl, cid)["status"], row(cl, cid)["reason"]) == ("na", NO_ACTIVE), cid
    assert cl["total"] is not None                                    # живая группа есть — балл показываем


def test_na_gate_never_hides_a_real_finding():
    g = make_group(1, status="blocked")
    rejected = make_banner(11, 1, status="blocked", moderation_status="banned")
    cl = checklist(make_ds([g], [rejected]))
    assert row(cl, "E8")["status"] == "fail"                          # отклонённое в паузной группе — ❌ остаётся


def test_active_groups_without_active_banners():
    g = make_group(1)
    cl = checklist(make_ds([g], [make_banner(11, 1, status="blocked"), make_banner(12, 1, status="blocked")]))
    for cid in ("A2", "A3", "E1", "E2", "E4", "E5", "E6", "E7"):
        assert (row(cl, cid)["status"], row(cl, cid)["reason"]) == ("na", "нечего проверять: нет активных объявлений"), cid
    assert row(cl, "E3")["status"] == "fail"                          # «группа включена, крутить нечего» — 🟠 остаётся


def test_naming_counts_only_live_groups_and_their_plans():
    live = make_group(1, name="холод_велоспорт_Мск_25-45", ad_plan_id=7)
    dead = [make_group(i, name=f"Группа {i}", status="deleted", ad_plan_id=8) for i in range(2, 9)]
    plans = [{"id": 7, "name": "холод_интересы_Мск", "status": "active", "budget_day_rub": None,
              "budget_total_rub": None, "has_budget": None},
             {"id": 8, "name": "Кампания 1", "status": "active", "budget_day_rub": None, "budget_total_rub": None,
              "has_budget": None}]
    ds = make_ds([live] + dead, plans=plans)
    assert found(ds, "naming") == [] and row(checklist(ds), "B7")["status"] == "pass"
    only_dead = make_ds(dead, plans=plans)
    assert found(only_dead, "naming") == []
    assert (row(checklist(only_dead), "B7")["status"], row(checklist(only_dead), "B7")["reason"]) == ("na", NO_ACTIVE)


# ---- 3. нулевой расход: нет живых групп — стоп, есть живые — отчёт с плашкой ----

def _norm(ws, *extra):
    from scripts.normalize import main as norm_main
    return norm_main(["--workspace", str(ws), "--date-from", D[0], "--date-to", D[1], "--path", "A", *extra])


def test_zero_spend_and_no_live_groups_stops_without_dataset(tmp_path, capsys):
    from scripts.normalize import spend_state
    ws = tmp_path / "ws"
    cabinet(ws / "raw", [{"id": i, "name": f"G{i}", "status": "deleted"} for i in range(1, 4)])
    assert spend_state(build_dataset(ws / "raw", *D)) == "idle"
    assert _norm(ws) == 5
    out = capsys.readouterr().out
    assert "нет живых групп" in out and "строить не на чем" in out
    assert not (ws / "dataset.json").exists()


def test_zero_spend_with_archived_only_also_stops(tmp_path):
    ws = tmp_path / "ws"
    cabinet(ws / "raw", [_active(1, issues=[{"code": "ARCHIVED"}])], daily={1: []})
    assert _norm(ws) == 5


def test_spend_on_archived_groups_builds_report_without_score(tmp_path):
    ws = tmp_path / "ws"
    cabinet(ws / "raw", [_active(1, issues=[{"code": "ARCHIVED"}])], daily={1: day_rows(2)})
    assert _norm(ws) == 0 and (ws / "dataset.json").exists()


def test_zero_spend_with_live_groups_builds_report_with_banner(tmp_path):
    import json
    from scripts.render_report import render
    ws = tmp_path / "ws"
    cabinet(ws / "raw", [_active(1)], daily={1: day_rows(3, spent="0", shows=0, clicks=0, goals=0)})
    assert _norm(ws) == 0
    ds = json.loads((ws / "dataset.json").read_text(encoding="utf-8"))
    assert ds["zero_spend"] is True
    html = render(ds, {**res(ds), "checklist": checklist(ds)})
    assert "Расход за период 0 ₽" in html and html.index("Расход за период 0 ₽") < html.index("Чек-лист")


def test_zero_spend_flag_only_when_spend_is_known(tmp_path):
    raw = tmp_path / "raw"
    cabinet(raw, [_active(1), _active(2)])                               # статистику не собирали — не «0 ₽»
    assert build_dataset(raw, *D)["zero_spend"] is False
    raw2 = tmp_path / "raw2"
    cabinet(raw2, [_active(1), _active(2)])
    _stat_call(raw2, "vk_ads_statistics_day", "campaigns", [1], [])          # группа 2 без статистики — неизвестно
    assert build_dataset(raw2, *D)["zero_spend"] is False
    raw3 = tmp_path / "raw3"
    cabinet(raw3, [_active(1)], daily={1: day_rows(2)})
    assert build_dataset(raw3, *D)["zero_spend"] is False


def test_skill_explains_zero_spend_stop():
    assert "нет живых групп" in SKILL and "кодом 5" in SKILL and "Расход за период 0 ₽" in SKILL
