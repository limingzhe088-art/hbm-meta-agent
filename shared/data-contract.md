# Data Contract — 人体内暴露 Meta 分析主表契约

> **版本**：`1.0.0`　**状态**：冻结（frozen）　**适用**：HBM-Meta-Agent 全线 Skill
>
> **这份文件是整个工作包的通用性引擎。** 所有 Skill（S1–S6）、所有子 Agent、所有脚本只依赖本契约，
> **不依赖任何具体暴露物**。换 PFOA / 换全球人群 / 换 1950–2050，只要本契约不变，下游全部代码零修改。

---

## 0. 为什么需要数据契约

本项目（砷，1980–2024，中国）曾出现四类可复现的质量事故，全部源于"没有单一事实来源"：

| 事故 | 表现 | 根因 | 本契约的解法 |
|---|---|---|---|
| 快照漂移 | 手稿数值基于旧 `xlsx`，返修时才发现时段/地区数值系统性过时 | 主表有 4 个日期快照，正文没绑定到具体快照 | §4 快照指纹强制写入每份报告 |
| 分期不一致 | 描述性分析用 `1980-1999/2000-2010`，EDI 脚本用 `1980-2000/2001-2010`，手稿混用 | 分期方案硬编码在两份 R 脚本里 | §5.2 分期方案由 `project.yaml` 单点定义 |
| 单位口径混用 | 尿砷有 μg/L、μg/g Cr、比重校正三类记录被直接合并 | 单位语义只存在于研究者脑中 | §3.6–3.7 单位口径与校正状态显式建模 |
| 重复计数 | 武汉出生队列 35 篇被当作 35 个独立人群，参与者数被重复计数 | 没有队列/人群标识字段 | §3.2 `cohort_id` + `dedup_group` 字段 |

---

## 1. 契约的三层结构

```
第 1 层  RECORD TABLE（记录表）  main records table，1 行 = 1 条可合并的浓度统计
第 2 层  PROJECT CONFIG（配置）  project.yaml，分期/地区/基质/单位口径/EDI 参数
第 3 层  ARTIFACT MANIFEST（产物） 每份产出的快照指纹、生成脚本、依赖配置版本
```

**不可违反的三条铁律**

1. **单一事实来源**：任何正文数字必须能追溯到记录表某一行 + 某一条配置。追溯不到 = 数字不合法。
2. **不推断未报告数据**：记录表中任何非原文直接报告的值，必须在 `inferred_flags` 中显式标记。
3. **口径变更即全局失效**：`project.yaml` 中分期/单位/基质口径的任何修改，必须使所有下游产物标记为 `stale` 并重算。

---

## 2. 记录表（Record Table）总体规定

| 规定项 | 要求 |
|---|---|
| 载体 | Excel `.xlsx`（单 sheet）或 CSV（UTF-8 BOM）。列名一律使用本契约定义的英文 canonical name |
| 粒度 | **1 行 = 1 条浓度统计记录**。同一研究报告多个基质/人群/时段/统计量 → 拆成多行 |
| 行标识 | `record_id` 全局唯一，格式 `R{seq:05d}`；`study_no` 标识研究，格式 `S{seq:03d}` |
| 快照命名 | `{analyte}_{YYYY-MM-DD}.xlsx`，例：`As_2026-06-05.xlsx`、`PFOA_2027-01-20.xlsx` |
| 列顺序 | 按 §3 的 `#` 序号排列（便于人工比对与 diff） |
| 空值 | 一律留空（不得填 `NA`/`N/A`/`-`/`0`）；`0` 只在数值真为 0 时使用 |
| 语言 | 列名英文；文本字段（标题、人群描述）保持原文语言，不做翻译 |
| 冻结 | 一旦进入 S4 标准化，记录表即冻结；后续修改必须新建日期快照并触发重算 |

---

## 3. 字段定义

图例：**必填** = `Y`（缺失则该记录不可合并）/ `Y*`（条件必填）/ `N`。类型：`str` / `num` / `int` / `date` / `enum` / `bool`。

### 3.1 来源标识（Source identity）

| # | 列名 | 类型 | 必填 | 说明与约束 |
|---|---|---|---|---|
| 1 | `record_id` | str | Y | `R00001` 起，全局唯一，**一经分配永不变更**（重编号会切断审计链） |
| 2 | `study_no` | str | Y | 研究编号 `S001`。同一论文只对应一个 `study_no` |
| 3 | `cohort_id` | str | N | 队列/人群标识。**同一批受试者被多篇论文报告时必须填同一值**（如 `WH-BIRTH-01`）。空 = 未识别队列 |
| 4 | `dedup_group` | str | N | 去重分组键，用于"同队列同分期只保留样本量最大者"策略。建议 `{cohort_id}_{matrix}_{period}` |
| 5 | `title` | str | Y | 论文标题原文 |
| 6 | `author` | str | Y | 第一作者（中文姓名或英文姓氏），用于 EndNote 定位与同作者聚类 |
| 7 | `doi` | str | Y* | 有 DOI 必填；无 DOI 填 `NO_DOI` 并在 `notes` 说明 |
| 8 | `t_publication` | date | Y | 发表年份，`YYYY`。**绝不可用发表年份替代采样年份**（见 3.2） |
| 9 | `source_database` | enum | N | `PubMed` / `WebOfScience` / `CNKI` / `Wanfang` / `Scopus` / `Other` |

### 3.2 时空标识（Spatiotemporal identity）

| # | 列名 | 类型 | 必填 | 说明与约束 |
|---|---|---|---|---|
| 10 | `time` | num | Y | **采样/招募年份**（不是发表年份）。多年采样取代表年并在 `notes` 说明取法 |
| 11 | `time_start` | num | N | 采样起始年（有区间时填） |
| 12 | `time_end` | num | N | 采样结束年（有区间时填） |
| 13 | `country` | str | Y | 国家。默认 `China`；换全球人群时此处即切换点 |
| 14 | `province` | str | Y* | 省级行政区**规范全称**（`云南省` 或 `Yunnan`，全局统一一种写法）。无省级信息时填 `UNKNOWN` |
| 15 | `city` | str | N | 市级。**单值**；原文多个市用 `\|` 分隔且不得出现全角逗号（否则聚类失效） |
| 16 | `county` | str | N | 区县级 |
| 17 | `region` | enum | Y* | 大区，取值由 `project.yaml → region_map` 决定。默认中国七分：`North`/`Northeast`/`East`/`Central`/`South`/`Southwest`/`Northwest`/`UNKNOWN` |
| 18 | `latitude` | num | N | 纬度（用于空间制图与相关性）；无实测时标记为推断 |
| 19 | `longitude` | num | N | 经度。**随机生成的坐标必须在 `inferred_flags` 标记 `coord_random`**，不得用于正式结论 |

### 3.3 人群标识（Population identity）

| # | 列名 | 类型 | 必填 | 说明与约束 |
|---|---|---|---|---|
| 20 | `population` | str | Y | 人群原始描述（如 `Pregnant women`、`成人`、`Children`），保留原文 |
| 21 | `population_group` | enum | Y | 归一类目：`Adults`/`Minors`/`Pregnant`/`Elderly`/`Mixed`/`Unknown`。**人口学参数与 EDI 参数按此列取用** |
| 22 | `recruit_crowd` | str | N | 招募来源（社区/医院体检/学校/出生队列/疾控监测） |
| 23 | `population_desc` | str | N | 人群描述与筛选说明原文摘要 |
| 24 | `classification_rule` | str | N | 研究自身的人群分类标准 |
| 25 | `age` | str | N | 年龄（区间或均值+SD 原文），**不要折算成单一数字** |
| 26 | `age_mean` | num | N | 年龄均值（仅当原文报告均值时填） |
| 27 | `height` | num | N | 身高 |
| 28 | `weight` | num | N | 体重 |
| 29 | `bmi` | num | N | BMI |
| 30 | `male_n` | int | N | 男性子样本量 |
| 31 | `female_n` | int | N | 女性子样本量 |
| 32 | `gender` | enum | N | 该行浓度所属性别：`Male`/`Female`/`Both`/`Unknown`。**分性别报告必须拆行** |

### 3.4 人群边界风险标记（Eligibility risk flags）

> 这四个字段直接服务于 GATE-2 人工裁决门，AI 只做标记，**不得自行排除**。

| # | 列名 | 类型 | 必填 | 说明与约束 |
|---|---|---|---|---|
| 33 | `flag_occupational` | bool | Y | 标题/摘要提示职业暴露人群。默认 `FALSE`；疑填 `TRUE` 并进疑似清单 |
| 34 | `flag_disease` | bool | Y | 提示特定疾病/病例对照人群 |
| 35 | `flag_endemic_area` | bool | Y | 提示地方性砷中毒病区等特殊高暴露区 |
| 36 | `flag_mixed_occupational` | bool | Y | 职业与非职业混合调查（可能仅非职业亚组可用） |
| 37 | `inclusion_decision` | enum | Y* | GATE-2 人工裁决结果：`Include`/`Exclude`/`Pending`。**仅人工可改**，AI 初始只可写 `Pending` |
| 38 | `exclusion_reason` | str | Y* | 当 `inclusion_decision=Exclude` 时必填 |

### 3.5 基质与检测（Matrix & measurement）

| # | 列名 | 类型 | 必填 | 说明与约束 |
|---|---|---|---|---|
| 39 | `sample_type` | enum | Y | **基质枚举（本契约核心）**：`Urine`/`Blood`/`CordBlood`/`BreastMilk`/`Nail`/`Hair`/`Serum`/`Plasma`/`Other` |
| 40 | `blood_matrix` | enum | Y* | `sample_type=Blood` 时必填：`WholeBlood`/`Serum`/`Plasma`/`Unspecified`。**`Serum`/`Plasma` 必须同时标记 `flag_blood_matrix`** |
| 41 | `flag_blood_matrix` | bool | Y* | 血基质存疑（标题提示 serum/plasma 而正文未明确全血）→ `TRUE`，进 SI 警示表 |
| 42 | `analyte` | str | Y | 目标物质：`As`/`Cd`/`Pb`/`Hg`/`PFOA`/…（换暴露物只改此列取值域） |
| 43 | `analyte_species` | enum | N | 形态：`Total`/`iAs`/`iAs+MMA+DMA`/`MeHg`/`Unknown`。本项目 688 条中仅 27 条提示做过形态分析 |
| 44 | `detection_method` | str | N | 检测方法（`ICP-MS`/`HG-AAS`/`GF-AAS`/`AES`/`Unknown`） |
| 45 | `qc_reported` | bool | N | 是否报告质控（标准参考物质/回收率/精密度） |

### 3.6 统计量（Reported statistics）— 原文原样留痕

| # | 列名 | 类型 | 必填 | 说明与约束 |
|---|---|---|---|---|
| 46 | `stat_type` | enum | Y | **原文报告的统计量类型**：`GM_GSD`/`GM_only`/`AM_SD`/`Median_IQR`/`Median_Range`/`Median_only`/`Min_Max`/`P25_P75`/`Other`。决定 S4 的换算路径 |
| 47 | `initial_gm` | num | Y* | 原文直接报告的 GM（若报告）。用于"仅直接报告 GM 子集"敏感性分析 |
| 48 | `initial_mean` | num | N | 原文报告的算术均值 |
| 49 | `initial_sd` | num | N | 原文报告的 SD |
| 50 | `initial_median` | num | N | 原文报告的中位数 |
| 51 | `initial_p25` | num | N | 原文报告的 P25 |
| 52 | `initial_p75` | num | N | 原文报告的 P75 |
| 53 | `initial_p05` | num | N | 原文报告的 P5 |
| 54 | `initial_p95` | num | N | 原文报告的 P95 |
| 55 | `initial_min` | num | N | 原文报告的最小值 |
| 56 | `initial_max` | num | N | 原文报告的最大值 |
| 57 | `initial_gsd` | num | N | 原文报告的 GSD |
| 58 | `unit_original` | str | Y | **原文单位原样**（如 `μg/L`、`ug/g Cr`、`nmol/L`、`ng/mL`）。**绝不规范化**——留痕用于审计 |
| 59 | `sample_size` | int | Y | 该行对应的样本量。**必须 > 0**；缺失则该行不可参与加权合并 |

### 3.7 标准化输出（Standardized outputs）— 由 S4 填写

| # | 列名 | 类型 | 必填 | 说明与约束 |
|---|---|---|---|---|
| 60 | `adjusted` | enum | Y* | 尿样校正状态：`No`/`Creatinine`/`SpecificGravity`/`ReferenceConversion`/`NotApplicable`。**`sample_type=Urine` 时必填** |
| 61 | `unit_final` | enum | Y | 标准化后单位。**尿 → `ug/g Cr`；血/脐血 → `ug/L`**（由 `project.yaml → unit_policy` 决定） |
| 62 | `gm_summary` | num | Y | **最终用于合并的几何均值**。由 S4 按 §5.4 路径产出 |
| 63 | `gsd_summary` | num | N | 几何标准差。用于误差线（GM/GSD ~ GM×GSD），不作为权重 |
| 64 | `conversion_path` | str | Y | 换算路径编码，如 `AM_SD→Wan_S1→GM`、`ug/L→ICRP89_Adult→ug/g Cr`。可读可审计 |
| 65 | `conversion_params` | str | N | 用到的参数留痕，如 `CE=1.35,V=1.4,CC=0.964,BW=60` |
| 66 | `inferred_flags` | str | Y* | **所有非原文直接值的标记**，`;` 分隔。允许值见 §6。**有推断而无标记 = 严重违规** |
| 67 | `notes` | str | N | 任何不确定项、原文矛盾、需人工复核之处 |

### 3.8 审计元数据（Audit metadata）

| # | 列名 | 类型 | 必填 | 说明与约束 |
|---|---|---|---|---|
| 68 | `extracted_by` | enum | Y | `AI`/`Human`/`AI+Human`。**逐条校对完成的记录必须是 `AI+Human`** |
| 69 | `verified` | bool | Y | 是否已完成逐条人工校对（GATE-3） |
| 70 | `verified_by` | str | N | 校对者标识 |
| 71 | `snapshot_tag` | str | Y | 本行所属快照标签，如 `As_2026-06-05`。由脚本自动写入 |
| 72 | `row_hash` | str | Y | 关键字段拼接的短哈希（`record_id|gm_summary|sample_size|time|sample_type`），用于 diff 漂移检测 |

---

## 4. 快照指纹（Snapshot fingerprint）

**每一个由本工作包生成的报告/图表/表格，必须携带以下指纹头：**

```yaml
snapshot:
  source_table: As_2026-06-05.xlsx          # 主表文件名
  sha256: 3f1a9c...                          # 主表文件哈希（前 12 位可读显示）
  generated_at: 2026-07-19T14:03:00+08:00    # 生成时间（含时区）
  config_version: project.yaml@1.0.0         # 配置版本
  contract_version: data-contract@1.0.0      # 本契约版本
  record_count: 688                          # 参与计算的记录数
  study_count: 384                           # 唯一研究数
  generator: scripts/audit_numbers.py        # 生成脚本
  git_commit: a1b2c3d                        # 若在 git 仓库内
```

**用途**：`S6/audit_numbers.py` 比对"手稿数字 vs 快照重算"时，若手稿引用的快照哈希与当前快照不一致，直接判定为 `STALE_MANUSCRIPT`，无需逐个人工核对。这从机制上根除"稿子数值与数据快照不同步"。

**快照规则**
- 主表每次修改 → 复制为新日期快照，**旧快照永不删除**（审计链）。
- 已投稿/已送审的稿件，其数字必须绑定到某个已冻结快照；换快照 = 必须重跑 §5.5 审计并更新正文。
- 快照登记表：`{project}/_state/snapshots.csv`（列：`snapshot_tag,sha256,created_at,record_count,note`）。

---

## 5. 项目配置（`project.yaml`）— 第二层契约

分期、地区、基质、单位口径、EDI 参数**一律在此定义，代码里不得出现字面量**。

```yaml
# project.yaml
project:
  name: "Spatiotemporal variation of human internal exposure"
  analyte: "As"                     # ← 换暴露物改这里
  country: "China"                  # ← 换全球人群改这里
  matrix_scope: ["Urine", "Blood", "CordBlood"]
  contract_version: "1.0.0"
  config_version: "1.0.0"

# 5.1 输入
input:
  master_table: "01_数据与检索/As_2026-06-05.xlsx"
  sheet: 0
  province_column: "province"       # 兼容旧表：可写 "省"
  region_column: "region"

# 5.2 分期方案 —— 全流程唯一来源（GATE-0 冻结）
period_scheme:
  name: "four_era"
  boundaries:
    - {label: "1980-2000", start: 1980, end: 2000}
    - {label: "2001-2010", start: 2001, end: 2010}
    - {label: "2011-2020", start: 2011, end: 2020}
    - {label: "2021-2024", start: 2021, end: 2024}
  freeze_date: "2026-07-19"         # 冻结日；此后变更即触发全量重算
  # 变更示例：边界改 1980-1999/2000-2010 时，必须 bump config_version 并重跑 S4/S5/S6

# 5.3 地区字典
region_map:
  Southwest: ["四川省","云南省","贵州省","重庆市","西藏自治区"]
  Northwest: ["陕西省","甘肃省","青海省","宁夏回族自治区","新疆维吾尔自治区"]
  North:     ["北京市","天津市","河北省","山西省","内蒙古自治区"]
  Central:   ["河南省","湖北省","湖南省"]
  East:      ["上海市","江苏省","浙江省","安徽省","福建省","江西省","山东省","台湾省"]
  South:     ["广东省","广西壮族自治区","海南省","香港特别行政区","澳门特别行政区"]
  Northeast: ["辽宁省","吉林省","黑龙江省"]

# 5.4 单位口径
unit_policy:
  urine:     target: "ug/g Cr"
  blood:     target: "ug/L"
  cordblood: target: "ug/L"
  default_conflict_resolution: "prefer_creatinine_corrected"   # 同一研究多口径时的优先规则

# 5.5 尿校正与人群生理参数（ICRP 89 派生）
urine_reference:
  source: "ICRP Publication 89"
  table:
    - {population_group: "Infant",   age: 1,       ce_g_day: 0.11,  volume_l_day: 0.4,  creat_g_l: 0.275}
    - {population_group: "Adults",   age: 18,      ce_g_day: 1.35,  volume_l_day: 1.4,  creat_g_l: 0.964}
    - {population_group: "Adults",   age: 18, sex: "Male",   ce_g_day: 1.7,  volume_l_day: 1.6,  creat_g_l: 1.063}
    - {population_group: "Adults",   age: 18, sex: "Female", ce_g_day: 1.0,  volume_l_day: 1.2,  creat_g_l: 0.833}
    - {population_group: "Pregnant", age: null, ce_g_day: 1.275, volume_l_day: 2.0, creat_g_l: 0.638}

# 5.6 EDI 参数（暴露途径相关，可插拔）
edi:
  absorption_fraction_ABS: 0.7        # 假设的可溶性无机砷经口吸收比例，必须附敏感性说明
  body_weight:
    Adults: 60.0
    Pregnant: 60.0
    Minors: 20.0
  urine_route:
    method_primary: "creatinine_excretion"     # EDI = GM × CE / (BW × ABS)
    method_secondary: "urine_volume"           # EDI = GM × CC_creat × V / (BW × ABS)
    note: "两条路径同时输出以供交叉验证"
  blood_route:
    vd_l_per_kg: 2.0
    tau_day: 1.0
    formula: "(BAs × Vd) / (ABS × tau)"        # 修正后公式，Vd 已按体重标准化
    interpretation: "exploratory"               # 血砷反映短期暴露，仅作探索性重建

# 5.7 加权与统计
weighting:
  method: "sample_size"               # 权重 = 样本量；替代方案 inverse_variance
  log_base: "natural"                 # ln 与 log10 在加权 GM 中等价，统一用自然对数
  percentile_method: "weighted"       # wtd.quantile 等价实现

# 5.8 质量门与产物路径
gates:
  required: ["GATE-0","GATE-1","GATE-2","GATE-3","GATE-4","GATE-5"]
paths:
  scaffold: "01-search,02-screening,03-extraction,04-standardization,05-pooling-edi,06-qc-audit,07-references,08-manuscript,09-presentation,10-reference-tools,11-revision"
```

---

## 6. `inferred_flags` 允许值（推断标记字典，20 项）

**任何非原文直接报告的值，必须在此登记。** 缺少标记 = 数据不可信。

| 标记 | 含义 | 典型场景 | 对分析的影响 |
|---|---|---|---|
| `gm_from_mean_sd` | GM 由 AM+SD 经 Wan 公式估算 | `stat_type=AM_SD` | 进敏感性子集"仅直接报告 GM" |
| `gm_from_median` | GM 由中位数估算 | `stat_type=Median_*` | 同上 |
| `gm_from_iqr` | GM 由 IQR/P25–P75 估算 | `stat_type=Median_IQR` | 同上 |
| `gm_from_range` | GM 由 Min–Max 估算 | `stat_type=Min_Max` | 同上，估算误差最大 |
| `sd_from_iqr` | SD 由 IQR 反算 | 原文只给 IQR | — |
| `unit_converted_creatinine` | 用 ICRP 89 参考值把 μg/L 换成 μg/g Cr | `adjusted=ReferenceConversion` | 进"校正方式"分层敏感性 |
| `unit_converted_volume` | μg/g Cr 反算为 μg/L | 反向换算 | — |
| `time_represented` | 多年采样区间取了代表年 | `time_start≠time_end` | 记录取法于 `notes` |
| `blood_matrix_assumed` | 假定为全血 | 原文未说明血基质 | 必须同时 `flag_blood_matrix=TRUE` |
| `species_total_assumed` | 假定为总砷 | 原文未做形态分析 | SI 表登记 |
| `coord_random` | 经纬度为随机/推算生成 | 无实测坐标 | **不得用于正式空间结论** |
| `value_interpolated` | 缺失值线性插补 | 宏观解释变量 | **禁止用于主表浓度字段** |
| `value_estimated_from_figure` | 从图中读数估算 | 仅图无表 | 必须人工复核 |
| `sample_size_estimated` | 样本量由文中推算 | 分段给出 | 进人工复核清单 |
| `record_split_from_study` | 同一研究按性别/年龄/地区/时段拆成多行记录 | 一篇论文报告 3 个年龄组 | 权重按各拆分行样本量分配；同一 `study_no` 多行时须确认样本量未重复计入 |
| `subgroup_selected` | 混合人群中仅取了符合纳入标准的亚组 | 职业与非职业混合调查仅取非职业亚组 | 必须在 `notes` 写明原文是否有该亚组的独立统计量；须与 GATE-2 裁决一致 |
| `no_n_fallback` | 因缺少样本量而回退到不需 n 的公式 | 只有 IQR 无 n → 走 Wan S6 | 精度下降；进敏感性分析分层 |
| `molar_conversion` | 摩尔单位换算，引入形态/摩尔质量假设 | `nmol/L → μg/L` | 必须在 `conversion_params` 记录 `MW` 与形态 |
| `coarse_age` | 使用粗年龄段参数 | 原文只写"儿童"→ 用 `Minors` 统一值 | 参数差异大（CC 0.275–0.929）；进敏感性分析分层 |
| `cordblood_edi_skipped` | 脐带血未计算 EDI | 缺乏胎儿/新生儿药代动力学参数 | 必须在结果表计数并写入局限说明 |

> **硬约束**：`gm_summary`、`sample_size`、`time`、`sample_type` 四个字段**禁止**来自插补（`value_interpolated`）。
> 缺失即该记录不可合并，不得"补一个看起来合理的值"。这是本项目"AI 容易漏提/错提、不允许推测数据"教训的编码化。

---

## 7. 契约校验（S3/S4 门禁）

`scripts/validate_table.py` 按以下顺序校验，任一 `ERROR` 阻断流程：

1. **列存在性**：§3 中所有 `必填=Y` 的列必须存在
2. **类型与枚举**：`sample_type`/`population_group`/`stat_type`/`adjusted`/`inclusion_decision` 取值必须在枚举内
3. **条件必填**：`sample_type=Blood` → `blood_matrix` 必填；`sample_type=Urine` → `adjusted` 必填；`inclusion_decision=Exclude` → `exclusion_reason` 必填
4. **数值合法性**：`sample_size>0`、`gm_summary>0`、`gsd_summary>0`、`1980≤time≤当前年+1`
5. **单位口径一致性**：`unit_final` 必须等于 `project.yaml → unit_policy` 对应该基质的目标单位
6. **推断标记完备性**：凡 `conversion_path` 含 `Wan_*` 或 `unit_converted_*`，`inferred_flags` 不得为空
7. **重复检测**：`record_id` 唯一；`(study_no, sample_type, population, time)` 组合重复 → `WARN`
8. **快照冻结校验**：若 `verified=TRUE` 比例 < 阈值（默认 100% 进入 S5 前）→ `ERROR`

输出：`validate_report.md` + 控制台 `ERROR/WARN` 计数，非零 `ERROR` 返回退出码 1。

---

## 8. 版本与变更控制

| 变更类型 | 允许？ | 要求 |
|---|---|---|
| 新增可选列 | ✅ | 契约 minor 版本 +1，向后兼容 |
| 修改枚举值 | ⚠️ | 契约 major 版本 +1，必须走迁移脚本重映射旧值 |
| 修改必填性 | ⚠️ | 同上，且必须重跑 `validate_table.py` 全量历史表 |
| 修改 `period_scheme` | ⚠️ | `config_version` +1，**所有下游产物标记 stale 并重算**（本项目 R3-1 审稿意见的根源） |
| 删除列 | ❌ | 只能标记 `deprecated`，至少保留一个大版本 |
| 修改 `gm_summary` 计算路径 | ⚠️ | 必须留存新旧对照表（本项目 R1-1 血 EDI 公式纠错的做法） |

---

## 9. 与其他资产的关系

| 资产 | 依赖本契约的哪部分 |
|---|---|
| `shared/quality-gates.md` | §3.4 边界风险标记、§4 快照指纹、§7 校验门禁 |
| `shared/human-verification-protocol.md` | §3.6/3.7 留痕字段、§6 推断标记字典 |
| `skills/S1` | §3.1–3.2 时空标识 |
| `skills/S2` | §3.4 边界风险标记 |
| `skills/S3` | §3 全表 + §6 + §7 |
| `skills/S4` | §3.6/3.7 + §5.2–5.5 |
| `skills/S5` | §3.7 + §5.2/5.3/5.6/5.7 |
| `skills/S6` | §4 快照指纹 + §5 全部配置 |
| 全部脚本 | `--config project.yaml`，代码内零字面量 |

---

**契约维护者**：HBM-Meta-Agent　**变更提案**：GitHub Issue（模板 `new-exposure-agent.md`）
