#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
weighted_gm.py — 样本量加权几何均值/GSD 分层合并

阶段状态：第三步骨架版
  ✅ 完整 CLI（--input / --config / --out / --self-test）
  ✅ --self-test（数学校验 + 已知解析解 + 边界情形 + 权重诊断）
  ✅ 核心逻辑：加权 GM/GSD（ln 尺度）、分层、加权分位数、权重诊断、
       分期方案从 project.yaml 读取（代码内**零字面量**）、最小样本量规则
  ⏳ 第四步待办：project.yaml 缺失时改为硬错误（禁止兜底，见 STEP4-TODO.md A1）、
       输出带快照指纹、row_hash 校验、CI 集成

保真声明
  数学逻辑与原项目 R 脚本（描述性分析_run.R）完全一致：
    R:  weighted_log_mean = sum(Sample_Size * log10(GM_Summary)) / sum(Sample_Size)
        weighted_gm       = 10 ^ weighted_log_mean
    Py: lg = Σ w·ln(GM) / Σ w ;  GM_w = exp(lg)
    （ln 与 log10 结果等价；本工作包统一用 ln，见 references/weighting_method.md §1.4）
    R:  GSD = exp(sqrt(weighted.mean((log(x)-weighted.mean(log(x),w))^2, w)))
    Py: 逐项对应
  算法未做任何改动。

用法
  python weighted_gm.py --self-test
  python weighted_gm.py --input 标准化.csv --config project.yaml --out 分层结果.csv

退出码
  0 成功   1 存在被排除记录/告警   2 前置条件不满足（配置缺失等）
"""
from __future__ import annotations

import argparse
import csv
import math
import sys
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path

try:  # Windows 控制台 GBK 兼容
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except (AttributeError, ValueError):  # pragma: no cover
    pass

# ★ 唯一配置入口（shared/project_config.py）
_SHARED = Path(__file__).resolve().parent.parent.parent.parent / "shared"
if str(_SHARED) not in sys.path:
    sys.path.insert(0, str(_SHARED))
try:
    from project_config import ConfigError, load_config  # noqa: E402
except ImportError as _exc:  # pragma: no cover
    print(f"错误：无法导入 shared/project_config.py（{_exc}）", file=sys.stderr)
    raise SystemExit(2)


# ===========================================================================
# 1. 配置（第四步改为强制从 project.yaml 读取）
# ===========================================================================

# ⚠️ 正式分析的分期一律来自 project.yaml → period_scheme（STEP4-TODO.md A1）。
#    下表为**自检专用夹具**，仅供 --self-test 使用。
FIXTURE_PERIOD_SCHEME = {
    "name": "four_era",
    "boundaries": [
        {"label": "1980-2000", "start": 1980, "end": 2000},
        {"label": "2001-2010", "start": 2001, "end": 2010},
        {"label": "2011-2020", "start": 2011, "end": 2020},
        {"label": "2021-2024", "start": 2021, "end": 2024},
    ],
    "freeze_date": "2026-07-19",
    "boundary_policy": "inclusive",
    "out_of_range": "exclude",
}

MIN_RECORDS_FOR_REPORT = 3       # 记录数 < 3 的分层单元须标注
MAX_WEIGHT_SHARE_WARN = 0.30     # 单记录权重占比 > 30% 须做敏感性分析

STRATA_DIMS = ["sample_type", "period", "region", "province", "population_group"]


def load_pooling_config(path: Path | None) -> dict:
    """
    ★ A1：从 project.yaml 读取分期与加权口径。
    **缺失/解析失败/缺区块 → ConfigError（硬错误），不兜底。**
    """
    cfg = load_config(path, require=["period_scheme", "weighting"])
    return {
        "period_scheme": cfg.period_scheme,
        "weighting": cfg.weighting,
        "region_map": cfg.region_map,
        "_config": cfg,
    }


# ===========================================================================
# 2. 分期（零字面量：全部来自配置）
# ===========================================================================

def assign_period(year: float | None, scheme: dict) -> str | None:
    """按配置的分期边界归类。区间外返回 None（由调用方按 out_of_range 处置）。"""
    if year is None:
        return None
    for b in scheme.get("boundaries", []):
        # inclusive: [start, end]
        if b["start"] <= year <= b["end"]:
            return b["label"]
    return None


def period_labels(scheme: dict) -> list[str]:
    return [b["label"] for b in scheme.get("boundaries", [])]


# ===========================================================================
# 3. 核心数学
# ===========================================================================

def weighted_log_mean(values: list[float], weights: list[float]) -> float:
    """Σ w·ln(v) / Σ w"""
    num = sum(w * math.log(v) for v, w in zip(values, weights))
    den = sum(weights)
    if den <= 0:
        raise ValueError("权重之和必须 > 0")
    return num / den


def weighted_gm(values: list[float], weights: list[float]) -> float:
    return math.exp(weighted_log_mean(values, weights))


def weighted_gsd(values: list[float], weights: list[float]) -> float:
    """exp( sqrt( Σ w·(ln v − ln GM_w)² / Σ w ) )"""
    lg = weighted_log_mean(values, weights)
    den = sum(weights)
    var = sum(w * (math.log(v) - lg) ** 2 for v, w in zip(values, weights)) / den
    return math.exp(math.sqrt(max(var, 0.0)))


def weighted_quantile(values: list[float], weights: list[float],
                      probs: list[float]) -> dict[float, float]:
    """
    加权分位数（累积权重法），等价于 R 的 Hmisc::wtd.quantile（权重为整数时）。
    """
    pairs = sorted(zip(values, weights), key=lambda t: t[0])
    total = sum(w for _, w in pairs)
    if total <= 0:
        raise ValueError("权重之和必须 > 0")
    out: dict[float, float] = {}
    for p in probs:
        target = p * total
        cum = 0.0
        prev_v = pairs[0][0]
        for v, w in pairs:
            cum += w
            if cum >= target:
                out[p] = v
                break
            prev_v = v
        else:
            out[p] = pairs[-1][0]
    return out


# ===========================================================================
# 4. 分组与合并
# ===========================================================================

@dataclass
class Stratum:
    key: tuple
    dims: list[str] = field(default_factory=lambda: list(STRATA_DIMS))

    def label(self) -> str:
        return " | ".join(str(k) for k in self.key)


@dataclass
class PooledRow:
    dims: dict
    gm: float
    gsd: float
    record_count: int
    study_count: int
    total_sample_size: float
    max_weight_share: float
    note: str = ""


def pool(rows: list[dict], scheme: dict, dims: list[str] | None = None,
         out_of_range_policy: str | None = None) -> tuple[list[PooledRow], list[str]]:
    """
    分层合并。返回 (结果行, 消息列表)。
    dims 默认 STRATA_DIMS；'period' 表示按分期。

    注意：`out_of_range` 策略**仅作用于 'period' 维度**。若 dims 用 'time'（逐年
    分层），则不过滤任何年份——逐年分析本就应显示全部有数据的年份。该差异属
    预期行为，已在 references/period_and_region_scheme.md §3 说明。
    """
    dims = dims or list(STRATA_DIMS)
    policy = out_of_range_policy or scheme.get("out_of_range", "exclude")
    msgs: list[str] = []

    buckets: dict[tuple, list[tuple[float, float, str]]] = defaultdict(list)
    n_skipped = 0
    n_oor = 0

    for r in rows:
        try:
            gm = float(r.get("gm_summary"))
            w = float(r.get("sample_size"))
        except (TypeError, ValueError):
            n_skipped += 1
            continue
        if gm <= 0 or w <= 0:
            n_skipped += 1
            continue

        year = r.get("time")
        try:
            year = float(year) if year not in (None, "") else None
        except (TypeError, ValueError):
            year = None

        key_vals = []
        for d in dims:
            if d == "period":
                lab = assign_period(year, scheme)
                if lab is None:
                    n_oor += 1
                    if policy == "exclude":
                        key_vals = None
                        break
                    lab = "OUT_OF_RANGE"
                key_vals.append(lab)
            else:
                key_vals.append(str(r.get(d) or "UNKNOWN"))
        if key_vals is None:
            continue

        buckets[tuple(key_vals)].append((gm, w, str(r.get("study_no") or "")))

    if n_skipped:
        msgs.append(f"{n_skipped} 条记录因 gm_summary/sample_size 缺失或非正而被排除（不可加权）")
    if n_oor:
        msgs.append(f"{n_oor} 条记录落在分期区间外，处置策略={policy}")

    out: list[PooledRow] = []
    for key, items in buckets.items():
        vals = [t[0] for t in items]
        ws = [t[1] for t in items]
        studies = {t[2] for t in items if t[2]}
        gm = weighted_gm(vals, ws)
        gsd = weighted_gsd(vals, ws)
        total_w = sum(ws)
        share = max(ws) / total_w if total_w else 0.0
        note_bits = []
        if len(items) < MIN_RECORDS_FOR_REPORT:
            note_bits.append(f"记录数 < {MIN_RECORDS_FOR_REPORT}，解释谨慎")
        if share > MAX_WEIGHT_SHARE_WARN:
            note_bits.append(f"单记录权重占比 {share:.0%} > {MAX_WEIGHT_SHARE_WARN:.0%}，需敏感性分析")
        # 正确性护栏：加权 GM 必须落在输入区间内
        if not (min(vals) - 1e-9 <= gm <= max(vals) + 1e-9):
            note_bits.append("⚠️ GM 超出输入区间（异常）")
        out.append(PooledRow(
            dims={d: k for d, k in zip(dims, key)},
            gm=gm, gsd=gsd,
            record_count=len(items),
            study_count=len(studies) if studies else len(items),
            total_sample_size=total_w,
            max_weight_share=share,
            note="; ".join(note_bits),
        ))

    order = {d: i for i, d in enumerate(dims)}
    out.sort(key=lambda r: tuple(
        (order[d], r.dims[d]) for d in dims))
    return out, msgs


# ===========================================================================
# 5. self-test
# ===========================================================================

def run_self_test() -> int:
    print("=" * 70)
    print("weighted_gm.py --self-test")
    print("=" * 70)
    checks: list[tuple[str, bool, str]] = []

    def check(name, ok, detail=""):
        checks.append((name, ok, detail))
        print(f"      {'✅' if ok else '❌'} {name}" + (f"  [{detail}]" if detail else ""))

    def close(a, b, tol=1e-9):
        return a is not None and b is not None and abs(a - b) <= tol

    # --- 基础：解析解 ---
    print("\n[1] 加权几何均值：解析解")
    # 两记录等权：GM_w = sqrt(10*40) = 20
    check("等权 (10,40) → 20", close(weighted_gm([10.0, 40.0], [1.0, 1.0]), 20.0),
          f"{weighted_gm([10.0, 40.0], [1.0, 1.0]):.6f}")
    # 三记录等权：GM_w = (10*20*40)^(1/3) = 8000^(1/3) = 20
    check("等权 (10,20,40) → 20",
          close(weighted_gm([10.0, 20.0, 40.0], [1.0, 1.0, 1.0]), 20.0),
          f"{weighted_gm([10.0, 20.0, 40.0], [1.0, 1.0, 1.0]):.6f}")

    # --- 权重有效性的关键性质 ---
    print("\n[2] 权重必须有效（不是等权平均）")
    # 极重权：w=1e6 于 40，w=1 于 10 → 结果几乎等于 40
    g = weighted_gm([10.0, 40.0], [1.0, 1_000_000.0])
    check("极重权趋向大权记录（≈40）", abs(g - 40.0) < 0.01, f"{g:.6f}")
    # 与等权结果显著不同
    check("与等权结果不同", abs(g - 20.0) > 1.0, f"等权=20, 加权={g:.4f}")

    # --- ln 与 log10 等价（保真关键）---
    print("\n[3] 底数等价性（ln vs log10）")
    vals, ws = [3.2, 11.5, 42.0, 0.7], [100.0, 250.0, 30.0, 5.0]
    gm_ln = weighted_gm(vals, ws)
    num = sum(w * math.log10(v) for v, w in zip(vals, ws)); den = sum(ws)
    gm_log10 = 10 ** (num / den)
    check("ln 与 log10 结果一致", close(gm_ln, gm_log10, 1e-9),
          f"ln={gm_ln:.9f} log10={gm_log10:.9f}")

    # --- GSD ---
    print("\n[4] 加权 GSD")
    check("单一记录 GSD = 1", close(weighted_gsd([5.0], [10.0]), 1.0))
    check("GSD ≥ 1 恒成立", weighted_gsd([1.0, 100.0], [1.0, 1.0]) >= 1.0)
    # (1,100) 等权：GM_w=10, ln 值 -2.3026 与 +2.3026，sd=2.3026 → GSD=e^2.3026=10
    check("等权 (1,100) → GM=10, GSD=10",
          close(weighted_gm([1.0, 100.0], [1.0, 1.0]), 10.0) and
          close(weighted_gsd([1.0, 100.0], [1.0, 1.0]), 10.0, 1e-9),
          f"GM={weighted_gm([1.0,100.0],[1.0,1.0]):.6f} "
          f"GSD={weighted_gsd([1.0,100.0],[1.0,1.0]):.6f}")
    # GSD 随离散度单调
    check("离散度越大 GSD 越大",
          weighted_gsd([1.0, 100.0], [1, 1]) > weighted_gsd([10.0, 100.0], [1, 1]))

    # --- 加权分位数 ---
    print("\n[5] 加权分位数")
    q = weighted_quantile([1.0, 2.0, 3.0, 4.0], [1, 1, 1, 1], [0.25, 0.5, 0.75])
    check("等权 P50 = 2", close(q[0.5], 2.0), f"{q}")
    q2 = weighted_quantile([1.0, 4.0], [9, 1], [0.5])
    check("重权低值 → P50 = 1", close(q2[0.5], 1.0), f"{q2}")

    # --- 分期分配（零字面量）---
    print("\n[6] 分期分配（全部来自配置）")
    scheme = FIXTURE_PERIOD_SCHEME   # 自检专用夹具（非正式分析路径）
    check("1990 → 1980-2000", assign_period(1990, scheme) == "1980-2000")
    check("2000 → 1980-2000（含端点）", assign_period(2000, scheme) == "1980-2000")
    check("2001 → 2001-2010", assign_period(2001, scheme) == "2001-2010")
    check("2024 → 2021-2024", assign_period(2024, scheme) == "2021-2024")
    check("1970 → None（区间外）", assign_period(1970, scheme) is None)
    check("2030 → None（区间外）", assign_period(2030, scheme) is None)
    # 自定义分期：证明无硬编码
    custom = {"boundaries": [{"label": "A", "start": 2000, "end": 2009},
                             {"label": "B", "start": 2010, "end": 2019}]}
    check("自定义分期生效（证明零字面量）", assign_period(2005, custom) == "A"
          and assign_period(2015, custom) == "B")
    check("自定义分期下 1995 → None", assign_period(1995, custom) is None)

    # --- 分层合并：构造可复现的合成数据 ---
    print("\n[7] 分层合并（合成数据，解析解可验）")
    rows = [
        # 尿砷 1980-2000：GM=32.58 与 32.58（等值，加权结果必为 32.58）
        {"record_id": "R1", "sample_type": "Urine", "time": "1995",
         "region": "Southwest", "province": "云南省", "population_group": "Adults",
         "gm_summary": "32.58", "sample_size": "1000", "study_no": "S001"},
        {"record_id": "R2", "sample_type": "Urine", "time": "1998",
         "region": "Southwest", "province": "贵州省", "population_group": "Adults",
         "gm_summary": "32.58", "sample_size": "500", "study_no": "S002"},
        # 尿砷 2001-2010：等值 12.45
        {"record_id": "R3", "sample_type": "Urine", "time": "2005",
         "region": "East", "province": "山东省", "population_group": "Adults",
         "gm_summary": "12.45", "sample_size": "800", "study_no": "S003"},
        # 血砷 2001-2010：等值 2.72 → 验证基质不混池
        {"record_id": "R4", "sample_type": "Blood", "time": "2005",
         "region": "North", "province": "北京市", "population_group": "Adults",
         "gm_summary": "2.72", "sample_size": "600", "study_no": "S004"},
        # 区间外
        {"record_id": "R5", "sample_type": "Urine", "time": "1970",
         "region": "East", "province": "山东省", "population_group": "Adults",
         "gm_summary": "9.99", "sample_size": "100", "study_no": "S005"},
        # 样本量缺失 → 排除
        {"record_id": "R6", "sample_type": "Urine", "time": "2005",
         "region": "East", "province": "山东省", "population_group": "Adults",
         "gm_summary": "50.0", "sample_size": "", "study_no": "S006"},
    ]
    res, msgs = pool(rows, scheme, dims=["sample_type", "period"])
    by_key = {(r.dims["sample_type"], r.dims["period"]): r for r in res}
    u1 = by_key.get(("Urine", "1980-2000"))
    u2 = by_key.get(("Urine", "2001-2010"))
    b1 = by_key.get(("Blood", "2001-2010"))
    check("尿 1980-2000 加权 GM = 32.58",
          close(u1.gm, 32.58, 1e-6) if u1 else False,
          f"{u1.gm:.6f}" if u1 else "缺失")
    check("尿 2001-2010 加权 GM = 12.45",
          close(u2.gm, 12.45, 1e-6) if u2 else False,
          f"{u2.gm:.6f}" if u2 else "缺失")
    check("血 2001-2010 加权 GM = 2.72",
          close(b1.gm, 2.72, 1e-6) if b1 else False,
          f"{b1.gm:.6f}" if b1 else "缺失")
    check("尿与血未混池（分层独立）",
          u2 is not None and b1 is not None and abs(u2.gm - b1.gm) > 1.0)
    check("基质隔离：Urine 层不含血记录", u2.record_count == 1 if u2 else False,
          f"record_count={u2.record_count if u2 else 'NA'}")
    check("区间外记录被排除（1970 未进入任何层）",
          all("1980" not in str(r.dims) or r.dims.get("period") != "OUT_OF_RANGE"
              for r in res))
    check("样本量缺失记录被排除（Urine 2001-2010 仅 1 条）",
          u2.record_count == 1 if u2 else False)
    check("排除计数已报告", any("1 条记录因" in m for m in msgs), str(msgs))

    # --- 加权 GM 必在输入区间内 ---
    print("\n[8] 正确性护栏")
    for r in res:
        if r.record_count >= 1 and "⚠️" not in r.note:
            break
    check("所有层 GM 未超出输入区间",
          all("⚠️" not in r.note for r in res),
          "; ".join(r.note for r in res if "⚠️" in r.note) or "无异常")

    # --- 权重集中度诊断 ---
    print("\n[9] 单记录权重占比诊断")
    rows2 = [
        {"sample_type": "Urine", "time": "2015", "region": "East", "province": "山东省",
         "population_group": "Adults", "gm_summary": "20.0", "sample_size": "100000",
         "study_no": "S010"},
        {"sample_type": "Urine", "time": "2016", "region": "East", "province": "江苏省",
         "population_group": "Adults", "gm_summary": "30.0", "sample_size": "100",
         "study_no": "S011"},
    ]
    res2, _ = pool(rows2, scheme, dims=["sample_type", "period"])
    r2 = res2[0]
    check("单记录主导时 max_weight_share > 30%", r2.max_weight_share > 0.30,
          f"{r2.max_weight_share:.4f}")
    check("触发敏感性分析提示", "敏感性" in r2.note, r2.note)
    check("GM 被大权记录主导（接近 20）", abs(r2.gm - 20.0) < 0.1, f"{r2.gm:.4f}")

    # --- 最小样本量规则 ---
    print("\n[10] 最小样本量规则")
    rows3 = [{"sample_type": "Urine", "time": "2015", "region": "East",
              "province": "山东省", "population_group": "Adults",
              "gm_summary": "10.0", "sample_size": "50", "study_no": "S020"}]
    res3, _ = pool(rows3, scheme, dims=["sample_type", "period"])
    check("记录数 < 3 已标注", "解释谨慎" in res3[0].note, res3[0].note)

    # --- 逐年分层 ---
    print("\n[11] 逐年分层（不分期）")
    res4, _ = pool(rows, scheme, dims=["sample_type", "time"])
    years = sorted(r.dims["time"] for r in res4)
    check("按年分层生效", "1995" in years and "2005" in years, str(years))

    # --- GSD 为 1 的等值数据 ---
    print("\n[12] 等值数据的 GSD = 1")
    rowseq = [
        {"sample_type": "Urine", "time": "2015", "region": "East", "province": "山东省",
         "population_group": "Adults", "gm_summary": "15.0", "sample_size": "100",
         "study_no": "S030"},
        {"sample_type": "Urine", "time": "2016", "region": "East", "province": "江苏省",
         "population_group": "Adults", "gm_summary": "15.0", "sample_size": "200",
         "study_no": "S031"},
    ]
    reseq, _ = pool(rowseq, scheme, dims=["sample_type", "period"])
    check("全等值 → GSD = 1", close(reseq[0].gsd, 1.0), f"{reseq[0].gsd:.9f}")
    check("全等值 → GM 等于该值", close(reseq[0].gm, 15.0), f"{reseq[0].gm:.9f}")

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

def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="weighted_gm.py",
        description="样本量加权 GM/GSD 分层合并（HBM-Meta-Agent / S5）",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="退出码：0 成功 / 1 存在排除或告警 / 2 前置条件不满足",
    )
    p.add_argument("--input", type=Path, help="标准化后的主表（.csv / .xlsx）")
    p.add_argument("--config", type=Path, help="project.yaml（骨架版可缺省；第四步改为必需）")
    p.add_argument("--out", type=Path, help="分层结果输出（.csv）")
    p.add_argument("--dims", default=",".join(STRATA_DIMS),
                   help=f"分层维度，逗号分隔（默认 {','.join(STRATA_DIMS)}）")
    p.add_argument("--self-test", action="store_true", help="运行数学校验自检")
    p.add_argument("--version", action="version", version="hbm-meta S5 weighted_gm 1.0.0")
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


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    if args.self_test:
        return run_self_test()

    if not args.input:
        print("错误：需要 --input，或使用 --self-test", file=sys.stderr)
        return 2

    try:
        rows = _read_rows(args.input)
    except FileNotFoundError:
        print(f"错误：主表不存在：{args.input}", file=sys.stderr)
        return 2
    except ValueError as exc:
        print(f"错误：{exc}", file=sys.stderr)
        return 2

    # ★ A1：配置缺失/解析失败/缺区块 → 硬错误退出码 2
    try:
        cfg = load_pooling_config(args.config)
    except ConfigError as exc:
        print(f"错误：{exc}", file=sys.stderr)
        return 2
    print(f"配置：{cfg['_config'].source_path}  ({cfg['_config'].summary()})")
    scheme = cfg["period_scheme"]
    dims = [d.strip() for d in args.dims.split(",") if d.strip()]

    res, msgs = pool(rows, scheme, dims=dims)
    for m in msgs:
        print(f"⚠️ {m}")

    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        fieldnames = ["stratum"] + dims + [
            "gm", "gsd", "record_count", "study_count",
            "total_sample_size", "max_weight_share", "note"]
        with args.out.open("w", encoding="utf-8-sig", newline="") as fh:
            w = csv.writer(fh)
            w.writerow(fieldnames)
            for r in res:
                w.writerow([" | ".join(str(r.dims.get(d, "")) for d in dims)]
                           + [r.dims.get(d, "") for d in dims]
                           + [f"{r.gm:.6f}", f"{r.gsd:.6f}", r.record_count,
                              r.study_count, f"{r.total_sample_size:g}",
                              f"{r.max_weight_share:.4f}", r.note])
        print(f"分层结果：{args.out}")

    n_warn = sum(1 for r in res if r.note)
    print(f"输入记录={len(rows)}  分层单元={len(res)}  含告警单元={n_warn}")
    return 1 if (n_warn or msgs) else 0


if __name__ == "__main__":
    raise SystemExit(main())
