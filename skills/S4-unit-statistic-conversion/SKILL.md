---
name: hbm-unit-statistic-conversion
description: "Standardizes heterogeneous reported concentrations and summary statistics into one comparable scale for internal-exposure / biomonitoring meta-analyses, in a strict three-level order: unify the exposure indicator and matrix, unify the concentration unit (mass units, molar units, urine creatinine correction via ICRP 89 reference values), then unify every statistic into a geometric mean. Implements the Wan et al. (2014) formulas S1-S7 for converting median / interquartile range / range to mean and SD, with explicit validity conditions, degenerate-case protection, and refusal to apply the formulas to records already reported on a log scale. Automatically derives the inferred-flags audit trail from a structured conversion-path code, so every estimated value is traceable to the rule that produced it. Analyte-agnostic: molar-mass table and unit map are pluggable for any exposure biomarker (e.g. arsenic, cadmium, lead, mercury, PFOA/PFAS, antibiotics, drug concentrations) in urine, blood, cord blood, breast milk, nail or hair. Triggers on: unit conversion, concentration conversion, standardize units, normalize units, creatinine correction, urine creatinine, specific gravity correction, urine dilution, geometric mean conversion, GM conversion, log-normal conversion, median to mean, median to geometric mean, IQR to SD, quartile to mean, range to SD, Wan formula, estimate SD from median, ICRP 89, urine reference values, molar conversion, nmol/L to ug/L, 单位换算, 浓度换算, 单位统一, 标准化, 尿肌酐校正, 肌酐校正, 尿比重校正, 尿稀释校正, GM 转换, 几何均值, 几何均值转换, 对数正态转换, 中位数转均值, 中位数转几何均值, 四分位数转标准差, 极值转标准差, Wan 公式, 标准差估算, 换算模板, 摩尔换算, ICRP89 参数."
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
  stage: 4
  gate: GATE-4
  contract_version: "1.0.0"
  task_type: open-ended
---

# S4 · Unit & Statistic Conversion — 单位与统计量标准化

**一句话**：把"各研究自己那套口径"的浓度数据，变成"**同一种单位、同一个统计量**"的可比较数据，且每一步换算都留下可审计的痕迹。

**核心立场**：**顺序不可颠倒，换算必须留痕，不明即停。** 宁可产出 `FAILED` 交人工，也不猜一个看起来合理的值。

---

## 1. 加载条件与前置依赖

| 项 | 要求 |
|---|---|
| **前置闸门** | **GATE-3 必须为 `passed`**（数据真实性已逐条复核） |
| 前置产物 | `03-extraction/{analyte}_{date}.xlsx`、校验报告、校对日志 |
| 前置校验 | `validate_table.py` 无 `ERROR`；`verified` 比例 ≥ 90% |
| 配置前置 | `project.yaml` **必须存在**且含 `unit_policy` / `standardization` / `urine_reference` / `period_scheme` |
| 负责人 | **A4 `statistics-agent`**（权限 L2） |
| 产出闸门 | **GATE-4**（标准化口径确认）——人工开启并批准快照冻结 |
| 可独立使用 | 是。只依赖主表 + `project.yaml` |

**拒绝启动的信号（硬阻断）**：
- GATE-3 ≠ `passed`
- **`project.yaml` 不存在或解析失败** → 硬错误，退出码 2（**禁止兜底**，见 `{skill_root}/STEP4-TODO.md` A1）
- `standardization` 区块缺失（= GATE-0 未完成）
- 主表存在 `stat_type` 或 `unit_original` 为空的记录 → 回 S3

---

## 2. 输入与输出契约

### 2.1 输入字段（`data-contract.md` §3.5 / §3.6）

> ★ **输入契约（必须明确）**：S4 的输入是 **S3 产出的「原始提取表」**——
> 含 `initial_*` 列、`unit_original`、`stat_type`（**原文报告的统计量**）。
> **不是** S4 自己的输出（`gm_summary` / `unit_final` / `conversion_path`）。
>
> | 表 | 谁产出 | 标志列 |
> |---|---|---|
> | **S3 原始提取表**（← S4 的输入） | S3 | `initial_gm` / `initial_median` / `initial_mean` / `initial_min` / `unit_original` / `stat_type` |
> | **S4 标准化表**（S4 的输出） | **本 Skill** | `gm_summary` / `unit_final` / `conversion_path` / `inferred_flags` |
> | **S5 合并结果**（示例 `pooled_results_OUTPUT_example.csv`） | S5 | `stratum` / `gm` / `gsd` / `weight_share` |
>
> **混用后果**：把 S4 输出（或 S5 输出）当输入再跑 S4，会构成"**用输出再跑一遍**"的循环错误——
> 最典型的是 `initial_*` 全空导致全部记录 `FAILED`，或误把 `gm_summary` 当作原文 GM
> 而跳过换算。`convert_units.py` 已加**输入前检**：`initial_*` 全空即报错退出码 2。

| # | 字段 | 用途 |
|---|---|---|
| 39 | `sample_type` | 决定目标单位与是否走尿校正 |
| 40 | `blood_matrix` / 41 `flag_blood_matrix` | 决定血样可否与全血同池 |
| 43 | `analyte` / 44 `analyte_species` | 摩尔质量查表；形态可比性 |
| **46** | **`stat_type`** | ★ 决定换算路径（首要分支） |
| **47–57** | **`initial_gm` … `initial_gsd`** | ★ 公式输入值 |
| **58** | **`unit_original`** | ★ 单位换算输入（原文原样，永不被覆盖） |
| **59** | **`sample_size`** | ★ 加权 + Wan 公式所需 |
| 21 | `population_group` / 32 `gender` / 26 `age_mean` | ★ ICRP 89 参数选择 |
| 10 | `time` / 14 `province` / 17 `region` | S5 分组（S4 仅继承） |
| 67 | `notes` | 继承并追加 |

### 2.2 输出字段（`data-contract.md` §3.7）

| # | 字段 | 填写规则 |
|---|---|---|
| 60 | `adjusted` | 尿样必填：`No`/`Creatinine`/`SpecificGravity`/`ReferenceConversion`/`NotApplicable` |
| 61 | `unit_final` | 由 `unit_policy` 决定（尿 → `ug/g Cr`；血/脐血 → `ug/L`） |
| 62 | `gm_summary` | ★ 最终合并用值 |
| 63 | `gsd_summary` | 有则可算；误差线用（不作权重） |
| 64 | `conversion_path` | ★ 结构化编码（见 `references/unit_conversion_playbook.md` §6.1） |
| 65 | `conversion_params` | 全部参数留痕（公式号、变体、ICRP 参数、MW…） |
| 66 | `inferred_flags` | ★ **由 `conversion_path` 自动登记**，人工在 GATE-4 复核 |
| 72 | `row_hash` | 脚本重算，与 S3 输入 diff |

**S4 不改动 §3.1–3.6 任何字段**（仅可追加 `notes`）。

### 2.3 交付产物

| 产物 | 路径 |
|---|---|
| 标准化主表（草案 → 冻结） | `04-standardization/{analyte}_{date}_standardized.xlsx` |
| 换算痕迹表 | `04-standardization/conversion_trace.csv` |
| **标记反推表** | `04-standardization/flags_derivation.csv` ★ 可审计性凭证 |
| 换算路径分布 / 尿校正分布 | `04-standardization/*.md` |
| 标准化报告 | `04-standardization/standardization_report.md` |
| 快照登记 | `_state/snapshots.csv`（GATE-4 通过后追加） |

---

## 3. 三级标准化流程（核心方法）

```
第 ① 级  统一暴露指标
   analyte 一致 · analyte_species 可比 · 基质口径明确
   └─ 血：WholeBlood 可与全血池；Serum/Plasma 必须分层并保留 flag
   └─ 尿：校正口径必须明确（Creatinine / SpecificGravity / No）
        ↓ ① 未完成，谈不上"可比"
第 ② 级  统一浓度单位
   质量单位归一 → 摩尔单位换算 → 尿校正（ICRP 89）
   └─ 目标：尿 → ug/g Cr；血 / 脐血 → ug/L
        ↓ ② 未完成，不同研究不能同池
第 ③ 级  统一为 GM
   直接报告 GM → 取用
   AM + SD   → 对数正态参数转换
   中位数/IQR/极值 → Wan 2014（S1–S7）
        ↓ ③ 未完成，不能加权合并
```

### 顺序为什么不可颠倒

若先算 GM 再换单位：线性缩放时结果看似正确，但**一旦统计量转换（非线性）与单位换算混在一起，就无法判断哪一步引入了偏差**，且 `conversion_path` 无法表达。因此统一坚持 **先单位 → 后统计量**。

### 步骤与命令

| 步 | 动作 | 命令 |
|---|---|---|
| 1 | 预检与配置校验（缺配置即硬错误） | `validate_table.py --config project.yaml --check-config-only` |
| 2 | 第①级：指标与基质归类 | 人工判断 + 脚本标注 |
| 3 | 第②级：单位换算 | `python convert_units.py --input 主表.xlsx --config project.yaml --out 换算后.csv --trace trace.csv` |
| 4 | 第③级：统计量 → GM | `python wan_convert.py --input 换算后.csv --config project.yaml --out 标准化.csv --trace wtrace.csv --flags-derivation flags_derivation.csv` |
| 5 | 自动登记 `inferred_flags` | 由步 4 产出 `flags_derivation.csv` |
| 6 | 复校 + 统计 + 量级检查 | `validate_table.py` 重跑 |
| 7 | 提交 GATE-4 待裁决包 | 用 `templates/standardization_report.md` |

---

## 4. Wan 2014 公式 S1–S7：适用条件与不可用情形

> 完整定义与推导见 `references/wan2014_formulas.md`。以下为**决策用**摘要。

### 4.1 公式速查

| 编号 | 已知 | 分母 | 路径编码 |
|---|---|---|---|
| **S1** | `n` + `min` + `max` + `Mean` | `ξ(n)` | `Min_Max→Wan_S1→GM` |
| **S2** | `n` + `min` + `max` + `Median` | `ξ(n)` | `Median_Range→Wan_S2→GM` |
| **S3** | `min` + `max`（无 `n`） | `2q(0.75)·√2 = 1.9079` | `Min_Max→Wan_S3→GM` |
| **S4** | `n` + `q1` + `q3` + `Median` | `η(n)` | `Median_IQR→Wan_S4→GM` |
| **S5** | `n` + `q1` + `q3` + `Mean` | `η(n)` | `AM_IQR→Wan_S5→GM` |
| **S6** | `q1` + `q3`（无 `n`） | `2q(0.75) = 1.349` | `Median_IQR→Wan_S6→GM` |
| **S7** | `n` + `q1` + `q3` + `Median` | `2Φ⁻¹((0.75n−0.125)/(n+0.25))` | `Median_IQR→Wan_S7→GM` |

其中 `ξ(n) = η(n) = 2Φ⁻¹((n − 0.375)/(n + 0.25))`。

### 4.2 成立前提（任一违反则结果不可信）

1. 数据近似**正态/对称**（公式基于正态分位数关系）
2. 样本量**中等以上**（建议 n ≥ 25；n < 25 时打 `WARN small_n`）
3. `min`/`max` **不是离群值**（范围法极敏感）
4. `q1`/`q3` 是**真四分位数**（不是 P5/P95，不是 Mean±SD）
5. 中心值确实是**均值或中位数**

### 4.3 ★ 不可用情形（脚本会拒绝执行）

| 情形 | 返回 | 说明 |
|---|---|---|
| **`stat_type = GM_GSD` / `GM_only`** | `SKIP` | ★ **原文已在对数尺度，严禁再套 Wan**（重复转换会压低 GM）。这是本项目最易犯、最隐蔽的错误 |
| 只有中位数、无任何离散度信息 | `FAILED` | 该记录**本质上不可合并**；计入局限说明 |
| `q3 ≤ q1` / `max ≤ min` | `FAILED` | 分位矛盾 → 回 GATE-3 复核 |
| 需 `n` 但 `n` 缺失 | 回退 `S3`/`S6` + `no_n_fallback` | 精度下降，进敏感性分层 |
| `η ≤ 0` 或中心值 ≤ 0 | `FAILED` | 公式退化 |
| 估算 `SD ≤ 0` | `FAILED` | 交人工 |

### 4.4 分母的渐近行为（决定"缺 n"的代价）

| 公式 | 分母 n=10 | n=100 | 大 n 极限 |
|---|---|---|---|
| S4 / S5 | 3.288 | 5.0 | **→∞** |
| S7 | ~1.29 | 1.330 | **→1.349** |
| S6 | 1.349 | 1.349 | 常数 |

**三条推论**：
1. **S7 → S6**（大 n 时二者接近）→ IQR 法缺 `n` 的损失相对可控
2. **S4 恒给出比 S6 更小的 SD**（`η(n) ≥ 1.349`）→ **S4 与 S6 绝不可互换**
3. 范围法缺 `n`（S3 分母 1.9079）系统性**高估** SD，是三类缺 n 情形中代价最大的

### 4.5 变换到 GM

```
σ   = sqrt( ln(1 + CV²) )      CV = SD / AM
GM  = AM / exp(σ² / 2)
GSD = exp(σ)
```

中位数路径另有一条**更稳**的做法：对数正态分布下**中位数 = GM**，可直接取用（`Median_as_GM`）。
选择由 `project.yaml → standardization.median_to_gm_strategy` 决定，**全项目必须统一**。

---

## 5. ICRP 89 参数选择规则

> 完整参数表与决策树见 `references/icrp89_urine_reference.md`。

### 5.1 默认三档（与原项目 R 脚本一致）

| `population_group` | `CE` (g/day) | `V` (L/day) | `CC_creat` (g/L) | 来源 |
|---|---|---|---|---|
| `Adults` | 1.35 | 1.4 | 0.964 | ICRP 89, ≥18 不分性别 |
| `Pregnant` | 1.275 | 2.0 | 0.638 | NHANES |
| `Minors` | 0.925 | 1.05 | 0.815 | ICRP 89, 10–15 区间（⚠️ 见 5.3） |

### 5.2 选择决策树

```
孕妇 / 哺乳期？
├─ 是 → Pregnant（CE=1.275, V=2.0, CC=0.638）    ← 覆盖年龄与性别
└─ 否 → 年龄已知？
        ├─ <0.5 → Newborn (0.167)      ├─ <4  → Infant (0.275)
        ├─ <8   → Toddler (0.66)       ├─ <13 → Child (0.929)
        ├─ <18  → Adolescent (0.857) / 分性别 0.875 / 0.833
        └─ ≥18  → 性别已知？
                  ├─ 男 → Adult_Male (1.063)
                  ├─ 女 → Adult_Female (0.833)
                  └─ 未分性别 → Adults 通用 (0.964)   ← 默认
```

**优先级**：`孕妇` > `年龄段` > `性别`。

### 5.3 ★ 关键注意事项

| # | 事项 | 后果 |
|---|---|---|
| 1 | **成人性别不明用通用值 `0.964`**，不得默认男性 | 默认男性使尿砷浓度系统性**低估约 10%** |
| 2 | `Minors` 档的 `CC=0.815` 与 `CE/V=0.881` **不自洽** | 本工作包**原样保留**（保真），但必须留痕 + 进敏感性分层 |
| 3 | 换算必须记录用的是 `CC` 直除 还是 `CE/V` 等价式 | 两条路径在 4 位小数下差约 0.03%，混用会造成表内不自洽 |
| 4 | 血 / 脐血**不走**尿校正 | ICRP 表仅适用尿样 |
| 5 | `μg/g`（组织基准）≠ `μg/g Cr` | 混用是语义 + 量级双错 |

**两条换算路径**（必须自洽）：
```
浓度(ug/g Cr) = 浓度(ug/L) ÷ CC          … CC 直除
浓度(ug/g Cr) = 浓度(ug/L) × V ÷ CE      … CE/V 等价式
```

---

## 6. `inferred_flags` 自动登记逻辑（从 `conversion_path` 反推）

> 完整规则表见 `references/unit_conversion_playbook.md` §6.2。这是 S4 的可审计性核心。

### 6.1 为什么由脚本登记而非 AI

AI 无法可靠判断"自己哪些值是推算的"。因此流程固定为：
**S4 脚本依据 `conversion_path` 确定性登记 → 产出反推表 → 人工在 GATE-4 复核**。

### 6.2 `conversion_path` 编码格式

```
<来源统计量>→<步骤1>→<步骤2>→…→<目标量>
```

示例：
```
AM_SD→AMSD_to_LN→GM
Median_IQR→Wan_S4→ug/L→ICRP89_Adults→ug/g Cr→GM
Median_IQR→Median_as_GM→ICRP89_Pregnant→ug/g Cr
ng/L→ug/L→AMSD_to_LN→GM
nmol/L→ug/L→reported_GM
reported_GM
reported_creatinine_corrected
```

### 6.3 反推规则（实现于 `wan_convert.py::derive_flags`）

| 路径命中 | 登记标记 |
|---|---|
| `Wan_S1` / `Wan_S5` | `gm_from_mean_sd`（+ `gm_from_range`/`gm_from_iqr`） |
| `Wan_S2` | `gm_from_median`, `gm_from_range` |
| `Wan_S3` | `gm_from_range`, `no_n_fallback` |
| `Wan_S4` / `Wan_S7` | `gm_from_median`, `gm_from_iqr` |
| `Wan_S6` | `gm_from_median`, `gm_from_iqr`, `no_n_fallback` |
| `AMSD_to_LN` | `gm_from_mean_sd` |
| `Median_as_GM` | `gm_from_median` |
| `nmol/L` | `molar_conversion` |
| `ICRP89` | `unit_converted_creatinine` |
| 来源 `ug/g Cr` → 目标 `ug/L` | `unit_converted_volume` |
| `reported_GM` / `reported_creatinine_corrected` | **（零标记）** ★ |

### 6.4 可审计性的四条硬性要求

1. **确定性**：同一 `conversion_path` 必然产出同一标记集合（`--self-test` 已验证两次运行一致）
2. **可反推**：必须产出 `flags_derivation.csv`（`record_id | conversion_path | flag | rule_hit | source_field`）
3. **双向可校验**：人工增删的标记若无法用规则表解释 → 报 `unverifiable_flag` 告警
4. **零推断子集完整**：`reported_GM` 记录的 `flags` 必须**为空**，否则"仅直接报告 GM"敏感性分析的定义被污染

### 6.5 标记字典（**20 项**文档化标记 + 3 项脚本内部标记）

> 权威定义：`shared/data-contract.md` §6｜单一来源：`shared/inferred_flags.py`
> 完整表：`skills/S3-extraction-standardization/references/field_dictionary.md` §8.5

S4 主要涉及：`gm_from_mean_sd` `gm_from_median` `gm_from_iqr` `gm_from_range`
`unit_converted_creatinine` `unit_converted_volume` `molar_conversion` `no_n_fallback` `coarse_age`
并从 S3 继承：`time_represented` `blood_matrix_assumed` `species_total_assumed` 等。

---

## 7. 引用的脚本与模板

### 脚本

| 脚本 | 用途 | 自检 | 状态 |
|---|---|---|---|
| `scripts/convert_units.py` | 质量/摩尔单位换算、尿校正、ICRP 参数选择 | `--self-test`（**59/59 通过**） | 骨架版 |
| `scripts/wan_convert.py` | Wan S1–S7、AM_SD→GM、`inferred_flags` 反推 | `--self-test`（**43/43 通过**） | 骨架版 |

**第四步待办**（详见 `{skill_root}/STEP4-TODO.md`）：
- **A1** `project.yaml` 缺失/解析失败 → 硬错误退出码 2，**移除 `DEFAULT_UNIT_POLICY` 兜底**
- **A3** 单位口径、ICRP 参数表、Wan 变体、转换策略全部从 `project.yaml` 读取
- **A4** 输出带快照指纹（`sha256` + `generated_at` + `config_version`）
- **A5** `flags_derivation.csv` 与 `conversion_trace.csv` 落盘（已在 `wan_convert.py` 实现，`convert_units.py` 需补齐）
- **A6** CI 集成 `--self-test`

### 模板

| 模板 | 用途 |
|---|---|
| `templates/conversion_trace.csv` | 换算痕迹表（14 行示例，覆盖 OK/SKIP/WARN/FAILED 全部状态） |
| `templates/standardization_report.md` | GATE-4 待裁决包正文 + 快照冻结依据 |

### 参考文档

| 文件 | 内容 |
|---|---|
| `references/unit_conversion_playbook.md` | 三级流程、质量/摩尔单位表、尿校正、**`inferred_flags` 反推规则表** |
| `references/wan2014_formulas.md` | S1–S7 原文定义、± 变体说明、失效条件、**分母渐近行为** |
| `references/icrp89_urine_reference.md` | 完整参数表、决策树、路径编码、一致性校验点 |
| `references/standardization_workflow.md` | 七步流程、10 条判断规则、GATE-4 出口条件 |

---

## 8. 与其他 Skill 的接口

### 8.1 上游：S3 → S4

依赖 S3 的**四条交付承诺**（见 `S3/SKILL.md` §7.2）：
1. `stat_type` 与 `initial_*` 列互相对位
2. `unit_original` 与 `sample_size` 100% 非空
3. `gm_summary` / `adjusted` / `unit_final` / `conversion_*` / `inferred_flags` **保持为空**
4. `notes` 记录所有不确定项

**违反任一承诺** → **不得自行修正**，回报 S3 走 GATE-3 重开流程（触发 stale 回滚）。
典型违约：`stat_type = GM_only` 但 `initial_gm` 为空 → 脚本返回 `FAILED` 并注明违约。

### 8.2 下游：S4 → S5 交接字段表

| # | 字段 | S5 用途 |
|---|---|---|
| 39 | `sample_type` | 分层维度 |
| **59** | **`sample_size`** | ★ 权重 |
| **62** | **`gm_summary`** | ★ 合并输入 |
| 63 | `gsd_summary` | 误差线 |
| 61 | `unit_final` | 结果表单位标注 |
| **60** | **`adjusted`** | ★ 敏感性分层（校正方式） |
| **64** | **`conversion_path`** | ★ 敏感性分层（GM 来源） |
| **66** | **`inferred_flags`** | ★ "仅直接报告 GM"子集筛选 |
| 10 / 14 / 17 | `time` / `province` / `region` | ★ 分期与地区分组 |
| **21** | **`population_group`** | ★ 人群分组 + EDI 取参 |
| 32 | `gender` | 分性别分析 |
| 40 / 41 | `blood_matrix` / `flag_blood_matrix` | 分层 |
| 43 | `analyte_species` | 分层与可比性声明 |
| 33–37 | 边界风险标记 | 去重与敏感性 |

**S4 的交付承诺（供 S5 依赖）**：
1. 所有记录 `gm_summary` 非空**或**明确 `FAILED`（无静默缺失）
2. `unit_final` 100% 等于 `unit_policy` 目标单位
3. `reported_GM` 记录 `flags` 为空 → "仅直接报告 GM"子集定义干净
4. `FAILED` 记录清单完整，供 S5 排除并计入局限

### 8.3 下游：S4 → S6

| 产出 | S6 用途 |
|---|---|
| `conversion_trace.csv` | 数值审计的证据链 |
| `flags_derivation.csv` | 推断标记可审计性复核 |
| 快照指纹 | 判定 `STALE_MANUSCRIPT` |
| `FAILED` / `WARN` 清单 | 局限说明与敏感性剔除候选 |

---

## 9. 判断规则（10 条）

| # | 规则 |
|---|---|
| R1 | **先单位后统计量，顺序不可颠倒** |
| R2 | **对 `GM_GSD`/`GM_only` 记录严禁套 Wan**（硬拒绝 → `SKIP`） |
| R3 | **`unit_original` 永不被覆盖**；规范化仅内部使用 |
| R4 | **成人性别不明用通用参数 `0.964`**，不得默认男性 |
| R5 | **血 / 脐血不走尿校正** |
| R6 | **`μg/g` ≠ `μg/g Cr`**（组织基准 vs 肌酐基准） |
| R7 | **`inferred_flags` 唯一来源规则**：权威来源是 `conversion_path → derive_flags()` 的反推结果。脚本写主表时**必须**走 `derive_flags()`；`convert_record()` 返回的中间 `res.flags` **仅用于自检断言，不得直接写入主表**。两者不一致时报 `FLAG_MISMATCH` 并使脚本返回非零退出码 |
| R8 | **`reported_GM` 记录 `flags` 必须为空** |
| R9 | **`FAILED` 记录不得进入合并**，必须计数并写入局限说明 |
| R10 | **换算不明即停**——不得猜测单位或统计量 |

---

## 10. GATE-4 出口条件

- [ ] `project.yaml` 存在且必需区块完整（**无兜底**）
- [ ] 所有记录 `conversion_path` 非空
- [ ] 所有记录 `unit_final` 等于 `unit_policy` 对应目标单位
- [ ] `flags_derivation.csv` 已产出，标记反推可追溯
- [ ] `reported_GM` 记录 `flags` 100% 为空
- [ ] `validate_table.py` 无 `ERROR`
- [ ] `FAILED` / `WARN` 清单已逐条处理并提交人工
- [ ] 换算后量级合理性检查通过（尿 1–100 ug/g Cr；血 0.1–10 ug/L）
- [ ] 快照指纹已生成
- [ ] **人工批准**（AI 不得自行开启 GATE-4）
- [ ] 批准后写入 `_state/snapshots.csv` 与 `_state/gates.json`

---

**维护者**：HBM-Meta-Agent　**上级**：`{skill_root}/SKILL.md`　**契约**：`shared/data-contract.md`
**上一阶段**：`skills/S3-extraction-standardization/SKILL.md`　**下一阶段**：`skills/S5-weighted-pooling-edi/SKILL.md`
