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
from html import escape

try:
    from scripts._console import setup_console
    from scripts._workspace import resolve_workspace
except ImportError:  # запуск напрямую
    from _console import setup_console
    from _workspace import resolve_workspace

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
@media print{body{margin:0}.f{break-inside:avoid}}
"""


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


def limitations(ds: dict, collect_log=None) -> list:
    lines = list(ALWAYS_LIMITATIONS)
    cov = ds["coverage"]
    if cov["path"] == "A":
        lines.append("Данные собраны через модель (путь A): объявления разобраны только у групп "
                     "с наибольшим расходом.")
    if not cov["banners"]:
        lines.append("Объявления не собирались — выводы на уровне групп.")
    if not cov["metrika"]:
        lines.append("Метрика не подключена: поведение на сайте после клика не проверялось.")
    for c in (collect_log or {}).get("calls", []):
        if not c.get("ok"):
            lines.append(f"Не удалось получить {c['tool']}: {c.get('error')}")
    if not cov.get("stats", True):
        lines.append("Статистика за период не собрана — выводы по расходу и результату недоступны.")
    if not cov.get("pixels", True):
        lines.append("Пиксели кабинета не проверялись.")
    if (collect_log or {}).get("aborted"):
        lines.append(f"Сбор прерван: {collect_log['aborted']}")
    return lines


def _finding_html(f) -> str:
    ev = "".join(f"<tr><th>{escape(k)}</th><td>{_num(v)}</td></tr>" for k, v in f["evidence"].items())
    effect = f"<p>Эффект: <b>{fmt_rub(f['effect_rub'])}</b></p>" if f["effect_rub"] else ""
    note = f.get("client_note")
    note_html = f'<p class="note">Объяснено клиентом: {escape(note)}</p>' if isinstance(note, str) and note.strip() else ""
    cls = "f explained" if note_html else "f"
    return (f'<div class="{cls}"><h3>{escape(f["title"])}</h3>'
            f'<p class="muted">{escape(f["entity"]["type"])} {escape(str(f["entity"]["id"]))} · '
            f'{escape(f["entity"]["name"])} · надёжность: {CONF.get(f["confidence"], f["confidence"])}</p>'
            f'<table>{ev}</table>{effect}<p>Действие: {escape(f["action"])}</p>{note_html}</div>')


def render(ds: dict, findings: dict, collect_log=None, title: str = "") -> str:
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
             "<div class='tiles'>" + "".join(f"<div class='tile'>{k}<b>{v}</b></div>" for k, v in tiles) + "</div>",
             "<h2>Находки</h2>"]
    if not findings["findings"]:
        parts.append("<p>Существенных проблем не найдено.</p>")
    for level, label in LEVELS:
        items = [f for f in findings["findings"] if f["level"] == level]
        if items:
            parts.append(f"<h2>{label} ({len(items)})</h2>" + "".join(_finding_html(f) for f in items))
    rows = "".join(
        f"<tr><td>{escape(g['name'])}</td><td>{escape(str(g['status']))}</td><td>{fmt_rub(g['stats']['spent'])}</td>"
        f"<td>{g['stats']['conversions']}</td><td>{fmt_rub(g['stats']['cpa'])}</td>"
        f"<td>{fmt_pct(g['stats']['ctr_pct'])}</td><td>{len(g['banner_ids'])}</td></tr>"
        for g in sorted(ds["groups"], key=lambda g: -g["stats"]["spent"]) if g["status"] != "deleted")
    parts.append("<h2>Структура</h2><table><tr><th>Группа</th><th>Статус</th><th>Расход</th><th>Конв.</th>"
                 f"<th>CPA</th><th>CTR</th><th>Объявл.</th></tr>{rows}</table>")
    parts.append("<h2>Ограничения</h2><ul>" +
                 "".join(f"<li>{escape(x)}</li>" for x in limitations(ds, collect_log)) + "</ul></body></html>")
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
    out = ws / f"АУДИТ_{args.slug}.html"
    out.write_text(render(ds, fs, log, title=args.slug), encoding="utf-8")
    print(f"✓ {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
