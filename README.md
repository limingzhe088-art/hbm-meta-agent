# HBM-Meta-Agent

**做「人体内暴露浓度」meta 分析的 Agent 工作包** — 从检索策略到返修闭环的完整流水线。

**An agent work package for meta-analyses of human internal-exposure biomarker concentrations** — a full pipeline from search-strategy construction to closed-loop revision.

[![validate](https://github.com/limingzhe088-art/hbm-meta-agent/actions/workflows/validate.yml/badge.svg)](https://github.com/limingzhe088-art/hbm-meta-agent/actions/workflows/validate.yml)
[![License: MIT](https://img.shields.io/badge/code-MIT-blue.svg)](LICENSE)
[![License: CC BY 4.0](https://img.shields.io/badge/docs-CC%20BY%204.0-lightgrey.svg)](LICENSE)

---

## 30 秒上手 / Quick Start

```bash
# 1) 安装依赖（单栈 Python，无 R）
pip install -r requirements.txt

# 2) 复制配置模板
cp templates/project.yaml ./project.yaml     # 改 analyte / country / period_scheme 等

# 3) 自检：确认环境正确（应 5/5 通过）
python tests/ci_local.py

# 4) 跑一个完整案例（脱敏可跑通）
cd examples/arsenic-china-1980-2024 && cat README.md

# 5) 在 Agent 中说出： 「开始内暴露 meta 分析，暴露物是 PFOA」
```

> **只要你的研究对象是「把多篇文献里的人体生物样本浓度合并成总体水平」，本包适用。**
> 如果你做的是效应量合并（OR/RR/HR）、单个体的原始数据、或非人体样本，本包**不适用**。

---

## 它解决什么坑 / What It Solves

内暴露浓度 meta 分析的研究者反复踩同一组坑。本工作包把**一类真实项目的四类历史事故**
编码成机制（而非写在文档里建议）：

| 事故 | 表现 | 机制化对策 |
|---|---|---|
| **快照漂移** | 手稿数值基于旧数据表，返修时才发现时段/地区数值系统性过时 | **快照指纹**（sha256 + 时间 + 记录数）；手稿数字追溯不到来源即判 `STALE_MANUSCRIPT` |
| **分期不一致** | 描述性分析用一套时段边界、EDI 脚本用另一套、手稿混用 | `project.yaml → period_scheme` **单点定义 + 冻结日**；改动强制下游重算 |
| **单位口径混用** | 尿样的 μg/L、μg/g Cr、比重校正三类记录被直接合并 | 契约把 `unit_original`（原文留痕）与 `unit_final`（标准化输出）**分列建模** |
| **重复计数** | 同一出生队列被 35 篇论文报告，参与者总数被重复累加 | `cohort_id` + `dedup_group` 字段；GATE-2/GATE-5 强制队列聚簇裁决 |

**再加两条用血换来的教训**：
- **AI 初提必漏字段**（采样年份、尿校正状态、血基质、统计量类型）→ GATE-3 要求**逐条人工校对**，`verified` 不足的记录不得用于主结论。
- **绝不允许推断未报告的数据**：`gm_summary` / `sample_size` / `time` / `sample_type` 四个字段**禁止插补**。

---

## 核心资产 / Core Assets

| 资产 | 位置 | 内容 |
|---|---|---|
| **数据契约** | `shared/data-contract.md` | 72 字段主表定义、单位口径、快照指纹、`project.yaml` 规范、**20 项推断标记字典** |
| **六道人工闸门** | `shared/quality-gates.md` | GATE-0…GATE-5 的触发条件、AI 产出物、**人工裁决项**、状态机 |
| **唯一配置入口** | `shared/project_config.py` | 配置缺失即**硬错误**（不允许兜底） |
| **标记单一来源** | `shared/inferred_flags.py` | 23 项注册表 + 使用处覆盖核对 + 文档项数一致性 |
| **快照指纹** | `shared/snapshot.py` | 字节级 sha256、漂移判定、追加式登记表 |
| **六个子技能** | `skills/S1–S6/` | 检索 → 筛选 → 提取 → 标准化 → 合并+EDI → 质控返修 |
| **可执行脚本** | `skills/*/scripts/` | 18 个脚本，**全部含 `--self-test`**（累计 ≈700 项断言） |
| **质控体系** | `tests/` | 跨脚本一致性 / 结构校验 / 本地 CI 预演 / **注入失败验证 CI** |
| **教学案例** | `examples/arsenic-china-1980-2024/` | **脱敏可跑通**：检索式 + 排除词银行 + 72 列表头 + 12 条覆盖全部分叉的记录 |

### 六个子技能 / Six Sub-Skills

| 子技能 | 阶段 | 闸门 | 一句话 |
|---|---|---|---|
| `S1-search-strategy` | 1 | GATE-1 | ⚠️ **规划中**（见 Known Limitations） |
| `S2-screening-quality` | 2 | GATE-2 | ⚠️ **规划中** |
| `S3-extraction-standardization` | 3 | GATE-3 | AI 初提 + **逐条人工校对**，把论文变成可追溯的主表 |
| `S4-unit-statistic-conversion` | 4 | GATE-4 | **三级标准化**：统一指标 → 统一单位 → 统一为 GM（Wan S1–S7 + ICRP 89） |
| `S5-weighted-pooling-edi` | 5 | — | 样本量加权 GM/GSD + EDI（尿双路径 + 血修正公式） |
| `S6-qc-audit-revision` | 6 | GATE-5 | 数值审计 + 去重 + 敏感性 + 文献链 + 审稿闭环 |

---

## 通用性 / Generality

**可变项与不变项在架构上分离** —— 换暴露物 / 换人群**不用重写代码**：

| 每次会变 | 改哪里 |
|---|---|
| 暴露物（砷 → PFOA / 抗生素 / 药物浓度） | `project.yaml → analyte` + 单位换算表 |
| 人群（中国一般人群 → 全球 / 特定人群） | `project.yaml → country` |
| 时空（1980–2024 中国 → 任意） | `project.yaml → period_scheme` + `region_map` |
| 检索式 | 每次由 S1 重新构造（**排除词银行**可复用） |

| 不变（核心资产） |
|---|
| 基质枚举（尿/血/脐血，可扩展母乳/指甲/头发） |
| 提取逻辑（AI 初提 + 人工逐条校对） |
| 三级标准化流程 |
| 样本量加权合并 |
| 质控与返修体系（数值审计/去重/敏感性/DOI 核验/审稿闭环） |

---

## 依赖 / Requirements

```bash
pip install -r requirements.txt
```

| 包 | 必需 | 用途 |
|---|---|---|
| **PyYAML** | ✅ | 解析 `project.yaml`（缺失即硬错误） |
| **openpyxl** | ✅ | 读取主表 `.xlsx` |
| pandas / numpy / scipy | ❌ 可选 | 当前实现用标准库完成；安装后可交叉验证 |

**Python ≥ 3.11。无 R 依赖。**

---

## 质量保障 / Quality Assurance

```bash
python tests/ci_local.py            # 本地 CI 预演：5/5 应通过
python tests/verify_consistency.py  # 跨脚本一致性：6/6 应通过
python tests/verify_structure.py    # 结构校验：6/6 应通过
python tests/inject_failure.py --inject --check   # 验证 CI 真能拦住失败
```

CI（`.github/workflows/validate.yml`）执行 5 步：
**全部脚本自检 → 模板与实现一致性 → 结构 → 跨脚本一致性 → 源码卫生**。

> 源码卫生检查含三个探针：损坏字符、硬编码路径、口径兜底（`DEFAULT_*`）。
> 详见 `agent/routing.md` **§2.6 源码改动纪律**。

---

## ⚠️ Known Limitations

| 项 | 状态 | 计划 |
|---|---|---|
| **S1 / S2 仅有目录骨架** | 未实现 | `STEP4-TODO.md` **F5** |
| `wan_variant="plus"` | **显式拒绝**（不做静默等价） | **F1** |
| 脐血 EDI | 方案 A：不计算并计数 | **F2** |
| `.docx` / `.enl` / `.ris` 解析 | 需先手工转换为 `.md` / `.csv` | **F3** |
| `inverse_variance` 加权 | 未实现 | **F4** |

> `tests/verify_structure.py` 对 S1/S2 标注"规划中不校验"，
> 因此它会显示 `6/6 通过`——**这不代表 6 个子技能都已完整实现**。
> 完整缺口清单见 `STEP4-TODO.md`。

---

## 贡献 / Contributing

请先读 **`CONTRIBUTING.md`**，特别是：
- **§2 源码改动纪律**（不要用 shell 往返改写源码——本项目真实因此损坏过 3 个文件）
- **§4 新增 `inferred_flags` 标记的四步**（单一来源 + 契约 + 文档 + 验证）
- **§8 数据与隐私红线**（不得提交原始数据 / 可识别信息 / 凭证）

---

## 引用 / Citation

```bibtex
@software{hbm_meta_agent_2026,
  title  = {HBM-Meta-Agent: Human Biomonitoring Meta-Analysis Agent},
  year   = {2026},
  version= {1.0.0},
  license= {MIT},
  url    = {https://github.com/limingzhe088-art/hbm-meta-agent}
}
```

见 `CITATION.cff`（含 Wan 2014 与 PRISMA 2020 的引用信息）。

---

## 许可 / License

- **代码**（`.py` / `.yml` / `.yaml`）：**MIT**
- **文档**（`.md`）：**CC BY 4.0**

详见 `LICENSE`。

---

## 设计基石 / The One Rule to Remember

> **本工作包的每一处「自动」，都必须有一处「人工裁决」与之配对。**
>
> Every automated step in this work package is paired with a human decision point.
>
> 六道闸门**只由人开启**——AI 只能产出"疑似 / 建议 / 差异"。
> 这不是能力限制，而是设计选择：**"这个研究该不该纳入""这个数是不是真的"只有领域研究者能做。**
