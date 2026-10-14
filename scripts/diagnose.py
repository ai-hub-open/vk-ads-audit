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


def _is_auto(g, th) -> bool:
    return g["autobidding_mode"] in th["auto_bidding_modes"]


def _run_days(ds, g) -> int:
    rows = g["daily"]
    return sum(1 for r in rows if r["shows"] > 0) if rows else ds["period"]["days"]


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
        codes = [c for c in g["issues"] if c in th["blocking_issue_codes"]]
        if _active(g, th) and codes:
            out.append(finding("blocking_issues", "orange", "group", g, "VK сообщает, почему группа не крутится",
                               {"issues": codes}, "Устранить причину: " + ", ".join(codes)))
    return out


@rule
def autodelete_risk(ds, th, ctx):
    if not ds["coverage"]["stats"]:  # статистики не собирали — «нет показов» было бы выдумкой
        return []
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
        if not _active(g, th) or not b:  # None или 0 — дневного лимита нет, сравнивать не с чем
            continue
        if b < th["min_daily_budget_rub"]:
            out.append(finding("low_budget", "orange", "group", g, "Дневной бюджет ниже минимума VK",
                               {"budget_day_rub": b, "minimum_rub": th["min_daily_budget_rub"]},
                               f"Поднять дневной бюджет до {th['min_daily_budget_rub']} ₽ и выше"))
        elif _is_auto(g, th) and ctx["ref_cpa"] is not None and \
                b < ctx["ref_cpa"] * th["learning_budget_cpa_multiplier"]:
            need = ctx["ref_cpa"] * th["learning_budget_cpa_multiplier"]
            out.append(finding("low_budget", "orange", "group", g,
                               "Бюджета не хватает на обучение автостратегии",
                               {"budget_day_rub": b, "needed_rub": need, "ref_source": ctx["ref_source"]},
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
    if ds["pixels"] or not ds["coverage"]["pixels"]:  # пустой список при загруженном запросе — факт
        return []
    return [finding("no_pixel", "orange", "group", g, "Цель «конверсии на сайте», а пикселя в кабинете нет",
                    {"objective": g["objective"]}, "Установить пиксель VK и настроить события")
            for g in _live(ds) if _active(g, th) and g["objective"] in th["pixel_objectives"]]


# ---------- эффективность ----------

@rule
def spend_no_conversions(ds, th, ctx):
    ref = ctx["ref_cpa"]
    limit = ref * th["no_conv_spend_cpa_multiplier"] if ref else th["no_conv_min_spend_rub"]
    out = []
    for g in _live(ds):
        s = g["stats"]
        if s["conversions"] == 0 and s["spent"] >= limit:
            out.append(finding("spend_no_conversions", "red", "group", g, "Расход без конверсий",
                               {"spent": s["spent"], "clicks": s["clicks"], "limit_rub": round(limit, 2),
                                "ref_source": ctx["ref_source"]},
                               "Остановить или пересобрать: аудитория, креатив, посадочная, событие пикселя",
                               effect_rub=s["spent"], conf=confidence(s["clicks"], th)))
    return out


@rule
def cpa_above_target(ds, th, ctx):
    ref = ctx["ref_cpa"]
    if not ref:
        return []
    out = []
    for g in _live(ds):
        s = g["stats"]
        if s["conversions"] >= th["min_conversions_for_cpa"] and s["cpa"] > ref * th["cpa_over_ratio"]:
            over = s["spent"] - s["conversions"] * ref
            out.append(finding("cpa_above_target", "red", "group", g, "CPA заметно выше опорного",
                               {"cpa": s["cpa"], "ref_cpa": ref, "ref_source": ctx["ref_source"],
                                "conversions": s["conversions"]},
                               "Сузить или сменить аудиторию, заменить слабые креативы, снизить ставку",
                               effect_rub=over, conf=confidence(s["clicks"], th)))
    return out


@rule
def low_ctr(ds, th, ctx):
    with_stats = [b for b in ds["banners"] if b["stats"] and b["status"] != "deleted"]
    if with_stats:
        live_ids = {g["id"] for g in _live(ds)}
        entities = [("banner", b, b["stats"]) for b in with_stats if b["group_id"] in live_ids]
    else:
        entities = [("group", g, g["stats"]) for g in _live(ds)]
    return [finding("low_ctr", "orange", etype, ent, "Низкий CTR при достаточных показах",
                    {"ctr_pct": s["ctr_pct"], "shows": s["shows"], "threshold_pct": th["low_ctr_pct"]},
                    "Заменить креатив или проверить, той ли аудитории он показывается",
                    effect_rub=s["spent"], conf=confidence(s["clicks"], th))
            for etype, ent, s in entities
            if s["shows"] >= th["low_ctr_min_shows"] and s["ctr_pct"] < th["low_ctr_pct"]]


def _ctr(rows):
    shows = sum(r["shows"] for r in rows)
    return sum(r["clicks"] for r in rows) / shows * 100 if shows else 0.0


@rule
def burnout(ds, th, ctx):
    out = []
    for g in _live(ds):
        rows = g["daily"]
        freq = g["stats"]["frequency"]
        if len(rows) < 4 or not freq or freq < th["burnout_min_frequency"] \
                or g["stats"]["shows"] < th["burnout_min_shows"]:
            continue
        rows = sorted(rows, key=lambda r: r["date"] or "")
        half = len(rows) // 2
        first, second = _ctr(rows[:half]), _ctr(rows[half:])
        if first and (first - second) / first >= th["burnout_ctr_drop"]:
            out.append(finding("burnout", "orange", "group", g, "Выгорание: CTR падает, частота высокая",
                               {"ctr_first_half_pct": round(first, 3), "ctr_second_half_pct": round(second, 3),
                                "frequency": freq},
                               "Обновить креативы или расширить аудиторию",
                               effect_rub=sum(r["spent"] for r in rows[half:])))
    return out


@rule
def learning_starved(ds, th, ctx):
    out = []
    for g in _live(ds):
        s = g["stats"]
        if not _active(g, th) or not _is_auto(g, th) or s["spent"] <= 0:
            continue
        weeks = max(_run_days(ds, g) / 7, 1)
        per_week = s["conversions"] / weeks
        if per_week < th["learning_min_conversions_per_week"]:
            out.append(finding("learning_starved", "orange", "group", g,
                               "Автостратегии не хватает конверсий для обучения",
                               {"conversions_per_week": round(per_week, 1),
                                "needed": th["learning_min_conversions_per_week"]},
                               "Объединить группы, расширить аудиторию или оптимизировать на более частое событие",
                               conf=confidence(s["clicks"], th)))
    return out


@rule
def spend_concentration(ds, th, ctx):
    live = [g for g in _live(ds) if g["stats"]["spent"] > 0]
    total = sum(g["stats"]["spent"] for g in live)
    if len(live) < th["concentration_min_groups"] or not total:
        return []
    top = max(live, key=lambda g: g["stats"]["spent"])
    share = top["stats"]["spent"] / total
    if share < th["concentration_share"]:
        return []
    return [finding("spend_concentration", "white", "group", top, "Почти весь расход в одной группе",
                    {"share": round(share, 3), "groups_with_spend": len(live)},
                    "Проверить, не голодают ли остальные группы, и осознанно перераспределить бюджет")]


@rule
def scale_leader(ds, th, ctx):
    ref = ctx["ref_cpa"]
    if not ref:
        return []
    out = []
    for g in _live(ds):
        s, b = g["stats"], g["budget_day_rub"]
        if not _active(g, th) or not b or not g["daily"] or s["conversions"] < th["min_conversions_for_cpa"]:
            continue
        spend_days = [r["spent"] for r in g["daily"] if r["spent"] > 0]
        if not spend_days:
            continue
        avg = sum(spend_days) / len(spend_days)
        if s["cpa"] <= ref * th["leader_cpa_ratio"] and avg >= b * th["leader_budget_use"]:
            out.append(finding("scale_leader", "green", "group", g, "Лидер упирается в бюджет",
                               {"cpa": s["cpa"], "ref_cpa": ref, "avg_daily_spent": round(avg, 2),
                                "budget_day_rub": b},
                               f"Поднять дневной бюджет на {int(th['leader_growth_share'] * 100)} % и следить за CPA",
                               effect_rub=avg * th["leader_growth_share"] * ds["period"]["days"],
                               conf=confidence(s["clicks"], th)))
    return out


# ---------- метрика ----------

def _with_metrika(ds, th):
    return [g for g in _live(ds) if _active(g, th) and g.get("metrika")]


@rule
def high_bounce(ds, th, ctx):
    return [finding("high_bounce", "orange", "group", g, "Высокий отказ трафика из группы (Метрика)",
                    {"bounce_rate": g["metrika"]["bounce_rate"], "visits": g["metrika"]["visits"]},
                    "Проверить соответствие посадочной обещанию креатива и скорость загрузки",
                    effect_rub=g["stats"]["spent"] * g["metrika"]["bounce_rate"],
                    conf=confidence(g["metrika"]["visits"], th))
            for g in _with_metrika(ds, th)
            if g["metrika"]["visits"] >= th["metrika_min_visits"]
            and (g["metrika"]["bounce_rate"] or 0) >= th["high_bounce_rate"]]


@rule
def click_loss(ds, th, ctx):
    out = []
    for g in _with_metrika(ds, th):
        clicks, visits = g["stats"]["clicks"], g["metrika"]["visits"]
        if clicks >= th["click_loss_min_clicks"] and visits / clicks < th["click_loss_visit_ratio"]:
            out.append(finding("click_loss", "orange", "group", g, "Кликов в VK заметно больше, чем визитов в Метрике",
                               {"clicks": clicks, "visits": visits, "ratio": round(visits / clicks, 3)},
                               "Проверить скорость посадочной, редиректы и разметку UTM",
                               effect_rub=g["stats"]["spent"] * (1 - visits / clicks)))
    return out


@rule
def goals_without_vk_conversions(ds, th, ctx):
    return [finding("goals_without_vk_conversions", "orange", "group", g,
                    "Цели в Метрике есть, а конверсий в VK нет — пиксель или атрибуция",
                    {"metrika_goal_reaches": g["metrika"]["goal_reaches"], "vk_conversions": 0},
                    "Проверить пиксель VK и событие оптимизации группы")
            for g in _with_metrika(ds, th)
            if g["metrika"]["goal_reaches"] > 0 and g["stats"]["conversions"] == 0]


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
    if not (ws / "dataset.json").exists():
        print("нет dataset.json — сначала нормализация (python -m scripts.normalize ...)", file=sys.stderr)
        return 2
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
