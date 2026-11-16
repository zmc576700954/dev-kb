"""索引维护 CLI 的公共行为。"""

import json
import sqlite3
from pathlib import Path

import yaml

from agent_app.indexing.metadata_store import MetadataStore
from agent_app import sync_cli


def test_backfill_does_not_read_source_documents(tmp_path, monkeypatch):
    source = tmp_path / "source"
    document = source / "docs" / "doc.md"
    document.parent.mkdir(parents=True)
    document.write_text("# 新标题\n\nLoRA 的使用方法。\n", encoding="utf-8")
    (tmp_path / "aliases.yaml").write_text(yaml.safe_dump({
        "aliases": {"LoRA": ["低秩适配"]},
    }), encoding="utf-8")
    config = tmp_path / "projects.yaml"
    config.write_text(yaml.safe_dump({"projects": [{
        "name": "kb", "path": str(source),
        "provides": ["category", "doc_role", "aliases"],
        "content_roots": ["."], "entry_docs": ["docs/doc.md"],
        "tuning": ["aliases.yaml"], "roles": {"docs/*": "guide"},
    }]}), encoding="utf-8")

    data_dir = tmp_path / "data"
    store = MetadataStore(data_dir / "meta.db")
    ids = store.replace_chunks("kb", "docs/doc.md", [{
        "source_type": "document", "heading_path": ["旧标题"],
        "category": "stale", "doc_role": "", "aliases": [],
        "start_line": 1, "end_line": 2, "content": "LoRA 的使用方法。",
    }])
    store.mark_embedded(ids)
    store.close()

    source_reads = []
    original_read_text = Path.read_text

    def observe_read_text(path, *args, **kwargs):
        if path == document:
            source_reads.append(path)
        return original_read_text(path, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", observe_read_text)
    monkeypatch.setattr(sync_cli, "DATA_DIR", data_dir)
    monkeypatch.setattr("sys.argv", [
        "sync_cli", "--backfill", "--no-embed", "--config", str(config),
    ])
    assert sync_cli.main() == 0
    assert source_reads == []

    conn = sqlite3.connect(data_dir / "meta.db")
    row = conn.execute(
        "SELECT category, doc_role, aliases, heading_path, embedded FROM chunks"
    ).fetchone()
    conn.close()
    assert row[:2] == ("docs", "guide")
    assert json.loads(row[2]) == ["LoRA"]
    assert json.loads(row[3]) == ["旧标题"]
    assert row[4] == 1
