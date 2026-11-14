# M1 知识源扫描报告

> 生成时间：2026-11-12 17:34:20（只读扫描，未修改任何项目文件）

## 总览

| 项目 | 类型 | 可索引文件 | 代码 | 文档 | 配置 | 主要语言 |
|---|---|---|---|---|---|---|
| ai-video-kb | document | 25 | 0 | 24 | 0 | — |
| kaiwu | mixed | 80 | 32 | 44 | 0 | python(30), html(1), shell(1) |
| new-bargain-laravel | mixed | 2749 | 2668 | 32 | 4 | php(2626), javascript(33), css(6), python(2) |
| tiangong | mixed | 1054 | 880 | 96 | 14 | typescript(858), javascript(15), shell(4), html(1) |
| repository-manage | mixed | 168 | 93 | 21 | 6 | typescript(49), rust(39), javascript(3), html(1) |

## ai-video-kb

- 根目录：`/Users/zhumingchen/ai-video-kb`
- 判定类型：**document**
- 清单文件：无
- 候选 Spec / 任务文档：无
- 已排除目录：`.synapse-fix-backup`(1)

## kaiwu

- 根目录：`/Users/zhumingchen/kaiwu`
- 判定类型：**mixed**
- 清单文件：无
- 候选 Spec / 任务文档：无
- 已排除目录：`__pycache__`(2), `evals`(1), `.screenshots`(1), `models`(1), `.venv`(1), `.git`(1), `data`(1), `notebooks`(1), `kb_corpus_real`(1), `kb_corpus_draft`(1), `kb_corpus`(1)
- 已排除文件模式：`.DS_Store`(1)

## new-bargain-laravel

- 根目录：`/Users/zhumingchen/new-bargain-laravel`
- 判定类型：**mixed**
- 清单文件：`composer.json`, `package.json`, `packages/filament-wechat-official-account/composer.json`, `pdd-capture/package.json`；锁文件（仅作技术栈事实）：`composer.lock`, `package-lock.json`, `pdd-capture/package-lock.json`
- 候选 Spec / 任务文档（6）：
  - `.superpowers/plans/2026-11-05-luck-admin-integration-spec.md`
  - `.superpowers/plans/2026-11-05-luck-admin-integration.md`
  - `docs/superpowers/plans/2026-11-01-anti-fraud-risk-control.md`
  - `docs/superpowers/plans/2026-11-02-luck-batch-ux-traceability.md`
  - `docs/superpowers/plans/2026-11-03-virtual-members.md`
  - `docs/superpowers/specs/2026-11-03-virtual-members-design.md`
- 已排除目录：`vendor`(3), `node_modules`(2), `.claude`(1), `storage`(1), `.workbuddy`(1), `.playwright-mcp`(1), `.worktrees`(1), `.zcode`(1), `.git`(1), `.ddev`(1), `.codegraph`(1), `cache`(1)
- 已排除文件模式：`.DS_Store`(13), `.env.*`(2), `.env`(1), `*.backup.*`(1), `.phpunit.result.cache`(1), `*.min.js`(1)

## tiangong

- 根目录：`/Users/zhumingchen/tiangong`
- 判定类型：**mixed**
- 清单文件：`eslint.config.mjs`, `examples/sample-react-app/package.json`, `package.json`, `spikes/agent-core/package.json`, `spikes/reaper-isolation/package.json`, `spikes/reaper-isolation/tsconfig.json`, `stubs/sharp/package.json`, `tsconfig.json`, `vite.config.ts`；锁文件（仅作技术栈事实）：`package-lock.json`, `spikes/agent-core/package-lock.json`
- 候选 Spec / 任务文档（37）：
  - `docs/superpowers/plans/2026-07-23-wiki-link-ingest.md`
  - `docs/superpowers/plans/2026-07-25-wiki-graph-compute.md`
  - `docs/superpowers/plans/2026-07-30-query-writeback.md`
  - `docs/superpowers/plans/2026-10-26-i18n-chinese.md`
  - `docs/superpowers/plans/2026-10-27-framework-hardening.md`
  - `docs/superpowers/plans/2026-10-27-structure-editing.md`
  - `docs/superpowers/plans/2026-10-31-ux-readonly-scope.md`
  - `docs/superpowers/plans/2026-10-31-ux-settings-ui.md`
  - `docs/superpowers/specs/2026-07-23-wiki-link-ingest-design.md`
  - `docs/superpowers/specs/2026-07-25-wiki-graph-compute-design.md`
  - `docs/superpowers/specs/2026-07-30-query-writeback-design.md`
  - `docs/superpowers/specs/2026-10-26-i18n-chinese-design.md`
  - `docs/superpowers/specs/2026-10-27-framework-hardening-design.md`
  - `docs/superpowers/specs/2026-10-27-structure-editing-design.md`
  - `docs/superpowers/specs/2026-10-31-ux-defaults-design.md`
  - `docs/tasks/2026-07-23-wiki-link-ingest.md`
  - `docs/tasks/2026-07-26-wiki-graph-compute.md`
  - `docs/tasks/2026-07-30-query-writeback.md`
  - `docs/tasks/2026-09-30-base-foundation.md`
  - `docs/tasks/2026-09-30-claude-md-boundaries.md`
  - `docs/tasks/2026-09-30-d3-wiki-coverage.md`
  - `docs/tasks/2026-10-01-d10a.md`
  - `docs/tasks/2026-10-01-d10b-yjs-awareness.md`
  - `docs/tasks/2026-10-01-d10c-yjs-persistence.md`
  - `docs/tasks/2026-10-01-d10d.md`
  - `docs/tasks/2026-10-01-d4-mcp-health-panel.md`
  - `docs/tasks/2026-10-01-d5a.md`
  - `docs/tasks/2026-10-01-d5b-recipe-dag-delegate.md`
  - `docs/tasks/2026-10-01-d5c-recipe-dag-viz.md`
  - `docs/tasks/2026-10-01-d6.md`
  - ……共 37 份，详见 JSON
- 已排除目录：`node_modules`(3), `.merge-backup-2026-10-28`(1), `.screenshots`(1), `dist-electron`(1), `dist`(1), `.claude`(1), `release`(1), `.workbuddy`(1), `playwright-report`(1), `.git`(1), `test-results`(1), `.superpowers/sdd`(1)
- 已排除文件模式：`*.log`(39), `.DS_Store`(12)

## repository-manage

- 根目录：`/Users/zhumingchen/repository-manage`
- 判定类型：**mixed**
- 清单文件：`package.json`, `postcss.config.js`, `src-tauri/Cargo.toml`, `tailwind.config.js`, `tsconfig.json`, `vite.config.ts`；锁文件（仅作技术栈事实）：`package-lock.json`, `src-tauri/Cargo.lock`
- 候选 Spec / 任务文档（14）：
  - `docs/superpowers/plans/2026-10-09-synapse-kb.md`
  - `docs/superpowers/plans/2026-10-11-i18n.md`
  - `docs/superpowers/plans/2026-10-12-inline-edit-rename-plan.md`
  - `docs/superpowers/plans/2026-10-12-kb-standards-v2.md`
  - `docs/superpowers/plans/2026-10-12-notification-system.md`
  - `docs/superpowers/plans/2026-10-12-search-and-markdown-plan.md`
  - `docs/superpowers/plans/2026-10-12-ui-layout-redesign.md`
  - `docs/superpowers/specs/2026-10-09-synapse-kb-design.md`
  - `docs/superpowers/specs/2026-10-11-i18n-design.md`
  - `docs/superpowers/specs/2026-10-12-inline-edit-rename-design.md`
  - `docs/superpowers/specs/2026-10-12-kb-standards-v2-design.md`
  - `docs/superpowers/specs/2026-10-12-notification-system-design.md`
  - `docs/superpowers/specs/2026-10-12-search-and-markdown-design.md`
  - `docs/superpowers/specs/2026-10-12-ui-layout-redesign-design.md`
- 已排除目录：`.screenshots`(1), `dist`(1), `node_modules`(1), `.claude`(1), `.git`(1), `target`(1), `.superpowers/brainstorm`(1)
- 已排除文件模式：`.DS_Store`(1)

---

完整文件清单见同目录 `<项目名>.json`。
