---
name: hbm-weighted-pooling-edi
description: "Pools standardized internal-exposure biomarker records into sample-size-weighted geometric means and geometric standard deviations stratified by matrix, period, region, province, population group, sex, or year, then derives estimated daily intake (EDI) from biomarker concentrations. Covers log-scale weighted accumulation, weight-scheme selection (sample size vs inverse variance), single-record weight-dominance diagnostics, minimum-records-per-stratum rules, weighted quantiles, period-scheme enforcement from configuration with zero hard-coded boundaries, region dictionaries, record-count versus unique-participant wording rules, and EDI via two cross-validating urine routes (creatinine excretion and urine volume) plus the corrected blood route marked as an exploratory reconstruction. Analyte-, matrix-, population- and region-agnostic: works for any exposure biomarker (e.g. arsenic, cadmium, lead, mercury, PFOA/PFAS, antibiotics, drug concentrations). Triggers on: weighted pooling, weighted geometric mean, pooled GM, sample-size weighted, meta-analysis pooling, stratified pooling, temporal trend, spatial distribution, by period, by region, by province, by population group, descriptive analysis, estimated daily intake, EDI, exposure reconstruction, exposure assessment, biomarker back-calculation, 加权合并, 合并分析, 加权几何均值, 加权 GM, 分层合并, 分时段, 分期, 分省份, 分地区, 分人群, 逐年趋势, 时间趋势, 空间分布, 描述性分析, 趋势分析, EDI, 估计每日摄入量, 每日摄入量, 暴露评估, 暴露重建, 参与者计数, 记录数, 权重集中度."
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
  stage: 5
  gate: null                        # S5 无独立闸门；GATE-5（去重与敏感性裁决）由 S6 承接
  output_gate: "none (GATE-5 handled by S6)"
  internal_gate: "reporting_checklist.md §1–§9（脚本级硬门禁，非人工裁决门）"
  contract_version: "1.0.0"
  task_type: open-ended
---

# S5 · Weighted Pooling & EDI — 加权合并与暴露评估

**一句话**：把"每条都能追溯到原文"的标准化记录，合并成**总体水平**与**时间/空间趋势**，并反推出可跨基质比较的每日摄入量。

**核心立场**：**口径单点定义，权重必须诊断，记录数不等于人数。**

---

## 1. 加载条件与前置依赖

| 项 | 要求 |
|---|---|
| **前置闸门** | **GATE-4 必须为 `passed`**（标准化口径已确认，主表已冻结） |
| 前置产物 | `04-standardization/{analyte}_{date}_standardized.xlsx` + 快照指纹 + `conversion_trace.csv` |
| 前置校验 | `validate_table.py` 无 `ERROR`；`reported_GM` 记录 `flags` 为空 |
| 配置前置 | `project.yaml` 含 `period_scheme`（有 `freeze_date`）、`region_map`、`unit_policy`、`weighting`、`edi` |
| 负责人 | **A4 `statistics-agent`**（权限 L2） |
| 产出闸门 | 无独立闸门；但**报告检查清单（§8）为硬门禁** |
| 交付下游 | S6（数值审计、敏感性分析、制图、稿件） |

**拒绝启动的信号**：
- GATE-4 ≠ `passed`
- **`project.yaml` 缺失 `period_scheme` 且未冻结** → 硬错误，退出码 2（**禁止兜底**）
- 主表含 `FAILED` 记录（S4 未处理完）
- `unit_final` 未全部统一

---

## 2. 输入与输出契约

### 2.1 输入字段（S4 交接，`data-contract.md` §3.7）

| # | 字段 | S5 用途 |
|---|---|---|
| 39 | `sample_type` | ★ 首要分层维度（不同基质**绝不合并**） |
| **59** | **`sample_size`** | ★ 权重来源 |
| **62** | **`gm_summary`** | ★ 合并输入 |
| 63 | `gsd_summary` | 误差线 |
| 61 | `unit_final` | 结果单位标注 |
| **60** | **`adjusted`** | ★ 敏感性分层（校正方式） |
| **64** | **`conversion_path`** | ★ 敏感性分层（GM 来源） |
| **66** | **`inferred_flags`** | ★ "仅直接报告 GM"子集筛选 |
| 10 | `time` | ★ 分期/逐年 |
| 14 | `province` / 17 `region` | ★ 空间分层 |
| **21** | **`population_group`** | ★ 人群分层 + EDI 取参 |
| 32 | `gender` | 分性别 |
| 40 | `blood_matrix` / 41 `flag_blood_matrix` | 分层 |
| 43 | `analyte_species` | 分层 |
| 33–37 | 边界风险标记 | 去重与敏感性 |

### 2.2 输出产物

| 产物 | 路径 |
|---|---|
| 分层合并结果 | `05-pooling-edi/pooled_results.csv` |
| EDI 逐条结果 | `05-pooling-edi/edi_results.csv` |
| EDI 分层汇总 | `05-pooling-edi/edi_summary.csv` |
| 权重诊断报告 | `05-pooling-edi/weight_diagnostics.md` |
| 图数据 | `05-pooling-edi/figure_data/` |
| 报告检查表 | `05-pooling-edi/reporting_checklist_filled.md` |

**输出字段**（每个分层单元）：`GM_w`、`GSD_w`、`record_count`、`study_count`、`total_sample_size`、`max_weight_share`、`note`。

---

## 3. 加权 GM/GSD 的数学定义与权重口径

### 3.1 定义

```
ln GM_w = Σᵢ (wᵢ · ln GMᵢ) / Σᵢ wᵢ
GM_w    = exp(ln GM_w)

σ_w     = sqrt( Σᵢ wᵢ·(ln GMᵢ − ln GM_w)² / Σᵢ wᵢ )
GSD_w   = exp(σ_w)

误差线  = [GM_w / GSD_w , GM_w × GSD_w]      ← ±1 对数单位，非 95% CI
```

**`GSD_w` 的含义**：**记录间**离散度（研究间异质性），**不是**个体内变异。图注必须写明。

### 3.2 权重口径

| 口径 | 定义 | 何时用 |
|---|---|---|
| **样本量加权**（默认） | `wᵢ = sample_sizeᵢ` | 绝大多数情形；样本量是唯一在全部记录上可得的精度代理 |
| 逆方差加权 | `wᵢ = 1/SEᵢ²` | 仅当记录普遍报告 SE/SD。否则会只保留少数记录，引入选择偏倚 |

由 `project.yaml → weighting.method` 决定。方法学中必须写明"**sample-size-weighted**"，不能只写"weighted"。

**对数底数**：`ln` 与 `log10` 在加权 GM 中**结果等价**（浮点验证见 `--self-test`）。
本工作包统一用 `ln`。（原项目两个 R 脚本分别用 `log10` 与 `log`，结果一致。）

### 3.3 权重的边界处理

| 情形 | 处理 |
|---|---|
| `sample_size` 缺失或 ≤ 0 | 排除并**计数**（不得默认权重 1） |
| `gm_summary` 缺失或 ≤ 0 | 同上（对数无定义） |
| 同研究多行样本量相同 | 疑为总样本量被复制 → 回 GATE-3 |
| 亚组样本量之和 > 总样本量 | 交叉分组所致 → 记 `notes`，去重交 GATE-5 |

### 3.4 ★ 单记录权重占比诊断（必做）

```
max_weight_share = max(wᵢ) / Σw
```

| 占比 | 判读 | 动作 |
|---|---|---|
| < 10% | 健康 | 正常报告 |
| 10–30% | 需注意 | 结果表加注 |
| **> 30%** | **结果被单条记录主导** | **强制**做"剔除该记录"的敏感性分析 |

> 这是"36 万参与者"教训的延伸：若某记录的样本量远大于其他记录，合并结果实际上等于该研究的结果。

### 3.5 最小样本量规则

| 分层单元记录数 | 处理 |
|---|---|
| ≥ 10 | 正常报告 |
| 3–9 | 报告 + 标注"基于少量记录，解释谨慎" |
| 1–2 | **不报告为独立结论**，或显著标注 |
| 0 | 留白，**不得插值**；图注说明"该时段无符合纳入标准的研究" |

### 3.6 正确性护栏（脚本内置）

- `GM_w` **必须**落在参与记录的 `[min(GMᵢ), max(GMᵢ)]` 区间内（对数加权均值不可能超出输入范围）
- `GSD_w ≥ 1` 恒成立
- 权重之和 > 0

---

## 4. 分期方案：必须来自 `project.yaml`，代码内零字面量

### 4.1 铁律

```
project.yaml → period_scheme  ← 唯一来源

      ┌──────────┬──────────┬──────────┐
      ▼          ▼          ▼          ▼
 weighted_gm  edi_calc   制图脚本   数值审计
```

| # | 约束 |
|---|---|
| 1 | 分期边界**只能**在 `project.yaml` 定义；代码里出现 `1980`、`2000` 等字面量即违规 |
| 2 | 分期方案有 **`freeze_date`**；冻结后变更须 bump `config_version` |
| 3 | 分期变更 → **所有下游产物标记 `stale` 并重算** |

**自检证明零字面量**：`--self-test` 用一套自定义分期（`A: 2000-2009 / B: 2010-2019`）验证分配逻辑随之改变。

### 4.2 本项目实际发生的错误（留档为反例）

| 脚本 | 分期边界 |
|---|---|
| 描述性分析脚本 | `1980-1999 / 2000-2010 / 2011-2020 / 2021-2024` |
| EDI 脚本 | `1980-2000 / 2001-2010 / 2011-2020 / 2021-2024` |

差异仅涉及 2000 年的 15 条尿记录，但导致同一指标在两份产出中不同（`32.56` vs `32.58`），手稿混用后被审稿人指出（R3-1）。**单点定义是唯一解。**

### 4.3 `period_scheme` 配置规范

```yaml
period_scheme:
  name: "four_era"
  boundaries:
    - {label: "1980-2000", start: 1980, end: 2000}     # 闭区间 [start, end]
    - {label: "2001-2010", start: 2001, end: 2010}
    - {label: "2011-2020", start: 2011, end: 2020}
    - {label: "2021-2024", start: 2021, end: 2024}
  freeze_date: "2026-07-19"
  boundary_policy: "inclusive"
  out_of_range: "exclude"          # 区间外记录的处置：exclude / flag
  representative_year_rule: "start"
```

**`out_of_range` 的作用范围**：仅作用于 `period` 维度。若用 `time`（逐年分层），不过滤任何年份——逐年分析本就应显示全部有数据的年份。该差异属预期行为。

### 4.4 地区字典

`region_map` 同为单点定义。规则：省份用**规范全称**并全局统一；未归类 → `UNKNOWN`（**不得猜测**）；排序**按数值降序**且**每次重算后重新生成**（不手工维护）。

> 本项目实证：原手稿地区排序过时且漏一个地区（尿砷漏西北），审稿人 R2-3 指出后重算。

---

## 5. EDI 双路径

> 完整公式、参数与校验点见 `references/edi_parameters.md`。

### 5.1 尿路：双路径交叉验证

| 路径 | 公式 | 角色 |
|---|---|---|
| **A · 肌酐排泄法** `creatinine_excretion` | `EDI = GM × CE / (BW × ABS)` | **主口径** |
| **B · 尿量法** `urine_volume` | `EDI = GM × CC_creat × V / (BW × ABS)` | 交叉验证 |

因 `CC = CE/V`，两式在参数自洽时**数学等价**（`--self-test` 已验证完全相等）。
**并存目的**：检测参数表自洽性。

| 双路径相对差 | 处理 |
|---|---|
| < 0.5% | 正常（舍入） |
| 0.5%–3% | 报告 + 标注 |
| **> 3%** | ★ 报 `WARN inconsistent_param`，进敏感性分层，写入局限 |

**已知识别**：`Minors` 档 `CC=0.815` vs `CE/V=0.881`，相对差 **7.49%** → 必标注（原项目沿用值，保真不改）。

### 5.2 血路：修正后公式 + 探索性重建标注

```
EDI_blood = (BAs × Vd) / (ABS × τ)
```

| 参数 | 值 |
|---|---|
| `Vd` | **2.0 L/kg**（**已按体重标准化，不再除以 BW**） |
| `ABS` | 0.7 |
| `τ` | 1.0 day |

**❌ 严禁使用已证伪的旧公式** `(BAs × Vd) / (BW × ABS × τ)`：
- `Vd` 单位已是 `L/kg`，再除 `BW` 使量纲错为 `μg·L/(kg²·day)`
- 数值上把结果**系统性缩小约 60 倍**（BW=60 时）
- 本项目实证：审稿意见 R1-1 指出该错误；修正后血砷 EDI 中位数 `6.54 / 5.11 / 3.36`

**护栏已内置**：`edi_calculation.py` 提供 `edi_blood_legacy_wrong()`，一调用即抛 `AssertionError`；自检断言 `edi_blood()` 的签名**不含 `BW`**，且改变 `BW` 不改变结果。

**必须标注**：结果表与图注明 **"exploratory reconstruction"**；讨论说明血路 EDI **不应**与尿路直接比较量级。

### 5.2b ★ 脐带血：不计算 EDI（方案 A，保守）

**决策**：脐血记录**跳过 EDI 并计数**，不套用成人血路参数。

**理由**：脐血反映**胎儿**内暴露。成人的 `Vd`（表观分布容积）、`τ`（平均滞留时间）以及经口吸收路径 (`ABS`) 对胎儿**在模型层面不适用**——这不是"参数精度不够、用成人值近似即可"的问题，而是**: 模型对象错配**。用成人参数算出"胎儿 EDI"会产生看似合理但无依据的数字，属于最危险的一类错误。

**实现**（`edi_calculation.py::compute_blood_edi`，`sample_type="CordBlood"` 分支）：

| 项 | 行为 |
|---|---|
| 状态 | `SKIP` |
| `edi_primary` | 留空（**不产出任何数值**） |
| 标记 | `cordblood_edi_skipped` |
| 参数留痕 | `route=cordblood;edi_not_computed;reason=fetal_parameters_unavailable` |
| 计数 | 必须进入结果表计数与局限说明 |

**被否决的两个方案（留档）**：

| 方案 | 内容 | 为何不采用 |
|---|---|---|
| B · 独立胎儿参数表 | 补充胎儿/新生儿 `Vd`、`τ`、经胎盘转运模型 | 需要专门的药代动力学文献与专家审定，超出本工作包范围；若用户能提供参数来源，可作为**可选扩展**接入（配置化，不硬编码） |
| C · 成人参数 + approximation 标注 | 继续计算但显著标注近似 | 标注无法消除"模型对象错配"。审稿人若追问参数依据，无正当来源可答。**用错误模型的数字 + 免责声明，不如不报数字** |

**若未来启用方案 B**：需在 `project.yaml → edi.cordblood_route` 新增参数块，且必须附参数出处、适用胎龄范围与不确定性；在此之前默认走方案 A。

### 5.3 参数表

| `population_group` | `CE` (g/day) | `V` (L/day) | `CC_creat` (g/L) | `BW` (kg) | 来源 |
|---|---|---|---|---|---|
| `Adults` | 1.35 | 1.4 | 0.964 | 60 | ICRP 89 |
| `Pregnant` | 1.275 | 2.0 | 0.638 | 60 | NHANES |
| `Minors` | 0.925 | 1.05 | 0.815 | 20 | ICRP 89（⚠️ 不自洽） |
| `Elderly` | 1.35 | 1.4 | 0.964 | 60 | 同成人（打 `coarse_age`） |
| `Mixed` / `Unknown` / 空 | — | — | — | — | ★ **不计算 EDI，但必须计数** |

**`ABS=0.7` 必须是"假设值"**，不得写成"文献值"；必须附敏感性说明（如 `ABS` 取 0.5–1.0 时 EDI 的变化范围）。
（本项目实证：审稿人 R1-2 要求补此说明。）

### 5.4 汇总口径

| 统计量 | 计算 |
|---|---|
| `EDI_GM` / `EDI_GSD` | 加权（同 §3.1） |
| `EDI_median` | **未加权**中位数（保留原 R 行为） |
| `EDI_P05/P25/P50/P75/P95` | **加权**分位数（累积权重法，等价 `Hmisc::wtd.quantile`） |

---

## 6. ★ 记录数 vs 唯一参与者

### 6.1 三个必须区分的量

| 量 | 定义 | 字段 |
|---|---|---|
| **记录数** | 主表行数（1 行 = 1 条浓度统计） | `record_count` |
| **研究数** | 唯一 `study_no` 数 | `study_count` |
| **参与者数** | 受试者人数；**同一人可能贡献多条记录** | `total_sample_size`（**记录级求和，非唯一人数**） |

### 6.2 本项目实证

原手稿写"more than 360,000 participants"，审稿人指出：同一出生队列被 35 篇论文报告，同一批孕妇可能被重复计数；且同一人可能同时贡献尿样与血样记录。因此该数字是**记录级求和**。

**修正后措辞**（已采用）：
> "...more than 360,000 **participant records**... these totals represent **record-level sums rather than counts of unique individuals**."

### 6.3 措辞规则（必须写进图注/表注）

| 情形 | ✅ 允许 | ❌ 禁止 |
|---|---|---|
| 记录级求和 | `participant records`、`record-level sums` | `participants`、`individuals`、`subjects` |
| 已做队列去重 | `unique participants`（须说明方法与影响） | 只说 `participants` 而不说明是否去重 |
| 研究数 | `studies` | 与记录数混用同一词 |

### 6.4 强制输出与检查

每个分层单元与总表必须同时输出 `record_count | study_count | total_sample_size | note`。
**任何结果表/图若只有 `n` 而无类型说明 → 校验失败，不得出图。**

---

## 7. 引用的脚本与模板

### 脚本

| 脚本 | 用途 | 自检 | 状态 |
|---|---|---|---|
| `scripts/weighted_gm.py` | 加权 GM/GSD、分层、加权分位数、权重诊断、分期归类 | `--self-test`（**35/35 通过**） | 骨架版 |
| `scripts/edi_calculation.py` | 尿路双路径、血路修正公式、旧公式护栏、EDI 汇总 | `--self-test`（**48/48 通过**） | 骨架版 |

**第四步待办**（详见 `{skill_root}/STEP4-TODO.md`）：
- **A1** `project.yaml` 缺失 → 硬错误退出码 2；移除 `DEFAULT_PERIOD_SCHEME` / `DEFAULT_EDI_PARAMS` 兜底
- **A3** 分期、地区、权重口径、EDI 参数全部从配置读取
- **A4** 输出带快照指纹
- **A6** CI 集成 `--self-test`

### 模板

| 模板 | 用途 |
|---|---|
| `templates/pooled_results_OUTPUT_example.csv` | 分层合并结果**示例**（14 行，含权重占比告警与记录数不足示例）。**命名带 `_OUTPUT_example` 以明确"这是输出示例、不是任何 Skill 的输入"** |
| `templates/edi_results_OUTPUT_example.csv` | EDI 逐条结果**示例**（10 行，覆盖 OK/WARN/SKIP 与双路径交叉验证）。命名约定同上 |

> **模板命名约定**：`*_OUTPUT_example.csv` = 某 Skill 的**输出示例**；
> 不带该后缀的（如 S3 的 `extraction_template.csv`）= 某 Skill 的**输入模板**。
> 目的是避免把输出误当作输入（例如把 S5 合并结果当 S4 输入 → 见 S4/SKILL.md §2.1）。

### 参考文档

| 文件 | 内容 |
|---|---|
| `references/weighting_method.md` | 数学定义、权重口径、占比诊断、正确性检查、与原 R 的逐项对应 |
| `references/period_and_region_scheme.md` | 单点定义铁律、配置规范、记录数 vs 参与者、地区字典、变更控制 |
| `references/edi_parameters.md` | 尿/血双路径公式、参数表、修正 vs 旧公式、汇总口径、校验点 |
| `references/reporting_checklist.md` | 9 节强制检查清单（出图前门禁） |

---

## 8. 与其他 Skill 的接口

### 8.1 上游：S4 → S5

依赖 S4 的四条交付承诺（见 `S4/SKILL.md` §8.2）：
1. 所有记录 `gm_summary` 非空**或**明确 `FAILED`
2. `unit_final` 100% 等于 `unit_policy` 目标单位
3. `reported_GM` 记录 `flags` 为空 → 零推断子集定义干净
4. `FAILED` 清单完整

**违反任一** → 不得自行修正，回报 S4 走 GATE-4 重开流程，触发 stale 回滚。

### 8.2 下游：S5 → S6 交接字段表

| S5 产出 | S6 用途 |
|---|---|
| `pooled_results.csv` | 图注/表注数值来源 |
| `edi_results.csv` | EDI 数值审计 |
| `weight_diagnostics.md` | 权重集中度写入局限 |
| **`max_weight_share`** | 敏感性分析（剔除主导记录） |
| **`note` 列**（记录数不足/权重集中） | 局限说明与敏感性剔除候选 |
| `record_count` / `study_count` / `total_sample_size` | ★ 措辞规则与防重复计数审计 |
| 快照指纹 | `STALE_MANUSCRIPT` 判定 |
| `FAILED` / `SKIP` 计数 | 局限说明 |

**S5 的交付承诺（供 S6 依赖）**：
1. 每个数字都带 `record_count` 与 `study_count`
2. `max_weight_share` 已计算；> 30% 的层已触发敏感性提示
3. 无数据时段已在 `note` 中说明（未画成 0）
4. EDI 血路结果均已带 `exploratory_reconstruction` 标记
5. `Mixed`/`Unknown` 跳过 EDI 的条数已计数

### 8.3 反馈：S6 → S5

S6 的数值审计若发现：
- 手稿数字与 `pooled_results.csv` 不一致 → `MISMATCH`，**不得自行改数**，回报 S5 重算
- 快照哈希不匹配 → `STALE_MANUSCRIPT`，触发全流程重算
- 分期/口径变更 → bump `config_version`，S5 全部产物标记 `stale`

---

## 9. 判断规则（12 条）

| # | 规则 |
|---|---|
| R1 | **`GM_w` 必须落在输入记录区间内**，否则视为异常 |
| R2 | **`GSD_w ≥ 1`** 恒成立 |
| R3 | **权重必须来自 `sample_size`**（或配置指定的口径）；缺失即排除并计数，不得默认 1 |
| R4 | **单记录权重占比 > 30% → 强制敏感性分析** |
| R5 | **分层单元记录数 < 3 → 不得作为独立结论** |
| R6 | **无数据时期留白，不得插值**；图注必须说明 |
| R7 | **分期与地区口径只能来自 `project.yaml`**，代码内零字面量 |
| R8 | **不同基质绝不合并**；血基质、形态、校正方式均分层 |
| R9 | **血路 EDI 用修正后公式**；旧公式一调用即抛异常 |
| R10 | **血路 EDI 必须标注 `exploratory reconstruction`** |
| R11 | **`ABS=0.7` 定义为假设值** + 敏感性说明 |
| R12 | **记录级求和不得表述为"参与者/个体"**；图注表注必须说明 `n` 的类型 |

---

## 10. 出图/出表前门禁

必须逐项通过 `references/reporting_checklist.md` 的 9 节检查：

```
□ §1 记录数 vs 唯一参与者（含措辞）
□ §2 数值可追溯性（含快照指纹）
□ §3 加权合并正确性（含权重诊断）
□ §4 误差线定义
□ §5 分层维度完整性
□ §6 EDI 专项（修正公式 / 探索性标注 / 双路径验证）
□ §7 图注表注 8 项要素
□ §8 局限事项清单（交 S6）
```

**未全部通过 → 不得出图，不得进入 S6。**

---

**维护者**：HBM-Meta-Agent　**上级**：`{skill_root}/SKILL.md`　**契约**：`shared/data-contract.md`
**上一阶段**：`skills/S4-unit-statistic-conversion/SKILL.md`　**下一阶段**：`skills/S6-qc-audit-revision/SKILL.md`
