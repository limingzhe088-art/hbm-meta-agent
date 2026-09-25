# 正文引用 ↔ 文献库记录对照表

> 模板来源：`skills/S6-qc-audit-revision/references/citation_verification_protocol.md`
> 生成脚本：`scripts/citation_crosswalk.py`
> 用途：核对并可在 EndNote / Word 中逐条对应。正文引用为作者-年份制。

---

## 0. 统计

| 项 | 数量 |
|---|---|
| 正文唯一引用 | {{n_citations}} |
| 库中记录 | {{n_library_records}} |
| `MATCHED` | {{n_matched}} |
| `MISSING` | {{n_missing}} |
| `AMBIGUOUS` | {{n_ambiguous}} |
| `UNUSED`（库中有、正文未引） | {{n_unused}} |

---

## 1. 对照表

| # | 正文引用 | 状态 | 库中记录（作者 / 标题 / 期刊 / DOI） | EndNote 搜索词 | 备注 |
|---|---|---|---|---|---|
| 1 | (Smedley et al., 2002) | `MATCHED` | Smedley, P. L.; Kinniburgh, D. G. / A review of the source, behaviour and distribution of arsenic / Appl Geochem / 10.1016/S0883-2927(02)00018-5 | `Smedley 2002` | |
| 2 | (EFSA, 2009) | `MATCHED` | Efsa Panel on Contaminants in the Food Chain / Scientific opinion on arsenic in food / EFSA J / 10.2903/j.efsa.2009.1351 | `EFSA 2009` | 机构作者 |
| 3 | (Zhang et al., 2024a) | `AMBIGUOUS` | 两条同姓同年记录 | `Zhang 2024` | ★ 需消歧（加 a/b 后缀） |
| 4 | (示例缺失, 2020) | `MISSING` | — | `示例缺失 2020` | ★ 必须补建记录 |

**说明**：
- `MATCHED` 共 {{n_matched}} 个
- `MISSING` 共 {{n_missing}} 个 → 未补建会导致后续自动插入失败
- `AMBIGUOUS` 共 {{n_ambiguous}} 个 → 需在库与稿中都加 `a`/`b` 后缀消歧

---

## 2. 库中有但正文未引用（`UNUSED`）

> 这些记录会导致参考文献表多出条目；应删除或确认是否为遗漏引用。

| # | 作者 | 年份 | 标题 | DOI |
|---|---|---|---|---|
| 1 | | | | |

---

## 3. 参考文献插入表（含完整信息）

> 用途：在 EndNote 库中按「搜索词」定位记录，用 `Cite While You Write` 在 Word 手稿对应位置插入。
> 作者超过编号上限时 EndNote 输出为「第一作者 et al.」，与手稿一致。

| # | 正文引用 | 搜索词 | 作者 | 年份 | 标题 | 期刊 | 卷 | 期 | 页 | DOI | 备注 |
|---|---|---|---|---|---|---|---|---|---|---|---|
| 1 | | | | | | | | | | | |
| 2 | | | | | | | | | | | |

---

## 4. 消歧记录

> 同姓同年（或同名机构）需加 `a`/`b` 后缀。脚本按稳定规则自动分配；**调用方须先按 `record_id` 排序**。

| 引用键 | 库中顺序 | 涉及文献 | record_id | 分配后缀 |
|---|---|---|---|---|
| Zhang 2024 | 1 | 土壤砷（Zhang, S. et al.） | L08 | `2024a` |
| Zhang 2024 | 2 | 镉时空分布（Zhang, X. et al.） | L09 | `2024b` |

**后缀分配规则**：`{{suffix_rule}}`（默认 `library_order`）
**库是否已按 record_id 排序**：☐ 是（必须）☐ 否 → 请先排序，否则后缀可能互换

**处置**：
- 正文带后缀（`Zhang, 2024a`）→ 脚本**精确命中**对应记录，状态 `MATCHED`
- 正文未加后缀（`Zhang, 2024`）→ 状态 `AMBIGUOUS`，**须人工按上表补后缀**
- 库与稿的后缀必须**完全一致**

---

## 5. 门禁

- [ ] 无 `MISSING` 引用
- [ ] 无 `AMBIGUOUS` 引用（已消歧）
- [ ] `UNUSED` 已处理（删除或确认）
- [ ] 插入表字段完整（作者/年/期刊/卷/期/页/DOI）
- [ ] 正文引用格式与目标期刊要求一致
- [ ] 库与稿同步

| 签署 | 内容 |
|---|---|
| 执行（AI） | |
| 核验（人） | |
| 日期 | |

---

**维护者**：HBM-Meta-Agent（S6）
