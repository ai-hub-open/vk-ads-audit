from __future__ import annotations

from scripts.checks import BLOCKS, run_checks
from scripts.diagnose import CHECK_OF, diagnose, load_thresholds, main
from scripts.questions import build_questions
from tests.helpers import make_banner, make_ds, make_group, stats

TH = load_thresholds()


def checklist(ds):
    res = diagnose(ds, TH)
    return run_checks(ds, TH, res["findings"], res["evaluated"])


def status(cl, cid):
    return next(c for b in cl["blocks"] for c in b["checks"] if c["id"] == cid)["status"]


def test_blocks_and_scores():
    cl = checklist(make_ds([make_group(1, budget_day_rub=200.0)], [make_banner(11, 1), make_banner(12, 1)]))
    assert [b["id"] for b in cl["blocks"]] == [b for b, _ in BLOCKS]
    assert status(cl, "D1") == "fail" and status(cl, "D4") == "pass"
    d = next(b for b in cl["blocks"] if b["id"] == "D")
    n, m = d["score"]
    assert m == sum(1 for c in d["checks"] if c["status"] in ("pass", "fail")) and n < m
    assert cl["total"] == [sum(b["score"][0] for b in cl["blocks"]), sum(b["score"][1] for b in cl["blocks"])]


def test_unknown_fields_are_not_fails():
    cl = checklist(make_ds([make_group(1)], [make_banner(11, 1), make_banner(12, 1)]))
    for cid in ("A2", "B6", "C4", "C6", "C7", "C10", "C15", "C16", "E1"):
        assert status(cl, cid) in ("unknown", "na"), cid


def test_funnels():
    gs = [make_group(1, objective="leadads", layer="warm"), make_group(2, objective="site_conversions")]
    cl = checklist(make_ds(gs, [make_banner(11, 1, urls=["https://vk.com/app5898182_-1#s=senler"])]))
    assert status(cl, "B1.leadform") == "pass" and status(cl, "B1.retargeting") == "pass"
    assert status(cl, "B1.site") == "pass" and status(cl, "B1.subscription") == "ask"
    assert status(cl, "B1.senler") == "pass"


def test_f4_unknown_on_short_period():
    ds = make_ds([make_group(1)], period={"from": "2026-09-21", "to": "2026-09-30", "days": 10})
    assert status(checklist(ds), "F4") == "unknown"


def test_metrika_block_unknown_without_metrika():
    assert status(checklist(make_ds([make_group(1)])), "G1") == "unknown"


def test_old_dataset_without_features():
    g = make_group(1)
    for k in ("features", "layer", "windows", "max_price_rub", "auto_utm"):
        g.pop(k)
    ds = make_ds([g], [make_banner(11, 1), make_banner(12, 1)])
    ds.pop("totals_windows")
    for b in ds["banners"]:
        for k in ("cta", "text", "urls", "ratios", "has_video", "content_ids", "age_days", "erid"):
            b.pop(k)
    res = diagnose(ds, TH)
    assert run_checks(ds, TH, res["findings"], res["evaluated"])["blocks"]


def test_questions_flag_relevant():
    qs = {q["id"]: q for q in build_questions(make_ds([make_group(1, objective="leadads")]), TH, [])}
    assert qs["leadform"]["important"] is True and qs["community"]["important"] is False
    assert all(q["text"] for q in qs.values())


def test_main_writes_checklist(tmp_path):
    import json
    ws = tmp_path / "ws"
    ws.mkdir()
    (ws / "dataset.json").write_text(json.dumps(make_ds([make_group(1)])), encoding="utf-8")
    assert main(["--workspace", str(ws)]) == 0
    out = json.loads((ws / "findings.json").read_text(encoding="utf-8"))
    assert out["checklist"]["blocks"] and out["questions"]


def _bare(ds, *, banner_keys=(), group_keys=()):
    for g in ds["groups"]:
        for k in group_keys:
            g.pop(k, None)
    for b in ds["banners"]:
        for k in banner_keys:
            b.pop(k, None)
    return ds


def test_layer_checks_unknown_without_targetings():
    # у группы есть расход: без него B3 — «нет расхода за период» раньше гейта слоёв (повторное ревью fix/live-run-2)
    g = make_group(1, stats=stats(500, 5000, 50, 1))
    ds = _bare(make_ds([g], [make_banner(11, 1), make_banner(12, 1)]), group_keys=("layer",))
    cl = checklist(ds)
    for cid in ("B1.retargeting", "B2", "B3", "B4"):
        assert status(cl, cid) == "unknown", cid


def test_a3_unknown_without_auto_utm():
    ds = _bare(make_ds([make_group(1)], [make_banner(11, 1), make_banner(12, 1)]), group_keys=("auto_utm",))
    assert status(checklist(ds), "A3") == "unknown"


def test_c11_unknown_without_keys():
    g = make_group(1)
    g["features"] = {k: v for k, v in (g.get("features") or {}).items() if k != "keys"}
    assert status(checklist(make_ds([g], [make_banner(11, 1), make_banner(12, 1)])), "C11") == "unknown"


def test_e1_unknown_when_no_banner_classified():
    ds = make_ds([make_group(1)], [make_banner(11, 1, ratios=[], has_video=False), make_banner(12, 1, ratios=[], has_video=False)])
    assert status(checklist(ds), "E1") == "unknown"


def test_e2_unknown_on_partial_banner_coverage_or_no_content():
    ds = make_ds([make_group(1), make_group(2)], [make_banner(11, 1, content_ids=[1]), make_banner(12, 1, content_ids=[2])])
    ds["coverage"]["banner_groups"] = [1]
    assert status(checklist(ds), "E2") == "unknown"
    ds2 = make_ds([make_group(1)], [make_banner(11, 1, content_ids=[]), make_banner(12, 1, content_ids=[])])
    assert status(checklist(ds2), "E2") == "unknown"


def test_e4_unknown_when_banner_age_missing():
    ds = make_ds([make_group(1)], [make_banner(11, 1, age_days=3), make_banner(12, 1, age_days=None)])
    assert status(checklist(ds), "E4") == "unknown"


def test_no_pass_without_evaluation_on_stripped_dataset():
    g = make_group(1)
    for k in ("features", "layer", "windows", "max_price_rub", "auto_utm"):
        g.pop(k)
    ds = make_ds([g], [make_banner(11, 1), make_banner(12, 1)])
    ds.pop("totals_windows")
    for b in ds["banners"]:
        for k in ("cta", "text", "urls", "ratios", "has_video", "content_ids", "age_days", "erid"):
            b.pop(k)
    res = diagnose(ds, TH)
    cl = run_checks(ds, TH, res["findings"], res["evaluated"])
    check_rules = {}
    for rule_id, cid in CHECK_OF.items():
        check_rules.setdefault(cid, []).append(rule_id)
    for b in cl["blocks"]:
        for c in b["checks"]:
            rules = check_rules.get(c["id"])
            if rules and c["status"] == "pass":
                assert sum(res["evaluated"].get(r, 0) for r in rules) > 0, c["id"]


def test_silent_rule_is_na_not_pass():
    cl = checklist(make_ds([make_group(1)], [make_banner(11, 1), make_banner(12, 1)]))
    assert status(cl, "E6") in ("unknown", "na") and status(cl, "B2") in ("unknown", "na", "pass", "fail")
    cl2 = run_checks(make_ds([make_group(1)]), TH, [], {})
    assert status(cl2, "B7") == "na"
    assert status(run_checks(make_ds([make_group(1)]), TH, [], None), "B7") == "pass"


def test_c1_to_c5_unknown_without_targetings():
    g = make_group(1)
    g["features"] = {}
    cl = checklist(make_ds([g], [make_banner(11, 1), make_banner(12, 1)]))
    for cid in ("C1", "C2", "C3", "C5"):
        assert status(cl, cid) == "unknown", cid


def test_duplicate_targeting_silent_without_targetings():
    gs = [make_group(1), make_group(2)]
    for g in gs:
        g.pop("layer")
    fs = diagnose(make_ds(gs), TH)["findings"]
    assert not [f for f in fs if f["rule"] == "duplicate_targeting"]


def test_a2_empty_erid_is_known():
    ds = make_ds([make_group(1)], [make_banner(11, 1, erid=""), make_banner(12, 1, erid="")])
    assert status(checklist(ds), "A2") == "fail"


def test_e5_needs_two_classified_banners():
    ds = make_ds([make_group(1)], [make_banner(11, 1, content_ids=[1], ratios=["1:1"]), make_banner(12, 1, content_ids=[2])])
    assert status(checklist(ds), "E5") == "unknown"
    ds = make_ds([make_group(1)], [make_banner(11, 1, content_ids=[1], ratios=["1:1"]),
                                   make_banner(12, 1, content_ids=[2], has_video=True)])
    assert status(checklist(ds), "E5") == "pass"


def test_partial_banner_coverage_gates_banner_checks():
    ds = make_ds([make_group(1), make_group(2)],
                 [make_banner(11, 1, content_ids=[1], ratios=["1:1"], text="Купите", cta="buy", age_days=3, erid="x"),
                  make_banner(12, 1, content_ids=[2], has_video=True, text="Купите", cta="buy", age_days=3, erid="x")])
    ds["coverage"]["banner_groups"] = [1]
    cl = checklist(ds)
    for cid in ("E3", "E4", "E6", "E7"):
        assert status(cl, cid) == "unknown", cid
    # ревью третьего прогона: E2 и E5 — «есть ли вообще»; видео и разные форматы найдены в выборке — ✅
    assert status(cl, "E2") == "pass" and status(cl, "E5") == "pass"
    # третий живой прогон: сработавшая находка (у группы 1 нет 4:5, 16:9, 9:16) гейтом не прячется
    assert status(cl, "E1") == "fail"
