from __future__ import annotations

from scripts.diagnose import diagnose, load_thresholds
from scripts.render_report import ALWAYS_LIMITATIONS, fmt_pct, fmt_rub, limitations, render
from tests.helpers import make_banner, make_ds, make_group, stats


def sample():
    g = make_group(1, name="Группа <script>alert(1)</script>",
                   stats=stats(spent=12345.6, shows=20000, clicks=200, conversions=0))
    ds = make_ds([g], [make_banner(11, 1)], target_cpa=500)
    ds["totals"] = g["stats"]
    return ds, diagnose(ds, load_thresholds())


def test_formatters():
    assert fmt_rub(12345.6) == "12 346 ₽"
    assert fmt_rub(None) == "—"
    assert fmt_pct(1.23456) == "1.23 %"


def test_numbers_and_findings_present():
    ds, fs = sample()
    html = render(ds, fs, title="acme")
    assert "12 346 ₽" in html
    for f in fs["findings"]:
        assert f["title"] in html
    for line in ALWAYS_LIMITATIONS:
        assert line in html


def test_names_escaped():
    ds, fs = sample()
    html = render(ds, fs)
    assert "<script>alert(1)</script>" not in html
    assert "&lt;script&gt;" in html


def test_dynamic_limitations():
    ds, _ = sample()
    ds["coverage"] = {"path": "A", "banners": False, "summary": True, "metrika": False}
    log = {"calls": [{"tool": "vk_ads_users_lists_list", "ok": False, "error": "HTTP 500"}], "errors": 1}
    lines = limitations(ds, log)
    assert any("путь A" in x for x in lines)
    assert any("Метрика" in x for x in lines)
    assert any("vk_ads_users_lists_list" in x for x in lines)


def test_coverage_stats_pixels_and_abort_limitations():
    ds, _ = sample()
    ds["coverage"] = {"path": "B", "banners": True, "summary": True, "stats": False,
                      "pixels": False, "metrika": True}
    log = {"calls": [], "errors": 0, "critical": True, "aborted": "RuntimeError: сеть недоступна"}
    lines = limitations(ds, log)
    assert "Статистика за период не собрана — выводы по расходу и результату недоступны." in lines
    assert "Пиксели кабинета не проверялись." in lines
    assert "Сбор прерван: RuntimeError: сеть недоступна" in lines
    # полного покрытия и чистого лога — ни одной из этих строк
    ds["coverage"].update(stats=True, pixels=True)
    clean = limitations(ds, {"calls": [], "errors": 0, "critical": False})
    assert not any("Статистика за период" in x or "Пиксели" in x or "Сбор прерван" in x for x in clean)


def test_empty_findings_message():
    ds = make_ds([make_group(1)], [make_banner(11, 1), make_banner(12, 1)])
    html = render(ds, {"ref_cpa": None, "ref_source": "none", "findings": []})
    assert "Существенных проблем не найдено" in html
