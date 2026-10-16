"""
features.py — признаки для методики v1 из сырых сущностей VK: таргетинг, объявления, сегменты.

Имена полей с пометкой «API?» взяты по документации и подтверждаются живым прогоном
(references/mcp-read-tools.md). Правило известности: поле считается известным, если его ключ
встретился хотя бы у одной сущности кабинета. Неизвестное поле → признак None → проверка «—».
"""
from __future__ import annotations

import json
import re
from datetime import datetime

INTEREST_KEYS = ("interests", "interests_soc_dem", "interests_stable")
COMMUNITY_KEYS = ("groups", "vk_groups", "ok_groups", "communities")                  # API?
KEYWORD_KEYS = ("keywords", "search_phrases", "vk_search_phrases", "context_phrases")  # API?
MINUS_KEYS = ("minus_phrases", "negative_phrases", "keywords_minus")                 # API?
KW_PERIOD_KEYS = ("search_period", "keywords_period", "phrases_period")              # API?
KW_EXPAND_KEYS = ("expand_keywords", "keywords_expansion", "phrases_expansion")      # API?
EXCLUDE_KEYS = ("segments_exclude", "exclude_segments", "excluded_segments")         # API?
AUTO_PADS_KEYS = ("auto_placements", "pads_auto")                                    # API?
DEVICE_ADJ_KEYS = ("device_bid_adjustments", "mobile_bid_adjustments")               # API?
DEMO_ADJ_KEYS = ("demography_bid_adjustments", "sex_age_bid_adjustments")            # API?
ERID_KEYS = ("ord_marker",)                                                          # подтверждено 16.10.2026 (объявление)
AUTO_UTM_KEYS = ("enable_utm",)                                                     # подтверждено 16.10.2026 (группа)
PLAN_BUDGET_KEYS = ("budget_limit", "budget_limit_day")                              # API? (кампания)
SIZE_KEYS = ("size", "count", "entries_count", "audience_size")                      # API? (сегмент, база)
UPDATED_KEYS = ("updated", "last_upload", "uploaded")                                # API? (база)
SEGMENT_RELATION_KEYS = ("relations",)                                               # API? (сегмент → список связей)
RELATION_TYPE_KEYS = ("object_type",)                                                # API? (элемент связи)
RELATION_ID_KEYS = ("object_id",)                                                      # API? (элемент связи)
USERS_LIST_TYPE_MARKERS = ("users_list", "user_list")                                # API? (тип объекта связи)
RATIOS = {"1:1": 1.0, "4:5": 0.8, "16:9": 16 / 9, "9:16": 9 / 16}
RATIO_TOL = 0.04
_LAL_RE = re.compile(r"lookalike|look-alike|\blal\b|похож", re.I)


def observed_keys(dicts) -> set:
    return {k for d in dicts if isinstance(d, dict) for k in d}


def _flag(t: dict, observed: set, keys) -> bool | None:
    if not any(k in observed for k in keys):
        return None
    return any(bool(t.get(k)) for k in keys)


def first_known(d: dict, keys, observed: set):
    """Значение первого известного ключа; None — если ни один ключ не встречался в кабинете."""
    if not any(k in observed for k in keys):
        return None
    return next((d[k] for k in keys if d.get(k) is not None), None)


def targeting_features(t, observed: set) -> dict:
    t = t if isinstance(t, dict) else {}
    geo = t.get("geo") if isinstance(t.get("geo"), dict) else {}
    regions = [r for r in (geo.get("regions") or []) if isinstance(r, int)]
    age = t.get("age") if isinstance(t.get("age"), dict) else {}
    ages = [a for a in (age.get("age_list") or []) if isinstance(a, int)]
    period = first_known(t, KW_PERIOD_KEYS, observed)
    return {
        "keys": sorted(t.keys()),
        "regions": regions,
        "age_span": (max(ages) - min(ages)) if ages else None,
        "sex": list(t.get("sex") or []),
        "has_interests": any(bool(t.get(k)) for k in INTEREST_KEYS),
        "has_communities": _flag(t, observed, COMMUNITY_KEYS),
        "has_keywords": _flag(t, observed, KEYWORD_KEYS),
        "has_minus": _flag(t, observed, MINUS_KEYS),
        "kw_period": period if isinstance(period, int) and not isinstance(period, bool) else None,
        "kw_expand": _flag(t, observed, KW_EXPAND_KEYS),
        "has_exclusions": _flag(t, observed, EXCLUDE_KEYS),
        "auto_placements": _flag(t, observed, AUTO_PADS_KEYS),
        "device_adjustments": _flag(t, observed, DEVICE_ADJ_KEYS),
        "demo_adjustments": _flag(t, observed, DEMO_ADJ_KEYS),
    }


def _ratio_of(role: str):
    sizes = re.search(r"(\d{3,4})x(\d{3,4})", role)
    if sizes:
        return int(sizes.group(1)) / int(sizes.group(2))
    pair = re.search(r"(?<!\d)(\d{1,2})_(\d{1,2})(?!\d)", role)
    if pair:
        return int(pair.group(1)) / int(pair.group(2))
    return None


def content_features(content) -> dict:
    ratios, video, ids = set(), False, []
    for role, item in (content.items() if isinstance(content, dict) else []):
        r = str(role).lower()
        video = video or r.startswith("video")
        if isinstance(item, dict) and item.get("id") is not None:
            ids.append(item["id"])
        if not r.startswith(("image", "video")):
            continue
        value = _ratio_of(r)
        if value is None:
            continue
        for name, target in RATIOS.items():
            if abs(value - target) / target <= RATIO_TOL:
                ratios.add(name)
    return {"ratios": sorted(ratios), "has_video": video, "content_ids": ids}


def url_strings(v) -> list:
    out: list = []
    if isinstance(v, dict):
        for k, x in v.items():
            if k == "url" and isinstance(x, str):
                out.append(x)
            else:
                out.extend(url_strings(x))
    elif isinstance(v, list):
        for x in v:
            out.extend(url_strings(x))
    return out


def banner_text(textblocks) -> str:
    if not isinstance(textblocks, dict):
        return ""
    parts = [tb.get("text") for tb in textblocks.values() if isinstance(tb, dict)]
    return " ".join(p for p in parts if isinstance(p, str) and p.strip())


def is_lal(segment) -> bool:
    return bool(_LAL_RE.search(json.dumps(segment, ensure_ascii=False)))


def layer_of(segment_ids, segments_by_id: dict):
    """cold / warm / lal; None — сегмент не найден в списке сегментов (или список не собран): слой неизвестен."""
    if not segment_ids:
        return "cold"
    if any(sid in segments_by_id and is_lal(segments_by_id[sid]) for sid in segment_ids):
        return "lal"
    if any(sid not in segments_by_id for sid in segment_ids):
        return None
    return "warm"


def users_list_ids(segment) -> list:
    """id пользовательских баз, на которые ссылается сегмент (связи relations)."""
    out: list = []
    for rel_key in SEGMENT_RELATION_KEYS:
        for rel in (segment.get(rel_key) or []) if isinstance(segment, dict) else []:
            if not isinstance(rel, dict):
                continue
            kind = next((rel[k] for k in RELATION_TYPE_KEYS if isinstance(rel.get(k), str)), "")
            oid = next((rel[k] for k in RELATION_ID_KEYS if rel.get(k) is not None), None)
            if oid is not None and any(m in kind.lower() for m in USERS_LIST_TYPE_MARKERS):
                out.append(oid)
    return out


def parse_dt(s):
    if not isinstance(s, str) or len(s) < 10:
        return None
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%dT%H:%M", "%Y-%m-%d"):
        try:
            return datetime.strptime(s[:19] if "T" in s or " " in s else s[:10], fmt)
        except ValueError:
            continue
    return None
