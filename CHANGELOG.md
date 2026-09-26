# 更新日志（Changelog）

本项目遵循 [Semantic Versioning](https://semver.org/lang/zh-CN/)。
格式参考 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/)。

---

## [Unreleased]

### Added · S1-search-strategy 完整实现（STEP4-TODO.md F5 之 S1 部分 / F3 的 RIS·EndNote·BibTeX 部分）

- `shared/record_schema.py` — 统一题录字段 / 来源枚举 / `raw_row_hash` 算法的**单一来源**
  （11 字段；`SUMMARY_FIELDS` 与待回填提示语同源于此），自带 41 项自检
- `skills/S1-search-strategy/scripts/`
  - `generate_search_terms.py` — 从 `project.yaml → search`（条件必需区块，缺失即硬错误）
    生成 PubMed/WoS/Scopus/Embase/CNKI/万方检索式**草稿** + GATE-1 人工检查清单；
    中文词缺失时输出"待人工补充"占位，不编造中文词
  - `parse_ris.py` / `parse_enl.py` / `parse_bibtex.py` — 题录解析器 → 统一格式 CSV；
    手解析（无新依赖）；`.enl` 二进制等不支持格式**显式拒绝**；warnings 不中断
  - `merge_sources.py` — 多库合并 + `source_summary.csv`（导出数实算；
    检索式/命中数**仅取自人工检索日志**，AI 不代填）；疑似重复只计数，去重裁决在 S2
- `skills/S1-search-strategy/` 文档 — `SKILL.md`、`search-strategy.md`
  （可复现检索记录规范 + PRISMA identification 要求）、
  `references/database_syntax.md`（六库语法差异 + 导出格式建议）、
  `references/exclusion_word_bank.md`（四组排除词银行 + 过度杀伤预警）
- `templates/project.yaml` 与示例配置新增 `search` 区块（砷示例词表）
- `gen_templates.py`（唯一模板生成器）新增 S1 模板：`raw_records_template.csv`、
  `source_summary_template.csv`（禁止手写，`--check` 进 CI）
- CI：`ci_local.py` / `validate.yml` 新增 6 个自检目标与 2 个随包模板断言；
  `verify_structure.py` 将 S1 移入已实现子技能

### 说明

- S2（AI 预处理 + 人工闸门筛选）仍未实现，见 STEP4-TODO.md F5
- 主表契约（`data-contract.md`）与闸门定义无变更；S1 的题录 `source_database`
  枚举含 Embase，与主表列枚举是两个层级（见 `record_schema.py` 模块注释）

---

## [1.0.0] — 2026-07-19

首个可用版本。**从想法到文章**的完整流水线：把一类真实项目（中国一般人群内暴露砷
时空分布，384 篇文献 / 688 条记录）的方法学经验，固化为可复用的 Agent 工作包。

### Added · 核心资产

**数据契约（`shared/`）**
- `data-contract.md` — **72 字段主表契约**：类型、必填性、枚举、单位口径；
  §4 **快照指纹**（sha256 + 生成时间 + 配置版本）；§5 `project.yaml` 完整规范；
  §6 **20 项 `inferred_flags` 标记字典**；§7 8 条校验顺序
- `quality-gates.md` — **六道人工闸门**（GATE-0…GATE-5）：触发条件、AI 产出物、
  人工裁决项、未通过信号、状态机；含"唯一能把闸门改为 `passed` 的是人"的权限铁律
- `project_config.py` — **唯一配置加载入口**：`project.yaml` 缺失/解析失败/缺区块 →
  硬错误（**不允许任何兜底**）
- `inferred_flags.py` — 标记字典**单一来源**（23 项 = 契约 20 + 脚本内部 3），
  自带使用处覆盖核对与文档项数一致性检查
- `snapshot.py` — 快照指纹（`{analyte}_{YYYY-MM-DD}` + 字节级 sha256 + 记录/研究计数
  + 漂移判定 + 追加式登记表）

**六个子技能**
- `S3-extraction-standardization` — AI 初提 + **逐条人工校对**；提取提示词模板
  （环境/人群/复核三轮）、校对优先级 P1–P9、AI 易漏字段 15 项黑名单、可疑值模式 B1–B5
- `S4-unit-statistic-conversion` — **三级标准化流程**；Wan 2014 公式 **S1–S7** 保真实现；
  ICRP 89 参数选择决策树；`inferred_flags` 反推规则表；**S4 输入契约**明确化
  （S3 原始提取表 vs S4 输出 vs S5 输出的区分）
- `S5-weighted-pooling-edi` — 样本量加权 GM/GSD；**分期零字面量**；权重占比诊断；
  EDI 尿路**双路径交叉验证** + 血路**修正后公式**（旧公式显式拒绝）；
  **脐血方案 A**（不计算 EDI 并计数）；记录数 vs 唯一参与者措辞规则
- `S6-qc-audit-revision` — **数值审计**（从主表真正重算 + 四状态分类 + 连带修改定位）；
  重复计数筛查（三级聚簇 + 三种去重策略影响估计）；敏感性分析（分层/子集/排除后 +
  形态判定 + 降级规则）；文献链核验（DOI / 引用对照 / 插入表）；审稿闭环状态机
- `S1-search-strategy`、`S2-screening-quality` — **目录骨架**（见下方 Known limitations）

**可执行脚本（18 个 `.py`，全部含 `--self-test`，累计约 700 项断言）**
`validate_table` · `wan_convert` · `convert_units` · `weighted_gm` · `edi_calculation` ·
`gen_templates` · `audit_numbers` · `dedup_screen` · `sensitivity` · `verify_dois` ·
`citation_crosswalk` · `project_config` · `inferred_flags` · `snapshot` ·
`verify_consistency` · `verify_structure` · `ci_local` · `inject_failure`

**质量保障体系**
- `tests/verify_consistency.py` — 跨脚本一致性核验（C1–C6）：单一来源导入、
  反推确定性、规则表覆盖、端到端闭集、**唯一反推实现**、自检冒烟
- `tests/verify_structure.py` — 结构校验（S1–S6）：frontmatter、`parent_skill`、
  目录完整性、`name` 一致性、description 长度、顶层文件
- `tests/ci_local.py` — **本地 CI 预演**（无 GitHub 环境也能验证）
- `tests/inject_failure.py` — **注入断言失败以验证 CI 真的能拦住**
- `.github/workflows/validate.yml` — 5 步 CI（自检 / 模板 / 结构 / 一致性 / 源码卫生）
- `.gitignore` — 原始数据、稿件产物、`_state/`、凭证的排除规则

**教学案例与文档**
- `examples/arsenic-china-1980-2024/` — **脱敏可跑通案例**：检索式（含**排除词银行
  四类分组**）、72 列表头 + 12 条覆盖全部分叉的脱敏记录、数值审计与返修清单样例；
  自带脱敏规则自检
- `templates/project.yaml` — 可复制的完整配置模板（三键口径逐条说明"决定什么 + 改动后果"）
- `LICENSE`（MIT + CC BY 4.0 双许可）、`CITATION.cff`、`CONTRIBUTING.md`、本文件

### Design decisions · 关键设计决策

| 决策 | 理由 |
|---|---|
| **闸门只由人开启** | AI 无法做"该不该纳入某研究""该采信哪个数值"的领域判断 |
| **`project.yaml` 缺失即硬错误** | 用默认值继续会"看起来跑通、但口径是错的"——最危险的一类失败 |
| **字典单一来源** | 曾 5 处各写一份，扩充 16→19→20 时漏同步 2 处 |
| **未实现的口径显式拒绝** | `wan_variant="plus"` 未实现 → 抛错，**不做静默等价** |
| **降幅百分比不自动重算** | 分母选择不唯一，自动重算会引入"假确定感" |
| **脐血不计算 EDI（方案 A）** | 成人参数对胎儿是**模型对象错配**，不是精度不足 |
| **模板禁止手写** | 手写模板在开发期连续出错三次（列错位、数值与代码不一致） |

### Fixed · 开发期发现并修复的实质缺陷

| # | 缺陷 | 后果 |
|---|---|---|
| 1 | `wan_convert` 不读 `wan_variant` / `median_to_gm_strategy` | 配置**读了却无效**（静默失败） |
| 2 | `wan_variant="plus"` 与 `minus` 结果相同 | **假实现**（静默等价）→ 改为显式拒绝 |
| 3 | `validate_table` 的 `unit_policy` 传嵌套字典 | 单位口径校验**永远失败** |
| 4 | `validate_table` 的字典副本停留在 16 项 | 闭集校验形同虚设 |
| 5 | `convert_units` 只取 `initial_gm` | 仅报告中位数的记录被误判为"缺值" |
| 6 | `wan_convert` 不保留输入行的单位换算段 | 链式运行 **7/12 记录 `FLAG_MISMATCH`** |
| 7 | `derive_flags` 的 `reported_GM` 未早退 | 零估算记录被叠加统计量类标记 |
| 8 | `dedup_screen` 用 `year[:4]` 切片分期 | 违反分期铁律（且无年份语义） |
| 9 | `dedup_screen` 的 `Study.year` 取自 `t_publication` | 用**发表年**替代采样年 |
| 10 | `sensitivity` 阈值用 `>=` | 恰好 5% 被误判为上升 |
| 11 | `citation_crosswalk` 正则字符类漏空格 | **9 条引用只提取到 2 条** |
| 12 | `audit_numbers` 按段落而非句子推断分层 | 跨句线索污染（血值被误判为时段值） |

### Known limitations · 已知限制（见 `STEP4-TODO.md`）

- **S1 / S2 仅有目录骨架**，尚未实现（`verify_structure.py` 标注为"规划中"不校验）。
  实现计划见 `STEP4-TODO.md` **F5**
- `wan_variant="plus"` 未实现（显式拒绝 → 见 **F1**）
- 脐血 EDI 走方案 A（不计算）；独立参数表见 **F2**
- `.docx` / `.enl` / `.ris` 需先手工转换为 `.md` / `.csv`（见 **F3**）
- `weighting.method = inverse_variance` 未实现（见 **F4**）

### Notes · 关于本项目的来源

本工作包脱胎于一个已完成的真实科研项目（中国一般人群内暴露砷时空分布 meta 分析，
1980–2024，384 篇文献 / 688 条记录）。所有方法学规则、易错点、审稿意见类型
均来自该项目的实证，而非推测。

**案例已完全脱敏**：具体研究标题、作者、DOI、省份级真实数值均已移除；
命中数（5714 / 546 / 4649 / 519 / 135 / 384）作为**检索充分性诊断**的核心证据保留。

---

## 版本路线图

| 版本 | 计划内容 |
|---|---|
| v1.1 | 实现 S1 / S2（F5）；补齐对应的结构校验与案例示例 |
| v1.2 | Wan `plus` 变体（F1）；`inverse_variance` 加权（F4） |
| v1.3 | `.docx` / 文献库解析器（F3）；脐血 EDI 参数表（F2） |
