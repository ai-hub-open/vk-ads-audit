from __future__ import annotations

import json
import re
import zipfile

from scripts import features
from scripts._workspace import SKILL_ROOT
from scripts.diagnose import CHECK_OF, RULES, load_thresholds
from scripts.make_bundle import build
from scripts.render_report import ALWAYS_LIMITATIONS

SKILL = (SKILL_ROOT / "SKILL.md").read_text(encoding="utf-8")


def test_skill_md_size():
    assert len(SKILL.splitlines()) <= 400


def test_frontmatter():
    assert SKILL.startswith("---\nname: \"vk-ads-audit\"\ndescription: \"")


def test_referenced_files_exist():
    for rel in set(re.findall(r"`((?:references|scripts)/[\w./-]+)`", SKILL)):
        assert (SKILL_ROOT / rel).exists(), rel


def test_versions_match():
    plugin = json.loads((SKILL_ROOT / ".claude-plugin" / "plugin.json").read_text(encoding="utf-8"))
    version = (SKILL_ROOT / "VERSION").read_text(encoding="utf-8").strip()
    top = re.search(r"^## (\d+\.\d+\.\d+)", (SKILL_ROOT / "CHANGELOG.md").read_text(encoding="utf-8"), re.M)
    assert plugin["version"] == version == top.group(1)


def test_every_rule_documented():
    doc = (SKILL_ROOT / "references" / "diagnostic-rules.md").read_text(encoding="utf-8")
    for fn in RULES:
        assert f"`{fn.__name__}`" in doc, fn.__name__


def test_every_threshold_key_documented():
    doc = (SKILL_ROOT / "references" / "diagnostic-rules.md").read_text(encoding="utf-8")
    for key in load_thresholds():
        if not key.startswith("_"):
            assert key in doc, key


def test_limitations_doc_matches_report():
    doc = (SKILL_ROOT / "references" / "limitations.md").read_text(encoding="utf-8")
    for line in ALWAYS_LIMITATIONS:
        assert line in doc


def test_bundle_excludes_dev_and_secrets(tmp_path):
    env = SKILL_ROOT / ".env"
    created = not env.exists()
    if created:
        env.write_text("SECRET=1", encoding="utf-8")
    try:
        names = zipfile.ZipFile(build(tmp_path / "vk-ads-audit.zip")).namelist()
    finally:
        if created:
            env.unlink()
    assert "vk-ads-audit/SKILL.md" in names and "vk-ads-audit/VERSION" in names
    assert "vk-ads-audit/references/thresholds.json" in names
    for bad in ("tests/", "evals/", "docs/", ".github/", ".claude-plugin/", ".env", "__pycache__", ".superpowers"):
        assert not any(bad in n for n in names), bad


def _ref(name):
    return (SKILL_ROOT / "references" / name).read_text(encoding="utf-8")


def test_skill_covers_401_and_path_a_log_and_duplicates():
    assert "HTTP 401" in SKILL and "credentials set" in SKILL and "переавторизовать" in SKILL
    assert '"critical": false' in SKILL and "collect_log.json" in SKILL and '"ok": false' in SKILL
    assert "но не оба" in SKILL


def test_references_document_new_behaviour():
    tools, lim, tpl, rules = (_ref("mcp-read-tools.md"), _ref("limitations.md"),
                              _ref("report-template.md"), _ref("diagnostic-rules.md"))
    assert "stateless" in tools and "Удалённые объявления" in tools
    assert "Метрика без UTM-разметки" in lim and "Объявления не собраны для N групп" in lim
    assert "Статистика не получена для N групп" in lim and "Сумма по объявлениям меньше" in lim
    assert "client_note" in tpl and "Объяснено клиентом" in tpl
    assert "banner_groups" in rules and "_measured" in rules and "stats_missing" in rules


def test_every_rule_has_check():
    assert {fn.__name__ for fn in RULES} - {"scale_leader"} <= set(CHECK_OF)


def test_check_ids_documented():
    doc = (SKILL_ROOT / "references" / "diagnostic-rules.md").read_text(encoding="utf-8")
    for cid in set(CHECK_OF.values()):
        assert f"`{cid}`" in doc, cid


def test_api_field_candidates_listed_for_live_gate():
    doc = (SKILL_ROOT / "references" / "mcp-read-tools.md").read_text(encoding="utf-8")
    for name in ("KEYWORD_KEYS", "MINUS_KEYS", "KW_PERIOD_KEYS", "KW_EXPAND_KEYS", "EXCLUDE_KEYS", "COMMUNITY_KEYS",
                 "AUTO_PADS_KEYS", "DEVICE_ADJ_KEYS", "DEMO_ADJ_KEYS", "ERID_KEYS", "AUTO_UTM_KEYS",
                 "PLAN_BUDGET_KEYS", "SIZE_KEYS", "UPDATED_KEYS"):
        for key in getattr(features, name):
            assert f"`{key}`" in doc, key


def test_client_questions_documented():
    from scripts.questions import build_questions
    from tests.helpers import make_ds
    doc = (SKILL_ROOT / "references" / "client-questions.md").read_text(encoding="utf-8")
    for q in build_questions(make_ds([]), load_thresholds(), []):
        assert q["text"] in doc
