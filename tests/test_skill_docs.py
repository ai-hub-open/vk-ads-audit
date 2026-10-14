from __future__ import annotations

import json
import re
import zipfile

from scripts._workspace import SKILL_ROOT
from scripts.diagnose import RULES, load_thresholds
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
