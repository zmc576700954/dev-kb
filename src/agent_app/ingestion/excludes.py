"""默认排除清单：目录、文件名模式、二进制扩展名。

来源：规划文档默认项 + 2026-11-12 对五个目录实测扩充项。
支持项目根目录 .agentignore 追加规则，并尊重 .gitignore 中的目录级规则。
"""

# 始终排除的目录名（任意层级命中即排除）
EXCLUDE_DIRS = {
    # 版本控制与工具缓存
    ".git", ".svn", ".hg",
    ".idea", ".vscode", ".pytest_cache",
    ".ddev", ".playwright-mcp", ".codegraph", ".zcode", ".workbuddy",
    ".screenshots", ".synapse-fix-backup", ".merge-backup-2026-10-28",
    ".worktrees", ".claude", ".phpunit.cache", ".serena",
    # 依赖与虚拟环境
    "node_modules", "vendor", ".venv", "venv", "__pycache__",
    # 构建产物
    "dist", "dist-electron", "build", "target", "out",
    # 运行时产物
    "storage", "cache", "logs", "tmp", "temp",
    # 大体积数据（kaiwu 实测）
    "models", "notebooks", "kb_corpus", "kb_corpus_draft", "kb_corpus_real",
}

# 部分保留的目录：只保留指定子目录，其余排除
# .superpowers/ 下仅 plans 含 Spec 文档（实测），brainstorm、sdd 等为工具缓存
PARTIAL_KEEP_DIRS = {
    ".superpowers": {"plans"},
}

# 始终排除的文件名 / 模式（fnmatch）
EXCLUDE_FILE_PATTERNS = {
    ".env", ".env.*",
    "*.pem", "*.key", "*.crt", "*.p12", "*.keystore",
    ".DS_Store", "Thumbs.db",
    ".phpunit.result.cache",
    "*.backup.*",
    "package-lock.json", "composer.lock", "yarn.lock", "pnpm-lock.yaml",
    "Cargo.lock", "poetry.lock",
    "*.min.js", "*.min.css", "*.map",
    # 评测题与路由标签不能进入知识索引，否则跨项目检索会命中题目/答案自身
    "cases_*.jsonl", "routing_cases_*.jsonl",
    "*.log",
}

# 二进制扩展名：不计入索引清单
BINARY_EXTENSIONS = {
    ".png", ".jpg", ".jpeg", ".gif", ".webp", ".ico", ".svg",
    ".mp4", ".mov", ".mp3", ".wav", ".m4a",
    ".zip", ".tar", ".gz", ".7z", ".rar",
    ".pdf", ".doc", ".docx", ".xls", ".xlsx", ".ppt", ".pptx",
    ".woff", ".woff2", ".ttf", ".otf", ".eot",
    ".so", ".dylib", ".dll", ".exe", ".bin",
    ".mlx", ".safetensors", ".gguf", ".onnx",
    ".pyc", ".pyo", ".class", ".o",
}

# 大文件阈值：超过则记录但不入库（字节）
MAX_FILE_SIZE = 2 * 1024 * 1024  # 2 MB
