---
name: hbm-qc-audit-revision
description: "The quality-control and revision engine for internal-exposure / biomonitoring meta-analyses: audits every number in a manuscript against a frozen data snapshot and reports drift with the specific sentences that must change, screens for duplicate cohort/population reuse that inflates participant counts, runs sensitivity analyses (stratification, subsets, leave-out), verifies DOIs and reference metadata against Crossref, crosswalks in-text citations to the reference library, and drives a closed-loop reviewer-comment tracker. Produces the six standard process documents that make a revision auditable: review progress, number audit, de-duplication report, reference-library fix list, citation crosswalk, and reference insertion table. Analyte-, matrix-, population- and region-agnostic. Triggers on: number audit, numerical consistency check, manuscript vs data, data snapshot drift, stale manuscript, orphan number, traceability check, duplicate cohort, population reuse, double counting, participant count wording, de-duplication, sensitivity analysis, robustness check, stratified analysis, subset analysis, leave-one-out, DOI verification, Crossref check, reference library cleanup, EndNote fixes, duplicate references, citation crosswalk, citation mismatch, reference insertion table, reviewer response, revision tracking, point-by-point reply, rebuttal, review comment status, 数值审计, 数据审计, 数值不一致, 对不上, 稿件核对, 孤儿数字, 快照漂移, 重复计数, 队列重复, 人群重复, 去重筛查, 参与者计数, 敏感性分析, 稳健性, 分层分析, 子集分析, 排除后分析, DOI 核验, 参考文献核对, 引用错配, EndNote 清理, 重复文献, 正文引用对照, 参考文献插入表, 审稿意见, 返修, 逐条回复, 回复审稿人, 审稿进度, 意见闭环."
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
  - Task
  - TodoWrite
  - AskUserQuestion
metadata:
  parent_skill: hbm-meta
  version: "1.0.0"
  last_updated: "2026-07-19"
  status: active
  stage: 6
  gate: GATE-5
  contract_version: "1.0.0"
  task_type: open-ended
---

# S6 · QC, Audit & Revision — 质控、审计与返修

**一句话**：让稿件里的**每一个数字都能追溯到快照某一行**，让**每一次修改都能追溯到某条意见**。

**核心立场**：**AI 只产差异与证据，裁决权在人。**

---

## 1. 加载条件与前置依赖

| 项 | 要求 |
|---|---|
| **可进入时机** | ★ **任一阶段均可进入**（本技能不强制前置闸门，因为它是横切能力） |
| 典型入口 | ① 分析完成后做数值审计；② 投稿前做全链核验；③ 收到审稿意见后做返修闭环 |
| 最小输入 | 稿件 + 主表快照（+ 可选：文献库导出、质量评分表） |
| 负责人 | **A5 `audit-agent`**（L1，只读+建报告）与 **A6 `revision-agent`**（L3，可改稿） |
| 产出闸门 | **GATE-5**（去重与敏感性裁决）+ 贯穿闸门（数值审计） |

**三种入口的最小依赖**

| 入口 | 必需输入 | 产出 |
|---|---|---|
| 数值审计 | 稿件 + 主表快照 + `project.yaml` | `02_数值审计对照表.md` |
| 去重筛查 | 主表（含 `city`/`sample_type`/`author`） | `03_去重与纳入标准筛查报告.md` |
| 敏感性分析 | S5 的标准化主表 | `04_敏感性分析报告.md` |
| 文献链核验 | 稿件 + 文献库导出（+ 网络） | `04`/`05`/`06` 文献三件套 |
| 审稿闭环 | 审稿意见 + 稿件 | `01_审稿意见处理进度与回查清单.md` + 回复信 |

**拒绝启动的信号**：
- `project.yaml` 缺失（无法确定分期口径）→ 硬错误，退出码 2（**禁止兜底**）
- 主表快照未声明 `sha256`（无法做漂移判定）→ 警告但继续，并提示"无快照保护"

---

## 2. 输入与输出契约

### 2.1 输入（`data-contract.md` §4 快照指纹 + §5 全部配置）

| 来源 | 内容 | 用途 |
|---|---|---|
| **§4 快照指纹** | `snapshot_tag`、`sha256`、`generated_at`、`config_version`、`record_count`、`study_count` | ★ 漂移判定与数字追溯 |
| **§5.2 `period_scheme`** | 分期边界 + `freeze_date` | 重算时段值 |
| §5.3 `region_map` | 地区字典 | 重算地区值 |
| §5.4 `unit_policy` | 单位口径 | 校验结果单位 |
| §5.6 `edi` | EDI 参数 | 重算 EDI |
| §5.7 `weighting` | 加权口径 | 重算加权 GM |
| 主表 | 全部字段 | 重算来源 |
| S5 产物 | `pooled_results.csv`、`edi_results.csv`、`weight_diagnostics.md` | 审计比对基准 |
| 稿件 | 正文 + 摘要 + 图注 + 表注 | 审计对象 |

### 2.2 输出产物

| 产物 | 路径 | 模板 |
|---|---|---|
| 01 审稿意见处理进度与回查清单 | `11-revision/01_review_progress.md` | `templates/01_review_progress.md` |
| 02 数值审计对照表 | `06-qc-audit/02_number_audit.md` | `templates/02_number_audit.md` |
| 03 去重与纳入标准筛查报告 | `06-qc-audit/03_dedup_report.md` | `templates/03_dedup_report.md` |
| 04 敏感性分析报告 | `06-qc-audit/04_sensitivity.md` | （见 `sensitivity.py` 输出） |
| 05 文献库需修改清单 | `07-references/04_endnote_fixes.md` | `templates/04_endnote_fixes.md` |
| 06 正文引用对照表 | `07-references/05_citation_crosswalk.md` | `templates/05_citation_crosswalk.md` |
| 07 参考文献插入表 | `07-references/06_reference_insert.md` | `templates/06_reference_insert.md` |

> 编号沿用本项目惯例（01–06 为过程文档核心集）；04 敏感性报告编号在 03 之后、文献三件套之前。

---

## 3. 数值审计协议

> 完整定义见 `references/audit_protocol.md`。脚本：`scripts/audit_numbers.py`。

### 3.1 唯一事实来源

```
快照（sha256 固定） + 冻结配置（config_version 固定） + 确定性脚本
                        ↓
              正文中的每一个数字
```

**硬规则**：无法追溯到"快照某一行 + 配置某一条"的数字 = **孤儿数字**，不允许存在。

### 3.2 四类状态

| 状态 | 触发 | 处置 |
|---|---|---|
| `OK` | 重算值与稿件一致（容差内） | 无需处理 |
| `MISMATCH` | 快照哈希一致但值不同 | ★ 人工裁决 |
| `STALE_MANUSCRIPT` | **稿件绑定的快照哈希 ≠ 当前快照** | ★ 全量重算，不做逐值比对 |
| `NO_SOURCE` | 追溯不到来源 | ★ 人工裁决（补来源或删除） |

### 3.3 容差

| 类型 | 容差 |
|---|---|
| 加权 GM / 浓度 | 相对 0.5% |
| 百分比 / 降幅 | 绝对 0.5 个百分点 |
| 相关系数 `r` | 绝对 0.001（须同时核对 P 值显著性方向） |
| 计数 | 精确 |
| EDI | 相对 1% |
| P 值 | 只比对显著性方向，不比具体值 |

### 3.4 ★ 连带修改定位

数值改了，**叙述往往也必须改**。脚本会自动检测含比较级/趋势/最高级表述的句子：

触发词：`declin` `decreas` `reduc` `rebound` `increas` `stabil` `higher` `lower` `highest` `lowest` `no data` `下降` `上升` `回升` `稳定` `高于` `低于` `最高` `最低` `无数据`

**高风险区（必须逐个检查）**：**摘要 · 结论 · Highlights · 图注 · 表注**。

> 本项目实证：时段 GM 变化导致"持续下降"→"下降—回升—稳定"、降幅 64%→62%、地区排序全部重排；这几处若只改正文数字而不改摘要与结论，审稿人必然再次指出。

### 3.5 两种取值模式

| 模式 | 写法 | 适用 | 重算能力 |
|---|---|---|---|
| **占位符模式**（推荐） | 稿件写 `{{period_GM:Urine:1980-2000}}` | 新稿件 | ★ 确定性最强，直接重算 |
| **线索模式** | 脚本按"数字 + 单位"提取句子，再**推断分层** | 既有稿件 | 需能推断出基质 + 分层 |

两种模式**互斥**：**同一份稿件中不应混用**。若混用，脚本按**占位符优先**处理——线索模式只审计**未被占位符覆盖的句子**，避免同一数值被审计两次（占位符项 `value=None` 需人工填值，线索项自带值，重复审计会互相干扰）。

**百分比不自动重算（决策）**：`61.79%` 这类降幅百分比一律标 `unresolved` → `NO_SOURCE`，原因写明"降幅百分比需由两个分层值算出，须人工核对（分母选择不唯一：相对基期、相对峰值或其他）"。
理由：降幅分母的选择可能不唯一，自动重算会引入**假确定感**；人工核对两个分层值的降幅成本极低。

> 补充：纯百分比（无浓度单位）本就不会被数字提取器捕获，因此不存在"被误当作浓度值"的风险；风险仅在"同句既有浓度又有百分比"时——该句整体标 `unresolved`。

**线索模式的分层推断规则（最具体优先）**：

```
基质（脐带血 > 尿/血）  →  时段（句中显式年份区间）  →  地区  →  人群
```

- 词边界匹配，避免 `east` 命中 `Northeast` 之类的子串误配
- **按句切分**后再推断——否则同段后一句的分期/地区线索会污染前一句的数值
  （实测曾把血值误判为 `period=1980-2000`）
- 推断不出的项标 `unresolved` → 产出 `NO_SOURCE` 并附原因，**不猜测**

### 3.6 ★ 从主表真正重算（`--master-table`）

```bash
python audit_numbers.py --input 稿件.md --master-table 主表快照.xlsx \
       --snapshot snapshots.csv --config project.yaml --out 02_number_audit.md
```

| 情形 | 行为 |
|---|---|
| 提供 `--master-table` | ★ **真正重算**：按推断分层从主表算加权 GM，逐项比对并分类 |
| 未提供 | **降级模式**：只输出待审项清单，**明确提示无法重算**（不假装审计过） |

**端到端实测**（合成主表 + 稿件）：

```
提取到 2 个待审数值项
OK=1  MISMATCH=1  STALE=0  NO_SOURCE=0
| 1 | period_GM | Urine | 1980-2000 | 32.58ug/g Cr | 32.5800 | 0.0000% | OK |
| 2 | region_GM | Blood | region=North | 5.99ug/L | 4.2400 | 41.2736% | MISMATCH |
```

> 第 2 行即真实审计价值：稿件写 5.99、主表重算 4.24，差 41% → 进待人工裁决。

---

## 4. 重复计数筛查协议

> 完整定义见 `references/dedup_cohort_protocol.md`。脚本：`scripts/dedup_screen.py`。

### 4.1 三级重复

| 级别 | 定义 | 识别 |
|---|---|---|
| 一级 · 研究级 | 同一论文重复收录 | DOI/标题匹配 |
| **二级 · 队列级** | 同一批受试者被多篇论文报告 | 同城 + 同基质聚簇（≥4 篇）；同作者聚簇；队列名识别 |
| 三级 · 人群级 | 同一人贡献多条记录 | 同 `study_no` 多行；尿+血同时报告 |

### 4.2 去重策略（人工在 GATE-2/GATE-5 裁决）

| 策略 | 做法 | 推荐 |
|---|---|---|
| `S1` 保留全部 + 修正措辞 | 只把计数表述改为记录级 | ★ **主分析** |
| `S2` 保留样本量最大者 | 同队列同基质同期只留最大 | **敏感性分析** |
| `S3` 同队列内部先合并 | 保留全部信息 | 实现复杂 |

### 4.3 ★ 措辞规则

| 情形 | ✅ 允许 | ❌ 禁止 |
|---|---|---|
| 未去重，记录级求和 | `participant records`、`record-level sums` | `participants`、`individuals`、`subjects` |
| 已去重 | `unique participants`（须说明方法与影响） | 只说 `participants` |

**强制图注文案**：
> Sample sizes are record-level sums; some participants contributed to more than one record or more than one matrix.

脚本会自动扫描稿件中的不合规措辞并给出修改建议。

---

## 5. 敏感性分析协议

> 完整定义见 `references/sensitivity_analysis_protocol.md`。脚本：`scripts/sensitivity.py`。

### 5.1 三类分析

| 类型 | 做法 | 本项目实例 |
|---|---|---|
| **分层** | 按关键类别分别算 | 尿校正方式（肌酐 177 / 比重 42 / 参考值换算 223） |
| **子集** | 只用最高质量子集 | 仅直接报告 GM（零推断）：88 / 442 条 |
| **排除后** | 剔除可疑记录 | 职业暴露 35 篇、疾病人群 55 篇、血基质存疑 27 篇 |

### 5.2 形态判定（逐时段数值，不凭印象）

```
相邻两期相对变化 < 5%  →  稳定（→）
下降 ≥ 5%             →  ↓
上升 ≥ 5%             →  ↑
```

组合成形如 `↓↑→` 的标签，并可转人读表述"下降—上升—稳定"。

### 5.3 降级规则

| 情形 | 处理 |
|---|---|
| 子集占全库 < 20% | ★ 结论**降级为定性参考** |
| 各层形态一致 | 可写"robust across strata"（**仍须人工批准**） |
| 存在不一致层 | ★ **必须在正文或局限中说明**，不得只放补充材料 |

**硬规则**：**AI 不得自行声明"结论稳健"**，只给事实与数值。

---

## 6. 文献链核验协议

> 完整定义见 `references/citation_verification_protocol.md`。脚本：`scripts/verify_dois.py`、`scripts/citation_crosswalk.py`。

### 6.1 三条链必须逐条对上

```
正文引用  ↔①  文献库记录  ↔②  真实发表信息（Crossref/DOI）
                              ③ 库内部（重复 / 机构作者字段）
```

| 链 | 检验 | 失败表现 |
|---|---|---|
| ① | 每个正文引用在库中唯一对应 | MISSING / AMBIGUOUS / UNUSED |
| ② | 年份/卷/期/页/期刊/DOI 正确 | DOI 解析到**不同文章** |
| ③ | 无重复记录；机构作者字段正确 | 引用乱码 |

### 6.2 状态与容差

| 状态 | 含义 |
|---|---|
| `VERIFIED` | 全部一致 |
| `MISMATCH` | 字段不一致（列出差异） |
| `DOI_INVALID` | 格式非法 / 解析失败 |
| `NO_DOI` | 无 DOI，需人工核验 |

比对容差：标题前 30 字符（归一化后）｜期刊名允许缩写（逐词前缀匹配）｜年份/卷/期/页精确。

### 6.3 本项目实证（必读）

| 文献 | 问题 |
|---|---|
| Castriota 2022 | 库中 Year=2020、Volume=128 → 应为 **2022 / 130** |
| **Shen 2016** | 库中记录指向**完全不同的文章**（Arch Toxicol），且 DOI 无效 → 应为 **Int J Environ Res Public Health / 13 / 205** |
| Yu 2007 | Journal/Volume/Pages 全错 |
| Zhang 2024a | Pages 673-683 → 应为 **766-775** |

**Shen 2016 这类错误"人工看一遍"不可能发现**——必须靠 DOI 解析。

### 6.4 常见陷阱

| 陷阱 | 说明 |
|---|---|
| 重复副本只改一个 | 改了副本 A、实际引用副本 B → 问题依旧。**每个副本都要改** |
| 机构作者 | `EFSA` / `NRC` 字段须为机构全称 |
| 同姓同年 | 需 `a`/`b` 后缀消歧，库中两条都要有 |
| 无 DOI 文献 | 书籍/标准/报告须人工核验 |

---

## 7. 审稿闭环协议

> 完整定义见 `references/review_response_workflow.md`。模板：`templates/01_review_progress.md`。

### 7.1 六份过程文档

| # | 文档 | 作用 |
|---|---|---|
| 01 | 审稿意见处理进度与回查清单 | 总控：状态、处理内容、负责人 |
| 02 | 数值审计对照表 | 数值可追溯 |
| 03 | 去重与纳入标准筛查报告 | 重复计数与边界人群 |
| 04 | 文献库需修改清单 | 字段级修正 |
| 05 | 正文引用对照表 | 引用链核验 |
| 06 | 参考文献插入表 | 逐条插入用完整信息 |

### 7.2 意见状态机

```
NEW → ASSIGNED → IN_PROGRESS → RESOLVED → VERIFIED → CLOSED
                     ↕
                NEEDS_HUMAN（人响应后回到 IN_PROGRESS）

任意状态 → DEFERRED（明确不改，必须写理由）
```

**门禁**：全部 `CLOSED` 或 `DEFERRED` 方可再次投稿。

### 7.3 关键纪律

| # | 纪律 |
|---|---|
| 1 | **一条意见可能含多个诉求** → 必须拆解成子项 |
| 2 | `RESOLVED ≠ CLOSED`，**必须复核** |
| 3 | **数值变更必须连带检索摘要与结论** |
| 4 | **不改也要写理由** |
| 5 | **过程文档随稿件版本化**，不覆盖旧版 |
| 6 | **凭证类信息**（账号密码等）**不得进入任何交付物** |

---

## 8. ★ AI 与人工的边界

### 8.1 AI 可以做的

拆解意见为子项 · 分类分派 · 执行脚本产出证据（数值审计/去重/敏感性/DOI 核验）· 定位连带修改 · 撰写回复草稿 · 维护状态机

### 8.2 AI 不得做的（硬约束）

| 动作 | 原因 |
|---|---|
| **决定"不改"**（`DEFERRED`） | 作者/导师决策 |
| **决定排除某研究** | 领域知识判断（GATE-2） |
| **采信哪个数值** | `MISMATCH` 可能是换算问题而非计算错误 |
| **自行声明"结论稳健"** | 敏感性结论由人裁 |
| 补填外部信息 | 基金编号、注册号、推荐审稿人 |
| 确认"是否预先注册" | 事实性声明 |
| 定稿措辞 | 尤其因果表述的强弱 |
| 删除库记录 | 可能误删唯一副本 |

### 8.3 必须标为 `NEEDS_HUMAN` 的事项

基金信息 · 是否预先注册及注册号 · 推荐审稿人 · CRediT 分工 · AI 使用声明 · 单位英文名/作者英文名 · 需从原文补标的数据 · 需裁决的排除名单 · 凭证处理

---

## 9. 引用的脚本与模板

### 脚本（5 个，全部含 `--self-test`）

| 脚本 | 用途 | 自检 | 状态 |
|---|---|---|---|
| `scripts/audit_numbers.py` | 数值审计：**从主表真正重算** + 四状态分类 + 连带修改定位 | **62/62** | 骨架版（已接入 `--master-table`） |
| `scripts/dedup_screen.py` | 同城/同作者/队列聚簇 + 三级去重影响估计 + 措辞检查 | **36/36** | 骨架版 |
| `scripts/sensitivity.py` | 分层 / 子集 / 排除后 + 形态判定 + 降级规则 | **46/46** | 骨架版 |
| `scripts/verify_dois.py` | DOI 核验（Crossref）+ 字段级差异清单 | **44/44** | 骨架版 |
| `scripts/citation_crosswalk.py` | 正文引用 ↔ 库对照 + 插入表 | **36/36** | 骨架版 |

**依赖关系**：`audit_numbers.py` 与 `sensitivity.py` 跨目录导入 S5 的 `weighted_gm` / `edi_calculation`（**避免复制算法，保证单一实现**）。

**第四步待办**（详见 `{skill_root}/STEP4-TODO.md`）：
- **A1** `project.yaml` 缺失 → 硬错误退出码 2
- **A2** 移除全部硬编码路径
- **A4** 输出带快照指纹
- **A5** `inferred_flags` 反推逻辑跨脚本一致
- **A6** CI 集成 `--self-test`
- 附加：真实 `.docx` / `.txt` 稿件解析器；`.enl`/`.ris` 文献库解析器；质量评分表接入

### 模板（6 份过程文档）

| 模板 | 用途 |
|---|---|
| `templates/01_review_progress.md` | 审稿意见总控（状态机 + 数值台账 + 闭环门禁） |
| `templates/02_number_audit.md` | 数值审计对照表（快照指纹 + 四状态 + 连带修改） |
| `templates/03_dedup_report.md` | 去重与纳入标准筛查报告（簇清单 + 策略影响 + 措辞） |
| `templates/04_endnote_fixes.md` | 文献库字段级修改清单 |
| `templates/05_citation_crosswalk.md` | 正文引用对照表 |
| `templates/06_reference_insert.md` | 参考文献逐条插入表 |

### 参考文档

| 文件 | 内容 |
|---|---|
| `references/audit_protocol.md` | 四状态、容差、连带修改定位、快照漂移机制 |
| `references/dedup_cohort_protocol.md` | 三级重复、聚簇方法、三种去重策略、措辞规则 |
| `references/sensitivity_analysis_protocol.md` | 三类分析、形态判定、降级规则、强制要求 |
| `references/citation_verification_protocol.md` | 三条链、DOI 核验、去重与字段规范、本项目 4 条实证错误 |
| `references/review_response_workflow.md` | 状态机、意见分类处置、AI/人工边界、回复信要求 |

---

## 10. 与其他 Skill 的接口

### 10.1 上游：S4 / S5 → S6

| 上游产出 | S6 用途 |
|---|---|
| S4 快照指纹 | ★ `STALE_MANUSCRIPT` 判定 |
| S4 `conversion_trace.csv` | 数值审计的证据链 |
| S4 `flags_derivation.csv` | 推断标记可审计性复核 |
| S4 `FAILED` / `WARN` 清单 | 局限说明与敏感性剔除候选 |
| S5 `pooled_results.csv` | 数值审计比对基准 |
| S5 `edi_results.csv` | EDI 审计 |
| S5 `weight_diagnostics.md` / `max_weight_share` | 敏感性（剔除主导记录） |
| S5 `record_count` / `study_count` / `total_sample_size` | 计数措辞审计 |

### 10.2 下游：S6 → 收尾

| 产物 | 用途 |
|---|---|
| `02_number_audit.md` | 定稿门禁依据 |
| `03_dedup_report.md` | 局限说明与措辞修正依据 |
| `04_sensitivity.md` | 方法学与稳健性声明依据 |
| `04/05/06` 文献三件套 | 投稿文件集的参考文献 |
| `01_review_progress.md` | 返修闭环总控 |

### 10.3 反馈：S6 → S3/S4/S5（stale 回滚）

S6 若发现以下情况，**不得自行修正**，必须触发对应闸门重开：

| 发现 | 回滚到 | 动作 |
|---|---|---|
| `stat_type` 与 `initial_*` 错位 | S3 / GATE-3 | 重开数据真实性复核 |
| 换算口径不一致 | S4 / GATE-4 | 重开标准化口径确认 |
| 分期/单位口径变更 | S4 | bump `config_version`，标记全部下游 `stale` |
| 主表新建快照 | S4→S5→S6 | 按阶段顺序重跑，产出新旧对照表 |
| 去重策略选定 | S5 | 重跑合并与敏感性 |

---

## 11. 判断规则（12 条）

| # | 规则 |
|---|---|
| R1 | **正文不允许孤儿数字**——每个数字必须可追溯到快照某一行 + 配置某一条 |
| R2 | **先查快照哈希，再查数值**；哈希不匹配时逐值比对无意义 |
| R3 | **每个数字都要审**，不抽样 |
| R4 | **摘要与结论必须并入审计范围**（最高风险遗漏区） |
| R5 | **数值变更必须连带检索摘要/结论/图注** |
| R6 | **AI 只列疑似，排除/去重由人裁决** |
| R7 | **计数措辞必须与计算方式一致**（记录级求和不表述为人数） |
| R8 | **敏感性分析未做，不得声明稳健**；子集 < 20% 结论降级 |
| R9 | **不一致的敏感性结果必须在正文或局限说明**，不得只放补充材料 |
| R10 | **三个文献链都要查**；重复记录**每个副本都要改** |
| R11 | **一条意见可能含多个诉求**，必须拆解；`RESOLVED ≠ CLOSED` |
| R12 | **凭证类信息不得进入任何交付物** |

---

## 12. 完成判据

- [ ] 数值审计对照表全部 `OK`（或差异已逐条裁决）
- [ ] 无 `NO_SOURCE` / `STALE_MANUSCRIPT` 残留
- [ ] 连带修改清单已全部执行（含摘要与结论）
- [ ] 去重筛查的簇与边界人群清单已逐条裁决
- [ ] 计数措辞已修正并写入图注/表注
- [ ] 敏感性分析已完成，不一致项已说明
- [ ] 文献三链核验完成，库与稿一致
- [ ] 审稿意见全部 `CLOSED` 或 `DEFERRED`（附理由）
- [ ] 过程文档与稿件版本号一致
- [ ] **无凭证类信息残留**
- [ ] 快照指纹已记录于所有产物

---

**维护者**：HBM-Meta-Agent　**上级**：`{skill_root}/SKILL.md`　**契约**：`shared/data-contract.md`
**上一阶段**：`skills/S5-weighted-pooling-edi/SKILL.md`
