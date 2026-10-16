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
