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
    from scripts.normalize import aggregate
except ImportError:  # запуск напрямую
    from _console import setup_console
    from _workspace import resolve_workspace
    from normalize import aggregate

SPIKE = 1.5
CPA_JUMP = 1.5
MAX_LINES = 10
ICON = {"stopped": "⛔", "spend_spike": "📈", "cpa_jump": "🔺", "over_target": "🔴"}
TEXT = {"stopped": "не открутилась", "spend_spike": "расход выше обычного", "cpa_jump": "CPA вырос",
        "over_target": "CPA выше цели"}


def _rub(v):
    return "—" if v is None else f"{v:,.0f} ₽".replace(",", " ")


def quick_summary(ds: dict, day: str, target_cpa=None) -> dict:
    d = date.fromisoformat(day)
    prev_days = {(d - timedelta(days=i)).isoformat() for i in range(1, 8)}
    today_rows, prev_rows, groups = [], [], []
    for g in ds["groups"]:
        if g["status"] == "deleted":
            continue
        t = [r for r in g["daily"] if r["date"] == day]
        p = [r for r in g["daily"] if r["date"] in prev_days]
        today_rows += t
        prev_rows += p
        ts, ps = aggregate(t), aggregate(p)
        avg = ps["spent"] / 7
        flags = []
        if ts["spent"] == 0 and avg > 0:
            flags.append("stopped")
        elif avg and ts["spent"] > avg * SPIKE:
            flags.append("spend_spike")
        if ts["cpa"] and ps["cpa"] and ts["cpa"] > ps["cpa"] * CPA_JUMP:
            flags.append("cpa_jump")
        if ts["cpa"] and target_cpa and ts["cpa"] > target_cpa:
            flags.append("over_target")
        groups.append({"id": g["id"], "name": g["name"], "spent": ts["spent"],
                       "conversions": ts["conversions"], "cpa": ts["cpa"], "flags": flags})
    today, prev = aggregate(today_rows), aggregate(prev_rows)
    lines = [f"📅 VK Реклама — {d.strftime('%d.%m.%Y')}"]
    if not today_rows:
        lines.append("Данных за этот день нет — возможно, статистика ещё не пришла.")
    else:
        lines.append(f"Расход: {_rub(today['spent'])} (в среднем {_rub(prev['spent'] / 7)} в день за неделю)")
        lines.append(f"Конверсии: {today['conversions']} · CPA: {_rub(today['cpa'])}")
    flagged = sorted((g for g in groups if g["flags"]), key=lambda g: -g["spent"])
    for g in flagged[:MAX_LINES - len(lines) - 1]:
        f = g["flags"][0]
        lines.append(f"{ICON[f]} «{g['name']}»: {TEXT[f]} ({_rub(g['spent'])}, CPA {_rub(g['cpa'])})")
    if flagged:
        lines.append("→ Разобрать подробно? Могу провести глубокий аудит.")
    elif today_rows:
        lines.append("Аномалий нет.")
    return {"day": day, "today": today, "prev_avg": {k: v for k, v in prev.items()},
            "groups": groups, "lines": lines[:MAX_LINES]}


def main(argv=None) -> int:
    setup_console()
    parser = argparse.ArgumentParser(description="Быстрый анализ дня")
    parser.add_argument("--workspace", required=True)
    parser.add_argument("--day", required=True)
    parser.add_argument("--target-cpa", type=float)
    args = parser.parse_args(argv)
    ws = resolve_workspace(args.workspace)
    ds = json.loads((ws / "dataset.json").read_text(encoding="utf-8"))
    res = quick_summary(ds, args.day, args.target_cpa)
    (ws / "quick.json").write_text(json.dumps(res, ensure_ascii=False, indent=1), encoding="utf-8")
    print("\n".join(res["lines"]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
