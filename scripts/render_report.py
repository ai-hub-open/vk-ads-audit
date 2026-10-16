"""
render_report.py — findings.json + dataset.json → самодостаточный HTML-отчёт (печать A4).

Каждое число отчёта — из dataset.json/findings.json, модель чисел не вписывает.
Раздел «Ограничения» обязателен: постоянные ограничения API + недобранное в прогоне.

    python -m scripts.render_report --workspace <абс. путь> --slug <slug>
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from html import escape

try:
    from scripts._console import setup_console
    from scripts._workspace import resolve_workspace
    from scripts.diagnose import (_banners, bidding_label, load_thresholds, utm_unconfirmed_groups,
                                  utm_unconfirmed_reason, utm_unconfirmed_text)
    from scripts.features import group_status_label, is_live_group, issue_label, ord_status_label
    from scripts.normalize import zero_spend_head
    from scripts.raw_store import mark_recovered
except ImportError:  # запуск напрямую
    from _console import setup_console
    from _workspace import resolve_workspace
    from diagnose import (_banners, bidding_label, load_thresholds, utm_unconfirmed_groups, utm_unconfirmed_reason,
                          utm_unconfirmed_text)
    from features import group_status_label, is_live_group, issue_label, ord_status_label
    from normalize import zero_spend_head
    from raw_store import mark_recovered

LEVELS = [("red", "🔴 Срезать слив"), ("orange", "🟠 Починить"), ("green", "🟢 Масштабировать"),
          ("white", "⚪ Гигиена")]
CONF = {"high": "надёжно", "medium": "средне", "low": "мало данных"}
ALWAYS_LIMITATIONS = [
    "В API нет разрезов по полу, возрасту, гео, площадкам и устройствам — «кто конвертит» по ним не определить.",
    "API не показывает, срабатывает ли пиксель: ноль конверсий может означать сломанный пиксель.",
    "Атрибуция (post-click или post-view) меняет число конверсий — сверяйте с настройкой кабинета.",
    "Данные за сегодня неполные — период заканчивается вчерашним днём.",
    "Статусы ОРД видны частично.",
]
CSS = """
body{font:14px/1.45 -apple-system,Segoe UI,Roboto,sans-serif;color:#1d1d1f;margin:24px;max-width:1100px}
h1{font-size:22px;margin:0 0 4px}h2{font-size:17px;margin:28px 0 8px;border-bottom:1px solid #ddd}
.muted{color:#666}.tiles{display:flex;gap:12px;flex-wrap:wrap;margin:16px 0}
.tile{border:1px solid #ddd;border-radius:8px;padding:10px 14px;min-width:120px}
.tile b{display:block;font-size:18px}.f{border:1px solid #e3e3e3;border-radius:8px;padding:10px 14px;margin:8px 0}
.f h3{font-size:15px;margin:0 0 4px}.f.explained{background:#f6f6f6;color:#555}.note{font-style:italic}table{border-collapse:collapse;width:100%;font-size:13px}
td,th{border-bottom:1px solid #eee;padding:4px 6px;text-align:left}th{background:#fafafa}
.alert{border:2px solid #c62828;background:#fdecea;color:#7f1d1d;border-radius:8px;padding:14px 18px;
margin:16px 0;font-size:17px;font-weight:600}.warn{border-left:4px solid #b26a00;background:#fff6e5;padding:8px 12px}.score{font-weight:600}.st-pass{color:#1a7f37}.st-fail{color:#c62828}.st-unk{color:#888}.st-pot{color:#1a7f37}
@media print{body{margin:0}.f{break-inside:avoid}}
"""
CHANGED_AFTER_PERIOD = "Кабинет менялся после конца периода: статусы и бюджеты показаны на дату сбора."
ZERO_SPEND_TAIL = " — выводов по расходу и результату нет, проверены только настройки кабинета."


def zero_spend_text(ds: dict) -> str:
    """Плашка «0 ₽» (формулировки — normalize.zero_spend_head, общие с быстрым анализом)."""
    return zero_spend_head(ds) + ZERO_SPEND_TAIL
STATUS_ICON = {"pass": "✅", "fail": "❌", "unknown": "—", "na": "—", "ask": "❓", "potential": "🟢"}
STATUS_CLASS = {"pass": "pass", "fail": "fail", "unknown": "unk", "na": "unk", "ask": "unk", "potential": "pot"}


def fmt_rub(v) -> str:
    return "—" if v is None else f"{v:,.0f} ₽".replace(",", " ")


def fmt_pct(v) -> str:
    return "—" if v is None else f"{v:.2f} %"


def _num(v) -> str:
    if isinstance(v, bool):
        return "да" if v else "нет"
    if v is None:
        return "—"
    if isinstance(v, float):
        return f"{v:,.2f}".replace(",", " ")
    if isinstance(v, int):
        return f"{v:,}".replace(",", " ")
    return escape(json.dumps(v, ensure_ascii=False) if isinstance(v, (list, dict)) else str(v))


# ---- доказательства находок по-русски (третий живой прогон: в HTML шли ref_source median, issues [...], account None) ----

EVIDENCE_LABELS = {
    "spent": "расход", "shows": "показы", "clicks": "клики", "conversions": "конверсии", "cpa": "CPA",
    "ref_cpa": "опорный CPA", "ref_source": "источник опорного CPA", "limit_rub": "порог расхода без конверсий",
    "cpc": "CPC", "avg_cpc": "средний CPC по кабинету", "ctr_pct": "CTR", "threshold_pct": "порог CTR",
    "frequency": "частота", "ctr_first_half_pct": "CTR первой половины периода",
    "ctr_second_half_pct": "CTR второй половины периода", "spent_7d": "расход за 7 дней", "cpa_7d": "CPA за 7 дней",
    "cpa_30d": "CPA за 30 дней", "conversions_30d": "конверсии за 30 дней", "conversions_7d": "конверсии за 7 дней",
    "conversions_per_week": "конверсий в неделю", "needed": "нужно", "avg_daily_spent": "средний расход в день",
    "budget_day_rub": "дневной бюджет", "minimum_rub": "минимум", "needed_rub": "нужно в день",
    "recommended_rub": "рекомендуется", "budget_max_rub": "бюджет до", "small_groups": "мелких групп",
    "max_price_rub": "предельная цена", "month_spend_rub": "расход в пересчёте на месяц",
    "banners_total": "объявлений всего", "banners_active": "активных объявлений", "active_banners": "активных объявлений",
    "moderation_status": "статус модерации", "reasons": "причины", "can_remoderate": "перемодерация доступна",
    "issues": "причины VK", "age_days": "возраст группы, дней", "groups": "группы",
    "segments_unused": "неиспользуемых сегментов",
    "only_in_deleted_or_archived_groups": "из них стояли только в удалённых или архивных группах",
    "examples": "примеры", "objective": "цель", "banners_without_erid": "объявлений без маркировки",
    "without_utm": "объявлений без UTM", "site_banners": "объявлений на сайт", "active_groups": "активных групп",
    "missing_layers": "не хватает слоёв", "actual": "фактические доли", "target": "рекомендованные доли",
    "guessed_share": "доля расхода групп со слоем по догадке", "layers": "слои",
    "default_names": "названий по умолчанию", "total": "названий всего", "share": "доля",
    "groups_with_spend": "групп с расходом", "period_days": "период поиска, дней", "regions": "регионы",
    "age_span": "возрастной диапазон, лет", "shared_segments": "общие сегменты", "segment_size": "размер сегмента",
    "minimum": "минимум", "days_since_update": "дней без обновления", "missing": "не хватает форматов",
    "present": "есть форматы", "banners_with_content": "объявлений с контентом",
    "newest_creative_age_days": "самому новому креативу, дней",
    "edited_recently": "объявлений с недавней правкой (дата смены креатива не видна)", "banners": "объявления",
    "hours": "часов на модерации", "norm_hours": "норма, часов", "bounce_rate": "доля отказов", "visits": "визиты",
    "ratio": "визиты / клики", "metrika_goal_reaches": "достижений целей в Метрике", "vk_conversions": "конверсий в VK",
    "potential": "потенциал", "client_answer": "ответ клиента",
}
_RUB_KEYS = {"spent", "cpa", "ref_cpa", "limit_rub", "cpc", "avg_cpc", "spent_7d", "cpa_7d", "cpa_30d",
             "avg_daily_spent", "budget_day_rub", "minimum_rub", "needed_rub", "recommended_rub", "budget_max_rub",
             "max_price_rub", "month_spend_rub"}
_SHARE_KEYS = {"share", "guessed_share", "bounce_rate", "ratio"}
REF_SOURCE_LABELS = {"target": "целевой CPA клиента", "median": "медиана CPA групп кабинета"}
LAYER_LABELS = {"warm": "тёплый", "lal": "LAL", "cold": "холодный", "warm_lal": "тёплый + LAL"}
OBJECTIVE_LABELS = {"site_conversions": "конверсии на сайте", "leadads": "лид-формы",
                    "socialengagement": "вовлечение в сообщество"}
MODERATION_LABELS = {"banned": "отклонено", "rejected": "отклонено", "allowed": "одобрено", "new": "на модерации",
                     "delayed": "на модерации", "moderating": "на модерации"}
ENTITY_LABELS = {"group": "группа", "banner": "объявление", "plan": "кампания", "segment": "сегмент", "list": "база",
                 "account": "кабинет"}


def _ev_text(v) -> str:
    """Значение без сырого repr: списки через запятую, словари «ключ: значение»."""
    if isinstance(v, list):
        return ", ".join(_ev_text(x) for x in v)
    if isinstance(v, dict):
        return "; ".join(f"{k}: {_ev_text(x)}" for k, x in v.items() if x not in (None, "", []))
    if isinstance(v, (bool, int, float)) or v is None:
        return _num(v)
    return str(v)


def _shares(d) -> str:
    return ", ".join(f"{LAYER_LABELS.get(k, k)} {x * 100:.0f} %" for k, x in d.items() if isinstance(x, (int, float)))


def evidence_value(key, v) -> str:
    if key in _RUB_KEYS and isinstance(v, (int, float)) and not isinstance(v, bool):
        return fmt_rub(v)
    if key.endswith("_pct") and isinstance(v, (int, float)):
        return fmt_pct(v)
    if key in _SHARE_KEYS and isinstance(v, (int, float)):
        return f"{v * 100:.0f} %"
    if key == "ref_source":
        return REF_SOURCE_LABELS.get(v, "—")
    if key == "issues" and isinstance(v, list):
        return ", ".join(issue_label(c) for c in v)
    if key in ("missing_layers", "layers") and isinstance(v, list):
        return ", ".join(LAYER_LABELS.get(x, str(x)) for x in v)
    if key in ("actual", "target") and isinstance(v, dict):
        return _shares(v)
    if key == "objective":
        return OBJECTIVE_LABELS.get(v) or f"другая цель ({v})"
    if key == "moderation_status":
        return MODERATION_LABELS.get(v) or f"другой статус ({v})"
    return _ev_text(v)


def _evidence_rows(evidence: dict) -> str:
    """Строки доказательства: подпись по-русски, None и пустое не выводим (раньше — «account None», «—»)."""
    return "".join(f"<tr><th>{escape(EVIDENCE_LABELS.get(k, 'показатель'))}</th>"
                   f"<td>{escape(evidence_value(k, v))}</td></tr>"
                   for k, v in evidence.items() if v not in (None, "", []))


def _entity_text(e: dict) -> str:
    kind = ENTITY_LABELS.get(e.get("type"), "объект")
    parts = [kind + (f" {e['id']}" if e.get("id") is not None else "")]
    if e.get("name") and e.get("type") != "account":
        parts.append(e["name"])
    return " · ".join(parts)


TOOL_LABELS = {
    "vk_ads_ad_plans_list": "список кампаний", "vk_ads_campaigns_list": "список групп",
    "vk_ads_banners_list": "объявления группы", "vk_ads_remarketing_segments_list": "список сегментов",
    "vk_ads_users_lists_list": "базы клиентов", "vk_ads_remarketing_pixels_list": "список пикселей",
    "vk_ads_statistics_day": "статистику по дням", "vk_ads_statistics_summary": "сводную статистику",
    "vk_ads_account_info": "данные кабинета", "vk_ads_auth_check": "проверку доступа",
}


def _error_brief(error) -> str:
    """Причина сбоя без сырого JSON: «ошибка VK 504 (upstream_timeout)»; не JSON — текст без имени инструмента."""
    text = str(error or "").strip()
    body = text[text.index("{"):] if "{" in text else ""
    try:
        obj = json.loads(body) if body else None
    except ValueError:
        obj = None
    if isinstance(obj, dict):
        status, code = obj.get("status"), obj.get("code")
        detail = obj.get("detail") if isinstance(obj.get("detail"), dict) else {}
        err = detail.get("error") if isinstance(detail.get("error"), dict) else {}
        code = code or err.get("code")
        head = f"ошибка VK {status}" if isinstance(status, int) else "ошибка VK"
        return head + (f" ({code})" if code else "")
    if ": " in text and text.split(": ", 1)[0].startswith("vk_ads_"):
        text = text.split(": ", 1)[1]
    return text[:200] or "причина не указана"


def _lists_no_access(c: dict) -> bool:
    return c.get("tool") == "vk_ads_users_lists_list" and bool(c.get("no_access")) and not c.get("ok")


def limitations(ds: dict, collect_log=None, checklist=None, th=None) -> list:
    th = th or load_thresholds()
    lines = list(ALWAYS_LIMITATIONS)
    cov = ds["coverage"]
    lines.extend(ds.get("notes") or [])
    any_live = any(is_live_group(g) for g in ds["groups"])  # все группы удалены — собирать было нечего
    measured = any(g["status"] != "deleted" for g in ds["groups"])  # статистику берём и у архивных
    if cov["path"] == "A" and any_live:
        lines.append("Данные собраны через модель (путь A): объявления разобраны только у групп "
                     "с наибольшим расходом.")
    if not cov["banners"] and any_live:
        lines.append("Объявления не собирались — выводы на уровне групп.")
    if not cov["metrika"]:
        lines.append("Метрика не подключена: поведение на сайте после клика не проверялось.")
    elif not cov.get("metrika_utm", True):
        lines.append("Метрика без UTM-разметки: склейка только на уровне всего трафика VK, "
                     "правила по группам не применялись.")
    collect_log = mark_recovered(collect_log)   # повтор того же вызова прошёл — сбой не ограничение
    calls = (collect_log or {}).get("calls", [])
    for c in calls:
        skip = c.get("superseded") or c.get("recovered") or c.get("preflight") or _lists_no_access(c)
        if not c.get("ok") and not skip:
            lines.append(f"Не удалось получить {TOOL_LABELS.get(c['tool'], 'данные VK')}: "
                         f"{_error_brief(c.get('error'))}")
    if any(_lists_no_access(c) and not c.get("recovered") for c in calls) and not any(
            "Нет доступа к базам клиентов" in n for n in ds.get("notes") or []):
        lines.append("Нет доступа к базам клиентов: проверки, зависящие от баз, не выполнялись.")
    if any(c.get("superseded") for c in calls):
        lines.append("Расширенные поля не приняты сервером — часть проверок (маркировка, UTM, кнопки) не проверена")
    if not cov.get("stats", True) and measured:
        lines.append("Статистика за период не собрана — выводы по расходу и результату недоступны.")
    if not cov.get("pixels", True):
        lines.append("Пиксели кабинета не проверялись.")
    if (collect_log or {}).get("aborted"):
        lines.append(f"Сбор прерван: {collect_log['aborted']}")
    unconfirmed = utm_unconfirmed_groups(ds, th)
    if unconfirmed:   # своя строка вместо «Не проверено «UTM…»» — и при ❌ по другим группам
        lines.append(utm_unconfirmed_text(len(unconfirmed)))
    for b in (checklist or {}).get("blocks", []):
        for c in b["checks"]:
            # A3 по неподтверждённой авто-UTM — своя строка выше; другую причину A3 (выборка объявлений) не теряем
            if c["status"] == "unknown" and not (c["id"] == "A3" and unconfirmed
                                                 and c["reason"] == utm_unconfirmed_reason(len(unconfirmed))):
                lines.append(f"Не проверено «{c['title']}»: {c['reason']}")
    return lines


def _finding_html(f) -> str:
    ev = _evidence_rows(f["evidence"])
    effect = f"<p>Эффект: <b>{fmt_rub(f['effect_rub'])}</b></p>" if f["effect_rub"] else ""
    note = f.get("client_note")
    note_html = f'<p class="note">Объяснено клиентом: {escape(note)}</p>' if isinstance(note, str) and note.strip() else ""
    cls = "f explained" if note_html else "f"
    return (f'<div class="{cls}"><h3>{escape(f["title"])}</h3>'
            f'<p class="muted">{escape(_entity_text(f["entity"]))} · '
            f'надёжность: {CONF.get(f["confidence"], "—")}'
            + (f' · проверка {escape(f["check"])}' if f.get("check") else "") + '</p>'
            f'<table>{ev}</table>{effect}<p>Действие: {escape(f["action"])}</p>{note_html}</div>')


CONVERSION_SOURCE_TEXT = "Конверсии — результат по цели оптимизации (как «Результат» в кабинете VK)"


def _conversion_source_html(totals) -> str:
    """Из какого показателя взяты конверсии (решение методиста: vk.result), base.goals — справочно."""
    goals = totals.get("goals_all")
    extra = f"; все цели пикселя (справочно): {_num(goals)}" if isinstance(goals, int) and goals else ""
    return f"<p class='muted'>{CONVERSION_SOURCE_TEXT}{extra}.</p>"


def _metrika_total_html(total) -> str:
    if not isinstance(total, dict):
        return ""
    bounce = total.get("bounce_rate")
    parts = [f"визитов {_num(total.get('visits'))}",
             f"отказы {fmt_pct(bounce * 100) if isinstance(bounce, (int, float)) else '—'}",
             f"достижений целей {_num(total.get('goal_reaches'))}"]
    return f"<p class='muted'>Метрика, весь трафик VK: {' · '.join(parts)}</p>"


def _checklist_html(cl) -> str:
    if not cl:
        return ""
    if cl.get("total") is None:   # нет живых групп — балл не считаем
        parts = [f"<h2>Чек-лист аудита — {escape(cl.get('idle') or 'балл не считается')}</h2>"]
    else:
        n, m = cl["total"]
        parts = [f"<h2>Чек-лист аудита — общий балл <span class='score'>{n} / {m}</span></h2>"]
    for b in cl["blocks"]:
        bn, bm = b["score"]
        rows = "".join(
            f"<tr><td>{escape(c['id'])}</td><td>{escape(c['title'])}</td>"
            f"<td class='st-{STATUS_CLASS[c['status']]}'>{STATUS_ICON[c['status']]}</td>"
            f"<td class='muted'>{escape(c['reason'])}</td></tr>" for c in b["checks"])
        parts.append(f"<h3>{escape(b['id'])}. {escape(b['title'])} — {bn} / {bm if bm else '—'}</h3>"
                     f"<table>{rows}</table>")
    return "".join(parts)


def _ord_status_html(ds, th) -> str:
    """Справочно: статусы ОРД (or_status) активных объявлений по-человечески («активен; коды: превышен дневной
    лимит»), на A2 и находки не влияет."""
    counts = Counter(ord_status_label(b.get("ord_status")) for b in _banners(ds)
                     if b["status"] in th["active_statuses"])
    counts.pop(None, None)
    if not counts:
        return ""
    parts = " · ".join(f"{escape(k)} — {v}" for k, v in sorted(counts.items(), key=lambda kv: (-kv[1], kv[0])))
    return ("<p class='muted'>Статус в ОРД (справочно) у активных объявлений — на проверку "
            f"маркировки A2 не влияет: {parts}</p>")


def _questions_html(qs) -> str:
    if not qs:
        return ""
    items = "".join(
        f"<li>{escape(q['text'])}" + (f" <b>— важно для вашего кабинета: {escape(q['why'])}</b>" if q["important"] else "")
        + "</li>" for q in sorted(qs, key=lambda q: not q["important"]))
    return f"<h2>Вопросы к клиенту</h2><ul>{items}</ul>"


def render(ds: dict, findings: dict, collect_log=None, title: str = "", th=None) -> str:
    th = th or load_thresholds()
    t = ds["totals"]
    tiles = [("Расход", fmt_rub(t["spent"])), ("Показы", _num(t["shows"])), ("Клики", _num(t["clicks"])),
             ("Конверсии", _num(t["conversions"])), ("CPA", fmt_rub(t["cpa"])), ("CTR", fmt_pct(t["ctr_pct"]))]
    ref = findings.get("ref_cpa")
    ref_note = {"target": "целевой CPA клиента", "median": "медиана CPA групп кабинета (цель не задана)",
                "none": "опорного CPA нет — пороги по минимальному расходу"}[findings.get("ref_source", "none")]
    parts = [f"<!doctype html><html lang='ru'><head><meta charset='utf-8'>"
             f"<title>Аудит VK Рекламы {escape(title)}</title><style>{CSS}</style></head><body>",
             f"<h1>Аудит VK Рекламы {escape(title)}</h1>",
             f"<p class='muted'>Период {ds['period']['from']} — {ds['period']['to']} · путь {ds['coverage']['path']}"
             f" · опорный CPA: {fmt_rub(ref)} ({ref_note})</p>",
             f"<div class='alert'>{escape(zero_spend_text(ds))}</div>" if ds.get("zero_spend") else "",
             f"<p class='warn'>{CHANGED_AFTER_PERIOD}</p>" if ds.get("changed_after_period") else "",
             "<div class='tiles'>" + "".join(f"<div class='tile'>{k}<b>{v}</b></div>" for k, v in tiles) + "</div>",
             _conversion_source_html(t),
             _metrika_total_html(ds.get("metrika_total")),
             _checklist_html(findings.get("checklist")),
             _ord_status_html(ds, th),
             "<h2>Находки</h2>"]
    if not findings["findings"]:
        parts.append("<p>Существенных проблем не найдено.</p>")
    for level, label in LEVELS:
        items = [f for f in findings["findings"] if f["level"] == level]
        if items:
            parts.append(f"<h2>{label} ({len(items)})</h2>" + "".join(_finding_html(f) for f in items))
    parts.append(_questions_html(findings.get("questions")))
    rows = "".join(
        f"<tr><td>{escape(g['name'])}</td><td>{escape(group_status_label(g))}</td>"
        f"<td>{escape(bidding_label(g.get('autobidding_mode'), th) or '—')}</td><td>{fmt_rub(g['stats']['spent'])}</td>"
        f"<td>{g['stats']['conversions']}</td><td>{fmt_rub(g['stats']['cpa'])}</td>"
        f"<td>{fmt_pct(g['stats']['ctr_pct'])}</td><td>{len(g['banner_ids'])}</td></tr>"
        for g in sorted(ds["groups"], key=lambda g: -g["stats"]["spent"]) if g["status"] != "deleted")
    parts.append("<h2>Структура</h2><table><tr><th>Группа</th><th>Статус</th><th>Ставка</th><th>Расход</th><th>Конв.</th>"
                 f"<th>CPA</th><th>CTR</th><th>Объявл.</th></tr>{rows}</table>")
    parts.append("<h2>Ограничения</h2><ul>" +
                 "".join(f"<li>{escape(x)}</li>" for x in limitations(ds, collect_log, findings.get("checklist"), th))
                 + "</ul></body></html>")
    return "\n".join(parts)


def main(argv=None) -> int:
    setup_console()
    parser = argparse.ArgumentParser(description="findings.json → HTML-отчёт")
    parser.add_argument("--workspace", required=True)
    parser.add_argument("--slug", required=True)
    args = parser.parse_args(argv)
    ws = resolve_workspace(args.workspace)
    ds = json.loads((ws / "dataset.json").read_text(encoding="utf-8"))
    fs = json.loads((ws / "findings.json").read_text(encoding="utf-8"))
    log_path = ws / "collect_log.json"
    log = json.loads(log_path.read_text(encoding="utf-8")) if log_path.is_file() else None
    log = mark_recovered(log, ws / "raw")   # путь A: удавшийся повтор лежит файлом в raw/
    out = ws / f"АУДИТ_{args.slug}.html"
    out.write_text(render(ds, fs, log, title=args.slug), encoding="utf-8")
    print(f"✓ {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
