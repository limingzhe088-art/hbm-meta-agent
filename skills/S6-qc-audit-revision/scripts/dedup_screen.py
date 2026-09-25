#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
dedup_screen.py — 重复计数筛查（同城/同基质/同作者聚簇 + 队列识别）

阶段状态：第三步骨架版
  ✅ 完整 CLI（--input / --config / --out / --self-test）
  ✅ --self-test（聚簇正确性 + 队列识别 + 影响估计 + 三级去重策略）
  ✅ 核心逻辑：
       同城 + 同基质聚簇（≥4 篇进人工复核）
       同作者聚簇
       队列命名模式识别
       去重策略 S1/S2/S3 的影响估计
       措辞规则检查（participants vs participant records）
  ✅ A1：配置一律来自 project.yaml；缺失/解析失败 → 硬错误（退出码 2）
  ⏳ 第四步待办：快照指纹（A4）、CI 集成（A6）

保真声明
  聚簇逻辑改写自原项目 _tmp/dedup_screen2.py（同城 + 同基质，≥4 篇），
  算法未改动；新增作者聚簇、队列识别、策略影响估计与措辞检查。

用法
  python dedup_screen.py --self-test
  python dedup_screen.py --input 主表.xlsx --config project.yaml --out 报告.md
"""
from __future__ import annotations

import argparse
import csv
import re
import sys
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except (AttributeError, ValueError):  # pragma: no cover
    pass

# 跨技能导入：分期口径必须与 S5/S6 其它脚本**同源**（禁止自行切片年份）
_S5 = Path(__file__).resolve().parent.parent.parent / "S5-weighted-pooling-edi" / "scripts"
if str(_S5) not in sys.path:
    sys.path.insert(0, str(_S5))
# ★ 唯一配置入口（shared/project_config.py）
_SHARED = Path(__file__).resolve().parent.parent.parent.parent / "shared"
if str(_SHARED) not in sys.path:
    sys.path.insert(0, str(_SHARED))
try:
    import weighted_gm as wgm  # noqa: E402
    from project_config import ConfigError, load_config  # noqa: E402
except ImportError as _exc:  # pragma: no cover
    print(f"错误：无法导入依赖模块（{_exc}）", file=sys.stderr)
    raise SystemExit(2)


CLUSTER_MIN_STUDIES = 4      # 同城同基质聚簇的最小研究数
COHORT_PATTERNS = [
    r"birth\s+cohort", r"出生队列", r"prospective\s+cohort", r"母子队列", r"母婴队列",
    r"队列研究", r"cohort\s+study", r"longitudinal\s+cohort",
]

# 三级去重策略
STRATEGY_S1 = "S1_keep_all_fix_wording"      # 保留全部，只改措辞（推荐为主分析）
STRATEGY_S2 = "S2_keep_largest"              # 同队列同基质同期只保留样本量最大者
STRATEGY_S3 = "S3_merge_within_cohort"       # 同队列记录内部先合并

# 措辞检查
BAD_WORDING = [r"\d[\d,]*\s+participants\b", r"\d[\d,]*\s+individuals\b",
               r"\d[\d,]*\s+subjects\b"]
GOOD_WORDING = [r"participant\s+records", r"record-level\s+sums", r"records\b"]


# ===========================================================================
# 1. 数据模型
# ===========================================================================

@dataclass
class Study:
    study_no: str
    author: str
    first_author: str
    city: str
    province: str
    matrix: str
    title: str
    time: str             # ★ 采样/招募年（契约 `time`，**不是** `t_publication`）
    sample_size: float
    cohort_hint: str = ""
    record_ids: list[str] = field(default_factory=list)


@dataclass
class Cluster:
    kind: str            # city_matrix / author / cohort
    key: str
    studies: list[Study] = field(default_factory=list)
    cohort_hint: str = ""

    @property
    def n_studies(self) -> int:
        return len(self.studies)

    @property
    def n_records(self) -> int:
        return sum(len(s.record_ids) for s in self.studies)

    @property
    def total_sample_size(self) -> float:
        return sum(s.sample_size for s in self.studies)


def first_author_name(a: str) -> str:
    """取第一作者姓氏（与原文 dedup_screen2.py 一致的逻辑）。"""
    if not a:
        return ""
    s = str(a).split(";")[0].split("；")[0].strip()
    m = re.search(r"[A-Za-z\u4e00-\u9fff\-]+", s)
    return m.group(0) if m else s[:10]


def clean_city(c: str) -> str:
    """
    规范化城市名。原实现：含全角逗号/分号或长度 > 12 视为无效（多市混写）。
    本实现保留该逻辑并补充半角逗号。
    """
    if not c:
        return ""
    s = str(c).strip()
    if any(ch in s for ch in "，,;；") or len(s) > 12:
        return ""
    return s


def detect_cohort_hint(title: str) -> str:
    t = (title or "").lower()
    for pat in COHORT_PATTERNS:
        if re.search(pat, t):
            return "cohort_hint"
    return ""


# ===========================================================================
# 2. 构建研究集
# ===========================================================================

def build_studies(rows: list[dict]) -> dict[str, Study]:
    studies: dict[str, Study] = {}
    for r in rows:
        if not str(r.get("sample_type") or "").strip():
            continue
        sno = str(r.get("study_no") or "").strip()
        if not sno:
            continue
        try:
            ss = float(r.get("sample_size"))
        except (TypeError, ValueError):
            ss = 0.0
        if sno not in studies:
            # ★ 年份取 `time`（采样/招募年），**不得**用 `t_publication` 代替
            #   （契约 §3.2 硬约束；本项目最高频错误）。
            studies[sno] = Study(
                study_no=sno,
                author=str(r.get("author") or ""),
                first_author=first_author_name(r.get("author")),
                city=clean_city(r.get("city")),
                province=str(r.get("province") or "").strip(),
                matrix=str(r.get("sample_type") or "").strip(),
                title=str(r.get("title") or ""),
                time=str(r.get("time") or "").strip(),      # ← 采样年
                sample_size=0.0,
                cohort_hint=detect_cohort_hint(r.get("title")),
            )
        s = studies[sno]
        s.sample_size += ss
        rid = str(r.get("record_id") or "").strip()
        if rid:
            s.record_ids.append(rid)
    return studies


# ===========================================================================
# 3. 聚簇
# ===========================================================================

def cluster_city_matrix(studies: dict[str, Study],
                        min_studies: int = CLUSTER_MIN_STUDIES) -> list[Cluster]:
    buckets: dict[tuple, list[Study]] = defaultdict(list)
    for s in studies.values():
        if s.city and s.matrix in ("Urine", "Blood", "CordBlood"):
            buckets[(s.city, s.matrix)].append(s)
    out = []
    for (city, matrix), lst in buckets.items():
        if len(lst) >= min_studies:
            hints = {s.cohort_hint for s in lst if s.cohort_hint}
            out.append(Cluster("city_matrix", f"{city}|{matrix}", lst,
                               "cohort_hint" if hints else ""))
    out.sort(key=lambda c: -c.n_studies)
    return out


def cluster_author(studies: dict[str, Study],
                   min_studies: int = 3) -> list[Cluster]:
    buckets: dict[tuple, list[Study]] = defaultdict(list)
    for s in studies.values():
        if s.first_author and s.city:
            buckets[(s.first_author, s.city, s.matrix)].append(s)
    out = []
    for (fa, city, matrix), lst in buckets.items():
        if len(lst) >= min_studies:
            out.append(Cluster("author", f"{fa}|{city}|{matrix}", lst))
    out.sort(key=lambda c: -c.n_studies)
    return out


def cluster_cohort(studies: dict[str, Study]) -> list[Cluster]:
    buckets: dict[tuple, list[Study]] = defaultdict(list)
    for s in studies.values():
        if s.cohort_hint and s.city:
            buckets[(s.city, s.matrix)].append(s)
    out = []
    for (city, matrix), lst in buckets.items():
        if len(lst) >= 2:
            out.append(Cluster("cohort", f"{city}|{matrix}", lst, "cohort_hint"))
    out.sort(key=lambda c: -c.n_studies)
    return out


# ===========================================================================
# 4. 去重策略影响估计
# ===========================================================================

@dataclass
class StrategyImpact:
    strategy: str
    total_sample_before: float
    total_sample_after: float
    n_records_before: int
    n_records_after: int

    @property
    def reduction(self) -> float:
        if self.total_sample_before == 0:
            return 0.0
        return (self.total_sample_before - self.total_sample_after) / self.total_sample_before


def estimate_strategy(studies: dict[str, Study], clusters: list[Cluster],
                      strategy: str, scheme: dict) -> StrategyImpact:
    """
    估计某去重策略对样本量之和与记录数的影响。

    ★ 分期口径**必须显式传入** `scheme`（来自 `project.yaml → period_scheme`），
    通过 `wgm.assign_period()` 归类。**本函数不提供任何默认值**——
    静默兜底会让"漏配口径"变成"用了内置默认分期"，重蹈兜底覆辙（A3）。
    历史问题：曾用 `s.year[:4]`（违规硬切片），且 `"1995"[:4] == "1995"` 并无年份语义。
    """
    if scheme is None:
        raise ValueError("estimate_strategy 需要显式传入 scheme（project.yaml → period_scheme）")
    before_ss = sum(s.sample_size for s in studies.values())
    before_n = sum(len(s.record_ids) for s in studies.values())

    if strategy == STRATEGY_S1:
        return StrategyImpact(strategy, before_ss, before_ss, before_n, before_n)

    if strategy == STRATEGY_S2:
        # 同簇内：同基质 + 同分期只保留样本量最大的一条
        # 注：簇本身近似代表"同一人群"（同城同基质密集报告）；队列级精确去重需
        #     人工在 GATE-2 指定 cohort_id 后按 cohort_id 分组。此处为策略影响估计。
        drop = set()
        for c in clusters:
            if c.kind != "city_matrix":
                continue
            by_key: dict[tuple, Study] = {}
            for s in c.studies:
                period = wgm.assign_period(_to_year(s.time), scheme)
                if period is None:
                    continue          # 分期区间外：不参与该策略
                k = (s.matrix, period)
                if k not in by_key or s.sample_size > by_key[k].sample_size:
                    if k in by_key:
                        drop.add(by_key[k].study_no)
                    by_key[k] = s
                else:
                    drop.add(s.study_no)
        after_ss = sum(s.sample_size for k, s in studies.items() if k not in drop)
        after_n = sum(len(s.record_ids) for k, s in studies.items() if k not in drop)
        return StrategyImpact(strategy, before_ss, after_ss, before_n, after_n)

    if strategy == STRATEGY_S3:
        # 同簇内部先合并为一条：样本量求和（人数仍可能重复，故仅记录数下降）
        merged_records = 0
        for c in clusters:
            if c.kind != "city_matrix":
                continue
            merged_records += 1     # 每簇合并成 1 条
        after_n = before_n - sum(c.n_records for c in clusters if c.kind == "city_matrix") + merged_records
        # 样本量不变（合并而非删除），但"参与者数"不再累加（保守：仍报记录级求和）
        return StrategyImpact(strategy, before_ss, before_ss, before_n, after_n)

    raise ValueError(f"未知策略 {strategy}")


def _to_year(v) -> float | None:
    """把 time 字段安全转为年份数值（供 assign_period 使用）。"""
    try:
        return float(str(v).strip())
    except (TypeError, ValueError):
        return None


# ===========================================================================
# 5. 措辞检查
# ===========================================================================

def check_wording(text: str) -> list[dict]:
    """检查稿件中的计数措辞是否合规。"""
    issues = []
    for pat in BAD_WORDING:
        for m in re.finditer(pat, text, re.IGNORECASE):
            ctx = text[max(0, m.start() - 60):m.end() + 40].replace("\n", " ")
            issues.append({"pattern": pat, "match": m.group(0), "context": ctx,
                           "advice": "改为 participant records / record-level sums"})
    return issues


def has_good_wording(text: str) -> bool:
    return all(re.search(p, text, re.IGNORECASE) for p in [GOOD_WORDING[0]])


# ===========================================================================
# 6. 报告
# ===========================================================================

def render_report(studies: dict[str, Study], clusters: list[Cluster],
                  impacts: list[StrategyImpact], wording_issues: list[dict]) -> str:
    total_studies = len(studies)
    total_records = sum(len(s.record_ids) for s in studies.values())
    total_ss = sum(s.sample_size for s in studies.values())
    city_clusters = [c for c in clusters if c.kind == "city_matrix"]
    author_clusters = [c for c in clusters if c.kind == "author"]

    lines = [
        "# 重复计数与纳入标准筛查报告",
        "",
        "> 本报告**仅列疑似问题**；是否排除/去重需人工在 GATE-2 / GATE-5 逐条裁决。",
        "",
        "## 〇、总体",
        "",
        f"- 唯一研究数：{total_studies}",
        f"- 记录数：{total_records}",
        f"- 样本量之和（**记录级求和**）：{total_ss:,.0f}",
        f"- 同城同基质聚簇（≥{CLUSTER_MIN_STUDIES} 篇）：{len(city_clusters)}",
        f"- 同作者同城同基质聚簇（≥3 篇）：{len(author_clusters)}",
        "",
        "## 一、同一人群重复计数风险",
        "",
    ]
    if not city_clusters:
        lines.append("未发现 ≥4 篇的同城同基质聚簇。")
    else:
        lines += [f"共 {len(city_clusters)} 个簇（按研究数降序）：", ""]
        for i, c in enumerate(city_clusters, start=1):
            hint = "　**⚠️ 疑似队列研究**" if c.cohort_hint else ""
            lines += [
                f"### 簇 {i}：{c.key}{hint}",
                f"- 研究数：{c.n_studies}　记录数：{c.n_records}　样本量之和：{c.total_sample_size:,.0f}",
                "",
                "| 研究编号 | 第一作者 | 采样年 | 矩阵 | 样本量 | 标题（截断） |",
                "|---|---|---|---|---|---|",
            ]
            for s in sorted(c.studies, key=lambda x: -x.sample_size):
                lines.append(f"| {s.study_no} | {s.first_author} | {s.time} | {s.matrix} | "
                             f"{s.sample_size:,.0f} | {s.title[:48]} |")
            lines += ["", "**待人工裁决**：☐ 同一队列（请填 `cohort_id`）　☐ 不同人群　☐ 需查原文", ""]

    lines += ["## 二、同作者聚簇", ""]
    if not author_clusters:
        lines.append("未发现同作者同城同基质的 ≥3 篇聚簇。")
    else:
        for c in author_clusters:
            lines.append(f"- **{c.key}**：{c.n_studies} 篇，样本量之和 "
                         f"{c.total_sample_size:,.0f}（研究："
                         f"{', '.join(s.study_no for s in c.studies)}）")

    lines += ["", "## 三、去重策略影响估计", "",
              "| 策略 | 记录数（前→后） | 样本量之和（前→后） | 降幅 |",
              "|---|---|---|---|"]
    for im in impacts:
        lines.append(f"| `{im.strategy}` | {im.n_records_before} → {im.n_records_after} | "
                     f"{im.total_sample_before:,.0f} → {im.total_sample_after:,.0f} | "
                     f"{im.reduction:.2%} |")
    lines += ["",
              "**推荐**：以 `S1`（保留全部 + 修正措辞）为主分析，`S2` 作为敏感性分析。",
              "理由：`S2` 会改变主分析结果，降级为敏感性可同时满足'不虚高'与'结论稳健性'。",
              ""]

    lines += ["## 四、计数措辞检查", ""]
    if not wording_issues:
        lines.append("未发现不合规的计数措辞。")
    else:
        for w in wording_issues:
            lines.append(f"- ⚠️ 命中 `{w['match']}` → {w['advice']}")
            lines.append(f"  - 上下文：…{w['context']}…")
    lines += ["",
              "**必须写入图注/表注**：Sample sizes are record-level sums; some participants "
              "contributed to more than one record or more than one matrix.", ""]

    lines += ["## 五、建议处理顺序", "",
              "1. 人裁决各簇是否为同一队列（填 `cohort_id`）",
              "2. 选定去重策略（S1/S2/S3）并记录于 GATE-5",
              "3. 修正摘要与正文的计数措辞",
              "4. 如需重算，触发 S5 重跑并同步数值审计",
              "", "---", "",
              "> AI 只列疑似与影响估计；排除、去重与措辞定稿由人裁决（见 "
              "`references/dedup_cohort_protocol.md` §7）。", ""]
    return "\n".join(lines)


# ===========================================================================
# 7. self-test
# ===========================================================================

def _mk_rows():
    rows = []
    # 武汉尿样 5 篇（疑似同队列），采样年跨两个分期：2 篇在 1980-2000、3 篇在 2011-2020
    for i, yr in enumerate((1995, 1998, 2017, 2018, 2019)):
        rows.append({"record_id": f"R{i+1:03d}", "study_no": f"S{i+1:03d}",
                     "author": f"Zhang{i}", "city": "武汉市", "province": "湖北省",
                     "sample_type": "Urine", "title": f"Prenatal urine metals in Wuhan birth cohort {i}",
                     "t_publication": "2020", "time": str(yr),
                     "sample_size": "300", "gm_summary": "20.0"})
    # 同城多市混写（应被 clean_city 判为无效）
    rows.append({"record_id": "R100", "study_no": "S100", "author": "Li", "city": "北京市，天津市",
                 "province": "北京市", "sample_type": "Urine", "title": "Multi-city survey",
                 "t_publication": "2019", "time": "2016",
                 "sample_size": "500", "gm_summary": "15.0"})
    # 同作者 3 篇同城
    for i in range(3):
        rows.append({"record_id": f"R20{i}", "study_no": f"S20{i}", "author": "Wang, X.",
                     "city": "上海市", "province": "上海市", "sample_type": "Blood",
                     "title": f"Blood metals study {i}", "t_publication": "2018",
                     "time": "2015", "sample_size": "200", "gm_summary": "3.0"})
    # 零散研究
    rows.append({"record_id": "R300", "study_no": "S300", "author": "Chen", "city": "广州市",
                 "province": "广东省", "sample_type": "Urine", "title": "Guangzhou survey",
                 "t_publication": "2017", "time": "2015",
                 "sample_size": "400", "gm_summary": "18.0"})
    return rows


def run_self_test() -> int:
    print("=" * 70)
    print("dedup_screen.py --self-test")
    print("=" * 70)
    checks: list[tuple[str, bool, str]] = []

    def check(name, ok, detail=""):
        checks.append((name, ok, detail))
        print(f"      {'✅' if ok else '❌'} {name}" + (f"  [{detail}]" if detail else ""))

    rows = _mk_rows()
    studies = build_studies(rows)

    print("\n[1] 工具函数")
    check("提取第一作者姓氏（含逗号）", first_author_name("Wang, X.; Li, Y.") == "Wang",
          first_author_name("Wang, X.; Li, Y."))
    check("中文姓名提取", first_author_name("张三；李四") == "张三")
    check("城市规范化：单市通过", clean_city("武汉市") == "武汉市")
    check("城市规范化：多市全角逗号 → 空", clean_city("北京市，天津市") == "")
    check("城市规范化：多市半角逗号 → 空", clean_city("Beijing,Tianjin") == "")
    check("城市规范化：超长 → 空", clean_city("a" * 15) == "")
    check("队列识别：birth cohort",
          detect_cohort_hint("Prenatal urine metals in Wuhan birth cohort") == "cohort_hint")
    check("队列识别：中文出生队列",
          detect_cohort_hint("武汉出生队列研究") == "cohort_hint")
    check("队列识别：普通研究不命中",
          detect_cohort_hint("A cross-sectional survey in Guangzhou") == "")

    print("\n[2] 研究集构建")
    check("研究数正确（5+1+3+1=10）", len(studies) == 10, str(len(studies)))
    check("多记录研究样本量求和", studies["S001"].sample_size == 300.0,
          str(studies["S001"].sample_size))
    check("记录数正确", sum(len(s.record_ids) for s in studies.values()) == 10)
    check("★ 采样年取自 time（非 t_publication）",
          studies["S001"].time == "1995", studies["S001"].time)

    print("\n[3] 同城同基质聚簇")
    cc = cluster_city_matrix(studies, min_studies=4)
    check("识别到 1 个 ≥4 篇簇", len(cc) == 1, str([c.key for c in cc]))
    if cc:
        check("簇为 武汉市|Urine 且 5 篇", cc[0].key == "武汉市|Urine" and cc[0].n_studies == 5,
              f"{cc[0].key} {cc[0].n_studies}")
        check("簇内样本量之和 = 1500", cc[0].total_sample_size == 1500.0,
              str(cc[0].total_sample_size))
        check("★ 队列提示被识别", cc[0].cohort_hint == "cohort_hint", cc[0].cohort_hint)
    cc3 = cluster_city_matrix(studies, min_studies=3)
    check("阈值降至 3 时出现 2 个簇（武汉 + 上海血）", len(cc3) == 2,
          str([c.key for c in cc3]))

    print("\n[4] 同作者聚簇")
    ac = cluster_author(studies, min_studies=3)
    check("识别 Wang 上海血 3 篇", any("Wang" in c.key for c in ac), str([c.key for c in ac]))

    print("\n[5] 队列聚簇")
    coh = cluster_cohort(studies)
    check("识别到队列簇", any(c.kind == "cohort" for c in coh), str([c.key for c in coh]))

    print("\n[6] 去重策略影响估计（★ 分期口径来自 scheme）")
    scheme = wgm.FIXTURE_PERIOD_SCHEME   # 自检专用夹具（正式路径由 CLI 从配置注入）
    imp = {s.strategy: s for s in [
        estimate_strategy(studies, cc, STRATEGY_S1, scheme),
        estimate_strategy(studies, cc, STRATEGY_S2, scheme),
        estimate_strategy(studies, cc, STRATEGY_S3, scheme),
    ]}
    check("S1 不改变任何数字",
          imp[STRATEGY_S1].reduction == 0.0 and
          imp[STRATEGY_S1].n_records_after == imp[STRATEGY_S1].n_records_before)
    check("S2 使样本量之和下降", imp[STRATEGY_S2].reduction > 0,
          f"{imp[STRATEGY_S2].reduction:.2%}")
    check("S2 至少剔除 1 个研究",
          imp[STRATEGY_S2].n_records_after < imp[STRATEGY_S2].n_records_before,
          f"{imp[STRATEGY_S2].n_records_before} → {imp[STRATEGY_S2].n_records_after}")
    check("S3 记录数下降但样本量不变",
          imp[STRATEGY_S3].n_records_after < imp[STRATEGY_S3].n_records_before and
          imp[STRATEGY_S3].total_sample_after == imp[STRATEGY_S3].total_sample_before,
          f"records {imp[STRATEGY_S3].n_records_before}→{imp[STRATEGY_S3].n_records_after}")
    check("未知策略抛异常",
          _raises(lambda: estimate_strategy(studies, cc, "S9", scheme)))

    # ★ S2 必须随分期方案变化（证明不再是 year[:4] 硬切片）
    rows_span = []
    for i, yr in enumerate((1995, 1999, 2001, 2005, 2010)):
        rows_span.append({
            "record_id": f"SP{i}", "study_no": f"SSP{i}", "author": f"Wu{i}",
            "city": "武汉市", "province": "湖北省", "sample_type": "Urine",
            "title": "Wuhan urine survey", "t_publication": "2021",
            "sample_size": "100", "gm_summary": "20.0", "time": str(yr)})
    st_span = build_studies(rows_span)
    cc_span = cluster_city_matrix(st_span, min_studies=4)
    s_default = estimate_strategy(st_span, cc_span, STRATEGY_S2, scheme)
    # 自定义分期：把 1995/1999/2001/2005/2010 拆到更多期 → 更多人被保留
    scheme_split = {"boundaries": [
        {"label": "1990-1995", "start": 1990, "end": 1995},
        {"label": "1996-1999", "start": 1996, "end": 1999},
        {"label": "2000-2005", "start": 2000, "end": 2005},
        {"label": "2006-2010", "start": 2006, "end": 2010}]}
    s_split = estimate_strategy(st_span, cc_span, STRATEGY_S2, scheme_split)
    check("默认分期：1995-2010 归为两期 → 每期留 1 条（共 2 条）",
          s_default.n_records_after == 2,
          f"保留 {s_default.n_records_after} 条（5 条输入）")
    check("★ 分期边界变化时 S2 影响随之变化（证明非硬切片）",
          s_split.n_records_after != s_default.n_records_after,
          f"默认保留 {s_default.n_records_after} vs 细分后保留 {s_split.n_records_after}")
    check("细分分期保留更多记录", s_split.n_records_after > s_default.n_records_after,
          f"{s_default.n_records_after} → {s_split.n_records_after}")
    # 只设一个覆盖 2000-2009 的分期 → 仅 2005 落入期内，其余 4 条未归期
    # ★ 未归期的记录**不参与剔除**（保守），故保留数 = 5 - (期内条数-1)
    imp_single = estimate_strategy(st_span, cc_span, STRATEGY_S2,
                                   {"boundaries": [{"label": "2000-2009",
                                                    "start": 2000, "end": 2009}]})
    check("单期分期：期内只留最大者，未归期者不剔除",
          imp_single.n_records_after == 4,
          f"保留 {imp_single.n_records_after} 条（期内 1 条 + 未归期 4 条）")
    imp_none = estimate_strategy(st_span, cc_span, STRATEGY_S2,
                                 {"boundaries": [{"label": "X", "start": 1900, "end": 1901}]})
    check("全部未归期 → 无人被剔除",
          imp_none.n_records_after == 5,
          f"保留 {imp_none.n_records_after} 条")

    print("\n[7] 计数措辞检查")
    bad = check_wording("Our analysis included 360,000 participants from 384 studies.")
    check("检出 'participants' 误用", len(bad) >= 1, f"{len(bad)} 处")
    check("给出修改建议", all("participant records" in b["advice"] for b in bad))
    good = check_wording("We included 360,000 participant records; totals are record-level sums.")
    check("合规措辞不报警", len(good) == 0, str([g["match"] for g in good]))
    check("检出 'individuals'", len(check_wording("360,000 individuals were included")) == 1)
    check("检出 'subjects'", len(check_wording("The 12,000 subjects were adults")) == 1)

    print("\n[8] 报告渲染")
    md = render_report(studies, cc + ac + coh,
                       [imp[STRATEGY_S1], imp[STRATEGY_S2], imp[STRATEGY_S3]], bad)
    check("含总体统计", "唯一研究数" in md and "记录级求和" in md)
    check("含簇明细表", "| 研究编号 |" in md)
    check("含队列提示", "疑似队列研究" in md)
    check("含策略影响表", "去重策略影响估计" in md)
    check("含措辞检查", "计数措辞检查" in md)
    check("含强制图注文案", "record-level sums" in md)
    check("含待人工裁决项", "待人工裁决" in md)

    print("\n" + "-" * 70)
    failed = [c for c in checks if not c[1]]
    if failed:
        print(f"self-test 失败：{len(failed)}/{len(checks)} 项未通过")
        for name, _, detail in failed:
            print(f"  - {name}  {detail}")
        return 1
    print(f"self-test 通过：{len(checks)}/{len(checks)} 项全部通过")
    return 0


def _raises(fn) -> bool:
    try:
        fn()
        return False
    except Exception:
        return True


# ===========================================================================
# 8. CLI
# ===========================================================================

def build_parser():
    p = argparse.ArgumentParser(
        prog="dedup_screen.py",
        description="重复计数筛查与队列去重影响估计（HBM-Meta-Agent / S6）",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="退出码：0 无风险 / 1 存在待裁决项 / 2 前置条件不满足")
    p.add_argument("--input", type=Path, help="主表（.csv / .xlsx）")
    p.add_argument("--config", type=Path, help="project.yaml（必需）")
    p.add_argument("--out", type=Path, help="报告输出（.md）")
    p.add_argument("--manuscript", type=Path, help="可选：稿件文件，用于计数措辞检查")
    p.add_argument("--min-studies", type=int, default=CLUSTER_MIN_STUDIES)
    p.add_argument("--self-test", action="store_true")
    p.add_argument("--version", action="version", version="hbm-meta S6 dedup_screen 1.0.0")
    return p


def _read_rows(path: Path) -> list[dict]:
    if path.suffix.lower() == ".csv":
        with path.open(encoding="utf-8-sig", newline="") as fh:
            return [dict(r) for r in csv.DictReader(fh)]
    if path.suffix.lower() in (".xlsx", ".xlsm"):
        import openpyxl
        wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
        ws = wb[wb.sheetnames[0]]
        it = ws.iter_rows(values_only=True)
        hdr = [str(h).strip() if h is not None else "" for h in next(it)]
        rows = []
        for r in it:
            if r is None or all(v is None for v in r):
                continue
            rows.append({hdr[i]: (r[i] if i < len(r) else None) for i in range(len(hdr))})
        wb.close()
        return rows
    raise ValueError(f"不支持的文件类型：{path.suffix}")


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    if args.self_test:
        return run_self_test()

    if not args.input:
        print("错误：需要 --input，或使用 --self-test", file=sys.stderr)
        return 2

    # ★ A1：配置缺失/解析失败/缺区块 → 硬错误退出码 2
    try:
        cfg = load_config(args.config, require=["period_scheme"])
    except ConfigError as exc:
        print(f"错误：{exc}", file=sys.stderr)
        return 2
    scheme = cfg.period_scheme
    print(f"配置：{cfg.source_path}  ({cfg.summary()})")

    try:
        rows = _read_rows(args.input)
    except FileNotFoundError:
        print(f"错误：主表不存在：{args.input}", file=sys.stderr)
        return 2
    except ValueError as exc:
        print(f"错误：{exc}", file=sys.stderr)
        return 2

    studies = build_studies(rows)
    cc = cluster_city_matrix(studies, args.min_studies)
    ac = cluster_author(studies)
    coh = cluster_cohort(studies)
    impacts = [estimate_strategy(studies, cc, s, scheme)
               for s in (STRATEGY_S1, STRATEGY_S2, STRATEGY_S3)]
    wording = []
    if args.manuscript and args.manuscript.exists():
        wording = check_wording(args.manuscript.read_text(encoding="utf-8"))

    md = render_report(studies, cc + ac + coh, impacts, wording)
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(md, encoding="utf-8")
        print(f"报告：{args.out}")
    else:
        print(md)

    n_pending = len(cc) + len(ac) + len(wording)
    print(f"研究={len(studies)}  同城簇={len(cc)}  同作者簇={len(ac)}  "
          f"队列簇={len(coh)}  措辞问题={len(wording)}")
    return 1 if n_pending else 0


if __name__ == "__main__":
    raise SystemExit(main())
