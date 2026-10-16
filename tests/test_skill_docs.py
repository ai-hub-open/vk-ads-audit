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
    assert {fn.__name__ for fn in RULES} - {"scale_leader", "unused_segments"} <= set(CHECK_OF)   # гигиенические карточки без пункта чек-листа


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


def test_funnel_questions_documented():
    from scripts.diagnose import ASK_FUNNELS
    doc = _ref("client-questions.md")
    for key, f in ASK_FUNNELS.items():
        assert f["question"] in doc and f["why"] in doc and f"funnel_{key}" in doc, key
    assert "client_answers.json" in doc and "client_answers.json" in SKILL
    rules = _ref("diagnostic-rules.md")
    assert "`funnel_potential`" in rules and "`ask`" in rules and "`potential`" in rules


def test_skill_documents_live_run_fixes():
    # В claude.ai / Desktop персональной ссылки нет → всегда путь A, ссылку не спрашивать
    assert "нет персональной ссылки подключения" in SKILL and "всегда идёт" in SKILL and "не спрашивай" in SKILL
    # предполётка расхода, большие ответы, нет прав, глубина истории
    assert 'entity="users"' in SKILL and "кодом 5" in SKILL and "| 5 |" in SKILL
    assert "raw_store import" in SKILL and "--arguments-file" in SKILL
    assert '"no_access": true' in SKILL and "~366 дней" in SKILL
    # Второй живой прогон: сводка не «игнорирует даты», а только за всё время — с датами MCP теперь отказывает.
    assert "даты не передавай" in SKILL and "суммой дней" in SKILL
    tools, lim, rules, log = (_ref("mcp-read-tools.md"), _ref("limitations.md"),
                              _ref("diagnostic-rules.md"), (SKILL_ROOT / "CHANGELOG.md").read_text(encoding="utf-8"))
    assert "Подтверждено 16.10.2026" in tools and "`ord_marker`" in tools and "`utm`" in tools
    assert "рубли" in tools and "уже проценты" in tools and "raw_store import" in tools
    for fragment in ("Частота и охват за период не определены", "Нет доступа к базам клиентов",
                     "MCP не возвращает размер сегментов"):
        assert fragment in lim, fragment
    assert "ARCHIVED" in rules and "video_landscape_180s" in rules
    assert "Исправления по первому живому прогону" in log


def test_report_limitation_lines_match_docs():
    """Строки ограничений из normalize / render_report дословно присутствуют в limitations.md."""
    import re
    src = (SKILL_ROOT / "scripts" / "normalize.py").read_text(encoding="utf-8")
    lim = _ref("limitations.md")
    # «Нет доступа…» — без имени инструмента в тексте для клиента (третий живой прогон)
    for fragment in ("Частота и охват за период не определены для", "Нет доступа к базам клиентов: проверки",
                     "MCP не возвращает размер сегментов и связи с базами клиентов"):
        assert fragment in src and fragment.split(" (")[0].split(" для")[0] in lim, fragment
    assert re.search(r"def import_response", (SKILL_ROOT / "scripts" / "raw_store.py").read_text(encoding="utf-8"))
