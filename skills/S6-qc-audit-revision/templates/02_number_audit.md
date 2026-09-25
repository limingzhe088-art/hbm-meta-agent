# 数值审计对照表

> 模板来源：`skills/S6-qc-audit-revision/references/audit_protocol.md`
> 生成脚本：`scripts/audit_numbers.py`
> 用途：以"快照 + 冻结分期 + 加权公式"重算稿件每个数字，输出差异与**连带修改**。

---

## 0. 快照指纹

```yaml
snapshot:
  source_table: {{source_table}}
  sha256: {{sha256}}
  generated_at: {{generated_at}}
  config_version: {{config_version}}
  contract_version: "1.0.0"
  record_count: {{record_count}}
  study_count: {{study_count}}
  period_scheme: {{period_scheme_name}}   # freeze_date: {{freeze_date}}
  generator: scripts/audit_numbers.py
```

**稿件绑定快照**：`{{manuscript_snapshot}}` / `{{manuscript_sha256}}`

> ⚠️ 若上两行哈希不一致 → **检测到快照漂移**，全部相关数字标 `STALE_MANUSCRIPT`，应全量重算而非逐个人工核对。

---

## 1. 状态统计

| 状态 | 数量 | 处置 |
|---|---|---|
| `OK` | {{n_ok}} | 无需处理 |
| `MISMATCH` | {{n_mismatch}} | ★ 人工裁决 |
| `STALE_MANUSCRIPT` | {{n_stale}} | ★ 全量重算 |
| `NO_SOURCE` | {{n_nosource}} | ★ 人工裁决（补来源或删除） |
| **合计** | {{n_total}} | |

---

## 2. 明细对照

| # | 类型 | 分层 | 稿件原值 | 重算值 | 相对差 | 状态 | 来源 | 连带修改 |
|---|---|---|---|---|---|---|---|---|
| 1 | `period_GM` | Urine \| 1980-2000 | | | | | | |
| 2 | `period_GM` | Urine \| 2001-2010 | | | | | | |
| 3 | `region_GM` | Urine \| Southwest \| 2011-2020 | | | | | | |
| 4 | `province_GM` | Urine \| 贵州省 \| 2011-2020 | | | | | | |
| 5 | `edi` | Urine \| Adults | | | | | | |
| 6 | `correlation` | Blood \| time | | | | | | |
| 7 | `count` | records | | | | | | |

**容差**（见协议 §3）：加权 GM 相对 0.5%｜百分比绝对 0.5 pt｜相关系数绝对 0.001｜计数精确｜P 值只比对显著性方向。

---

## 3. 连带修改清单

> 数值改了，叙述往往也必须改。含比较级/趋势/最高级表述的句子**必须**复审。

| # | 数值项 | 需修改的句子（段落 + 原句） | 触发词 | 建议改法 |
|---|---|---|---|---|
| 1 | | "urinary arsenic **declined steadily** from …" | declined | 改为 "declined then rebounded" |
| 2 | | "**64%** reduction" | 百分比 | 改为 62% |
| 3 | | "**South and Central** China were higher" | higher | 删除该表述 |
| 4 | | 摘要中的对应表述 | 摘要 | 同步修改 |

**高风险区（必须逐个检查）**：摘要 · 结论 · Highlights · 图注 · 表注。

---

## 4. 待人工裁决

| # | 分层 | 稿件原值 | 重算值 | 状态 | 请裁决 |
|---|---|---|---|---|---|
| 1 | | | | `MISMATCH` | ☐ 采信重算值 ☐ 修正换算 ☐ 保留并注明 |
| 2 | | | | `NO_SOURCE` | ☐ 补来源 ☐ 删除该数字 |
| 3 | | | | `STALE_MANUSCRIPT` | ☐ 批准全量重算 ☐ 指定重算范围 |

> AI 只产出差异与证据；上述裁决**只能由人做出**（协议 §8）。

---

## 5. 残留问题

> 已知但暂不处理的项，须写明原因与影响。

| # | 项 | 原因 | 影响 | 是否写入局限 |
|---|---|---|---|---|
| 1 | | | | ☐ |

---

## 6. 定稿门禁

- [ ] 全部数字状态为 `OK`，或差异已逐条裁决
- [ ] 无 `NO_SOURCE`（孤儿数字）残留
- [ ] 摘要 / 结论 / 图注 / 表注 已同步
- [ ] 连带修改清单已全部执行
- [ ] 本报告与稿件版本号一致
- [ ] 快照指纹已记录

| 签署 | 内容 |
|---|---|
| 执行（AI） | |
| 裁决（人） | |
| 日期 | |
| 结论 | ☐ 全部 OK，可定稿 ☐ 仍有差异 |

---

**维护者**：HBM-Meta-Agent（S6）
