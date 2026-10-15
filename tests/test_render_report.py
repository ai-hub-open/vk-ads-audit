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


def test_client_note_rendered_escaped_and_marked():
    ds, _ = sample()
    fs = {"ref_cpa": None, "ref_source": "none", "findings": [
        {"rule": "x", "level": "red", "entity": {"type": "group", "id": 1, "name": "g"}, "title": "T",
         "evidence": {}, "effect_rub": 0, "confidence": "high", "action": "A",
         "client_note": "тест <script>alert(1)</script>"},
        {"rule": "y", "level": "red", "entity": {"type": "group", "id": 2, "name": "h"}, "title": "T2",
         "evidence": {}, "effect_rub": 0, "confidence": "high", "action": "A"}]}
    html = render(ds, fs)
    assert "Объяснено клиентом: тест &lt;script&gt;" in html
    assert "<script>alert" not in html
    assert html.count('class="f explained"') == 1
    assert html.count("Объяснено клиентом") == 1


def test_notes_appended_to_limitations():
    ds, _ = sample()
    ds["notes"] = ["Объявления не собраны для 2 групп — по ним выводы на уровне группы."]
    assert ds["notes"][0] in limitations(ds)


def test_metrika_without_utm_limitation():
    ds, _ = sample()
    ds["coverage"].update(metrika=True, metrika_utm=False)
    assert any("Метрика без UTM-разметки" in x for x in limitations(ds))
    ds["coverage"]["metrika_utm"] = True
    assert not any("Метрика без UTM-разметки" in x for x in limitations(ds))


def test_metrika_total_block_rendered():
    ds, fs = sample()
    ds["coverage"].update(metrika=True, metrika_utm=False)
    ds["metrika_total"] = {"visits": 1234, "bounce_rate": 0.456, "goal_reaches": 7}
    html = render(ds, fs)
    assert "Метрика, весь трафик VK" in html and "1 234" in html and "45.60 %" in html


def test_no_metrika_total_block_without_data():
    ds, fs = sample()
    assert "Метрика, весь трафик VK" not in render(ds, fs)


from scripts.checks import run_checks
from scripts.diagnose import load_thresholds as _lt
from scripts.questions import build_questions


def full_sample():
    ds, fs = sample()
    th = _lt()
    fs["checklist"] = run_checks(ds, th, fs["findings"], fs["evaluated"])
    fs["questions"] = build_questions(ds, th, fs["findings"])
    return ds, fs


def test_checklist_and_score_rendered():
    ds, fs = full_sample()
    html = render(ds, fs)
    n, m = fs["checklist"]["total"]
    assert f"{n} / {m}" in html and "Техническая настройка" in html and "✅" in html and "❌" in html


def test_questions_rendered():
    ds, fs = full_sample()
    html = render(ds, fs)
    assert "Вопросы к клиенту" in html and "важно для вашего кабинета" in html


def test_unknown_checks_go_to_limitations():
    ds, fs = full_sample()
    lines = limitations(ds, None, fs["checklist"])
    assert any(x.startswith("Не проверено «") for x in lines)


def test_finding_shows_check_id():
    ds, fs = full_sample()
    assert "проверка F6" in render(ds, fs)


def test_old_findings_without_checklist():
    ds, fs = sample()
    assert "Чек-лист" not in render(ds, fs)
