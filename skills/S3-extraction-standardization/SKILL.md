---
name: hbm-extraction-standardization
description: "AI-assisted data extraction with mandatory human line-by-line proofing for human internal-exposure / biomonitoring meta-analyses, plus normalization of every extracted record into one analysis-ready master table under a frozen data contract. Supplies ready-to-use extraction prompts (environmental-sample and population/biomonitoring variants, multi-paper batch mode, and a second-pass self-check prompt), a proofing rulebook ordered by downstream blast radius, a complete field dictionary with enumerations, and an explicit watchlist of the fields AI most often drops or hallucinates. Enforces 'never infer unstated data' and blocks imputation on the four core fields (concentration GM, sample size, sampling year, sample matrix). Analyte-, matrix-, population- and region-agnostic: works for any exposure biomarker (e.g. arsenic, cadmium, lead, mercury, PFOA/PFAS, antibiotics, drug concentrations) in urine, blood, cord blood, breast milk, nail or hair. Triggers on: data extraction, information extraction, extraction table, data abstraction, record-level database, master table construction, extract from papers, AI extraction prompt, proofreading extracted data, double-check extracted values, data entry, 数据提取, 信息提取, 文献提取, 提取表, 主表, 主表构建, 数据库构建, 数据录入, 提取模板, 提取提示词, 校对, 复核, 逐条核对, 数据核对, 缺失字段, 可疑数据, 样本量核对, 采样年份."
license: MIT (code) / CC-BY-4.0 (docs)
compatibility: opencode claude-code dsh
allowed-tools:
  - Read
  - Write
  - Edit
  - Grep
  - Glob
  - Bash
  - Task
  - TodoWrite
  - AskUserQuestion
metadata:
  parent_skill: hbm-meta
  version: "1.0.0"
  last_updated: "2026-07-19"
  status: active
  stage: 3
  gate: GATE-3
  contract_version: "1.0.0"
  task_type: open-ended
---

# S3 · Extraction & Standardization — 数据提取与校对

**一句话**：把 384 篇论文里的浓度数据，变成一张**每条值都能追溯到原文**的主表。

**核心立场**：AI 负责提速，人负责真实性。**未经逐条校对的记录不得用于主结论。**

---

## 1. 加载条件与前置依赖

| 项 | 要求 |
|---|---|
| **前置闸门** | **GATE-2 必须为 `passed`**（人群边界已裁决，无 `inclusion_decision=Pending`） |
| 前置产物 | `02-screening/` 的纳入研究清单、质量评分表、疑似排除清单 |
| 前置校验 | 只对 `inclusion_decision = Include` 的研究提取；存在 `Pending` 项时**拒绝启动** |
| 负责人 | **A3 `extraction-agent`**（权限 L2：可写数据表，不可改稿件、不可改闸门） |
| 产出闸门 | **GATE-3**（数据真实性复核）——由**人工**开启 |
| 可独立使用 | 是。若你已有文献全文与清单，可单独加载本技能，无需先跑 S1/S2 |

**启动前必须完成的判断**（否则会做无用功）：
- [ ] 目标物质（`analyte`）与基质范围已确定
- [ ] 单位口径已由 GATE-0 冻结（尿 → 什么单位？血 → 什么单位？）
- [ ] 分期方案已冻结（影响 `time` 字段的代表年取法）

**违规信号（拒绝启动）**：
- `_state/gates.json` 中 GATE-2 ≠ `passed`
- 纳入清单中存在 `inclusion_decision = Pending`
- `project.yaml` 未指定 `unit_policy`

---

## 2. 输入与输出契约

### 2.1 输入

| 来源 | 内容 | 契约字段 |
|---|---|---|
| A2 筛选产物 | 纳入研究清单（含 `study_no`、`inclusion_decision = Include`） | §3.4 `inclusion_decision` |
| A2 质量评分 | AHRQ 11 条得分与分级 | （不进主表，供 `qc_reported` 参考） |
| 文献全文 / 附件 | PDF、Word、Supplementary | — |
| `project.yaml` | `analyte`、`matrix_scope`、`unit_policy`、`period_scheme` | §5 |

### 2.2 输出（对应 `data-contract.md` §3 的字段号）

| 输出组 | 字段号 | 字段名 | 谁填 |
|---|---|---|---|
| 来源标识 | 1–9 | `record_id` `study_no` `cohort_id` `dedup_group` `title` `author` `doi` `t_publication` `source_database` | 主 Agent 分配 #1–4；AI 提取 #5–9 |
| 时空标识 | 10–19 | `time` `time_start` `time_end` `country` `province` `city` `county` `region` `latitude` `longitude` | AI + 人工校对 |
| 人群标识 | 20–32 | `population` `population_group` `recruit_crowd` `population_desc` `classification_rule` `age` `age_mean` `height` `weight` `bmi` `male_n` `female_n` `gender` | AI + 人工校对 |
| 边界风险标记 | 33–38 | `flag_occupational` `flag_disease` `flag_endemic_area` `flag_mixed_occupational` `inclusion_decision` `exclusion_reason` | AI 只标 flag；**`inclusion_decision` 仅人工** |
| 基质与检测 | 39–45 | `sample_type` `blood_matrix` `flag_blood_matrix` `analyte` `analyte_species` `detection_method` `qc_reported` | AI + 人工校对 |
| **原文统计量** | 46–59 | `stat_type` `initial_gm` `initial_mean` `initial_sd` `initial_median` `initial_p25` `initial_p75` `initial_p05` `initial_p95` `initial_min` `initial_max` `initial_gsd` `unit_original` `sample_size` | AI 提取 + 人工逐条校对 |
| 审计元数据 | 68–72 | `extracted_by` `verified` `verified_by` `snapshot_tag` `row_hash` | 人 + 脚本 |

**S3 明确不填**（留空给 S4 或主 Agent）：
`adjusted`(60) `unit_final`(61) `gm_summary`(62) `gsd_summary`(63) `conversion_path`(64)
`conversion_params`(65) `inferred_flags`(66)

> 例外：`notes`(67) **S3 必须填写**（一切疑问与不确定项）。

### 2.3 交付产物清单

| 产物 | 路径 | 说明 |
|---|---|---|
| 主表剧本 | `03-extraction/{analyte}_{YYYY-MM-DD}.xlsx` | 带日期快照 |
| 缺字段清单 | `03-extraction/缺字段清单.md` | 校验器产出 |
| 可疑值清单 | `03-extraction/可疑值清单.md` | 校验器产出 |
| 逐条核对任务表 | `03-extraction/逐条核对任务表.md` | 按记录分组的 checklist |
| 校对日志 | `03-extraction/校对日志.md` | 字段级修改记录（用 `templates/proofing_log.md`） |
| 记录卡 | `03-extraction/记录卡/{record_id}.md` | 仅争议记录需要（用 `templates/record_card.md`） |
| 校验报告 | `03-extraction/validate_report.md` | `validate_table.py --out` 产出 |

---

## 3. 核心流程

```
① 初始化
   ├─ 校验 GATE-2 = passed
   ├─ 由主 Agent 分配 record_id / study_no（永不重编号）
   └─ 从 project.yaml 读取 analyte / unit_policy / period_scheme

② AI 初提（逐篇）
   ├─ 用 references/ai_extraction_prompts.md 提示词 B（人群内暴露型）
   ├─ 输出落到 templates/extraction_template.csv 的列结构
   ├─ 多篇批处理时一文一表、表头一致、不跨文合并
   └─ 支撑材料必须单独报告，不得静默跳过

③ AI 自查（第二轮，可选但推荐）
   └─ 用提示词 C 把提取表回喂给 AI 回原文核对
      ⚠️ 自查结果不能替代人工校对（同模型有系统性盲区）

④ 自动校验
   ├─ python scripts/validate_table.py --input <主表> --out <报告>
   ├─ ERROR 必须清零才能继续；WARN 进人工判断
   └─ 产出缺字段清单 + 可疑值清单

⑤ 逐条人工校对（GATE-3 核心，不可省）
   ├─ 按 references/proofing_rules.md 的 P1→P9 优先级
   ├─ P1 样本量 → P2 时间 → P3 基质与校正 → P4 统计量 → P5 单位
   ├─ 疑义记录开 record_card；所有修改写入 校对日志
   └─ 留空而非补值：原文没有的，一律留空 + notes

⑥ 标记与提交
   ├─ extracted_by = AI+Human；verified = TRUE；verified_by = 校对者
   ├─ 重跑 validate_table.py 确认无 ERROR
   └─ 提交 GATE-3 待裁决包（含留空记录清单与待决项）

⑦ GATE-3（人工开启）
   └─ 人批准后主表冻结 → 交 S4
```

---

## 4. 判断规则（核心决策点）

### R1 · 不推断未报告的数据

`gm_summary` / `sample_size` / `time` / `sample_type` 四个字段**禁止插补**。
原文没有 → **留空 + `notes` 说明**。校验器对 `value_interpolated` 标记与这四个字段的组合会直接报 `V-04` 严重违规。

### R2 · 一条浓度统计 = 一行记录

同一篇论文报告多个基质 / 人群 / 性别 / 年龄组 / 时段 → **拆成多行**，并登记 `record_split_from_study`。
不得把多组数据合并成一个"总体值"（除非原文就是这么报告的）。

### R3 · 分性别、分年龄必须拆行

`gender` 列标 `Male`/`Female`/`Both`/`Unknown`。合并性别报告用 `Both`。
分性别报告的浓度值若只有一条（未拆分）→ 视为数据缺口，在 `notes` 说明。

### R4 · 亚组样本量不得用总样本量代替

原文只给总样本量而未给亚组样本量 → 该亚组行的 `sample_size` **留空** + `notes` 写"仅报告总样本量 N=xxx"。
**这是本项目权重被系统性放大的主要来源。**

### R5 · `time` 是采样/招募年份

不是发表年份。多年区间取代表年，取法必须写进 `notes` 并登记 `time_represented`。
若 `time == t_publication`，校验器会 `WARN`，需回原文确认。

### R6 · `unit_original` 原样抄录

**不许规范化**（`μg/L`、`ug/L`、`µg/L` 保留原写法）。规范化是 S4 的职责，且必须留痕。

### R7 · `stat_type` 决定 S4 的换算路径

必须准确标注原文报告的是哪一类统计量（GM / AM+SD / 中位数+IQR / 中位数+范围 / 仅极值）。
填错会导致 S4 用错 Wan 公式，估算偏差不可见。

### R8 · 血基质存疑必须打标记

标题或摘要提示 `serum` / `plasma` / `血清` / `血浆` 而正文未明确全血 → `flag_blood_matrix = TRUE`。
不得默认按全血处理而不留痕。

### R9 · `inferred_flags` 的填写边界

**AI 不得自行填写 `inferred_flags`**——它无法可靠判断自己哪些值是推算的。
由 S4 换算脚本依据 `conversion_path` 自动登记，人工在 GATE-4 复核。

### R10 · 边界判断交人工

`inclusion_decision`、`exclusion_reason`、`cohort_id`、`dedup_group` **仅人工可写**。
AI 只能设 flag 并产出疑似清单。有疑问 → `Pending`，不得自行排除。

---

## 5. AI 易漏字段黑名单

> 完整版见 `references/missing_field_watchlist.md`（清单 A）。校对按此优先级。

| 排序 | 字段 | 漏提表现 | 下游影响 |
|---|---|---|---|
| 1 | `sample_size` | 亚组行填总样本量或整列空 | **权重错 → 全部加权结果错** |
| 2 | `time` | 填发表年份 / 空缺 | 分期与趋势全错 |
| 3 | `adjusted` | 尿样几乎不被主动填写 | 单位口径不明 |
| 4 | `blood_matrix` | 血样基质沉默 | 全血/血清浓度差异大 |
| 5 | `stat_type` | 不填或与实际列不符 | 换算路径选错 |
| 6 | `unit_original` | 被"规范化" | 换算依据丢失 |
| 7 | `population_group` | 留空或全填 `Unknown` | EDI 取参失败 |
| 8 | `city` / `province` | 多个市用逗号连写 | 同城聚类失效 |
| 9 | `analyte_species` | 不区分总砷与形态砷 | 可比性声明失据 |
| 10 | `qc_reported` | 不填 | 质量评分缺依据 |
| 11 | `initial_gsd` | 与 `initial_sd` 混填 | 误差线错误 |
| 12 | `gender` / `male_n` / `female_n` | 分性别数据未拆行 | 分层结果缺失 |
| 13 | `detection_method` | 只写"检测了" | 质量评分与可比性 |
| 14 | 附件内容 | 静默跳过 Supplementary | 漏掉大量数据 |
| 15 | `notes` | 一片空白 | 后人无法追溯 |

**实证**：血基质与尿校正状态两项 AI 漏提率接近 100%，必须靠专项扫描而非依赖提示词。

---

## 6. 引用的脚本与模板

### 脚本

| 脚本 | 用途 | 命令 |
|---|---|---|
| `scripts/validate_table.py` | 契约校验（8 条规则）+ 可疑值扫描（清单 B）+ 生成报告 | `python validate_table.py --input 主表.xlsx --config project.yaml --out report.md` |
| 〃 | 自检（内置脱敏夹具） | `python validate_table.py --self-test` |

**状态**：第三步骨架版已跑通 `--self-test`（8/8 断言通过，退出码 0）。
第四步待办（详见 `{skill_root}/STEP4-TODO.md`）：**A1** 移除 `project.yaml` 缺失时的 `DEFAULT_CONFIG` 兜底（改为硬错误 + 退出码 2）；**A3** 口径全部改由配置读取；**A4** 输出带快照指纹；**A6** CI 集成 `--self-test`。

### 模板

| 模板 | 用途 |
|---|---|
| `templates/extraction_template.csv` | 主表列结构与三行示例（含边界情形示范） |
| `templates/record_card.md` | 单条争议记录的完整证据留痕卡 |
| `templates/proofing_log.md` | 批次校对日志（字段级修改 + 留空记录 + 提示词迭代） |

### 参考文档

| 文件 | 内容 |
|---|---|
| `references/ai_extraction_prompts.md` | 提示词 A/B/C + 硬约束 + 已知失败模式 |
| `references/proofing_rules.md` | 校对操作手册（P1–P9 优先级 + 逐字段动作） |
| `references/field_dictionary.md` | 72 字段速查 + 枚举速查 + AI 不填的列 |
| `references/missing_field_watchlist.md` | 清单 A（易漏字段）+ 清单 B（可疑值模式） |

---

## 7. 与其他 Skill 的接口

### 7.1 上游：S2 → S3

| 我从 S2 需要 | 字段/产物 | 校验 |
|---|---|---|
| 纳入研究清单 | `study_no` + `inclusion_decision=Include` | 无 `Pending` |
| 队列归属 | `cohort_id` | 已由 GATE-2 裁决 |
| 质量评分 | AHRQ 得分 | 供 `qc_reported` 参考 |
| 边界人群裁决 | `exclusion_reason` | `Exclude` 的不得提取 |

**拒绝条件**：GATE-2 `pending`，或存在 `Pending` 项。

### 7.2 下游：S3 → S4 交接字段表 ★

**这张表是 S3/S4 的契约边界。S3 交出的必须完整，S4 才能无歧义地换算。**

| # | 字段 | 由谁产出 | 交接要求 |
|---|---|---|---|
| 46 | `stat_type` | **S3** | 必须准确；决定 S4 选哪条 Wan 公式 |
| 47 | `initial_gm` | **S3** | 原文直接报告 GM 时填；决定"仅直接 GM 子集"敏感性分析 |
| 48 | `initial_mean` | **S3** | 算术均值 |
| 49 | `initial_sd` | **S3** | **算术** SD |
| 50 | `initial_median` | **S3** | |
| 51 | `initial_p25` | **S3** | 与 `initial_p75` 配对 |
| 52 | `initial_p75` | **S3** | |
| 53 | `initial_p05` | **S3** | 尾部百分位，勿与 P25 混 |
| 54 | `initial_p95` | **S3** | |
| 55 | `initial_min` | **S3** | 与 `initial_max` 配对（范围法） |
| 56 | `initial_max` | **S3** | |
| 57 | `initial_gsd` | **S3** | **几何** SD |
| 58 | `unit_original` | **S3** | 原样抄录，**不许规范化** |
| 59 | `sample_size` | **S3** | > 0；亚组独立样本量 |
| 39 | `sample_type` | **S3** | 决定目标单位 |
| 40 | `blood_matrix` | **S3** | 决定血样可否与全血合并 |
| 41 | `flag_blood_matrix` | **S3** | 存疑标记 |
| 43 | `analyte_species` | **S3** | 影响可比性声明 |
| 10 | `time` | **S3** | 用于分期 |
| 14 | `province` / 17 `region` | **S3** | S4/S5 分组依据 |
| 21 | `population_group` | **S3** | EDI 生理参数取用依据 |
| 60 | `adjusted` | **S4** | S3 留空；由 S4 依据 `unit_original` 与原文判定后填写 |
| 61 | `unit_final` | **S4** | 由 `unit_policy` 决定 |
| 62 | `gm_summary` | **S4** | S4 的最终输出 |
| 63 | `gsd_summary` | **S4** | |
| 64 | `conversion_path` | **S4** | 如 `AM_SD→Wan_S1→GM`、`ug/L→ICRP89_Adult→ug/g Cr` |
| 65 | `conversion_params` | **S4** | 如 `CE=1.35,V=1.4,CC=0.964,BW=60` |
| 66 | `inferred_flags` | **S4**（脚本自动登记）→ 人工在 GATE-4 复核 | **S3 不填** |
| 67 | `notes` | **S3** | 一切不确定项；S4 可追加 |
| 68 | `extracted_by` | **S3** | 校对完成后必须为 `AI+Human` |
| 69 | `verified` | **S3** | 进入 S4 ≥90%；进入 S5 主结论 100% |
| 72 | `row_hash` | 脚本 | 关键字段短哈希，用于 S4 前后 diff |

**S3 的交付承诺（供 S4 依赖）**：
1. 所有 `Include` 研究的 `stat_type` 与 `initial_*` 列**互相对位**，无错位
2. `unit_original` 与 `sample_size` **100% 非空**
3. `gm_summary`/`adjusted`/`unit_final`/`conversion_*`/`inferred_flags` **保持为空**（避免 S4 误读为已有值）
4. `notes` 记录了所有原文矛盾与不确定项

### 7.3 反馈：S4 → S3

S4 换算过程中若发现：
- `stat_type` 与可用列不匹配（如标 `GM_only` 但 `initial_gm` 为空）
- `unit_original` 无法解析
- `sample_size` 与亚组描述矛盾

→ **不得自行修正**，必须回报 S3 走 GATE-3 重开流程（触发 stale 回滚）。

---

## 8. 完成判据（GATE-3 出口）

- [ ] `validate_table.py` 无 `ERROR`
- [ ] P1–P5 五项**逐条**核对完毕
- [ ] `inferred_flags` 未填（留给 S4），但所有会触发推断的情形已在 `notes` 记录
- [ ] 所有记录 `extracted_by = AI+Human`、`verified = TRUE`、`verified_by` 已填
- [ ] `verified` 比例 ≥90%（S5 主结论要求 100%）
- [ ] `校对日志.md` 完整（含 AI 原值）
- [ ] 争议记录已开 `record_card.md`
- [ ] 待决项已提交 GATE-2/GATE-3 人工裁决，**AI 未自行结论**
- [ ] `notes` 中无空洞；留空记录已逐条说明

---

**维护者**：HBM-Meta-Agent　**上级**：`{skill_root}/SKILL.md`　**契约**：`shared/data-contract.md`
**下一阶段**：`skills/S4-unit-statistic-conversion/SKILL.md`
