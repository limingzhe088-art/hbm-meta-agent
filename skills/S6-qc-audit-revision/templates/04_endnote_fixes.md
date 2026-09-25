# EndNote 文献库需修改清单（字段级一一对应）

> 模板来源：`skills/S6-qc-audit-revision/references/citation_verification_protocol.md`
> 生成脚本：`scripts/verify_dois.py`
> 来源：文献库（`.enl`）与手稿最终参考文献逐条比对，并逐条经 Crossref/DOI 核验。

---

## 0. 版本信息

| 项 | 内容 |
|---|---|
| 核查日期 | {{date}} |
| 库文件 | `{{library_path}}` |
| 库中记录数 | {{n_library_records}} |
| 去重后唯一文献数 | {{n_unique}} |
| 核验方式 | Crossref API（`api.crossref.org`）+ 人工确认 |

**在库中的定位方法**：在搜索框输入「作者姓氏 + 年份」（如 `Shen 2016`）打开该记录，点击相应字段修改。
**每条唯一文献在库中有一至多条重复副本**——**请把每个副本都改**，或先 `Find Duplicates` 合并后只改保留的那条。

> 若库被 EndNote 占用而无法读取 record id，请先关闭该库后再继续。

---

## 一、内容错误（字段级）

| 文献 | DOI | 字段 | 库中现为 | **应改为** |
|---|---|---|---|---|
| {{author_year}} | {{doi}} | Year | {{current}} | **{{correct}}** |
| 〃 | 〃 | Volume | | **{{correct}}** |
| 〃 | 〃 | Journal | | **{{correct}}** |
| 〃 | 〃 | Pages | | **{{correct}}** |
| 〃 | 〃 | DOI | | **{{correct}}** |

> 逐条按上表格式展开。**特别注意** DOI 指向不同文章的情形——人工看一遍很难发现，必须靠 DOI 解析。

---

## 二、缺失记录（需新增）

| 文献 | 需新增记录内容 |
|---|---|
| {{author_year}} | 作者: {{authors}}　年份: {{year}}　标题: {{title}}　期刊: {{journal}}　卷: {{volume}}　期: {{issue}}　页: {{pages}}　DOI: {{doi}} |

> 新增后才能正确处理同名同年的 `a`/`b` 后缀（如 `Zhang 2024a` / `Zhang 2024b`）。

---

## 三、重复记录清理（{{n_before}} → {{n_after}}）

| 重复程度 | 涉及文献 | EndNote 操作 |
|---|---|---|
| {{n_dup}} 条重复 | {{list}} | `References → Find Duplicates`，每篇合并为 1 条（保留信息最全的） |

> `Find Duplicates` 会逐对提示；选 `Keep` 保留正确的那条，删除其余。

---

## 四、机构作者（确认字段）

| 手稿引用 | 库中作者字段应为 |
|---|---|
| (EFSA, 2009) / (EFSA, 2014) | `European Food Safety Authority` |
| (NRC, 2001) | `National Research Council` |

> 若字段为 `Efsa Panel on Contaminants...`，EndNote 样式会按作者字段输出缩写；
> 只需确认引用时能正确生成 `EFSA, 2009` 而非乱码。

---

## 五、核对无误的其余 {{n_ok}} 条

> 仅需清理重复，字段无需修改。

{{list_of_ok_entries}}

---

## 六、需人工核验（无 DOI 或 DOI 无效）

| 文献 | 问题 | 核验方式 | 结果 |
|---|---|---|---|
| | 无 DOI（书籍/标准/报告） | 手工查证 | |
| | DOI 解析失败 | 查出版社页面 | |

---

## 七、门禁

- [ ] 所有 `MISMATCH` 已逐条确认并修改
- [ ] 重复记录已清理至唯一文献数
- [ ] 机构作者字段已确认
- [ ] 新增记录已建立
- [ ] 无 DOI 项已人工核验
- [ ] **库与稿件同步**：库改了稿也必须改（或反之）

| 签署 | 内容 |
|---|---|
| 执行（AI） | |
| 核验（人） | |
| 日期 | |

---

**维护者**：HBM-Meta-Agent（S6）
