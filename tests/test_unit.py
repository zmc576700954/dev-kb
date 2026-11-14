"""单元测试：文档切块、代码符号切块、FTS 中文分词、排除规则、扫描器。"""

import os

from agent_app.ingestion.document_parser import chunk_markdown
from agent_app.ingestion.code_parser import chunk_code
from agent_app.ingestion.excludes import EXCLUDE_DIRS, EXCLUDE_FILE_PATTERNS
from agent_app.ingestion.scanner import scan_project
from agent_app.indexing.fts_tokenizer import fts_tokenize, fts_query


# ---- 文档切块 ----

def test_markdown_heading_chunks_keep_path():
    md = "# 标题A\n\n开头。\n\n## 小节B\n\n内容B。\n\n## 小节C\n\n内容C。\n"
    chunks = chunk_markdown(md)
    assert len(chunks) >= 3
    b = next(c for c in chunks if "内容B" in c.content)
    assert b.heading_path[:1] == ["标题A"]
    assert b.start_line > 0 and b.end_line >= b.start_line


def test_markdown_chinese_headings():
    md = "# 知识库索引\n\n## 视频模型\n\n中文内容段落。\n"
    chunks = chunk_markdown(md)
    assert any("视频模型" in c.heading_path for c in chunks)


def test_markdown_no_heading_single_block():
    chunks = chunk_markdown("没有标题的一段文字。")
    assert len(chunks) == 1
    assert chunks[0].heading_path == []


def test_markdown_long_section_splits():
    md = "# 大章节\n\n" + "\n\n".join(f"第{i}段 " + "字" * 300 for i in range(20))
    chunks = chunk_markdown(md, )
    assert len(chunks) > 1
    assert all(len(c.content) <= 2200 for c in chunks)


# ---- 代码切块 ----

def test_php_class_methods_qualified():
    php = "<?php\nclass PayController {\n    public function refund() {\n        return 1;\n    }\n    public function pay() {\n        return 2;\n    }\n}\n"
    chunks, fb = chunk_code(php, "php")
    symbols = {c.symbol for c in chunks}
    assert not fb
    assert "PayController" in symbols
    assert "PayController@refund" in symbols and "PayController@pay" in symbols
    m = next(c for c in chunks if c.symbol == "PayController@refund")
    assert m.start_line == 3 and m.end_line == 5


def test_python_class_and_functions():
    py = "def top():\n    pass\n\nclass A:\n    def m(self):\n        pass\n"
    chunks, fb = chunk_code(py, "python")
    symbols = {c.symbol for c in chunks}
    assert {"top", "A", "A.m"} <= symbols


def test_ts_const_object_and_arrow_and_pair():
    ts = (
        "export const RULES = { a: 1, b: 2 };\n"
        "export const fn = (x: number) => x + 1;\n"
        "const store = { updateNode: (id: string) => id };\n"
    )
    chunks, fb = chunk_code(ts, "typescript")
    symbols = {c.symbol for c in chunks}
    assert "RULES" in symbols           # 常量对象字面量
    assert "fn" in symbols               # 箭头函数
    assert "updateNode" in symbols       # 对象字面量方法（zustand 风格）


def test_rust_symbols():
    rs = "struct Cli { name: String }\nfn main() {}\nimpl Cli {\n    fn run(&self) {}\n}\n"
    chunks, fb = chunk_code(rs, "rust")
    symbols = {c.symbol for c in chunks}
    assert "Cli" in symbols and "main" in symbols
    assert any("run" in s for s in symbols)


def test_code_fallback_on_unknown_language():
    chunks, fb = chunk_code("line1\nline2\n", "brainfuck")
    assert fb and chunks[0].symbol == "__fallback__"


def test_chunk_line_numbers_accurate():
    py = "import os\n\n\ndef f():\n    return 42\n"
    chunks, _ = chunk_code(py, "python")
    f = next(c for c in chunks if c.symbol == "f")
    lines = py.splitlines()
    assert lines[f.start_line - 1].startswith("def f")
    assert f.end_line == 5


# ---- FTS 中文分词 ----

def test_fts_tokenize_chinese_bigrams():
    tokens = fts_tokenize("退款逻辑").split()
    assert tokens == ["退款", "款逻", "逻辑"]


def test_fts_tokenize_mixed():
    tokens = fts_tokenize("PayController 处理退款").split()
    assert "paycontroller" in tokens and "退款" in tokens


def test_fts_query_escaped():
    q = fts_query('退款 " OR 1=1 --')
    assert '"' in q and "OR" not in q.replace('"', '')


# ---- 排除规则与扫描器 ----

def test_exclude_critical_entries():
    for d in ("node_modules", "vendor", ".git", ".venv", ".pytest_cache", "models", ".playwright-mcp"):
        assert d in EXCLUDE_DIRS
    for p in (".env", ".env.*", "*.key", ".phpunit.result.cache",
              "cases_*.jsonl", "routing_cases_*.jsonl"):
        assert p in EXCLUDE_FILE_PATTERNS


def test_scanner_respects_excludes(tmp_path):
    (tmp_path / "node_modules").mkdir()
    (tmp_path / "node_modules" / "junk.js").write_text("x")
    (tmp_path / ".pytest_cache").mkdir()
    (tmp_path / ".pytest_cache" / "README.md").write_text("cache")
    (tmp_path / ".env").write_text("SECRET=1")
    (tmp_path / "cases_v1.jsonl").write_text('{"id":"q001","gold":{"any_of":["secret"]}}')
    (tmp_path / "app.py").write_text("def f(): pass\n")
    (tmp_path / "README.md").write_text("# 项目\n\n说明。\n")
    report = scan_project("t", str(tmp_path))
    paths = [f.path for f in report.files]
    assert "app.py" in paths and "README.md" in paths
    assert not any("node_modules" in p or ".env" in p for p in paths)
    assert not any(".pytest_cache" in p or "cases_v1.jsonl" in p for p in paths)
    assert report.project_type in ("mixed", "code")


def test_scanner_partial_keep_superpowers_plans(tmp_path):
    sp = tmp_path / ".superpowers"
    (sp / "plans").mkdir(parents=True)
    (sp / "brainstorm").mkdir()
    (sp / "plans" / "2026-01-01-x.md").write_text("# X 计划\n\n> 状态：待评审\n")
    (sp / "brainstorm" / "noise.md").write_text("# 噪声\n")
    report = scan_project("t", str(tmp_path))
    paths = [f.path for f in report.files]
    assert any(".superpowers/plans" in p for p in paths)
    assert not any("brainstorm" in p for p in paths)


def test_scanner_spec_candidate_detection(tmp_path):
    (tmp_path / "docs" / "superpowers" / "specs").mkdir(parents=True)
    (tmp_path / "docs" / "superpowers" / "specs" / "a-design.md").write_text("# A\n")
    (tmp_path / "docs" / "readme.md").write_text("# R\n")
    report = scan_project("t", str(tmp_path))
    spec = next(f for f in report.files if "a-design" in f.path)
    plain = next(f for f in report.files if "readme" in f.path)
    assert spec.is_spec_candidate and not plain.is_spec_candidate
