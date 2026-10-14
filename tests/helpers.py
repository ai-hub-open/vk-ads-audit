from __future__ import annotations

from scripts.normalize import _ratios, targeting_key


def stats(spent=0, shows=0, clicks=0, conversions=0, frequency=None):
    return {"spent": float(spent), "shows": shows, "clicks": clicks, "conversions": conversions,
            **_ratios(float(spent), shows, clicks, conversions),
            "vk_ctr_pct": None, "reach": 0, "frequency": frequency}


def make_group(id=1, **kw):
    g = {"id": id, "name": f"G{id}", "status": "active", "objective": "site_conversions", "package_id": 1,
         "ad_plan_id": 7, "delivery": "delivering", "autobidding_mode": None, "budget_day_rub": 1000.0,
         "budget_total_rub": None, "issues": [], "created": "2026-09-01 00:00:00", "date_end": None,
         "targeting_key": targeting_key({"geo": {"regions": [id]}}), "segment_ids": [],
         "stats": stats(), "daily": [], "banner_ids": []}
    g.update(kw)
    return g


def make_banner(id=11, group_id=1, **kw):
    b = {"id": id, "name": f"B{id}", "group_id": group_id, "status": "active", "moderation_status": "allowed",
         "moderation_reasons": None, "issues": [], "can_remoderate": False, "stats": None}
    b.update(kw)
    return b


def make_ds(groups, banners=(), **kw):
    ds = {"period": {"from": "2026-09-01", "to": "2026-09-30", "days": 30}, "target_cpa": None,
          "plans": [{"id": 7, "name": "План", "status": "active"}], "groups": list(groups),
          "banners": list(banners), "segments": [], "users_lists": [], "pixels": [{"id": 1, "name": "px"}],
          "totals": stats(), "coverage": {"path": "B", "banners": True, "summary": True, "stats": True,
                                 "pixels": True, "metrika": False}}
    ds.update(kw)
    for g in ds["groups"]:
        if not g["banner_ids"]:
            g["banner_ids"] = [b["id"] for b in ds["banners"] if b["group_id"] == g["id"]]
    return ds
