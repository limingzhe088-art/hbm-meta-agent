---
name: hbm-search-strategy
description: "Search-strategy construction and bibliographic-record ingestion for human biomonitoring meta-analyses: drafts database-specific Boolean search strings (PubMed MeSH + field tags, Web of Science TS=, Scopus TITLE-ABS-KEY, Embase Emtree /exp, CNKI SU=, Wanfang 主题:) from the analyte, matrix, population and exposure-assessment term banks in project.yaml, applies a four-group exclusion-word bank (animal / cell mechanism / occupational / clinical) with over-killing warnings, and parses database exports (RIS, EndNote RefMan XML, tab-delimited text, BibTeX) into one unified raw-record CSV with a cross-library duplicate-detection hash for downstream screening. Covers reproducible search recording (exact query text, date, hit count per database), PRISMA 2020 identification-stage accounting, and the GATE-1 human confirmation workflow. Analyte- and database-agnostic. Triggers on: search strategy, Boolean search string, literature retrieval, MeSH, Emtree, database search, hit count, PRISMA identification, raw records, RIS, EndNote export, BibTeX, dedupe export, 检索式, 检索策略, 文献检索, 数据库检索, 检索词, 布尔检索, 主题词, 命中数, 检索记录, 题录, 题录导出, 文献导入, 排除词."
license: MIT (code) / CC-BY-4.0 (docs)
compatibility: opencode claude-code dsh
allowed-tools:
  - Read
  - Write
  - Edit
  - Grep
  - Glob
  - Bash
  - WebFetch
  - WebSearch
  - Task
  - TodoWrite
  - AskUserQuestion
metadata:
  parent_skill: hbm-meta
  version: "1.0.0"
  last_updated: "2026-09-26"
  status: active
  stage: 1
  gate: GATE-1
  output_gate: "GATE-1（检索充分性，人工确认）"
  contract_version: "1.0.0"
  task_type: open-ended
---

# S1 · Search Strategy — 检索策略与题录导入

**一句话**：把 `project.yaml` 里的暴露物/基质/人群/暴露评估词表，变成**各数据库可执行、可复现、可审计**的检索式草稿，并把人工检索导出的题录收进**统一格式**的原始记录表。

**核心立场**：**检索式是草稿，执行与命中数属于人工记录；AI 不代填检索记录，更不开启 GATE-1。**

---

## 1. 加载条件与前置依赖

| 项 | 要求 |
|---|---|
| **前置闸门** | **GATE-0 必须为 `passed`**（`project.yaml` 六项口径已冻结） |
| 配置前置 | `project.yaml` 含 `search` 区块（**条件必需**：使用本子技能时缺失即硬错误） |
| 负责人 | **A1 `search-agent`**（权限 L1：只读 + 新建报告，无 `Edit`，可联网） |
| 产出目录 | `01-search/` |
| 人工闸门 | **GATE-1**（检索充分性）：检索式原文/日期/命中数齐备后由**人**确认 |

---

## 2. 脚本清单（均含 `--self-test`）

| 脚本 | 用途 | 关键命令 |
|---|---|---|
| `generate_search_terms.py` | 从 `project.yaml → search` 生成各库检索式**草稿**（PubMed/WoS/Scopus/Embase/CNKI/万方）+ GATE-1 人工检查清单 | `--config project.yaml --output search_strategy_draft.md` |
| `parse_ris.py` | RIS 题录解析（PubMed/WoS/Scopus/Embase/万方导出）→ 统一 CSV | `--input x.ris --output raw.csv --source pubmed` |
| `parse_enl.py` | EndNote 导出解析（RefMan XML / 制表符 TXT / CSV；`.enl` 二进制**显式拒绝**） | `--input x.xml --output raw.csv --source wos` |
| `parse_bibtex.py` | BibTeX 解析（嵌套花括号 / 宏展开 / `#` 连接值） | `--input x.bib --output raw.csv --source other` |
| `merge_sources.py` | 合并各库统一 CSV + 生成 `source_summary.csv`（导出数实算；检索式/命中数**仅取自人工检索日志**） | `--inputs a.csv b.csv --output all.csv --summary summary.csv [--search-log log.csv]` |

通用参数：`--encoding`（GBK 导出用 `--encoding gbk`）、`--validate`（写盘后立即校验字段完整性）、`--self-test`。

---

## 3. 统一题录格式（单一来源 `shared/record_schema.py`）

| 字段 | 说明 |
|---|---|
| `record_id` | `{来源缩写}-{序号:05d}`（如 `pubmed-00001`），跨库不冲突 |
| `source_database` | `PubMed/WebOfScience/Scopus/Embase/CNKI/Wanfang/Other`（`--source` 别名大小写不敏感） |
| `title` / `abstract` / `authors` / `year` / `doi` / `pmid` / `journal` / `keywords` | 题录元数据；缺失**留空，不插补**（契约 §2 空值规则） |
| `raw_row_hash` | `sha256(规范化 title + "\n" + 规范化 abstract)`，跨库疑似重复的检测键 |

注意：这里的 `year` 是**发表年**（检索窗口过滤用）；主表的 `time` 是**采样年**（契约 §3.2），两者绝不可混用。

`source_summary.csv` 的 `search_string / search_date / hit_count` 只能来自**人工维护的检索日志**（`--search-log`）；`exported_count` 由脚本实算。未回填的库会标注"检索式/命中数待人工回填（GATE-1）"。

---

## 4. 工作流程（AI 与人的分工）

```
[1] generate_search_terms.py  → search_strategy_draft.md（草稿）
        │ 人工核改草稿（同义词增删 / 字段限定 / 语法试错）
[2] 人工逐库执行检索 → 记录检索式原文 + 日期 + 命中数（检索日志 / 01-search/检索记录.csv）
        │ 人工逐库导出题录
[3] parse_ris / parse_enl / parse_bibtex → 各库 raw_records_*.csv（--validate）
[4] merge_sources.py → all_raw_records.csv + source_summary.csv
        │ 命中数 vs 导出数对账；排除词过度杀伤诊断
[5] 人 → GATE-1：确认检索充分性后开启闸门 → 释放题录给 S2（AI 预处理 + 人工裁决）
```

**AI 可以做**：起草检索式、解析题录、合并计数、疑似重复计数、量级诊断（命中数过少 → 提示放宽；异常巨大 → 提示补排除词）。
**只有人能做**：确定最终检索式、执行检索、填报命中数、确认检索充分性（GATE-1）。

---

## 5. 红线（继承 SKILL.md §7 + routing.md §2）

1. **AI 不代填命中数/检索式** —— `source_summary.csv` 中这些列只接受人工检索日志。
2. **AI 不开启 GATE-1** —— 闸门只由人在 `_state/gates.json` 写入 `passed`。
3. **缺失留空，不插补** —— 解析失败的字段留空并记 warnings，不猜。
4. **不支持的格式显式拒绝** —— `.enl/.enlx` 等二进制格式报错并给出导出指引，不做猜测性解析。
5. **联网边界** —— 仅 A1 可联网（WebSearch/WebFetch 核验检索语法）；不得下载全文自动提取。

---

## 6. 方法学文档

| 文档 | 内容 |
|---|---|
| `search-strategy.md` | 从 project.yaml 到检索式的构造方法、可复现性要求、PRISMA 2020 identification 记录规范 |
| `references/database_syntax.md` | 六库检索语法差异表 + 导出格式建议 |
| `references/exclusion_word_bank.md` | 排除词银行四组（动物/细胞机制/职业/临床）+ 过度杀伤预警 |

---

## 7. 自检

```bash
python skills/S1-search-strategy/scripts/generate_search_terms.py --self-test
python skills/S1-search-strategy/scripts/parse_ris.py --self-test
python skills/S1-search-strategy/scripts/parse_enl.py --self-test
python skills/S1-search-strategy/scripts/parse_bibtex.py --self-test
python skills/S1-search-strategy/scripts/merge_sources.py --self-test
python shared/record_schema.py --self-test
```

模板（`templates/raw_records_template.csv`、`templates/source_summary_template.csv`）由 `skills/S5-weighted-pooling-edi/scripts/gen_templates.py` 生成 —— **禁止手写**（`--check` 进 CI）。
