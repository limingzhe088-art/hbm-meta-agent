# S1 检索策略样例（脱敏）

> **教学案例**。检索式与命中数可复现；具体研究标题、作者、DOI、省份级真实数值已全部移除。
> **命中数用于检索充分性诊断，保留以便教学。**

---

## 0. 为什么保留命中数

S1 的核心产出之一就是 **「根据命中数判断检索式是否合适」**：

| 命中量级 | 诊断 | 动作 |
|---|---|---|
| 过少（如 < 300） | 检索式可能过窄 | 放宽基质/人群限定；检查是否有过度排除词 |
| 落在合理区间 | 可用 | 进入去重与筛选 |
| 过多（如 > 20000） | 排除词不足 | 补充排除词（见下方四类分组） |

命中数**不含个人信息**，任何做同类内暴露 meta 分析的人都能复现同量级结果；
删掉它会让 S1 的教学价值归零。本案例保留，具体研究信息已移除。

---

## 1. 数据库与检索式

### 1.1 PubMed（命中 972）

```
(((arsenic) OR (As)) AND ((Blood) OR (plasma) OR (Urine) OR (Urinary))
 NOT ((rat) OR (mouse) OR (review) OR (Cell) OR (patients)
      OR (Kidney) OR (liver) OR (disease))
 AND ((China) OR (Chinese)))
```

**要点**：PubMed 的 `NOT` 是**全局**排除——它会连带排除"混合人群研究中提到 disease"的记录。
本项目实证：`NOT (occupational)` 会连带排除同时含职业与非职业亚组的研究，
故**检索阶段不排除 occupational**，留到筛选阶段人工裁决。

### 1.2 Web of Science（命中 107）

```
TS=("arsenic" OR "As") AND TS=("Blood" OR "plasma" OR "Urine" OR "Urinary")
AND TS=("china" OR "Chinese" OR "taiwan*")
NOT TS=("rat*" OR "mouse*" OR "Kidney*" OR "liver*" OR "Leukemia*"
        OR "Folic acid*" OR "patient*" OR "cell" OR "medicinal*"
        OR "United States*" OR "gene" OR "protein" OR "network"
        OR "hamster*" OR "rabbit" OR "animal" OR "in vitro" OR "review*" OR "meta*")
```

**要点**：WoS 用 `TS=`（主题）而非 PubMed 的 `[Title/Abstract]`；
通配符 `*` 必须显式写（`patient*` 才能覆盖 patients/patient）。
**检索时间**：1980–2024；**文献类型**：article / other / meeting；**排除**：organisms=all。

### 1.3 CNKI 知网（命中 2485）

```
TKA=('砷' * ('尿' + '血') - '铀')
```

**要点**：CNKI 用 `TKA=`（篇关摘）与 `* + -` 运算符；
`- '铀'` 是**必要的**——中文语境下"砷铀"常共现于矿物/核相关文献。
**检索范围**：学术期刊 + 学位论文；**时间**：1980.01.01–2024.12.31。

```
主题:(砷 and 尿液 or 血砷 not 砷中毒 or 细胞 or 铀 or 职业性砷 or 砒霜 or 1价砷)
```

### 1.4 万方（命中 2150）

```
主题:(砷 and (尿液 or 血砷)) 时间:1980-2024
```

**要点**：万方与 CNKI 检索语法不同（万方用"主题:"字段限定）；
两库**必须都查**——中文文献覆盖有差异（学位论文尤其）。

---

## 2. ★ 排除词银行（四类分组，S1 的核心可复用资产）

> **为什么要分类**：不同类型的排除词有不同的**误杀风险**。
> 分类后可按需开关，而不是一次性 `NOT` 掉一大串。

### 第一类 · 动物实验

```
rat* / mouse* / mice / hamster* / rabbit* / dog / bird / chicken / animal / in vivo (animal)
中文：大鼠 / 小鼠 / 家兔 / 动物实验 / 鹌鹑
```
**误杀风险**：低。（人体内暴露研究不会以动物为主）
**注意**：`in vivo` 有歧义——人体研究也可能写 `in vivo`，**不要排除**。

### 第二类 · 细胞与机制研究

```
cell / in vitro / gene / protein / RNA / DNA / network / pathway
transporter / bacteria / microbe / kinase / catalyze / material / 3D
中文：细胞 / 机制 / 通路 / 基因 / 蛋白
```
**误杀风险**：**中**。人体流行病学论文的摘要常提到 biomarker 机制，
一刀切会误杀。建议在**筛选阶段**而非检索阶段排除。

### 第三类 · 职业暴露人群

```
occupational / worker / smelter / mining / factory / plant worker
中文：职业 / 工人 / 冶炼 / 矿工 / 焦化
```
**误杀风险**：★ **高**。本项目实证：**检索阶段不得排除 occupational**——
① 它会把"职业与非职业混合调查"整体排除，而那些研究里**非职业亚组是可用的**；
② 排除后无法在筛选阶段发现这类研究的存在。
**正确做法**：检索时不排除 → 筛选阶段标 `flag_occupational=TRUE` → GATE-2 人工逐条裁决。

### 第四类 · 临床与特定疾病人群

```
patient* / disease / case / tumor / leukemia / kidney / liver / NHANES
diastolic blood pressure / folic acid
中文：患者 / 病例 / 肿瘤 / 肾病 / 肝病
```
**误杀风险**：**中高**。病例对照研究的**对照组**浓度往往可用。
同样建议**留到筛选阶段**人工判断。

### 建议的开关组合

| 阶段 | 启用类别 | 理由 |
|---|---|---|
| **检索阶段** | 第一类（动物） | 误杀风险低，能有效压缩命中量 |
| **筛选阶段** | 第二、三、四类 | 需逐条判断，避免误杀可用亚组 |

---

## 3. 命中数与 PRISMA 数字（可复现）

| 阶段 | 数字 | 说明 |
|---|---|---|
| PubMed 命中 | 972 | |
| Web of Science 命中 | 107 | |
| CNKI 命中 | 2485 | |
| 万方命中 | 2150 | |
| **合计检出** | **5714** | 四库合计 |
| 去重移除 | 546 | EndNote 自动去重 + 人工核对 |
| 进入题摘筛 | 5168 | 5714 − 546 |
| 题摘筛排除 | 4649 | 动物研究 / 特定地区或人群 / 综述 / 标题摘要未涉及主题 |
| 进入全文筛 | 519 | 5168 − 4649 |
| 全文筛排除 | 135 | 低质量 / 缺砷浓度数据 / 无目标参数（尿、血、脐血） |
| **最终纳入** | **384** | 519 − 135 |

### 诊断结论

| 项 | 判定 |
|---|---|
| 检出总量 5714 | 落在"可用但偏多"区间 → 排除词基本到位，但筛选工作量大 |
| WoS 仅 107 | ⚠️ **偏少** → 说明 WoS 的 `NOT` 列表过长（`review*`/`meta*` 等）造成过度杀伤 |
| 中文库合计 4635 | 占 81% → **中文文献是本领域主力**，绝不可只查英文库 |
| 题摘筛排除率 90% | 偏高但合理（检索式为了召回而偏宽） |
| 全文筛排除率 26% | 正常 |

> **教学要点**：WoS 的 107 条是"过度杀伤"的典型信号。
> 若重新设计，应把 `review*`/`meta*` 从检索式移出，改在筛选阶段排除。

---

## 4. 检索记录表（S1 的产出格式）

`检索记录.csv` 应包含以下列：

| 列 | 含义 |
|---|---|
| `database` | 数据库名 |
| `query_raw` | **检索式原文**（必须原样留存，供复现） |
| `date` | 检索日期 |
| `hits` | 命中数 |
| `filters` | 时间/文献类型/语言等限定 |
| `notes` | 该库的语法备注与异常 |

**注意**：检索式原文必须**逐字留存**。改写后再记录会使检索不可复现
（本项目收到的审稿意见之一就是"需补完整检索式"，见 S6 的 `review_progress` 示例）。

---

## 5. 相关

- 排除词分组依据与三类偏倚：`skills/S2-screening-quality/`（**规划中**，见 `STEP4-TODO.md` F5）
- 命中数与 PRISMA：`shared/quality-gates.md` GATE-1
- 本案例的筛选结果示例：`11-revision/review_progress_EXAMPLE.md`
