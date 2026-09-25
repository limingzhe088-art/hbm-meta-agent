#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
sensitivity.py — 敏感性分析（分层 / 子集 / 排除后）

阶段状态：第三步骨架版
  ✅ 完整 CLI（--input / --config / --out / --self-test）
  ✅ --self-test（三类分析 + 主形态一致性判定 + 样本量降级 + 缺失数据留白）
  ✅ 核心逻辑：
       ① 分层分析：按尿校正方式 / GM 来源 / 血基质 / 形态 / 人群
       ② 子集分析：仅直接报告 GM（零推断） / 排除缺 n 回退
       ③ 排除后分析：职业暴露 / 疾病人群 / 血基质存疑 / 病区
       ④ 主形态一致性判定（逐时段数值，不凭印象）
       ⑤ 子集占比报告与降级规则
  ✅ A1：配置一律来自 project.yaml；缺失/解析失败 → 硬错误（退出码 2）
  ⏳ 第四步待办：快照指纹（A4）、CI 集成（A6）

保真声明
  分层与子集逻辑改写自原项目 _tmp/sensitivity.py（按 adjusted 分层 + 直接 GM 子集），
  算法未改动；新增排除后分析、形态判定与降级规则。

用法
  python sensitivity.py --self-test
  python sensitivity.py --input 主表.xlsx --config project.yaml --out 报告.md
"""
from __future__ import annotations

import argparse
import csv
import sys
from dataclasses import dataclass, field
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except (AttributeError, ValueError):  # pragma: no cover
    pass

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
    from inferred_flags import unknown_flags  # noqa: E402  ★ 标记字典单一来源
except ImportError as _exc:  # pragma: no cover
    print(f"错误：无法导入依赖模块（{_exc}）", file=sys.stderr)
    raise SystemExit(2)


FLAT_THRESHOLD = 0.05        # 相对变化 < 5% 视为 flat
MIN_SUBSET_RATIO = 0.20      # 子集占全库比例 < 20% → 结论降级为定性参考

STRATIFY_BY = ["adjusted", "gm_source", "blood_matrix", "analyte_species", "population_group"]

EXCLUDE_GROUPS = {
    "occupational": "flag_occupational",
    "disease": "flag_disease",
    "blood_matrix_flagged": "flag_blood_matrix",
    "endemic_area": "flag_endemic_area",
}


# ===========================================================================
# 1. 形态判定
# ===========================================================================

def trend_label(a: float | None, b: float | None) -> str:
    """
    相邻两期趋势。|相对变化| **严格小于** FLAT_THRESHOLD 视为稳定。
    即恰好 5% 记为稳定（与'< 5% 为稳定'的表述一致）；此前用 `>=` 会把
    恰好 5% 误判为上升（自查发现）。
    """
    if a is None or b is None or a <= 0:
        return "?"
    rel = (b - a) / a
    if rel < -FLAT_THRESHOLD:
        return "↓"
    if rel > FLAT_THRESHOLD:
        return "↑"
    return "→"


def pattern_label(gms: list[float | None]) -> str:
    """把逐时段 GM 序列转成形如 '↓→→' 的形态标签。"""
    if not gms or all(g is None for g in gms):
        return "no_data"
    seq = []
    for i in range(len(gms) - 1):
        seq.append(trend_label(gms[i], gms[i + 1]))
    return "".join(seq)


def human_pattern(label: str) -> str:
    m = {"↓": "下降", "↑": "上升", "→": "稳定", "?": "缺数据"}
    if label == "no_data":
        return "无数据"
    return "—".join(m[c] for c in label)


# ===========================================================================
# 2. GM 来源分类
# ===========================================================================

def gm_source(row: dict) -> str:
    """从 conversion_path 判定 GM 来源类别。"""
    p = str(row.get("conversion_path") or "")
    if "reported_GM" in p:
        return "reported_GM"
    if "Wan_S" in p:
        return "Wan_estimated"
    if "Median_as_GM" in p:
        return "Median_as_GM"
    if "AMSD_to_LN" in p:
        return "AMSD_to_LN"
    return "other"


# ===========================================================================
# 3. 分层计算
# ===========================================================================

@dataclass
class StratumResult:
    analysis_id: str
    type: str                 # stratified / subset / leave_out
    definition: str
    matrix: str
    per_period: dict = field(default_factory=dict)   # period -> (gm, n_records, n_studies)
    pattern: str = ""
    n_records: int = 0
    ratio_of_total: float = 1.0
    consistent_with_main: bool | None = None
    note: str = ""


def _pool_by_period(rows: list[dict], scheme: dict, matrix: str) -> dict:
    buckets: dict[str, list[tuple[float, float, str]]] = {}
    for r in rows:
        if str(r.get("sample_type") or "").strip() != matrix:
            continue
        try:
            gm = float(r.get("gm_summary"))
            w = float(r.get("sample_size"))
        except (TypeError, ValueError):
            continue
        if gm <= 0 or w <= 0:
            continue
        try:
            yr = float(r.get("time"))
        except (TypeError, ValueError):
            continue
        lab = wgm.assign_period(yr, scheme)
        if lab is None:
            continue
        buckets.setdefault(lab, []).append((gm, w, str(r.get("study_no") or "")))
    out = {}
    for lab, items in buckets.items():
        vals = [t[0] for t in items]
        ws = [t[1] for t in items]
        out[lab] = (wgm.weighted_gm(vals, ws), len(items),
                    len({t[2] for t in items if t[2]}) or len(items))
    return out


def analyze(rows: list[dict], scheme: dict, matrix: str,
            total_records: int) -> tuple[StratumResult, list[StratumResult]]:
    """返回 (主分析, 敏感性结果列表)。"""
    periods = wgm.period_labels(scheme)

    main_pool = _pool_by_period(rows, scheme, matrix)
    main = StratumResult("MAIN", "main", "全部纳入记录", matrix)
    main.per_period = main_pool
    main.pattern = pattern_label([main_pool.get(p, (None,))[0] for p in periods])
    main.n_records = sum(v[1] for v in main_pool.values())
    main.ratio_of_total = 1.0
    main.consistent_with_main = True

    results: list[StratumResult] = []
    counter = [0]

    def add(type_: str, definition: str, sub: list[dict]) -> None:
        counter[0] += 1
        aid = f"S{counter[0]}"
        pool = _pool_by_period(sub, scheme, matrix)
        sr = StratumResult(aid, type_, definition, matrix)
        sr.per_period = pool
        sr.pattern = pattern_label([pool.get(p, (None,))[0] for p in periods])
        sr.n_records = sum(v[1] for v in pool.values())
        sr.ratio_of_total = (sr.n_records / total_records) if total_records else 0.0
        sr.consistent_with_main = (sr.pattern == main.pattern)
        notes = []
        if sr.ratio_of_total < MIN_SUBSET_RATIO:
            notes.append(f"子集仅占全库 {sr.ratio_of_total:.1%}（< {MIN_SUBSET_RATIO:.0%}），结论降级为定性参考")
        if sr.n_records == 0:
            notes.append("该层无记录")
        sr.note = "; ".join(notes)
        results.append(sr)

    # --- ① 分层：尿校正方式 ---
    if matrix == "Urine":
        for key in ("Creatinine", "SpecificGravity", "ReferenceConversion", "No"):
            sub = [r for r in rows if str(r.get("adjusted") or "").strip() == key]
            if sub:
                add("stratified", f"尿校正方式 = {key}", sub)

    # --- ① 分层：GM 来源 ---
    for key in ("reported_GM", "Wan_estimated", "Median_as_GM", "AMSD_to_LN"):
        sub = [r for r in rows if gm_source(r) == key]
        if sub:
            add("stratified", f"GM 来源 = {key}", sub)

    # --- ① 分层：血基质 ---
    if matrix in ("Blood", "CordBlood"):
        for key in ("WholeBlood", "Serum", "Plasma", "Unspecified"):
            sub = [r for r in rows if str(r.get("blood_matrix") or "").strip() == key]
            if sub:
                add("stratified", f"血基质 = {key}", sub)

    # --- ① 分层：形态 ---
    for key in ("Total", "iAs", "iAs+MMA+DMA"):
        sub = [r for r in rows if str(r.get("analyte_species") or "").strip() == key]
        if sub:
            add("stratified", f"形态 = {key}", sub)

    # --- ① 分层：人群 ---
    for key in ("Adults", "Minors", "Pregnant", "Elderly"):
        sub = [r for r in rows if str(r.get("population_group") or "").strip() == key]
        if sub:
            add("stratified", f"人群 = {key}", sub)

    # --- ② 子集：仅直接报告 GM（零推断）---
    sub_direct = [r for r in rows if gm_source(r) == "reported_GM"]
    add("subset", "仅直接报告 GM（零推断子集）", sub_direct)

    # --- ② 子集：排除缺 n 回退 ---
    sub_nn = [r for r in rows
              if "no_n_fallback" not in str(r.get("inferred_flags") or "")]
    if len(sub_nn) != len(rows):
        add("subset", "排除缺样本量回退（no_n_fallback）记录", sub_nn)

    # --- ③ 排除后：边界人群与可疑标记 ---
    def truthy(v):
        return str(v).strip().lower() in ("true", "1", "yes", "y", "是")

    for name, col in EXCLUDE_GROUPS.items():
        excluded = [r for r in rows if truthy(r.get(col))]
        if excluded:
            kept = [r for r in rows if not truthy(r.get(col))]
            add("leave_out", f"排除 {name}（{col}=TRUE，共 {len(excluded)} 条）", kept)

    return main, results


# ===========================================================================
# 4. 报告
# ===========================================================================

def render_report(main: StratumResult, results: list[StratumResult],
                  scheme: dict, matrix: str) -> str:
    periods = wgm.period_labels(scheme)
    lines = [
        "# 敏感性分析报告",
        "",
        f"- 基质：`{matrix}`",
        f"- 分期方案：`{scheme.get('name', 'unnamed')}`　时段：{' / '.join(periods)}",
        f"- 主分析记录数：{main.n_records}",
        f"- 主分析形态：`{main.pattern}`（{human_pattern(main.pattern)}）",
        "",
        "## 一、主分析",
        "",
        "| 时段 | 加权 GM | 记录数 | 研究数 |",
        "|---|---|---|---|",
    ]
    for p in periods:
        gm, nr, ns = main.per_period.get(p, (None, 0, 0))
        lines.append(f"| {p} | {'—' if gm is None else f'{gm:.4f}'} | {nr} | {ns} |")

    lines += ["", "## 二、敏感性分析结果", "",
              "| ID | 类型 | 定义 | 记录数 | 占比 | 形态 | 与主分析一致 | 备注 |",
              "|---|---|---|---|---|---|---|---|"]
    for sr in results:
        flag = "✅" if sr.consistent_with_main else "❌"
        lines.append(f"| {sr.analysis_id} | `{sr.type}` | {sr.definition} | {sr.n_records} | "
                     f"{sr.ratio_of_total:.1%} | `{sr.pattern}` | {flag} | {sr.note} |")

    lines += ["", "## 三、逐时段数值（用于人工判读形态）", ""]
    for sr in results:
        lines.append(f"### {sr.analysis_id} · {sr.definition}")
        lines.append("")
        lines.append("| 时段 | 加权 GM | 记录数 |")
        lines.append("|---|---|---|")
        for p in periods:
            gm, nr, _ = sr.per_period.get(p, (None, 0, 0))
            lines.append(f"| {p} | {'—' if gm is None else f'{gm:.4f}'} | {nr} |")
        lines.append("")

    consistent = [s for s in results if s.consistent_with_main]
    inconsistent = [s for s in results if not s.consistent_with_main]
    degraded = [s for s in results if s.ratio_of_total < MIN_SUBSET_RATIO]

    lines += ["## 四、汇总判定", "",
              f"- 与主分析形态一致：{len(consistent)} / {len(results)}",
              f"- 不一致：{len(inconsistent)}" + (f"（{', '.join(s.analysis_id for s in inconsistent)}）"
                                                if inconsistent else ""),
              f"- 因占比过低而降级：{len(degraded)}" + (f"（{', '.join(s.analysis_id for s in degraded)}）"
                                                    if degraded else ""),
              ""]
    if inconsistent:
        lines += ["### ⚠️ 不一致项（必须在正文或局限中说明，不得只放补充材料）", ""]
        for s in inconsistent:
            lines.append(f"- **{s.analysis_id}**（{s.definition}）：形态 `{s.pattern}` "
                         f"vs 主分析 `{main.pattern}`")
        lines.append("")
    else:
        lines += ["> 各层形态与主分析一致。**但请注意**：`consistent_with_main` 是基于"
                  f"{FLAT_THRESHOLD:.0%} 阈值的自动判定，人工仍须核对逐时段数值。", ""]

    lines += ["## 五、AI / 人工边界", "",
              "AI 已给出：逐层逐时段数值、形态标签、一致性自动判定、占比与降级提示。",
              "**人工须裁决**：",
              "1. 哪些子集因样本量不足而**不得**用于结论",
              "2. 若排除后主结论改变，如何改写正文叙述",
              "3. 是否批准\"结论稳健\"的表述",
              "",
              "> AI **不得**自行声明\"结论稳健\"，只提供事实与数值"
              "（见 `references/sensitivity_analysis_protocol.md` §6）。",
              "", "---", "",
              f"> 阈值：变化 < {FLAT_THRESHOLD:.0%} 视为稳定；子集占比 < {MIN_SUBSET_RATIO:.0%} 结论降级。",
              ""]
    return "\n".join(lines)


# ===========================================================================
# 5. self-test
# ===========================================================================

def _mk_dataset():
    """构造含全部时段 × 多分层 × 多标记的合成数据。"""
    rows = []

    def add(rid, st, yr, gm, n, adjusted="Creatinine", gmsrc="reported_GM",
            flags="", blood_matrix="", species="Total", pg="Adults",
            occ="FALSE", dis="FALSE", bflag="FALSE", endemic="FALSE", sno=None):
        rows.append({
            "record_id": rid, "study_no": sno or f"S{rid}", "sample_type": st,
            "time": str(yr), "gm_summary": str(gm), "sample_size": str(n),
            "adjusted": adjusted, "conversion_path": gmsrc,
            "inferred_flags": flags, "blood_matrix": blood_matrix,
            "analyte_species": species, "population_group": pg,
            "flag_occupational": occ, "flag_disease": dis,
            "flag_blood_matrix": bflag, "flag_endemic_area": endemic,
        })

    # 主形态：↓ → ↑ → →（32.58 → 12.45 → 23.50 → 23.52）
    add("R001", "Urine", 1995, 32.58, 1000, sno="S001")
    add("R002", "Urine", 1996, 32.58, 500, sno="S002")
    add("R003", "Urine", 2005, 12.45, 800, sno="S003")
    add("R004", "Urine", 2015, 23.50, 900, sno="S004")
    add("R005", "Urine", 2022, 23.52, 700, sno="S005")
    # 同样形态，比重校正层（4 条）
    add("R006", "Urine", 1995, 30.00, 300, adjusted="SpecificGravity", sno="S006")
    add("R007", "Urine", 2005, 12.00, 300, adjusted="SpecificGravity", sno="S007")
    add("R008", "Urine", 2015, 22.00, 300, adjusted="SpecificGravity", sno="S008")
    add("R009", "Urine", 2022, 22.50, 300, adjusted="SpecificGravity", sno="S009")
    # 直接报告 GM 子集（零推断）
    add("R010", "Urine", 1995, 32.58, 600, gmsrc="reported_GM", sno="S010")
    add("R011", "Urine", 2005, 12.45, 600, gmsrc="reported_GM", sno="S011")
    add("R012", "Urine", 2015, 23.50, 600, gmsrc="reported_GM", sno="S012")
    add("R013", "Urine", 2022, 23.52, 600, gmsrc="reported_GM", sno="S013")
    # Wan 估算层
    add("R014", "Urine", 1995, 32.58, 400, gmsrc="Wan_S4→GM",
        flags="gm_from_median;gm_from_iqr", sno="S014")
    add("R015", "Urine", 2005, 12.45, 400, gmsrc="Wan_S4→GM",
        flags="gm_from_median;gm_from_iqr", sno="S015")
    add("R016", "Urine", 2015, 23.50, 400, gmsrc="Wan_S4→GM",
        flags="gm_from_median;gm_from_iqr", sno="S016")
    add("R017", "Urine", 2022, 23.52, 400, gmsrc="Wan_S4→GM",
        flags="gm_from_median;gm_from_iqr", sno="S017")
    # 职业暴露 2 条（排除后分析）
    add("R018", "Urine", 2015, 60.00, 200, occ="TRUE", sno="S018")
    add("R019", "Urine", 2022, 55.00, 200, occ="TRUE", sno="S019")
    # 疾病人群 1 条
    add("R020", "Urine", 2015, 40.00, 150, dis="TRUE", sno="S020")
    # 血样（含 serum 存疑）
    add("R021", "Blood", 2005, 2.72, 600, blood_matrix="WholeBlood",
        adjusted="NotApplicable", sno="S021")
    add("R022", "Blood", 2015, 1.99, 600, blood_matrix="WholeBlood",
        adjusted="NotApplicable", sno="S022")
    add("R023", "Blood", 2022, 1.39, 600, blood_matrix="WholeBlood",
        adjusted="NotApplicable", sno="S023")
    add("R024", "Blood", 2015, 5.00, 100, blood_matrix="Serum", bflag="TRUE",
        adjusted="NotApplicable", sno="S024")
    return rows


def run_self_test() -> int:
    print("=" * 70)
    print("sensitivity.py --self-test")
    print("=" * 70)
    checks: list[tuple[str, bool, str]] = []

    def check(name, ok, detail=""):
        checks.append((name, ok, detail))
        print(f"      {'✅' if ok else '❌'} {name}" + (f"  [{detail}]" if detail else ""))

    scheme = wgm.FIXTURE_PERIOD_SCHEME   # 自检专用夹具
    rows = _mk_dataset()
    urine = [r for r in rows if r["sample_type"] == "Urine"]

    print("\n[1] 形态判定")
    check("↓（32.58→12.45，降 61.8%）", trend_label(32.58, 12.45) == "↓")
    check("↑（12.45→23.50，升 88.8%）", trend_label(12.45, 23.50) == "↑")
    check("→（23.50→23.52，升 0.09%）", trend_label(23.50, 23.52) == "→")
    check("阈值边界：升 5.0% → →", trend_label(100.0, 105.0) == "→", trend_label(100.0, 105.0))
    check("阈值边界：升 6.0% → ↑", trend_label(100.0, 106.0) == "↑")
    check("缺失数据 → ?", trend_label(None, 10.0) == "?")
    check("主形态组合 = ↓↑→", pattern_label([32.58, 12.45, 23.50, 23.52]) == "↓↑→",
          pattern_label([32.58, 12.45, 23.50, 23.52]))
    check("无数据 → no_data", pattern_label([None, None]) == "no_data")
    check("人读形态 = 下降—上升—稳定",
          human_pattern("↓↑→") == "下降—上升—稳定", human_pattern("↓↑→"))

    print("\n[2] GM 来源分类")
    check("reported_GM 识别", gm_source({"conversion_path": "reported_GM"}) == "reported_GM")
    check("Wan 估算识别", gm_source({"conversion_path": "Median_IQR→Wan_S4→GM"}) == "Wan_estimated")
    check("中位数直接识别", gm_source({"conversion_path": "Median_as_GM→GM"}) == "Median_as_GM")
    check("AM_SD 识别", gm_source({"conversion_path": "AM_SD→AMSD_to_LN→GM"}) == "AMSD_to_LN")

    print("\n[3] 主分析（尿）")
    main, results = analyze(urine, scheme, "Urine", len(urine))
    check("主分析记录数 = 尿记录数", main.n_records == len(urine), f"{main.n_records}/{len(urine)}")
    check("主形态 = ↓↑→", main.pattern == "↓↑→", main.pattern)
    p1 = main.per_period.get("1980-2000", (None,))[0]
    check("1980-2000 加权 GM 可算", p1 is not None and p1 > 0, f"{p1:.4f}" if p1 else "None")

    print("\n[4] 三类分析均产出")
    types = {r.type for r in results}
    check("含 stratified", "stratified" in types, str(sorted(types)))
    check("含 subset", "subset" in types, str(sorted(types)))
    check("含 leave_out", "leave_out" in types, str(sorted(types)))

    print("\n[5] 分层：尿校正方式")
    sg = [r for r in results if "SpecificGravity" in r.definition]
    check("识别比重校正层", len(sg) == 1)
    check("比重层形态 = ↓↑→（与主分析一致）", sg[0].pattern == "↓↑→", sg[0].pattern)
    check("比重层标记为一致", sg[0].consistent_with_main is True)
    check("比重层记录数 = 4", sg[0].n_records == 4, str(sg[0].n_records))

    print("\n[6] 子集：仅直接报告 GM")
    direct = [r for r in results if "零推断子集" in r.definition]
    check("识别零推断子集", len(direct) == 1)
    check("零推断子集形态 = ↓↑→", direct[0].pattern == "↓↑→", direct[0].pattern)

    print("\n[7] 排除后：职业暴露")
    occ = [r for r in results if "occupational" in r.definition]
    check("识别职业暴露排除项", len(occ) == 1)
    check("排除了 2 条记录", "共 2 条" in occ[0].definition, occ[0].definition)
    check("排除后形态仍为 ↓↑→（稳健）", occ[0].pattern == "↓↑→", occ[0].pattern)
    check("排除后记录数 = 尿记录数 - 2", occ[0].n_records == len(urine) - 2,
          f"{occ[0].n_records}/{len(urine)}")

    print("\n[8] ★ 不一致检测（构造反例）")
    rows2 = list(urine)
    rows2 += [
        {"record_id": "X1", "study_no": "SX1", "sample_type": "Urine", "time": "1995",
         "gm_summary": "1.00", "sample_size": "99999", "adjusted": "No",
         "conversion_path": "reported_GM", "inferred_flags": "",
         "population_group": "Adults", "analyte_species": "Total",
         "flag_occupational": "FALSE", "flag_disease": "FALSE",
         "flag_blood_matrix": "FALSE", "flag_endemic_area": "FALSE"},
    ]
    _, res2 = analyze(rows2, scheme, "Urine", len(rows2))
    no_layer = [r for r in res2 if r.definition.endswith("= No")]
    check("'No' 校正层被识别", len(no_layer) == 1)
    if no_layer:
        check("该层形态与主分析不同 → ❌ 标记",
              no_layer[0].consistent_with_main is False,
              f"{no_layer[0].pattern} vs {main.pattern}")

    print("\n[9] 子集占比与降级")
    check("零推断子集占比 = 80%",
          abs(direct[0].ratio_of_total - 0.80) < 1e-9,
          f"{direct[0].ratio_of_total:.4f} ({direct[0].n_records}/{len(urine)})")
    check("占比 80% 不触发降级", direct[0].ratio_of_total >= MIN_SUBSET_RATIO)

    # 构造占比 < 20%：分母需 > 16/0.20 = 80，故加 61 条非 reported_GM 记录 → 16/81 ≈ 19.75%
    rows3 = list(urine)
    for i in range(61):
        rows3.append({
            "record_id": f"B{i}", "study_no": f"SB{i}", "sample_type": "Urine",
            "time": "2015", "gm_summary": "20.0", "sample_size": "100",
            "adjusted": "Creatinine",
            "conversion_path": "Median_IQR→Wan_S4→GM",
            "inferred_flags": "gm_from_iqr", "population_group": "Adults",
            "analyte_species": "Total", "flag_occupational": "FALSE",
            "flag_disease": "FALSE", "flag_blood_matrix": "FALSE",
            "flag_endemic_area": "FALSE"})
    _, res3 = analyze(rows3, scheme, "Urine", len(rows3))
    d3 = [r for r in res3 if "零推断子集" in r.definition][0]
    check("占比随分母下降", d3.ratio_of_total < direct[0].ratio_of_total,
          f"{d3.ratio_of_total:.2%} < {direct[0].ratio_of_total:.2%}")
    check("★ 占比 < 20% 时触发降级", d3.ratio_of_total < MIN_SUBSET_RATIO,
          f"{d3.ratio_of_total:.2%} ({d3.n_records}/{len(rows3)})")
    check("降级提示文案正确", "降级" in d3.note and "定性参考" in d3.note, d3.note)

    print("\n[10] 血样分析（含血基质分层）")
    blood = [r for r in rows if r["sample_type"] == "Blood"]
    bmain, bres = analyze(blood, scheme, "Blood", len(blood))
    bstrat = [r for r in bres if "血基质" in r.definition]
    check("识别血基质分层", len(bstrat) >= 2, str([r.definition for r in bstrat]))
    check("识别 Serum 层", any("Serum" in r.definition for r in bstrat))
    bflag = [r for r in bres if "blood_matrix_flagged" in r.definition]
    check("识别血基质存疑排除项", len(bflag) == 1)
    check("排除存疑后形态保持", bflag[0].pattern == bmain.pattern,
          f"{bflag[0].pattern} vs {bmain.pattern}")

    print("\n[11] 报告渲染")
    md = render_report(main, results, scheme, "Urine")
    check("含主分析表", "一、主分析" in md)
    check("含敏感性结果表", "二、敏感性分析结果" in md)
    check("含逐时段数值表", "三、逐时段数值" in md)
    check("含一致性判定", "与主分析形态一致" in md)
    check("含 AI/人工边界说明", "不得" in md and "稳健" in md)
    check("含阈值声明", f"{FLAT_THRESHOLD:.0%}" in md)

    print("\n[12] ★ 标记一致性（读取的 inferred_flags 必须在闭集内）")
    # 本脚本**读取** inferred_flags 做子集筛选（不派生标记），
    # 因此校验收到的标记合法；越界标记会被忽略而非污染分层。
    bad = unknown_flags("no_n_fallback;gm_from_iqr;gm_from_median")
    check("自检夹具用到的标记都在闭集内", not bad, str(bad) or "全部合法")
    bad2 = unknown_flags("no_n_fallback;not_a_real_flag")
    check("越界标记能被识别", bad2 == ["not_a_real_flag"], str(bad2))

    print("\n" + "-" * 70)
    failed = [c for c in checks if not c[1]]
    if failed:
        print(f"self-test 失败：{len(failed)}/{len(checks)} 项未通过")
        for name, _, detail in failed:
            print(f"  - {name}  {detail}")
        return 1
    print(f"self-test 通过：{len(checks)}/{len(checks)} 项全部通过")
    return 0


# ===========================================================================
# 6. CLI
# ===========================================================================

def build_parser():
    p = argparse.ArgumentParser(
        prog="sensitivity.py",
        description="敏感性分析：分层 / 子集 / 排除后（HBM-Meta-Agent / S6）",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="退出码：0 全部一致 / 1 存在不一致或降级项 / 2 前置条件不满足")
    p.add_argument("--input", type=Path, help="标准化后的主表（.csv / .xlsx）")
    p.add_argument("--config", type=Path, help="project.yaml（必需）")
    p.add_argument("--matrix", default="Urine", help="基质（默认 Urine）")
    p.add_argument("--out", type=Path, help="报告输出（.md）")
    p.add_argument("--self-test", action="store_true")
    p.add_argument("--version", action="version", version="hbm-meta S6 sensitivity 1.0.0")
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

    sub = [r for r in rows if str(r.get("sample_type") or "").strip() == args.matrix]
    if not sub:
        print(f"错误：主表中无基质 `{args.matrix}` 的记录", file=sys.stderr)
        return 2

    main_res, results = analyze(sub, scheme, args.matrix, len(sub))
    md = render_report(main_res, results, scheme, args.matrix)
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(md, encoding="utf-8")
        print(f"报告：{args.out}")
    else:
        print(md)

    inconsistent = [s for s in results if not s.consistent_with_main]
    degraded = [s for s in results if s.ratio_of_total < MIN_SUBSET_RATIO]
    print(f"基质={args.matrix}  记录={len(sub)}  敏感性项={len(results)}  "
          f"不一致={len(inconsistent)}  降级={len(degraded)}")
    return 1 if (inconsistent or degraded) else 0


if __name__ == "__main__":
    raise SystemExit(main())
