"""
quick.py — быстрый анализ: день против среднего за 7 дней до него, по группам.

В чат уходит ≤ 10 строк: итог дня и только аномалии. Серьёзное → предложить глубокий аудит.

    python -m scripts.quick --workspace <абс. путь> --day 2026-10-13 [--target-cpa N]
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import date, timedelta

try:
    from scripts._console import setup_console
    from scripts._workspace import resolve_workspace
    from scripts.features import is_live_group, plural
    from scripts.normalize import aggregate, zero_spend_head
except ImportError:  # запуск напрямую
    from _console import setup_console
    from _workspace import resolve_workspace
    from features import is_live_group, plural
    from normalize import aggregate, zero_spend_head

SPIKE = 1.5
CPA_JUMP = 1.5
MAX_LINES = 10
SEVERITY = ["stopped", "over_target", "cpa_jump", "spend_spike"]  # от самого серьёзного
RANK = {f: i for i, f in enumerate(SEVERITY)}
ICON = {"stopped": "⛔", "spend_spike": "📈", "cpa_jump": "🔺", "over_target": "🔴"}
TEXT = {"stopped": "не открутилась", "spend_spike": "расход выше обычного", "cpa_jump": "CPA вырос",
        "over_target": "CPA выше цели"}


def _rub(v):
    return "—" if v is None else f"{v:,.0f} ₽".replace(",", " ")


def _flags(ts: dict, ps: dict, n_prev: int, target_cpa) -> list:
    """Флаги одной активной группы. Пустое предыдущее окно → средний расход 0 → без stopped/spend_spike."""
    avg = ps["spent"] / n_prev if n_prev else 0.0
    flags = []
    if ts["spent"] == 0 and avg > 0:
        flags.append("stopped")
    if ts["cpa"] and target_cpa and ts["cpa"] > target_cpa:
        flags.append("over_target")
    if ts["cpa"] and ps["cpa"] and ts["cpa"] > ps["cpa"] * CPA_JUMP:
        flags.append("cpa_jump")
    if avg and ts["spent"] > avg * SPIKE and "stopped" not in flags:
        flags.append("spend_spike")
    return sorted(flags, key=RANK.get)


def _group_line(g: dict) -> str:
    texts = ", ".join(TEXT[f] for f in g["flags"])
    return f"{ICON[g['flags'][0]]} «{g['name']}»: {texts} ({_rub(g['spent'])}, CPA {_rub(g['cpa'])})"


def quick_summary(ds: dict, day: str, target_cpa=None) -> dict:
    d = date.fromisoformat(day)
    prev_days = {(d - timedelta(days=i)).isoformat() for i in range(1, 8)}
    live = [g for g in ds["groups"] if is_live_group(g)]
    today_rows = [r for g in live for r in g["daily"] if r["date"] == day]
    prev_all = [r for g in live for r in g["daily"] if r["date"] in prev_days]
    has_today = bool(today_rows)
    today, prev_total = aggregate(today_rows), aggregate(prev_all)
    prev_day_count = len({r["date"] for r in prev_all})  # дни с данными в окне, 0..7

    groups = []
    for g in live:
        t = [r for r in g["daily"] if r["date"] == day]
        p = [r for r in g["daily"] if r["date"] in prev_days]
        flags = []
        if has_today and g["status"] == "active":  # паузные группы в итогах есть, в флагах нет
            flags = _flags(aggregate(t), aggregate(p), len({r["date"] for r in p}), target_cpa)
        ts = aggregate(t)
        groups.append({"id": g["id"], "name": g["name"], "spent": ts["spent"],
                       "conversions": ts["conversions"], "cpa": ts["cpa"], "flags": flags})

    lines = [f"📅 VK Реклама — {d.strftime('%d.%m.%Y')}"]
    if not has_today:
        # 0 ₽ подтверждён: VK не отдаёт строк для сущностей без показов — «статистика не пришла» было бы неправдой
        lines.append(zero_spend_head(ds, short=True) + "." if ds.get("zero_spend")
                     else "Данных за этот день нет — возможно, статистика ещё не пришла.")
        return {"day": day, "today": today, "prev_total": prev_total, "prev_days": prev_day_count,
                "groups": groups, "lines": lines[:MAX_LINES]}

    if prev_day_count:
        prev_txt = f"в среднем {_rub(prev_total['spent'] / prev_day_count)} в день за {prev_day_count} дн."
    else:
        prev_txt = "предыдущих данных нет"
    lines.append(f"Расход: {_rub(today['spent'])} ({prev_txt})")
    lines.append(f"Конверсии: {today['conversions']} · CPA: {_rub(today['cpa'])}")

    flagged = sorted((g for g in groups if g["flags"]), key=lambda g: (RANK[g["flags"][0]], -g["spent"]))
    if not flagged:
        lines.append("Аномалий нет.")
    else:
        room = MAX_LINES - len(lines) - 1  # последняя строка — «→ Разобрать…»
        if len(flagged) <= room:
            shown = flagged
        else:
            shown = flagged[:room - 1]  # одна строка уходит на «…и ещё N»
        lines += [_group_line(g) for g in shown]
        hidden = len(flagged) - len(shown)
        if hidden:
            lines.append(f"…и ещё {hidden} {plural(hidden, 'группа', 'группы', 'групп')} с отклонениями")
        lines.append("→ Разобрать подробно? Могу провести глубокий аудит.")
    return {"day": day, "today": today, "prev_total": prev_total, "prev_days": prev_day_count,
            "groups": groups, "lines": lines[:MAX_LINES]}


def main(argv=None) -> int:
    setup_console()
    parser = argparse.ArgumentParser(description="Быстрый анализ дня")
    parser.add_argument("--workspace", required=True)
    parser.add_argument("--day", required=True)
    parser.add_argument("--target-cpa", type=float)
    args = parser.parse_args(argv)
    try:
        day = date.fromisoformat(args.day)
    except ValueError:
        print(f"Неверная дата «{args.day}»: нужен формат ГГГГ-ММ-ДД, например 2026-10-13.", file=sys.stderr)
        return 2
    if day >= date.today():
        print(f"{day.isoformat()} — день ещё не завершён, статистика неполная. "
              "Укажите вчерашнюю или более раннюю дату.", file=sys.stderr)
        return 2
    ws = resolve_workspace(args.workspace)
    path = ws / "dataset.json"
    if not path.is_file():
        print(f"В рабочей папке нет dataset.json ({path}). Сначала соберите данные и запустите normalize.",
              file=sys.stderr)
        return 2
    try:
        ds = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        print(f"Не удалось прочитать dataset.json: {e}", file=sys.stderr)
        return 2
    res = quick_summary(ds, args.day, args.target_cpa)
    (ws / "quick.json").write_text(json.dumps(res, ensure_ascii=False, indent=1), encoding="utf-8")
    print("\n".join(res["lines"]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
