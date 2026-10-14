"""
diagnose.py — dataset.json + references/thresholds.json → findings.json.

Каждое правило — функция (ds, th, ctx) -> list[finding], зарегистрированная @rule.
Модель не считает: все числа находок — из dataset.json. Методичка меняет пороги
(thresholds.json) и правила здесь, контракт находки остаётся прежним.

    python -m scripts.diagnose --workspace <абс. путь> [--thresholds <path>]
"""
from __future__ import annotations

import argparse
import json
import statistics
import sys
from collections import defaultdict
from datetime import date, datetime
from pathlib import Path

try:
    from scripts._console import setup_console
    from scripts._workspace import SKILL_ROOT, resolve_workspace
except ImportError:  # запуск напрямую
    from _console import setup_console
    from _workspace import SKILL_ROOT, resolve_workspace

LEVEL_ORDER = {"red": 0, "orange": 1, "green": 2, "white": 3}
DEFAULT_THRESHOLDS = SKILL_ROOT / "references" / "thresholds.json"
RULES: list = []


def rule(fn):
    RULES.append(fn)
    return fn


def load_thresholds(path=None) -> dict:
    return json.loads(Path(path or DEFAULT_THRESHOLDS).read_text(encoding="utf-8"))


def finding(rule_id, level, etype, ent, title, evidence, action, effect_rub=0.0, conf="high") -> dict:
    return {"rule": rule_id, "level": level,
            "entity": {"type": etype, "id": ent.get("id"), "name": ent.get("name", "")},
            "title": title, "evidence": evidence, "effect_rub": round(float(effect_rub), 2),
            "confidence": conf, "action": action}


def confidence(clicks: int, th) -> str:
    if clicks >= th["confidence_high_clicks"]:
        return "high"
    return "medium" if clicks >= th["confidence_medium_clicks"] else "low"


def _active(g, th) -> bool:
    return g["status"] in th["active_statuses"]


def _live(ds):
    return [g for g in ds["groups"] if g["status"] != "deleted"]


def reference_cpa(ds, th):
    if ds.get("target_cpa"):
        return float(ds["target_cpa"]), "target"
    cpas = [g["stats"]["cpa"] for g in _live(ds)
            if g["stats"]["cpa"] is not None and g["stats"]["conversions"] >= th["min_conversions_for_cpa"]]
    if cpas:
        return round(float(statistics.median(cpas)), 2), "median"
    return None, "none"


def _banners_of(ds, g):
    ids = set(g["banner_ids"])
    return [b for b in ds["banners"] if b["id"] in ids]


# ---------- гигиена ----------

@rule
def no_active_banners(ds, th, ctx):
    if not ds["coverage"]["banners"]:
        return []
    out = []
    for g in _live(ds):
        if not _active(g, th):
            continue
        active = [b for b in _banners_of(ds, g) if b["status"] in th["active_statuses"]]
        if not active or "NO_BANNERS_WITH_ACTIVE_STATUS" in g["issues"]:
            out.append(finding("no_active_banners", "orange", "group", g,
                               "Группа включена, но крутить нечего — нет активных объявлений",
                               {"banners_total": len(g["banner_ids"]), "banners_active": len(active)},
                               "Добавить или включить объявления — либо выключить группу"))
    return out


@rule
def rejected_banners(ds, th, ctx):
    return [finding("rejected_banners", "orange", "banner", b, "Объявление отклонено модерацией",
                    {"moderation_status": b["moderation_status"], "reasons": b["moderation_reasons"],
                     "can_remoderate": b["can_remoderate"]},
                    "Исправить по причине отказа и отправить на перемодерацию"
                    if b["can_remoderate"] else "Исправить и создать заново: перемодерация недоступна")
            for b in ds["banners"]
            if b["status"] != "deleted" and b["moderation_status"] in th["rejected_moderation_statuses"]]


@rule
def blocking_issues(ds, th, ctx):
    out = []
    for g in _live(ds):
        codes = [c for c in g["issues"] if c in th["blocking_issue_codes"] and c != "NO_BANNERS_WITH_ACTIVE_STATUS"]
        if _active(g, th) and codes:
            out.append(finding("blocking_issues", "orange", "group", g, "VK сообщает, почему группа не крутится",
                               {"issues": codes}, "Устранить причину: " + ", ".join(codes)))
    return out


@rule
def autodelete_risk(ds, th, ctx):
    out = []
    end = date.fromisoformat(ds["period"]["to"])
    for g in _live(ds):
        if not g.get("created") or g["stats"]["shows"]:
            continue
        age = (end - datetime.strptime(g["created"][:10], "%Y-%m-%d").date()).days
        if age >= th["autodelete_warn_days"]:
            out.append(finding("autodelete_risk", "white", "group", g,
                               "Нет показов за период, группа старая — VK удаляет группы без показов за 180 дней",
                               {"age_days": age, "shows": 0},
                               "Решить: перезапустить или осознанно выключить"))
    return out


@rule
def banners_per_group(ds, th, ctx):
    if not ds["coverage"]["banners"]:
        return []
    out = []
    for g in _live(ds):
        if not _active(g, th):
            continue
        n = len([b for b in _banners_of(ds, g) if b["status"] in th["active_statuses"]])
        if n == 1:
            out.append(finding("banners_per_group", "white", "group", g,
                               "В группе одно объявление — алгоритму не из чего выбирать",
                               {"active_banners": n}, "Добавить 2–4 варианта креатива"))
        elif n > th["max_active_banners_per_group"]:
            out.append(finding("banners_per_group", "white", "group", g,
                               "Слишком много объявлений — бюджет размазан, выводы по каждому слабые",
                               {"active_banners": n}, "Оставить лучшие по результату"))
    return out


@rule
def low_budget(ds, th, ctx):
    out = []
    for g in _live(ds):
        b = g["budget_day_rub"]
        if not _active(g, th) or b is None:
            continue
        if b < th["min_daily_budget_rub"]:
            out.append(finding("low_budget", "orange", "group", g, "Дневной бюджет ниже минимума VK",
                               {"budget_day_rub": b, "minimum_rub": th["min_daily_budget_rub"]},
                               f"Поднять дневной бюджет до {th['min_daily_budget_rub']} ₽ и выше"))
        elif g["autobidding_mode"] and ds.get("target_cpa") and \
                b < ds["target_cpa"] * th["learning_budget_cpa_multiplier"]:
            need = ds["target_cpa"] * th["learning_budget_cpa_multiplier"]
            out.append(finding("low_budget", "orange", "group", g,
                               "Бюджета не хватает на обучение автостратегии",
                               {"budget_day_rub": b, "needed_rub": need},
                               f"Дневной бюджет не ниже {need:.0f} ₽ (≥ {th['learning_budget_cpa_multiplier']} × CPA)"))
    return out


@rule
def duplicate_targeting(ds, th, ctx):
    by_key = defaultdict(list)
    for g in _live(ds):
        if _active(g, th):
            by_key[(g["objective"], g["targeting_key"])].append(g)
    out = []
    for gs in by_key.values():
        if len(gs) > 1:
            out.append(finding("duplicate_targeting", "orange", "group", gs[0],
                               "Одинаковые таргетинги у активных групп — они конкурируют между собой",
                               {"groups": [g["id"] for g in gs]},
                               "Объединить группы или развести аудитории"))
    return out


@rule
def unused_segments(ds, th, ctx):
    used = {i for g in _live(ds) for i in g["segment_ids"]}
    return [finding("unused_segments", "white", "segment", s, "Сегмент не используется ни в одной группе",
                    {}, "Удалить или запустить на него группу")
            for s in ds["segments"] if s["id"] not in used]


@rule
def no_pixel(ds, th, ctx):
    if ds["pixels"] or not ds["coverage"]["banners"]:
        return []
    return [finding("no_pixel", "orange", "group", g, "Цель «конверсии на сайте», а пикселя в кабинете нет",
                    {"objective": g["objective"]}, "Установить пиксель VK и настроить события")
            for g in _live(ds) if _active(g, th) and g["objective"] in th["pixel_objectives"]]


# ---------- запуск ----------

def diagnose(ds: dict, th: dict) -> dict:
    ref, source = reference_cpa(ds, th)
    ctx = {"ref_cpa": ref, "ref_source": source}
    out = []
    for fn in RULES:
        out.extend(fn(ds, th, ctx))
    out.sort(key=lambda f: (LEVEL_ORDER[f["level"]], -f["effect_rub"]))
    return {"ref_cpa": ref, "ref_source": source, "findings": out}


def main(argv=None) -> int:
    setup_console()
    parser = argparse.ArgumentParser(description="dataset.json → findings.json")
    parser.add_argument("--workspace", required=True)
    parser.add_argument("--thresholds")
    args = parser.parse_args(argv)
    ws = resolve_workspace(args.workspace)
    ds = json.loads((ws / "dataset.json").read_text(encoding="utf-8"))
    result = diagnose(ds, load_thresholds(args.thresholds))
    (ws / "findings.json").write_text(json.dumps(result, ensure_ascii=False, indent=1), encoding="utf-8")
    counts = defaultdict(int)
    for f in result["findings"]:
        counts[f["level"]] += 1
    print(f"находок: {len(result['findings'])} ({dict(counts)}), опорный CPA: {result['ref_cpa']} "
          f"({result['ref_source']})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
