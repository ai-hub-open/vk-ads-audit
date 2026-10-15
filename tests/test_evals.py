from __future__ import annotations

import pytest

from evals.fixtures import CABINETS
from evals.run_evals import checklist_of, run

NEW_RULES = {"ord_missing", "utm_missing", "no_retargeting", "layer_missing", "layer_share", "mixed_plan",
             "fragmentation", "no_cbo", "naming", "no_minus_phrases", "kw_period", "kw_expand_off", "no_exclusions",
             "too_broad", "creative_overlap", "lal_small_source", "stale_list", "manual_placements", "cap_too_early",
             "missing_ratios", "no_video", "stale_creatives", "no_cta_text", "generic_button", "moderation_stuck",
             "cpc_outlier", "cpa_trend"}


@pytest.mark.parametrize("name", sorted(CABINETS))
def test_cabinet(name, tmp_path):
    found, expected = run(name, tmp_path)
    assert found == expected


def test_cabinets_listed():
    assert set(CABINETS) == {"healthy", "leaking", "hygiene", "empty", "path_a_partial", "expert_1", "expert_2"}


def test_every_new_rule_fires_somewhere(tmp_path):
    fired = set()
    for name in ("expert_1", "expert_2"):
        found, _ = run(name, tmp_path)          # для экспертных кабинетов found — множество сработавших правил
        fired |= found
    assert NEW_RULES <= fired, sorted(NEW_RULES - fired)


def test_healthy_checklist_has_no_fails(tmp_path):
    cl = checklist_of("healthy", tmp_path)
    fails = [c["id"] for b in cl["blocks"] for c in b["checks"] if c["status"] == "fail"]
    assert fails == ["B1.subscription", "B1.leadform", "B1.senler"]   # воронок этих нет — это не ошибка кабинета


def test_expert_checklist_mostly_fails(tmp_path):
    cl = checklist_of("expert_1", tmp_path)
    fails = {c["id"] for b in cl["blocks"] for c in b["checks"] if c["status"] == "fail"}
    assert {"A2", "A3", "B4", "B5", "B6", "B7", "C7", "C8", "C9", "C10", "C11", "C12", "C13", "C14", "C15",
            "D2", "E1", "E2", "E4", "E6", "E7", "E8", "E9", "F2", "F3", "F4"} <= fails


def test_path_a_partial_covers_only_five_groups(tmp_path):
    from evals.fixtures import path_a_partial
    from scripts.normalize import build_dataset
    meta = path_a_partial(tmp_path / "raw")
    ds = build_dataset(tmp_path / "raw", meta["date_from"], meta["date_to"], meta["target_cpa"], "A")
    assert ds["coverage"]["banner_groups"] == [1, 2, 3, 4, 5]
    assert any("для 2 групп" in n for n in ds["notes"])
