"""规则注册表：KB-<章节>-<slug> 的唯一权威定义。

规则 ID 以规范章节号为锚（决策 Q4/Q10）；本表是 ID↔章节↔严重级的唯一权威源，
checks_*/operations 中出现的每个 ID 都必须在此登记（tests/test_kb_rules.py 强制）。

严重级映射：
- 规范「必须/禁止」→ error；「建议」→ warning；含主观判断、无法纯机械判定 → review。
UNCODED 记录规范中「确实无法机械执行」的条目及其原因——没有静默遗漏。
"""

from __future__ import annotations

ERROR = "error"
WARNING = "warning"
REVIEW = "review"

# rule_id -> (严重级, 一句话说明)
RULES: dict[str, tuple[str, str]] = {
    # ---- §1 目录层级与职责（项目级，checks_project） ----
    "KB-1-root-only-md":        (ERROR, "项目根只允许 README.md 一个 Markdown 文件"),
    "KB-1-has-category":        (ERROR, "每个项目至少有一个分类目录"),
    "KB-1-empty-category":      (ERROR, "禁止空分类目录"),
    "KB-1-nested-category":     (ERROR, "禁止分类目录下再建子分类目录"),
    "KB-1-category-size":       (WARNING, "分类目录内文档建议 3–15 篇"),

    # ---- §2 README 索引 ----
    "KB-2.1-h1":                (ERROR, "README 第一块是一级标题"),
    "KB-2.1-intro-quote":       (ERROR, "H1 后紧跟一段 `> ` 项目说明引用"),
    "KB-2.1-grouped-list":      (ERROR, "按分类分组：每个分类目录有对应 `## ` 小节"),
    "KB-2.1-entry-format":      (ERROR, "条目格式 `- [标题](./<category>/<doc>.md) — 一句话说明`"),
    "KB-2.1-entry-desc":        (WARNING, "条目缺少「 — 一句话说明」"),
    "KB-2.2-coverage":          (ERROR, "README 链接必须覆盖项目内全部文档"),
    "KB-2.2-broken-link":       (ERROR, "README 链接目标存在，断链为 0"),
    "KB-2.2-readme-frontmatter": (ERROR, "README 禁止 frontmatter（§3.1）"),
    "KB-2.2-no-body":           (REVIEW, "README 疑似堆叠正文（代码块/表格/长散文）"),
    "KB-2.2-length":            (WARNING, "README 建议 150 行以内"),

    # ---- §3 frontmatter 与元数据 ----
    "KB-3.1-doc-missing-fm":    (ERROR, "普通文档必须有 frontmatter 且位于第一行"),
    "KB-3.1-readme-frontmatter": (ERROR, "README 禁止有 frontmatter"),
    "KB-3.2-fence":             (ERROR, "frontmatter 块以 `---` 开启并收束"),
    "KB-3.2-line-format":       (ERROR, "块内每行恰为一个 `键: 值`"),
    "KB-3.2-flat-scalar":       (ERROR, "禁止嵌套、列表、多行值、锚点引用"),
    "KB-3.2-empty-block":       (ERROR, "禁止空 frontmatter 块"),
    "KB-3.2-prose-value":       (REVIEW, "frontmatter 值疑似正文内容"),
    "KB-3.3-missing-status":    (ERROR, "缺必填键 `状态`"),
    "KB-3.3-missing-last-updated": (ERROR, "缺必填键 `最后更新`"),
    "KB-3.3-missing-next-review": (ERROR, "缺必填键 `下次复核`"),
    "KB-3.4-priority-value":    (ERROR, "`优先级` 只能取 高/中/低"),
    "KB-3.4-tag-format":        (ERROR, "`标签` 须为逗号分隔小写词，每个≤12字符、总数≤5"),
    "KB-3.5-status-value":      (ERROR, "`状态` 只能取五个基准值，补充说明须用全角括号"),
    "KB-3.5-date-format":       (ERROR, "日期严格 YYYY-MM-DD，月日补零"),
    "KB-3.5-review-order":      (ERROR, "`下次复核` 不得早于 `最后更新`"),
    "KB-3.5-dup-key":           (ERROR, "禁止重复键"),
    "KB-3.5-update-semantics":  (REVIEW, "`最后更新` 应为登记/复核日期而非文件系统时间，需人工确认"),

    # ---- §4 文件与目录命名 ----
    "KB-4.1-charset":           (ERROR, "路径只允许中文、英文、数字、-、.、/"),
    "KB-4.1-dot-tilde-prefix":  (ERROR, "禁止以 . 或 ~ 开头的文件/目录"),
    "KB-4.1-internal-linked":   (ERROR, "`_` 开头的内部目录不得进入 README/被链接"),
    "KB-4.2-category-naming":   (WARNING, "分类目录建议 `<NN>-<分类名>`"),
    "KB-4.2-doc-numbering":     (WARNING, "文档建议 `<NN>-<标题>.md`，同分类编号唯一"),
    "KB-4.2-case-collide":      (ERROR, "同目录文件名不得仅靠大小写区分"),
    "KB-4.2-name-length":       (ERROR, "文件名主体不超过 60 字符"),
    "KB-4.2-version-suffix":    (ERROR, "禁止版本后缀（-v2/-最终版/-new 等）"),
    "KB-4.2-uninformative":     (ERROR, "禁止「未命名/新建文档/副本/copy」等无信息词"),
    "KB-4.2-lowercase":         (WARNING, "文件名中的英文建议统一小写"),

    # ---- §5 标题层级与正文骨架 ----
    "KB-5.1-one-h1":            (ERROR, "恰好一个一级标题，位于 frontmatter 后正文最前"),
    "KB-5.1-level-jump":        (ERROR, "标题层级不跳级"),
    "KB-5.1-adjacent-same-level": (ERROR, "禁止两个同级标题直接相邻"),
    "KB-5.1-dup-heading":       (ERROR, "同一父级下标题文字不重复"),
    "KB-5.1-heading-links":     (ERROR, "标题中禁止链接、图片、代码块"),
    "KB-5.1-heading-punct":     (ERROR, "标题不以句号结尾；禁止只有编号没有文字"),
    "KB-5.1-question-heading":  (WARNING, "标题建议不写疑问句"),
    "KB-5.1-max-depth":         (WARNING, "标题层级建议不超过 4 级"),
    "KB-5.2-summary-required":  (ERROR, "H1 后第一块必须是一句 `> ` 摘要"),
    "KB-5.2-summary-length":    (ERROR, "摘要 30–80 字"),
    "KB-5.2-empty-section":     (ERROR, "空章节：标题下既无正文也无任何子标题"),
    "KB-5.2-group-intro":       (WARNING, "导航式分组标题下建议加一句导语（§5.2 修正版）"),
    "KB-5.2-placeholder":       (ERROR, "非草案文档禁止 TODO/待补充/xxx/... 等占位内容"),

    # ---- §6 内部链接与交叉引用 ----
    "KB-6.1-rel-prefix":        (ERROR, "站内链接以 ./ 或 ../ 开头（禁止裸相对路径/裸文件名）"),
    "KB-6.1-abs-link":          (ERROR, "禁止 / 开头的根相对路径或绝对路径"),
    "KB-6.1-wikilink":          (ERROR, "禁止 [[双向链接]] 语法"),
    "KB-6.1-generic-text":      (ERROR, "禁止「点击这里/本文档/更多/详见/这里」等链接文字"),
    "KB-6.1-escapes-root":      (ERROR, "链接归一后不得越出项目根"),
    "KB-6.1-broken-target":     (ERROR, "链接目标必须在同一项目内存在"),
    "KB-6.2-style-mix":         (ERROR, "同一目标全文使用同一种链接写法"),
    "KB-6.2-orphan":            (ERROR, "每篇文档至少有一条入链"),
    "KB-6.2-link-count":        (WARNING, "单篇内部链接建议 3–12 条"),
    "KB-6.1-link-text":         (REVIEW, "链接文字应能不看 URL 判断去向"),

    # ---- §7 「相关文档」段 ----
    "KB-7-missing":             (WARNING, "建议每篇文档有「相关文档」段"),
    "KB-7-title-exact":         (ERROR, "小节标题逐字为 `## 相关文档`"),
    "KB-7-format":              (ERROR, "内容为无序列表 `- [描述](路径)`，禁止散文/有序/表格/代码块"),
    "KB-7-broken-target":       (ERROR, "每条链接的目标存在"),
    "KB-7-non-doc-target":      (ERROR, "禁止站外链接、文内锚点、图片等非文档目标"),
    "KB-7-self-link":           (ERROR, "不链接自身"),
    "KB-7-dup-target":          (ERROR, "不重复同一目标"),
    "KB-7-count":               (WARNING, "条目建议 3–8 条"),
    "KB-7-desc-length":         (WARNING, "描述建议 30 字以内"),

    # ---- §8 单篇篇幅与结构 ----
    "KB-8-over-limit":          (ERROR, "超过 5000 字必须拆分"),
    "KB-8-under-limit":         (ERROR, "少于 300 字且无独立价值的文档不单独成篇"),
    "KB-8-length-range":        (WARNING, "正文建议 800–3000 字"),
    "KB-8-long-para":           (WARNING, "单段建议不超过 5 行"),
    "KB-8-list-min":            (ERROR, "列表只有 1 项（单条不用列表）"),
    "KB-8-list-two":            (WARNING, "列表只有 2 项（建议并列项 3 条以上；字段清单可保留）"),
    "KB-8-summary-heading":     (REVIEW, "文末「总结/小结/结论」段疑似复述前文"),
    "KB-8-topic-overlap":       (REVIEW, "疑似与既有文档主题重合，需裁决"),
    "KB-8-topic-focus":         (REVIEW, "一篇只讲一个主主题，需人工确认"),

    # ---- §9 表格 ----
    "KB-9-header-sep":          (ERROR, "表头下一行是分隔行（|---|）"),
    "KB-9-column-count":        (ERROR, "每行列数与表头一致"),
    "KB-9-media-in-cell":       (ERROR, "单元格内禁止图片、代码块、多段落"),
    "KB-9-max-cols":            (WARNING, "列数建议不超过 6"),
    "KB-9-cell-density":        (REVIEW, "单元格疑似承载多个事实，需拆行"),
    "KB-9-missing-unit":        (REVIEW, "数值列表头疑似未标注单位"),
    "KB-9-long-table":          (REVIEW, "表格超过 8 行，疑似承载叙述性内容"),

    # ---- §10 代码块与命令 ----
    "KB-10-language-tag":       (ERROR, "每个代码块标注语言"),
    "KB-10-prompt-strip":       (ERROR, "命令块不含提示符 `$` 与输出结果"),
    "KB-10-secret-scan":        (REVIEW, "疑似真实凭据/内部地址/个人信息，需确认"),
    "KB-10-image-in-code":      (ERROR, "禁止用截图代替可复制的代码"),
    "KB-10-block-length":       (WARNING, "单块代码建议不超过 60 行"),

    # ---- §11 图片与附件 ----
    "KB-11-asset-location":     (ERROR, "图片与附件放在 assets/ 目录并用相对路径引用"),
    "KB-11-alt-text":           (ERROR, "每个图片引用都有替代文本"),
    "KB-11-alt-quality":        (REVIEW, "替代文本疑似无信息（「图片/截图」），需改写"),
    "KB-11-asset-name":         (ERROR, "附件文件名只含中文、英文、数字、连字符、点；扩展名小写"),
    "KB-11-caption":            (ERROR, "图片下方用一行斜体或引用标注「图 N：说明」"),
    "KB-11-figure-numbering":   (ERROR, "图 N 在同一文档内连续编号"),
    "KB-11-external-image":     (ERROR, "禁止引用项目外或网络图片地址"),
    "KB-11-broken-asset":       (ERROR, "引用的图片/附件文件必须存在（对 §11 相对路径规则的落实）"),
    "KB-11-fixed-size":         (ERROR, "禁止在 Markdown 中硬编码像素宽高"),
    "KB-11-image-count":        (WARNING, "每篇文档图片建议不超过 8 张"),
    "KB-11-size":               (WARNING, "单张图片建议不超过 2MB"),

    # ---- §12 更新、废弃与归档 ----
    "KB-12.1-readme-status-mark": (ERROR, "草案/已废弃文档在 README 中须带状态标注（对 §12.1 表按最严格解释）"),
    "KB-12.2-deprecation-block": (ERROR, "已废弃文档文首（摘要前）有 `> ⚠️ 已废弃：…` 声明"),
    "KB-12.2-replacement-valid": (ERROR, "废弃声明中的替代文档链接存在，或写「无替代」"),
    "KB-12.2-archive-dir":      (WARNING, "已归档文档统一移入 archive/ 目录"),
    "KB-12.2-archive-group":    (ERROR, "存在已归档文档时 README 单列归档分组"),
    "KB-12.2-review-overdue":   (WARNING, "文档已过 `下次复核` 日期，触碰时先复核"),
    "KB-12.2-review-overdue-monitored": (ERROR, "`需监控` 文档已过 `下次复核`，按规范强制复核"),
    "KB-12.2-no-silent-delete": (ERROR, "禁止静默删除：删除必须给出原因并在提案留痕（§12.2，operations 层强制）"),

    # ---- 工具前置条件（非规范条目，提案规划层自有校验） ----
    "KB-OP-conflict":           (ERROR, "操作前置条件不满足（目标已存在/不存在）"),
    "KB-OP-sha-conflict":       (ERROR, "expected_sha 与当前内容不一致（乐观并发冲突）"),
    "KB-OP-regression":         (ERROR, "修复后 error 数不允许增加（渐进式修复准入条件）"),

    # ---- §13 检索友好性 ----
    "KB-13-generic-title":      (ERROR, "禁止纯代号/无上下文标题（方案A/其他/杂项等）"),
    "KB-13-opaque-title":       (REVIEW, "标题疑似纯缩写/代号，需补上下文"),
    "KB-13-stacked-keywords":   (ERROR, "标题末尾禁止堆叠定位词，一个标题只保留一个"),
    "KB-13-title-body-mismatch": (REVIEW, "标题核心词与正文首段用词疑似不一致"),
    "KB-13-naming-consistency": (REVIEW, "疑似同一概念多种叫法/近似主题文档，需统一叫法"),
    "KB-13-section-name-mismatch": (WARNING, "README 小节标题与分类目录名建议保持一致"),
}

# 规范中存在、但无法机械执行的条目（不产出 Finding，此处显式登记原因）。
UNCODED: dict[str, str] = {
    "§2.1-阅读路径": "可选小节且「与状态优先级不混用」为主观编排，无机械判据",
    "§3.5-复核周期建议": "3/6 个月取决于内容类别（法规/方法论/历史），分类本身是主观判断",
    "§5.2-结论先行段": "「核心结论段≤100字」无法定位哪一段是结论段",
    "§6.2-首提及链接": "「首次提到关联概念」需要语义理解",
    "§7-排序建议": "上位概念→平行→下游的排序是语义判断",
    "§8-读者自检两项": "「能回答是什么/为什么/怎么做/边界中的至少两项」是内容理解",
    "§8-可读性自检": "「只读标题能判断相关」是内容理解",
    "§8-禁止改写废弃文档": "需要版本历史对比，校验器无历史视图（operations 层以提案留痕缓解）",
    "§10-代码块前后说明": "「一句说明做什么」是语义判断",
    "§11-图片承载唯一信息": "需要看图",
    "§11-图片压缩建议": "质量判断，不做有损操作",
    "§12.2-静默删除": "校验器无法从现存文件反推删除；由 operations 层强制（kb_propose_delete 必填 reason），见 KB-12.2-no-silent-delete",
    "§12.2-状态变更同步日期": "同上：在 kb_propose_update/deprecate/archive 中机械保证（状态变→最后更新=当日）",
    "§13-摘要含检索词": "「1–3 个检索关键词」的抽取是语义判断（摘要本身存在性由 KB-5.2 保证）",
    "§13-专有名词大小写": "「保持原文大小写」需领域词表",
    "§4.2-中文不夹拼音": "拼音与英文词形相同，无法可靠区分",
}


def get(rule_id: str) -> tuple[str, str]:
    return RULES[rule_id]


def severity_of(rule_id: str) -> str:
    return RULES[rule_id][0]
