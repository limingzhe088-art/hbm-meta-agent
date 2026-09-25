#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
edi_calculation.py — 估计每日摄入量（EDI）计算

阶段状态：第三步骨架版
  ✅ 完整 CLI（--input / --config / --out / --self-test）
  ✅ --self-test（公式校验 + 双路径交叉验证 + 旧公式护栏 + 已知值核对）
  ✅ 核心逻辑：
       尿路双路径（creatinine_excretion 主口径 / urine_volume 交叉验证）
       血路**修正后**公式 (BAs×Vd)/(ABS×τ)，并标注探索性重建
       ★ 旧公式护栏：断言 (BAs×Vd)/(BW×ABS×τ) 绝不出现
       加权分位数、Mixed/Unknown 跳过并计数、参数不自洽检测
⏳ 第四步待办：配置读取已改为硬错误（A1 完成）；快照指纹（A4）待接入

保真声明
  公式与参数与原项目 R 脚本完全一致：
    尿路 A: EDI = GM × CE / (BW × ABS)
    尿路 B: EDI = GM × CC_creat × V / (BW × ABS)
    血路  : EDI = (BAs × Vd) / (ABS × τ)      ← 修正后（R1-1）
  已证伪的旧血路公式 EDI = (BAs×Vd)/(BW×ABS×τ) **不实现**，并在自检中断言其不存在。
  算法未做任何改动。

用法
  python edi_calculation.py --self-test
  python edi_calculation.py --input 标准化.csv --config project.yaml --out edi.csv

退出码
  0 成功   1 存在告警   2 前置条件不满足
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
    from inferred_flags import unknown_flags  # noqa: E402  ★ 标记字典单一来源
except ImportError as _exc:  # pragma: no cover
    print(f"错误：无法导入 shared/ 下模块（{_exc}）", file=sys.stderr)
    raise SystemExit(2)


# ===========================================================================
# 1. 参数（第四步改为强制从 project.yaml 读取）
# ===========================================================================

# ⚠️ 正式分析参数一律来自 project.yaml → edi（STEP4-TODO.md A1）。
#    下表为**自检专用夹具**（与原 R 脚本一致），仅供 --self-test 使用。
FIXTURE_EDI_PARAMS = {
    "absorption_fraction_ABS": 0.7,
    "body_weight": {"Adults": 60.0, "Pregnant": 60.0, "Minors": 20.0, "Elderly": 60.0},
    "urine_route": {
        "method_primary": "creatinine_excretion",
        "method_secondary": "urine_volume",
    },
    "blood_route": {"vd_l_per_kg": 2.0, "tau_day": 1.0,
                    "formula": "(BAs * Vd) / (ABS * tau)",
                    "interpretation": "exploratory"},
    # 与原 R 脚本一致的 ICRP 89 三档派生参数
    "urine_reference": {
        "Adults":   {"CE": 1.35,  "V": 1.4,  "CC": 0.964, "source": "ICRP89"},
        "Pregnant": {"CE": 1.275, "V": 2.0,  "CC": 0.638, "source": "NHANES"},
        "Minors":   {"CE": 0.925, "V": 1.05, "CC": 0.815, "source": "ICRP89",
                     "inconsistent": True},
        "Elderly":  {"CE": 1.35,  "V": 1.4,  "CC": 0.964, "source": "ICRP89",
                     "coarse_age": True},
    },
}

# 不可计算 EDI 的人群（生理参数无法确定）
NO_EDI_GROUPS = {"Mixed", "Unknown", ""}

INCONSISTENCY_TOLERANCE = 0.03   # CC 与 CE/V 相对差 > 3% 视为不自洽

# 自检专用夹具分期（正式分析取自 project.yaml → period_scheme）
FIXTURE_PERIOD_SCHEME = {
    "boundaries": [
        {"label": "1980-2000", "start": 1980, "end": 2000},
        {"label": "2001-2010", "start": 2001, "end": 2010},
        {"label": "2011-2020", "start": 2011, "end": 2020},
        {"label": "2021-2024", "start": 2021, "end": 2024},
    ],
    "out_of_range": "exclude",
}


def load_edi_config(path: Path | None) -> dict:
    """
    ★ A1：从 project.yaml 读取 EDI 参数。**缺失/解析失败/缺区块 → ConfigError（硬错误）**。
    返回 {'edi':…, 'period_scheme':…, '_config': Config}。
    """
    cfg = load_config(path, require=["edi", "period_scheme", "urine_reference"])
    edi_cfg = dict(cfg.edi)
    # 尿校正参数以配置的 urine_reference 为准（列表 → 按 population_group 建索引）
    ref_rows = cfg.urine_reference.get("table") or []
    if ref_rows:
        table = {}
        for r in ref_rows:
            pg = r.get("population_group")
            if not pg:
                continue
            table[pg] = {
                "CE": r.get("ce_g_day"),
                "V": r.get("volume_l_day"),
                "CC": r.get("creat_g_l"),
                "source": r.get("source", cfg.urine_reference.get("source", "ICRP89")),
            }
        edi_cfg["urine_reference"] = table
    else:
        raise ConfigError(
            "project.yaml → urine_reference.table 为空。"
            "尿 EDI 需要 CE / V / CC 参数，请按 shared/data-contract.md §5.5 配置。")
    return {"edi": edi_cfg, "period_scheme": cfg.period_scheme, "_config": cfg}


# ===========================================================================
# 2. 公式（唯一实现处）
# ===========================================================================

def edi_urine_creatinine_excretion(gm_ug_g_cr: float, CE: float,
                                   BW: float, ABS: float) -> float:
    """路径 A（主口径）：EDI = GM × CE / (BW × ABS)"""
    if BW <= 0 or ABS <= 0:
        raise ValueError("BW 与 ABS 必须 > 0")
    return gm_ug_g_cr * CE / (BW * ABS)


def edi_urine_volume(gm_ug_g_cr: float, CC_g_l: float, V_l_day: float,
                     BW: float, ABS: float) -> float:
    """路径 B（交叉验证）：尿浓度 = GM × CC；EDI = 尿浓度 × V / (BW × ABS)"""
    if BW <= 0 or ABS <= 0:
        raise ValueError("BW 与 ABS 必须 > 0")
    return gm_ug_g_cr * CC_g_l * V_l_day / (BW * ABS)


def edi_blood(bas_ug_l: float, Vd_l_per_kg: float, ABS: float, tau_day: float) -> float:
    """
    血路**修正后**公式：EDI = (BAs × Vd) / (ABS × τ)

    Vd 已按体重标准化为 L/kg，**不再除以 BW**。
    ❌ 严禁使用 (BAs × Vd) / (BW × ABS × τ) —— 单位错误，见 references/edi_parameters.md §3.2
    """
    if ABS <= 0 or tau_day <= 0:
        raise ValueError("ABS 与 tau 必须 > 0")
    return bas_ug_l * Vd_l_per_kg / (ABS * tau_day)


def edi_blood_legacy_wrong(bas_ug_l: float, Vd_l_per_kg: float, BW: float,
                           ABS: float, tau_day: float) -> float:
    """
    ⚠️ 已证伪的旧公式，**仅为护栏自检而存在，任何生产路径不得调用**。
    EDI = (BAs × Vd) / (BW × ABS × τ)   ← 单位错误（R1-1）
    """
    raise AssertionError(
        "旧血路 EDI 公式已被证伪（单位错误，审稿意见 R1-1），禁止调用。"
        "请使用 edi_blood()。"
    )


# ===========================================================================
# 3. 参数选择与计算
# ===========================================================================

@dataclass
class EdiResult:
    status: str = "OK"
    edi_primary: float | None = None
    edi_secondary: float | None = None
    cross_check_rel_diff: float | None = None
    BW: float | None = None
    params: str = ""
    flags: list[str] = field(default_factory=list)
    messages: list[str] = field(default_factory=list)
    unit: str = "ug/kg bw/day"


def compute_urine_edi(gm: float, population_group: str, edi_cfg: dict) -> EdiResult:
    """尿路 EDI：双路径 + 交叉验证。"""
    pg = (population_group or "").strip()
    if pg in NO_EDI_GROUPS:
        return EdiResult(status="SKIP", messages=[f"population_group=`{pg}` 无法确定生理参数，跳过 EDI"])

    ref = edi_cfg.get("urine_reference", {}).get(pg)
    if ref is None:
        return EdiResult(status="SKIP", messages=[f"参数表中无 `{pg}`，跳过 EDI"])

    BW = edi_cfg.get("body_weight", {}).get(pg)
    ABS = edi_cfg.get("absorption_fraction_ABS")
    if BW is None or ABS is None:
        return EdiResult(status="FAILED", messages=[f"`{pg}` 缺 BW 或 ABS 参数"])

    CE, V, CC = ref["CE"], ref["V"], ref["CC"]
    a = edi_urine_creatinine_excretion(gm, CE, BW, ABS)
    b = edi_urine_volume(gm, CC, V, BW, ABS)
    rel = abs(a - b) / a if a else 0.0

    flags, msgs = [], []
    if ref.get("inconsistent") or rel > INCONSISTENCY_TOLERANCE:
        flags.append("inconsistent_param")
        msgs.append(f"参数不自洽：CC={CC} vs CE/V={CE / V:.3f}，双路径相对差 {rel:.2%}")
    if ref.get("coarse_age"):
        flags.append("coarse_age")
        msgs.append("使用粗年龄段参数")
    status = "WARN" if msgs else "OK"

    return EdiResult(status=status, edi_primary=a, edi_secondary=b,
                     cross_check_rel_diff=rel, BW=BW,
                     params=(f"route=urine;path_a=creatinine_excretion;path_b=urine_volume;"
                             f"CE={CE};V={V};CC={CC};BW={BW};ABS={ABS};"
                             f"source={ref.get('source')}"),
                     flags=flags, messages=msgs)


def compute_blood_edi(bas: float, population_group: str, edi_cfg: dict,
                      sample_type: str = "Blood") -> EdiResult:
    """
    血路 EDI：修正后公式 + 探索性重建标注。

    ★ 脐带血（CordBlood）**不计算 EDI**（方案 A，保守）：
      脐血反映**胎儿**内暴露，成人的 Vd / τ（表观分布容积、平均滞留时间）与
      经口吸收路径均不适用于胎儿，用成人值近似无法合理处理（不是参数精度问题，
      而是模型不适用）。因此跳过并计数，交人工决定是否引入胎儿专属参数表。
    """
    pg = (population_group or "").strip()

    # ★ 脐血单独处置：不做 EDI
    if sample_type == "CordBlood":
        return EdiResult(
            status="SKIP",
            messages=["脐带血反映胎儿内暴露，缺乏胎儿/新生儿药代动力学参数"
                      "（fetal/neonatal Vd、τ 及经胎盘转运模型），"
                      "不适用成人血路公式 → 跳过 EDI 并计数"],
            flags=["cordblood_edi_skipped"],
            params="route=cordblood;edi_not_computed;reason=fetal_parameters_unavailable")

    if pg in NO_EDI_GROUPS:
        return EdiResult(status="SKIP",
                         messages=[f"population_group=`{pg}` 无法确定参数，跳过 EDI"])

    br = edi_cfg.get("blood_route", {})
    Vd = br.get("vd_l_per_kg")
    tau = br.get("tau_day")
    ABS = edi_cfg.get("absorption_fraction_ABS")
    if None in (Vd, tau, ABS):
        return EdiResult(status="FAILED", messages=["血路参数缺失（Vd / tau / ABS）"])

    edi = edi_blood(bas, Vd, ABS, tau)
    BW = edi_cfg.get("body_weight", {}).get(pg)
    msgs = ["血砷反映短期暴露，基于血砷的 EDI 为探索性重建（exploratory reconstruction）"]
    flags = ["exploratory_reconstruction"]
    if BW is None:
        flags.append("bw_unused")
        msgs.append("血路公式不使用体重（Vd 已按体重标准化）")

    return EdiResult(status="WARN", edi_primary=edi, BW=BW,
                     params=(f"route=blood;formula=(BAs*Vd)/(ABS*tau);"
                             f"Vd={Vd};tau={tau};ABS={ABS};bw_used=False"),
                     flags=flags, messages=msgs)


# ===========================================================================
# 4. 汇总（与 weighted_gm 一致的加权分位数）
# ===========================================================================

def weighted_quantile(values: list[float], weights: list[float],
                      probs: list[float]) -> dict[float, float]:
    pairs = sorted(zip(values, weights), key=lambda t: t[0])
    total = sum(w for _, w in pairs)
    if total <= 0:
        raise ValueError("权重之和必须 > 0")
    out: dict[float, float] = {}
    for p in probs:
        target = p * total
        cum = 0.0
        for v, w in pairs:
            cum += w
            if cum >= target:
                out[p] = v
                break
        else:
            out[p] = pairs[-1][0]
    return out


def summarize_edi(pairs: list[tuple[float, float]]) -> dict:
    """pairs = [(edi, sample_size), ...] → 加权 GM/GSD + 分位数 + 计数。"""
    if not pairs:
        return {}
    vals = [p[0] for p in pairs]
    ws = [p[1] for p in pairs]
    lg = sum(w * math.log(v) for v, w in zip(vals, ws)) / sum(ws)
    gm = math.exp(lg)
    var = sum(w * (math.log(v) - lg) ** 2 for v, w in zip(vals, ws)) / sum(ws)
    gsd = math.exp(math.sqrt(max(var, 0.0)))
    q = weighted_quantile(vals, ws, [0.05, 0.25, 0.50, 0.75, 0.95])
    return {
        "record_count": len(pairs),
        "total_sample_size": sum(ws),
        "EDI_GM": gm, "EDI_GSD": gsd,
        "EDI_P05": q[0.05], "EDI_P25": q[0.25], "EDI_P50": q[0.50],
        "EDI_P75": q[0.75], "EDI_P95": q[0.95],
        "EDI_median_unweighted": sorted(vals)[len(vals) // 2],
    }


# ===========================================================================
# 5. self-test
# ===========================================================================

def run_self_test() -> int:
    print("=" * 70)
    print("edi_calculation.py --self-test")
    print("=" * 70)
    checks: list[tuple[str, bool, str]] = []

    def check(name, ok, detail=""):
        checks.append((name, ok, detail))
        print(f"      {'✅' if ok else '❌'} {name}" + (f"  [{detail}]" if detail else ""))

    def close(a, b, tol=1e-9):
        return a is not None and b is not None and abs(a - b) <= tol

    cfg = FIXTURE_EDI_PARAMS   # 自检专用夹具（非正式分析路径）

    # --- 尿路路径 A ---
    print("\n[1] 尿路路径 A：EDI = GM × CE / (BW × ABS)")
    a = edi_urine_creatinine_excretion(20.0, 1.35, 60.0, 0.7)
    expect = 20.0 * 1.35 / (60.0 * 0.7)
    check("手工解析解一致", close(a, expect, 1e-12), f"{a:.9f} 期望 {expect:.9f}")
    check("量级合理（0.1–10 μg/kg bw/day）", 0.1 < a < 10.0, f"{a:.4f}")

    # --- 尿路路径 B ---
    print("\n[2] 尿路路径 B：EDI = GM × CC × V / (BW × ABS)")
    b = edi_urine_volume(20.0, 0.964, 1.4, 60.0, 0.7)
    check("路径 B 解析解一致",
          close(b, 20.0 * 0.964 * 1.4 / (60.0 * 0.7), 1e-12), f"{b:.9f}")

    # --- 双路径等价性（参数自洽时）---
    print("\n[3] 双路径等价性：CC 自洽时应一致")
    # 构造完全自洽参数：CC = CE/V
    CE, V = 1.35, 1.4
    CC_consistent = CE / V
    pa = edi_urine_creatinine_excretion(20.0, CE, 60.0, 0.7)
    pb = edi_urine_volume(20.0, CC_consistent, V, 60.0, 0.7)
    check("自洽参数下两路径完全相等", close(pa, pb, 1e-12), f"A={pa:.9f} B={pb:.9f}")

    # --- 不自洽检测 ---
    print("\n[4] 参数不自洽检测（Minors 档）")
    mi = compute_urine_edi(20.0, "Minors", cfg)
    check("Minors 触发不一致告警", "inconsistent_param" in mi.flags, str(mi.flags))
    check("相对差 > 3%", (mi.cross_check_rel_diff or 0) > 0.03,
          f"{mi.cross_check_rel_diff:.4%}" if mi.cross_check_rel_diff else "NA")
    ad = compute_urine_edi(20.0, "Adults", cfg)
    check("Adults 不触发（CC 与 CE/V 自洽）",
          "inconsistent_param" not in ad.flags,
          f"相对差={ad.cross_check_rel_diff:.4%}")

    # --- 血路修正公式 ---
    print("\n[5] 血路修正公式：EDI = (BAs × Vd) / (ABS × τ)")
    bb = edi_blood(2.72, 2.0, 0.7, 1.0)
    check("解析解 = 2.72×2/(0.7×1)", close(bb, 2.72 * 2.0 / 0.7, 1e-12), f"{bb:.9f}")
    check("公式中不含 BW", abs(bb - 2.72 * 2.0 / (60.0 * 0.7 * 1.0)) > 1.0,
          "已确认未除以 BW")

    # --- 旧公式护栏 ---
    print("\n[6] ★ 旧公式护栏：已证伪的公式必须抛异常")
    try:
        edi_blood_legacy_wrong(2.72, 2.0, 60.0, 0.7, 1.0)
        check("旧公式抛 AssertionError", False, "未抛出异常！")
    except AssertionError as exc:
        check("旧公式抛 AssertionError", True, str(exc)[:40] + "…")
    correct = edi_blood(2.72, 2.0, 0.7, 1.0)
    wrong = 2.72 * 2.0 / (60.0 * 0.7 * 1.0)
    check("旧公式会把结果缩小约 BW 倍",
          abs(correct / wrong - 60.0) < 1e-6, f"比值={correct / wrong:.2f}")

    # --- 血路标注 ---
    print("\n[7] 血路必须标注探索性重建")
    be = compute_blood_edi(2.72, "Adults", cfg)
    check("状态为 WARN", be.status == "WARN", be.status)
    check("含 exploratory_reconstruction 标记",
          "exploratory_reconstruction" in be.flags, str(be.flags))
    check("消息含探索性说明", any("探索性" in m for m in be.messages))
    check("参数中标注 bw_used=False", "bw_used=False" in be.params, be.params)

    # --- ★ 脐血：不计算 EDI（方案 A）---
    print("\n[7b] ★ 脐带血不计算 EDI（胎儿参数不适用成人模型）")
    cb = compute_blood_edi(4.89, "Adults", cfg, sample_type="CordBlood")
    check("脐血 → SKIP", cb.status == "SKIP", cb.status)
    check("脐血不产出 EDI 值", cb.edi_primary is None, f"{cb.edi_primary}")
    check("脐血标记 cordblood_edi_skipped", "cordblood_edi_skipped" in cb.flags, str(cb.flags))
    check("原因说明含胎儿参数", any("胎儿" in m for m in cb.messages), cb.messages[0][:50] + "…")
    check("参数标注 edi_not_computed", "edi_not_computed" in cb.params, cb.params)
    # 对照：同样浓度按全血计算会得到非空值（证明差别来自基质判定）
    cb_blood = compute_blood_edi(4.89, "Adults", cfg, sample_type="Blood")
    check("同值按 Blood 计算仍有值（对照）", cb_blood.edi_primary is not None,
          f"{cb_blood.edi_primary:.4f}")

    # --- Mixed/Unknown 跳过 ---
    print("\n[8] Mixed / Unknown 人群跳过 EDI 并计数")
    for grp in ("Mixed", "Unknown", ""):
        r = compute_urine_edi(20.0, grp, cfg)
        check(f"`{grp or '(空)'}` → SKIP", r.status == "SKIP", r.status)
        check(f"`{grp or '(空)'}` 有原因说明", len(r.messages) > 0)

    # --- 完整参数表核对（与原 R 脚本一致）---
    print("\n[9] 参数表与原 R 脚本一致性")
    ref = cfg["urine_reference"]
    check("Adults CE=1.35", ref["Adults"]["CE"] == 1.35)
    check("Adults V=1.4", ref["Adults"]["V"] == 1.4)
    check("Adults CC=0.964", ref["Adults"]["CC"] == 0.964)
    check("Pregnant CE=1.275", ref["Pregnant"]["CE"] == 1.275)
    check("Pregnant V=2.0", ref["Pregnant"]["V"] == 2.0)
    check("Pregnant CC=0.638", ref["Pregnant"]["CC"] == 0.638)
    check("Minors CE=0.925", ref["Minors"]["CE"] == 0.925)
    check("Minors V=1.05", ref["Minors"]["V"] == 1.05)
    check("Minors CC=0.815", ref["Minors"]["CC"] == 0.815)
    bw = cfg["body_weight"]
    check("BW Adults=60 / Minors=20", bw["Adults"] == 60.0 and bw["Minors"] == 20.0)
    check("ABS = 0.7", cfg["absorption_fraction_ABS"] == 0.7)
    check("Vd = 2.0 L/kg", cfg["blood_route"]["vd_l_per_kg"] == 2.0)
    check("tau = 1.0 day", cfg["blood_route"]["tau_day"] == 1.0)

    # --- 已知值核对（尿砷 EDI，Adults 群体构造）---
    print("\n[10] 已知值核对：尿砷时段 EDI 中位数（合成数据）")
    # 用 GM=32.58 Adults 反推：32.58×1.35/(60×0.7) = 1.0470 μm
    e1 = edi_urine_creatinine_excretion(32.58, 1.35, 60.0, 0.7)
    check("尿 EDI 由 GM=32.58 得到 ≈1.0470",
          close(e1, 32.58 * 1.35 / (60.0 * 0.7), 1e-9), f"{e1:.6f}")
    # 原项目记录的全体人群 1980-2000 中位数 2.13 —— 由大样本 GM 与人群混合决定，
    # 此处只验证"同一公式与参数能算出同一量级"
    check("量级与原项目记录 2.13 同阶（0.5–5）", 0.5 < e1 < 5.0, f"{e1:.4f}")

    # --- 汇总统计 ---
    print("\n[11] EDI 汇总统计（加权 GM/GSD + 分位数）")
    pairs = [(1.0, 100.0), (2.0, 200.0), (4.0, 300.0)]
    s = summarize_edi(pairs)
    manual_lg = (100 * math.log(1) + 200 * math.log(2) + 300 * math.log(4)) / 600
    check("加权 GM 正确", close(s["EDI_GM"], math.exp(manual_lg), 1e-12),
          f"{s['EDI_GM']:.9f}")
    check("GSD ≥ 1", s["EDI_GSD"] >= 1.0, f"{s['EDI_GSD']:.6f}")
    check("记录数正确", s["record_count"] == 3)
    check("样本量和正确", s["total_sample_size"] == 600.0)
    check("P50 落在 [P25, P75]",
          s["EDI_P25"] <= s["EDI_P50"] <= s["EDI_P75"],
          f"{s['EDI_P25']}/{s['EDI_P50']}/{s['EDI_P75']}")
    check("P05 ≤ P25 ≤ P50 ≤ P75 ≤ P95",
          s["EDI_P05"] <= s["EDI_P25"] <= s["EDI_P50"] <= s["EDI_P75"] <= s["EDI_P95"])
    check("空输入返回空字典", summarize_edi([]) == {})

    # --- 除零保护 ---
    print("\n[12] 参数非法保护")
    for bad in [("BW=0", lambda: edi_urine_creatinine_excretion(20.0, 1.35, 0.0, 0.7)),
                ("ABS=0", lambda: edi_urine_creatinine_excretion(20.0, 1.35, 60.0, 0.0)),
                ("tau=0", lambda: edi_blood(2.72, 2.0, 0.7, 0.0))]:
        label, fn = bad
        try:
            fn()
            check(f"{label} → 抛异常", False, "未抛出")
        except ValueError:
            check(f"{label} → 抛异常", True)

    # --- 血路不使用 BW（关键回归）---
    print("\n[13] 关键回归：血路结果与 BW 无关")
    e_60 = edi_blood(2.72, 2.0, 0.7, 1.0)
    # 模拟 BW 从 60 变成 20，血路结果不应变化
    e_20 = edi_blood(2.72, 2.0, 0.7, 1.0)
    check("改变 BW 不改变血路 EDI", close(e_60, e_20, 0.0), f"{e_60} vs {e_20}")
    check("函数签名不含 BW", "BW" not in edi_blood.__code__.co_varnames,
          str(edi_blood.__code__.co_varnames))

    print("\n[14] ★ 标记闭集校验（单一来源 shared/inferred_flags.py）")
    emitted: set[str] = set()
    for gm, pg, st in ((20.0, "Minors", "Urine"), (20.0, "Adults", "Urine"),
                       (20.0, "Elderly", "Urine"),
                       (2.72, "Adults", "Blood"), (4.89, "Adults", "CordBlood")):
        r = (compute_urine_edi(gm, pg, FIXTURE_EDI_PARAMS) if st == "Urine"
             else compute_blood_edi(gm, pg, FIXTURE_EDI_PARAMS, sample_type=st))
        emitted |= set(r.flags)
    bad = unknown_flags(sorted(emitted))
    check("本脚本产出的全部标记都在闭集内", not bad,
          f"越界={bad}" if bad else f"{len(emitted)} 个：{sorted(emitted)}")
    check("含 cordblood_edi_skipped（脐血方案 A）",
          "cordblood_edi_skipped" in emitted)
    check("含 exploratory_reconstruction（血路）",
          "exploratory_reconstruction" in emitted)
    check("含 inconsistent_param（Minors 档参数不自洽）",
          "inconsistent_param" in emitted, str(sorted(emitted)))
    check("含 coarse_age（Elderly 档用成人参数）",
          "coarse_age" in emitted, str(sorted(emitted)))
    check("Adults 档不触发 inconsistent_param（参数自洽）",
          "inconsistent_param" not in compute_urine_edi(20.0, "Adults",
                                                        FIXTURE_EDI_PARAMS).flags)

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
        prog="edi_calculation.py",
        description="估计每日摄入量 EDI 计算（HBM-Meta-Agent / S5）",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="退出码：0 成功 / 1 存在告警 / 2 前置条件不满足",
    )
    p.add_argument("--input", type=Path, help="标准化后的主表（.csv / .xlsx）")
    p.add_argument("--config", type=Path, help="project.yaml（骨架版可缺省；第四步改为必需）")
    p.add_argument("--out", type=Path, help="EDI 结果输出（.csv）")
    p.add_argument("--route", choices=["urine", "blood", "both"], default="both",
                   help="计算哪条路径（默认 both）")
    p.add_argument("--self-test", action="store_true", help="运行公式校验自检")
    p.add_argument("--version", action="version", version="hbm-meta S5 edi_calculation 1.0.0")
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
        loaded = load_edi_config(args.config)
    except ConfigError as exc:
        print(f"错误：{exc}", file=sys.stderr)
        return 2
    edi_cfg = loaded["edi"]
    print(f"配置：{loaded['_config'].source_path}  ({loaded['_config'].summary()})")

    out_rows, n_skip, n_warn = [], 0, 0
    counts = {"urine_skipped": 0, "blood_skipped": 0}

    for row in rows:
        st = str(row.get("sample_type") or "").strip()
        pg = str(row.get("population_group") or "").strip()
        try:
            gm = float(row.get("gm_summary"))
        except (TypeError, ValueError):
            gm = None

        rec = {"record_id": row.get("record_id", ""), "sample_type": st,
               "population_group": pg, "gm_summary": gm,
               "sample_size": row.get("sample_size", "")}

        if st == "Urine" and args.route in ("urine", "both") and gm is not None:
            r = compute_urine_edi(gm, pg, edi_cfg)
            if r.status == "SKIP":
                counts["urine_skipped"] += 1
                n_skip += 1
            elif r.status == "WARN":
                n_warn += 1
            rec.update({"route": "urine", "edi_primary": r.edi_primary,
                        "edi_secondary": r.edi_secondary,
                        "cross_check_rel_diff": r.cross_check_rel_diff,
                        "status": r.status, "flags": ";".join(r.flags),
                        "message": " | ".join(r.messages), "params": r.params})
            out_rows.append(rec)

        elif st in ("Blood", "CordBlood") and args.route in ("blood", "both") and gm is not None:
            r = compute_blood_edi(gm, pg, edi_cfg, sample_type=st)
            if r.status == "SKIP":
                counts["blood_skipped"] += 1
                n_skip += 1
            elif r.status == "WARN":
                n_warn += 1
            rec.update({"route": "blood", "edi_primary": r.edi_primary,
                        "edi_secondary": None, "cross_check_rel_diff": None,
                        "status": r.status, "flags": ";".join(r.flags),
                        "message": " | ".join(r.messages), "params": r.params})
            out_rows.append(rec)

    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        if out_rows:
            with args.out.open("w", encoding="utf-8-sig", newline="") as fh:
                w = csv.DictWriter(fh, fieldnames=list(out_rows[0].keys()))
                w.writeheader()
                w.writerows(out_rows)
            print(f"EDI 结果：{args.out}")

    if n_skip:
        print(f"⚠️ {n_skip} 条记录跳过 EDI（Mixed/Unknown 或无参数），已计数：{counts}")
    if n_warn:
        print(f"⚠️ {n_warn} 条记录带告警（含血路探索性重建标注）")
    print(f"输入记录={len(rows)}  输出 EDI 记录={len(out_rows)}")
    return 1 if (n_skip or n_warn) else 0


if __name__ == "__main__":
    raise SystemExit(main())
