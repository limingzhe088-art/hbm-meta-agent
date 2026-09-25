# 字段字典（Field Dictionary）

> 用途：S3 提取与校对时的速查表。**权威定义以 `shared/data-contract.md` §3 为准**，本文件是操作视角的精简版 + 提取注意事项。
> 图例：必填 `Y`（缺失则该记录不可合并）/ `Y*`（条件必填）/ `N`。

---

## 1. 来源标识

| 列名 | 类型 | 必填 | 枚举/格式 | 提取注意事项 |
|---|---|---|---|---|
| `record_id` | str | Y | `R00001` | **不由 AI 填**。主 Agent 分配，一经指定永不更改 |
| `study_no` | str | Y | `S001` | **不由 AI 填**。一篇论文一个值 |
| `cohort_id` | str | N | 自由 | **不由 AI 填**。同一批受试者被多篇论文报告时填同一值；GATE-2 裁决 |
| `dedup_group` | str | N | `{cohort}_{matrix}_{period}` | **不由 AI 填** |
| `title` | str | Y | 原文 | 完整标题，不截断 |
| `author` | str | Y | 第一作者 | 中文姓名或英文姓氏；用于同作者聚类 |
| `doi` | str | Y* | 有则填，无则 `NO_DOI` | `NO_DOI` 时必须 `notes` 说明 |
| `t_publication` | date | Y | `YYYY` | 发表年。**注意不要与 `time` 混淆** |
| `source_database` | enum | N | `PubMed`/`WebOfScience`/`CNKI`/`Wanfang`/`Scopus`/`Other` | 从哪个库检出的 |

---

## 2. 时空标识

| 列名 | 类型 | 必填 | 枚举/格式 | 提取注意事项 |
|---|---|---|---|---|
| `time` | num | **Y** | 年份 | ★ **采样/招募年份，不是发表年份**。最高频错误点 |
| `time_start` | num | N | 年份 | 有采样区间时填 |
| `time_end` | num | N | 年份 | 同上 |
| `country` | str | Y | 默认 `China` | 换全球人群时的切换点 |
| `province` | str | Y* | 规范全称 | 全局统一一种写法；无省级信息填 `UNKNOWN` |
| `city` | str | N | **单值或 `\|` 分隔** | ★ 不得出现全角逗号，否则同城聚类失效 |
| `county` | str | N | — | 区县级 |
| `region` | enum | Y* | 由 `region_map` 决定 | 默认中国七分：`North`/`Northeast`/`East`/`Central`/`South`/`Southwest`/`Northwest`/`UNKNOWN` |
| `latitude` | num | N | 十进制度 | 无实测时标 `coord_random` |
| `longitude` | num | N | 十进制度 | 同上 |

---

## 3. 人群标识

| 列名 | 类型 | 必填 | 枚举/格式 | 提取注意事项 |
|---|---|---|---|---|
| `population` | str | Y | 原文描述 | 保留原文语言，不翻译、不归一 |
| `population_group` | enum | **Y** | `Adults`/`Minors`/`Pregnant`/`Elderly`/`Mixed`/`Unknown` | 决定生理参数与 EDI 取参。`Mixed` 会被 EDI 跳过 |
| `recruit_crowd` | str | N | — | 社区/医院体检/学校/出生队列/疾控监测 |
| `population_desc` | str | N | — | 纳入排除与筛选说明摘要 |
| `classification_rule` | str | N | — | 研究自身的分类标准 |
| `age` | str | N | 原文 | **不要折算成单一数字**（保留区间/均值±SD） |
| `age_mean` | num | N | — | 仅当原文报告均值时填 |
| `height` / `weight` / `bmi` | num | N | — | 原文报告时填 |
| `male_n` / `female_n` | int | N | — | 子样本量 |
| `gender` | enum | N | `Male`/`Female`/`Both`/`Unknown` | ★ **分性别报告必须拆行**；`Both` 表示未分性别的总体值 |

---

## 4. 人群边界风险标记

| 列名 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `flag_occupational` | bool | Y | 标题/摘要提示职业暴露人群 → `TRUE` 并进疑似清单 |
| `flag_disease` | bool | Y | 特定疾病/病例对照人群 |
| `flag_endemic_area` | bool | Y | 地方性中毒病区等特殊高暴露区 |
| `flag_mixed_occupational` | bool | Y | 职业与非职业混合调查 |
| `inclusion_decision` | enum | Y* | ★ **仅人工可写**：`Include`/`Exclude`/`Pending`。AI 只能写 `Pending` |
| `exclusion_reason` | str | Y* | `Exclude` 时必填 |

---

## 5. 基质与检测

| 列名 | 类型 | 必填 | 枚举/格式 | 提取注意事项 |
|---|---|---|---|---|
| `sample_type` | enum | **Y** | `Urine`/`Blood`/`CordBlood`/`BreastMilk`/`Nail`/`Hair`/`Serum`/`Plasma`/`Other` | ★ 决定单位口径 |
| `blood_matrix` | enum | Y* | `WholeBlood`/`Serum`/`Plasma`/`Unspecified` | `sample_type=Blood` 时必填 |
| `flag_blood_matrix` | bool | Y* | 血基质存疑 → `TRUE` | 标题提示 serum/plasma 而正文未明确全血时 |
| `analyte` | str | **Y** | `As`/`Cd`/`Pb`/`Hg`/`PFOA`/… | 换暴露物的切换点 |
| `analyte_species` | enum | N | `Total`/`iAs`/`iAs+MMA+DMA`/`MeHg`/`Unknown` | 实证：多数研究只报告总砷 |
| `detection_method` | str | N | `ICP-MS`/`HG-AAS`/`GF-AAS`/`AES`/`Unknown` | 有助质量评价 |
| `qc_reported` | bool | N | — | 是否报告质控（SRM/回收率/精密度） |

---

## 6. 原文统计量（留痕）

| 列名 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `stat_type` | enum | **Y** | `GM_GSD`/`GM_only`/`AM_SD`/`Median_IQR`/`Median_Range`/`Median_only`/`Min_Max`/`P25_P75`/`Other`。★ 决定 S4 换算路径 |
| `initial_gm` | num | Y* | 原文直接报告的 GM（敏感性分析用） |
| `initial_mean` | num | N | 算术均值 |
| `initial_sd` | num | N | **算术** SD |
| `initial_median` | num | N | 中位数 |
| `initial_p25` / `initial_p75` | num | N | 四分位数 |
| `initial_p05` / `initial_p95` | num | N | 尾部百分位 |
| `initial_min` / `initial_max` | num | N | 极值 |
| `initial_gsd` | num | N | **几何** SD |
| `unit_original` | str | **Y** | ★ **原样抄录，不许规范化**（`μg/L`、`ug/L`、`µg/L` 写法差异要保留） |
| `sample_size` | int | **Y** | ★ 该行对应样本量，必须 > 0 |

---

## 7. 标准化输出（S4 填写，S3 留空）

| 列名 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `adjusted` | enum | Y* | `No`/`Creatinine`/`SpecificGravity`/`ReferenceConversion`/`NotApplicable`。**尿样必填** |
| `unit_final` | enum | Y | 尿 → `ug/g Cr`；血/脐血 → `ug/L`（由 `unit_policy` 决定） |
| `gm_summary` | num | Y | 最终用于合并的 GM |
| `gsd_summary` | num | N | 几何标准差（用于误差线，不作权重） |
| `conversion_path` | str | Y | 如 `AM_SD→Wan_S1→GM`、`ug/L→ICRP89_Adult→ug/g Cr` |
| `conversion_params` | str | N | 如 `CE=1.35,V=1.4,CC=0.964,BW=60` |
| `inferred_flags` | str | Y* | ★ **20 项**文档化标记字典，`;` 分隔。完整表见本文件 **§8.5**；权威定义见 `shared/data-contract.md` §6；单一来源为 `shared/inferred_flags.py` |
| `notes` | str | N | 一切疑问与不确定项 |

---

## 8. 审计元数据

| 列名 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `extracted_by` | enum | Y | `AI`/`Human`/`AI+Human`。逐条校对完成后必须是 `AI+Human` |
| `verified` | bool | Y | 是否完成逐条人工校对 |
| `verified_by` | str | N | 校对者标识 |
| `snapshot_tag` | str | Y | 脚本自动写入，如 `As_2026-06-05` |
| `row_hash` | str | Y | 脚本自动写入，用于 diff 漂移检测 |

---

## 8.5 ★ `inferred_flags` 标记全表（20 项文档化标记）

> **单一来源**：`shared/inferred_flags.py`（脚本导入此模块，不再各自维护副本）。
> 权威定义见 `shared/data-contract.md` §6。**本节必须与之一一对应，无多无少。**

| 分组 | 标记 | 触发 | 影响 |
|---|---|---|---|
| 统计量 | `gm_from_mean_sd` | `stat_type=AM_SD` | 进"仅直接报告 GM"子集 |
| 统计量 | `gm_from_median` | `stat_type=Median_*` | 同上 |
| 统计量 | `gm_from_iqr` | `stat_type=Median_IQR` | 同上 |
| 统计量 | `gm_from_range` | `stat_type=Min_Max` | 估算误差最大（S3） |
| 统计量 | `sd_from_iqr` | 原文只给 IQR | — |
| 统计量 | `no_n_fallback` | 只有 IQR/Range 无 n → Wan S3/S6 | 精度下降；进敏感性分层 |
| 单位 | `unit_converted_creatinine` | `adjusted=ReferenceConversion` | 进"校正方式"分层 |
| 单位 | `unit_converted_volume` | `μg/g Cr` → `μg/L` 反向 | — |
| 单位 | `molar_conversion` | `nmol/L` → `μg/L` | 须记录 `MW` 与形态 |
| 时空 | `time_represented` | `time_start ≠ time_end` | 代表年取法入 `notes` |
| 基质 | `blood_matrix_assumed` | 原文未说明血基质 | 须同时 `flag_blood_matrix=TRUE` |
| 形态 | `species_total_assumed` | 原文未做形态分析 | SI 表登记 |
| 空间 | `coord_random` | 经纬度为推算生成 | **不得用于正式空间结论** |
| 质量 | `value_interpolated` | 缺失值线性插补 | ★ **禁止用于浓度字段**（V-04） |
| 质量 | `value_estimated_from_figure` | 仅图无表 | 必须人工复核 |
| 质量 | `sample_size_estimated` | 样本量由文中推算 | 进人工复核清单 |
| 拆分 | `record_split_from_study` | 一篇论文多亚组拆行 | 须确认样本量未重复计入 |
| 拆分 | `subgroup_selected` | 混合人群仅取合格亚组 | 须与 GATE-2 裁决一致 |
| 参数 | `coarse_age` | 原文只写"儿童"→ `Minors` 统一值 | 参数差异大；进敏感性分层 |
| EDI | `cordblood_edi_skipped` | 脐血缺乏胎儿药代参数（方案 A） | 须计数并写入局限 |

**脚本内部标记（3 项，非"推断"语义，但需在闭集内以便校验）**：
`exploratory_reconstruction`（血路 EDI 为探索性重建）、`inconsistent_param`（参数表不自洽）、`bw_unused`（血路不使用体重）。

> 新增标记的流程：改 `shared/inferred_flags.py` → 同步 `data-contract.md` §6 → 同步本节
> → 跑 `python shared/inferred_flags.py --self-test` 验证覆盖一致。

---

## 9. 枚举值速查（复制粘贴用）

```
sample_type            : Urine | Blood | CordBlood | BreastMilk | Nail | Hair | Serum | Plasma | Other
population_group       : Adults | Minors | Pregnant | Elderly | Mixed | Unknown
gender                 : Male | Female | Both | Unknown
stat_type              : GM_GSD | GM_only | AM_SD | Median_IQR | Median_Range | Median_only | Min_Max | P25_P75 | Other
adjusted               : No | Creatinine | SpecificGravity | ReferenceConversion | NotApplicable
blood_matrix           : WholeBlood | Serum | Plasma | Unspecified
analyte_species        : Total | iAs | iAs+MMA+DMA | MeHg | Unknown
inclusion_decision     : Include | Exclude | Pending
extracted_by           : AI | Human | AI+Human
region (default CN)    : North | Northeast | East | Central | South | Southwest | Northwest | UNKNOWN
```

---

## 10. AI 不填的列（一览）

`record_id` `study_no` `cohort_id` `dedup_group` `inclusion_decision` `exclusion_reason` `adjusted`
`unit_final` `gm_summary` `gsd_summary` `conversion_path` `conversion_params` `inferred_flags`
`extracted_by` `verified` `verified_by` `snapshot_tag` `row_hash`

> `inferred_flags` 特殊：**AI 不得自行填写**（它无法可靠判断自己哪些值是推的），改由 S4 换算脚本根据 `conversion_path` 自动登记，并由人工在 GATE-4 复核。

---

**维护者**：HBM-Meta-Agent　**关联**：`shared/data-contract.md` §3、`proofing_rules.md`
