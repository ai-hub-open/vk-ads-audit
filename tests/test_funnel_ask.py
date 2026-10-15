"""Отсутствующие воронки подписки / лид-формы / Senler: «уточнить у клиента», не ❌."""
from __future__ import annotations

import json

from scripts.checks import run_checks
from scripts.diagnose import ASK_FUNNELS, diagnose, load_client_answers, load_thresholds, main
from scripts.questions import build_questions
from scripts.render_report import limitations, render
from tests.helpers import make_banner, make_ds, make_group

TH = load_thresholds()
KEYS = ("subscription", "leadform", "senler")


def ds_site():
    return make_ds([make_group(1, objective="site_conversions")], [make_banner(11, 1, urls=["https://site.ru/"])])


def pipeline(ds, answers=None):
    res = diagnose(ds, TH, answers)
    cl = run_checks(ds, TH, res["findings"], res["evaluated"], answers)
    qs = build_questions(ds, TH, res["findings"], answers)
    return res, cl, qs


def row(cl, cid):
    return next(c for b in cl["blocks"] for c in b["checks"] if c["id"] == cid)


def test_absent_funnels_are_ask_not_fail():
    ds = make_ds([make_group(1, objective="site_conversions")], [make_banner(11, 1, urls=["https://site.ru/"])])
    _, cl, qs = pipeline(ds)
    for k in KEYS:
        r = row(cl, f"B1.{k}")
        assert r["status"] == "ask" and "уточнить у клиента" in r["reason"], k
    assert row(cl, "B1.site")["status"] == "pass"
    assert {q["id"] for q in qs if q["id"].startswith("funnel_")} == {f"funnel_{k}" for k in KEYS}
    for q in qs:
        if q["id"].startswith("funnel_"):
            assert q["important"] is True and q["why"]


def test_retargeting_and_site_stay_fail():
    ds = make_ds([make_group(1, objective="leadads")], [make_banner(11, 1)])
    _, cl, _ = pipeline(ds)
    assert row(cl, "B1.retargeting")["status"] == "fail" and row(cl, "B1.site")["status"] == "fail"


def test_ask_excluded_from_score_and_limitations():
    ds = ds_site()
    res, cl, _ = pipeline(ds)
    b = next(b for b in cl["blocks"] if b["id"] == "B")
    assert b["score"][1] == sum(1 for c in b["checks"] if c["status"] in ("pass", "fail"))
    ask_titles = [c["title"] for c in b["checks"] if c["status"] == "ask"]
    assert len(ask_titles) == 3
    assert not any(t in line for line in limitations(ds, None, cl) for t in ask_titles)


def test_no_active_groups_no_questions_and_na():
    ds = make_ds([make_group(1, status="blocked")], [make_banner(11, 1)])
    _, cl, qs = pipeline(ds)
    for k in KEYS:
        assert row(cl, f"B1.{k}")["status"] == "na"
    assert not [q for q in qs if q["id"].startswith("funnel_")]


def test_present_funnel_no_question():
    ds = make_ds([make_group(1, objective="leadads")], [make_banner(11, 1)])
    _, cl, qs = pipeline(ds)
    assert row(cl, "B1.leadform")["status"] == "pass"
    assert "funnel_leadform" not in {q["id"] for q in qs}


def test_senler_unknown_without_urls_not_asked():
    ds = make_ds([make_group(1, objective="site_conversions")], [make_banner(11, 1, urls=[])])
    _, cl, qs = pipeline(ds)
    assert row(cl, "B1.senler")["status"] == "unknown"
    assert "funnel_senler" not in {q["id"] for q in qs}


def test_answer_na():
    answers = {"leadform": {"verdict": "na", "answer": "продаём через корзину на сайте"}}
    res, cl, qs = pipeline(ds_site(), answers)
    r = row(cl, "B1.leadform")
    assert r["status"] == "na" and r["reason"] == "не применимо: продаём через корзину на сайте"
    assert "funnel_leadform" not in {q["id"] for q in qs}
    assert "funnel_subscription" in {q["id"] for q in qs}
    assert not [f for f in res["findings"] if f["rule"] == "funnel_potential"]


def test_answer_potential_adds_green_finding():
    answers = {"senler": {"verdict": "potential", "answer": "не знали про рассылки"}}
    res, cl, qs = pipeline(ds_site(), answers)
    r = row(cl, "B1.senler")
    assert r["status"] == "potential" and r["reason"] == "потенциал роста: не знали про рассылки"
    assert "funnel_senler" not in {q["id"] for q in qs}
    f = [f for f in res["findings"] if f["rule"] == "funnel_potential"]
    assert len(f) == 1
    assert f[0]["level"] == "green" and f[0]["check"] == "B1.senler" and f[0]["entity"]["type"] == "account"
    assert f[0]["title"] == "Потенциал роста: Senler"
    assert ASK_FUNNELS["senler"]["why"] in json.dumps(f[0], ensure_ascii=False)
    assert "не знали про рассылки" in json.dumps(f[0], ensure_ascii=False)


def test_answers_never_make_fail_or_pass():
    answers = {k: {"verdict": v, "answer": "x"} for k, v in zip(KEYS, ("na", "potential", "na"))}
    _, cl, _ = pipeline(ds_site(), answers)
    assert {row(cl, f"B1.{k}")["status"] for k in KEYS} == {"na", "potential"}


def test_answer_for_present_funnel_ignored():
    ds = make_ds([make_group(1, objective="leadads")], [make_banner(11, 1)])
    res, cl, _ = pipeline(ds, {"leadform": {"verdict": "potential", "answer": "x"}})
    assert row(cl, "B1.leadform")["status"] == "pass"
    assert not [f for f in res["findings"] if f["rule"] == "funnel_potential"]


def test_score_excludes_potential():
    ds = ds_site()
    base = pipeline(ds)[1]["total"]
    ans = {k: {"verdict": "potential", "answer": "x"} for k in KEYS}
    assert pipeline(ds, ans)[1]["total"] == base


# ---- файл client_answers.json ----

def write(ws, text):
    (ws / "client_answers.json").write_text(text, encoding="utf-8")


def test_load_answers_missing_file(tmp_path):
    assert load_client_answers(tmp_path) == {}


def test_load_answers_ok(tmp_path):
    write(tmp_path, json.dumps({"funnel_leadform": {"verdict": "na", "answer": "онлайн-оплата"}}))
    assert load_client_answers(tmp_path) == {"leadform": {"verdict": "na", "answer": "онлайн-оплата"}}


def test_load_answers_malformed_ignored(tmp_path, capsys):
    for bad in ("{не json", "[1, 2]", '"строка"'):
        write(tmp_path, bad)
        assert load_client_answers(tmp_path) == {}
        assert capsys.readouterr().err.strip()


def test_load_answers_unknown_verdict_ignored(tmp_path, capsys):
    write(tmp_path, json.dumps({"funnel_leadform": {"verdict": "ok", "answer": "x"},
                                "funnel_senler": {"verdict": "na", "answer": "y"},
                                "funnel_site": {"verdict": "na", "answer": "z"},
                                "funnel_subscription": "na"}))
    assert load_client_answers(tmp_path) == {"senler": {"verdict": "na", "answer": "y"}}
    assert capsys.readouterr().err.count("client_answers.json") >= 3


def test_main_uses_answers_and_survives_garbage(tmp_path):
    ws = tmp_path / "ws"
    ws.mkdir()
    (ws / "dataset.json").write_text(json.dumps(ds_site()), encoding="utf-8")
    write(ws, "{сломан")
    assert main(["--workspace", str(ws)]) == 0
    out = json.loads((ws / "findings.json").read_text(encoding="utf-8"))
    assert "funnel_leadform" in {q["id"] for q in out["questions"]}
    write(ws, json.dumps({"funnel_leadform": {"verdict": "potential", "answer": "нет формы"}}))
    assert main(["--workspace", str(ws)]) == 0
    out = json.loads((ws / "findings.json").read_text(encoding="utf-8"))
    assert "funnel_leadform" not in {q["id"] for q in out["questions"]}
    assert any(f["rule"] == "funnel_potential" for f in out["findings"])


# ---- отчёт ----

def test_render_ask_and_potential_icons():
    ds = ds_site()
    ans = {"leadform": {"verdict": "potential", "answer": "нет формы"}}
    res, cl, qs = pipeline(ds, ans)
    res["checklist"], res["questions"] = cl, qs
    html = render(ds, res)
    assert "❓" in html and "🟢" in html and "Потенциал роста: лид-форма" in html
    assert "Почему не используете подписку на сообщество?" in html
