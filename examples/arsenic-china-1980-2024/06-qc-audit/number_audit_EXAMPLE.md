# 数值审计对照表（脱敏样例）

> **教学案例**。演示 S6 数值审计的输出格式与四状态分类。
> 稿件数字为**合成值**；具体研究标题、作者、DOI、省份级真实数值已移除。

---

## 0. 快照指纹

```yaml
snapshot:
  source_table: 03-extraction/master_table_EXAMPLE.csv
  sha256: 4c7bb4ccf3eea60ff9dd496ed12c00ec316fb264c1a309385e0f0b52c65933c0
  generated_at: 2026-07-19T10:00:00+08:00
  config_version: 1.0.0
  contract_version: 1.0.0
  record_count: 12
  study_count: 12
  generator: convert_units.py
  git_commit: null
```

**稿件绑定快照**：`As_EXAMPLE@4c7bb4ccf3ee`

> ⚠️ 若两处 `sha256` 不一致 → **检测到快照漂移**，全部相关数字标
> `STALE_MANUSCRIPT`，应全量重算而非逐个人工核对。

---

## 1. 状态统计

| 状态 | 数量 | 处置 |
|---|---|---|
| `OK` | 5 | 无需处理 |
| `MISMATCH` | 2 | ★ 人工裁决 |
| `STALE_MANUSCRIPT` | 1 | ★ 全量重算 |
| `NO_SOURCE` | 2 | ★ 人工裁决（补来源或删除） |
| **合计** | **10** | |

---

## 2. 明细对照

| # | 类型 | 分层 | 稿件原值 | 重算值 | 相对差 | 状态 | 来源 | 连带修改 |
|---|---|---|---|---|---|---|---|---|
| 1 | `period_GM` | Urine \| 2011-2020 | 23.50 | 23.5021 | 0.01% | `OK` | snapshot@4c7bb4ccf3ee | — |
| 2 | `period_GM` | Urine \| 2001-2010 | 12.45 | 12.4489 | 0.01% | `OK` | 同上 | — |
| 3 | `region_GM` | Blood \| North | 2.72 | 2.7194 | 0.02% | `OK` | 同上 | — |
| 4 | `population_GM` | Urine \| Minors | 28.86 | 28.8600 | 0.00% | `OK` | 同上 | — |
| 5 | `count` | records | 688 | 688 | 0 | `OK` | 同上 | — |
| 6 | `period_GM` | Urine \| 1980-2000 | **38.10** | **32.5800** | 14.5% | **`MISMATCH`** | 同上 | ★ 见 §3-1 |
| 7 | `region_GM` | Urine \| Southwest | **41.33** | **39.0300** | 5.6% | **`MISMATCH`** | 同上 | ★ 见 §3-2 |
| 8 | `period_GM` | Urine \| 2021-2024 | 24.05 | — | — | **`STALE_MANUSCRIPT`** | 稿件哈希 ≠ 当前哈希 | ★ 全量重算 |
| 9 | `correlation` | Blood \| aquatic product | 0.069 | — | — | **`NO_SOURCE`** | 未提供 SPSS 输出 | ★ 见 §4-1 |
| 10 | `percentage` | 尿砷降幅 | 64% | — | — | **`NO_SOURCE`** | 降幅需由两个分层值算出 | ★ 见 §4-2 |

**容差**（见 `references/audit_protocol.md` §3）：
加权 GM 相对 0.5%｜百分比绝对 0.5 pt｜相关系数绝对 0.001｜计数精确｜P 值只比对显著性方向。

---

## 3. 连带修改清单（★ 审计的真正难点）

> 数值改了，**叙述往往也必须改**。含比较级/趋势/最高级表述的句子必须复审。

### 3-1（对应 #6 时段 GM 38.10 → 32.58）

| 项 | 内容 |
|---|---|
| 触发词 | `declined steadily`、`64%` |
| 原句 | "Urinary arsenic **declined steadily** from 38.10 μg/g Cr during 1980-2000 to 12.45 μg/g Cr, a **64%** reduction." |
| 建议改法 | "…decreased from **32.58** μg/g Cr during 1980-2000 to 12.45 μg/g Cr, a **62%** reduction, then rebounded…" |
| 连带位置 | 摘要 · §3.1 · §3.3 概述 · 讨论 · 结论 · Highlights |

### 3-2（对应 #7 地区 GM 41.33 → 39.03）

| 项 | 内容 |
|---|---|
| 触发词 | `highest`、`higher than` |
| 原句 | "Southwest China showed the **highest** urinary arsenic (41.33 μg/g Cr), **higher than** all other regions." |
| 建议改法 | "Southwest China showed the **highest** urinary arsenic (**39.03** μg/g Cr); the ranking of other regions differed between urinary and blood arsenic." |
| 连带位置 | 摘要 · §3.2 · Figure 3 图注 |

> **高风险区（必须逐个检查）**：**摘要 · 结论 · Highlights · 图注 · 表注**。
> 本项目实证：时段 GM 变化会连带影响趋势描述、降幅百分比、地区排序三处表述；
> 只改正文数字而不改摘要与结论，审稿人必然再次指出。

---

## 4. 待人工裁决

### 4-1（#9 相关系数无来源）

| 项 | 内容 |
|---|---|
| 稿件原值 | `r_s = 0.069`，`P = 0.039` |
| 问题 | **r 与 P 的显著性不匹配**（r=0.069 通常对应 P > 0.05）；且未提供 SPSS 输出 |
| 请裁决 | ☐ 采信重算值 ☐ 修正换算 ☐ 保留并注明 ☐ 补来源 |

> 本项目实证：该类不一致必须核实原始 SPSS 输出；核对后发现真实为 `rₛ=0.102, P=0.039`。

### 4-2（#10 降幅百分比）

| 项 | 内容 |
|---|---|
| 稿件原值 | `64%` |
| 系统判定 | **不自动重算** |
| 原因 | **降幅百分比需由两个分层值算出，须人工核对**（分母选择不唯一：相对基期、相对峰值或其他） |
| 请裁决 | ☐ 按基期算（(32.58−12.45)/32.58 = **62%**）☐ 按峰值算 ☐ 其他 |

> **设计说明**：自动重算会引入"假确定感"。人工核对两个分层值的降幅成本极低。
> 纯百分比不会被数字提取器捕获，不存在"被误当作浓度值"的风险；
> 风险仅在"同句既有浓度又有百分比"时——该句整体标 `unresolved`。

---

## 5. 残留问题

| # | 项 | 原因 | 影响 | 是否写入局限 |
|---|---|---|---|---|
| 1 | 成人尿 EDI 1980-2000 中位数 | 大样本主导，数值不稳定 | 该时段 EDI 谨慎解释 | ☐ 待定 |
| 2 | 血基质存疑（标题提示 serum） | 需查原文确认 | 进 SI 警示表；与全血分层 | ☑ 已写入 |

---

## 6. 定稿门禁

- [ ] 全部数字状态为 `OK`，或差异已逐条裁决
- [ ] 无 `NO_SOURCE`（孤儿数字）残留
- [ ] **摘要 / 结论 / Highlights / 图注 / 表注 已同步**
- [ ] 连带修改清单已全部执行
- [ ] 本报告与稿件版本号一致
- [ ] 快照指纹已记录（见 §0）

| 签署 | 内容 |
|---|---|
| 执行（AI） | `audit_numbers.py` |
| 裁决（人） | （待填） |
| 日期 | （待填） |
| 结论 | ☐ 全部 OK，可定稿 ☐ 仍有差异 |

---

> **AI / 人工边界**：AI 只产出差异与证据（上表）；
> `MISMATCH` / `NO_SOURCE` 的处置**只能由人裁决**
> （见 `skills/S6-qc-audit-revision/references/audit_protocol.md` §8）。
