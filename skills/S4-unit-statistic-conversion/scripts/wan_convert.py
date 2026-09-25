#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
wan_convert.py — Wan et al. (2014) 公式 S1–S7 实现 + 统计量 → GM 转换

来源：Wan X, Wang W, Liu J, Tong T. BMC Med Res Methodol 2014;14:135.
      doi:10.1186/1471-2288-14-135

阶段状态：第三步骨架版
  ✅ 完整 CLI（--input / --config / --out）
  ✅ --self-test（S1–S7 逐条数学校验 + 边界情形）
  ✅ S1–S7 保真实现；GM/GSD 转换；AM_SD→GM 转换
  ✅ 退化保护（η ≤ 0、分位矛盾、n 缺失回退、GM 记录硬拒绝）
  ✅ inferred_flags 反推（按 unit_conversion_playbook.md §6.2 规则表）
  ⏳ 第四步待办：口径全部从 project.yaml 读取（禁止兜底）、输出带快照指纹、
     写 flags_derivation.csv 与 conversion_trace.csv、CI 集成

算法保真声明
  公式实现与原文一致，**未做任何算法改动**。
  唯一需明确的约定：S1–S5 原文以 ± 形式给出，本实现采用**减号变体**（conservative），
  并在输出中记录 wan_variant，使该选择可审计、可切换。

用法
  python wan_convert.py --self-test
  python wan_convert.py --input 主表.xlsx --config project.yaml --out 换算结果.csv

退出码
  0 成功（可能有 WARN）   1 存在 FAILED 记录   2 前置条件不满足
"""
from __future__ import annotations

import argparse
import math
import sys
from dataclasses import dataclass, field, asdict
from pathlib import Path

try:  # Windows 控制台 GBK 兼容
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except (AttributeError, ValueError):  # pragma: no cover
    pass

# ★ 唯一配置入口 + 标记字典单一来源（shared/）
_SHARED = Path(__file__).resolve().parent.parent.parent.parent / "shared"
if str(_SHARED) not in sys.path:
    sys.path.insert(0, str(_SHARED))
try:
    from inferred_flags import CLOSED_FLAG_SET, inferred_flag_count  # noqa: E402
    from project_config import ConfigError, load_config  # noqa: E402
except ImportError as _exc:  # pragma: no cover
    print(f"错误：无法导入 shared/ 下模块（{_exc}）", file=sys.stderr)
    raise SystemExit(2)


# ===========================================================================
# 1. 数学工具（按原文定义）
# ===========================================================================

def phi(x: float) -> float:
    """标准正态分布函数 Φ(x)。"""
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def phi_inv(p: float) -> float:
    """标准正态分位数 Φ⁻¹(p)，即 q(p)。"""
    if not (0.0 < p < 1.0):
        raise ValueError(f"phi_inv 定义域为 (0,1)，收到 {p}")
    # 用二分法求逆（无 scipy 依赖，精度 1e-12）
    lo, hi = -40.0, 40.0
    for _ in range(200):
        mid = 0.5 * (lo + hi)
        if phi(mid) < p:
            lo = mid
        else:
            hi = mid
        if hi - lo < 1e-14:
            break
    return 0.5 * (lo + hi)


# 原文常数（保留 4 位小数，与论文一致）
Q75 = 0.6745                      # q(0.75)
TWO_Q75 = 2.0 * Q75               # 1.3490
FOUR_Q75 = 4.0 * Q75              # 2.6980
TWO_Q75_SQRT2 = 2.0 * Q75 * math.sqrt(2.0)   # 1.9079

WAN_VARIANT_DEFAULT = "minus"     # 本工作包约定：减号变体
WAN_VARIANTS_IMPLEMENTED = {"minus"}   # ★ 目前仅实现减号变体（见下方说明）


def xi(n: int, variant: str = WAN_VARIANT_DEFAULT) -> float:
    """
    ξ = 2·Φ⁻¹((n − 0.375)/(n + 0.25))，返回**幅值**。

    ⚠️ **未实现的变体**：Wan 2014 的 S1–S5 以 `±` 形式给出，两个变体在 n 较小时的
    数值分支不同（论文中的 B、C、D 参数含 ±）。本实现只做减号变体；对 `plus`
    **显式抛错**，避免"配置了 plus 但结果与 minus 相同"的静默错误。
    若要启用 plus：需按论文附录展开 ± 分支并新增自检对照，届时把
    `WAN_VARIANTS_IMPLEMENTED` 加上 "plus"。
    """
    if variant not in WAN_VARIANTS_IMPLEMENTED:
        raise NotImplementedError(
            f"wan_variant=`{variant}` 尚未实现（当前仅支持 "
            f"{sorted(WAN_VARIANTS_IMPLEMENTED)}）。"
            "请把 project.yaml → standardization.wan_variant 改回已实现的变体，"
            "或先实现该变体（见 wan2014_formulas.md §3）。")
    if n is None or n <= 0:
        raise ValueError("ξ 需要正整数 n")
    p = (n - 0.375) / (n + 0.25)
    return abs(2.0 * phi_inv(p))


def eta(n: int, variant: str = WAN_VARIANT_DEFAULT) -> float:
    """
    η = 2·Φ⁻¹((n − 0.375)/(n + 0.25))，用于 S4/S5。
    与 ξ 同式；此处按论文对 IQR 法的记法命名。
    """
    return xi(n, variant)


# ===========================================================================
# 2. 结果容器
# ===========================================================================

@dataclass
class WanResult:
    status: str                 # OK / SKIP / FAILED / WARN
    formula: str = ""           # S1…S7
    sd: float | None = None
    center: float | None = None  # 中心值（Mean 或 Median）
    mean: float | None = None
    gm: float | None = None
    gsd: float | None = None
    path: str = ""              # conversion_path
    params: str = ""            # conversion_params
    flags: list[str] = field(default_factory=list)
    messages: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        d = asdict(self)
        d["flags"] = ";".join(self.flags)
        d["messages"] = " | ".join(self.messages)
        return d


def _fail(msg: str, path: str = "") -> WanResult:
    return WanResult(status="FAILED", path=path, messages=[msg])


def _ok(sd, center, formula, path, params, flags, warn_msgs=None) -> WanResult:
    status = "WARN" if warn_msgs else "OK"
    return WanResult(status=status, formula=formula, sd=sd, center=center,
                     mean=center, path=path, params=params,
                     flags=list(flags), messages=list(warn_msgs or []))


# ===========================================================================
# 3. 统计量 → GM 转换
# ===========================================================================

def mean_sd_to_gm(mean: float, sd: float) -> tuple[float, float]:
    """
    AM + SD → GM + GSD（对数正态假设）
        CV  = SD / AM
        σ   = sqrt(ln(1 + CV²))
        GM  = AM / exp(σ² / 2)
        GSD = exp(σ)
    """
    if mean is None or mean <= 0:
        raise ValueError("AM 必须 > 0")
    if sd is None or sd < 0:
        raise ValueError("SD 必须 ≥ 0")
    cv = sd / mean
    sigma2 = math.log(1.0 + cv * cv)
    gm = mean / math.exp(sigma2 / 2.0)
    gsd = math.exp(math.sqrt(sigma2))
    return gm, gsd


# ===========================================================================
# 4. Wan 公式 S1–S7（逐条保真）
# ===========================================================================

def wan_s1(n: int, minimum: float, maximum: float, mean: float,
           variant: str = WAN_VARIANT_DEFAULT) -> WanResult:
    """S1：n + min + max + Mean → SD = (max − min) / ξ"""
    if None in (n, minimum, maximum) or n <= 0:
        return _fail("S1 需要 n / min / max", "Min_Max→Wan_S1→GM")
    if maximum <= minimum:
        return _fail(f"max({maximum}) ≤ min({minimum})，数据可疑", "Min_Max→Wan_S1→GM")
    g = xi(n, variant)
    if g <= 0 or not math.isfinite(g):
        return _fail(f"ξ={g} 退化", "Min_Max→Wan_S1→GM")
    sd = (maximum - minimum) / g
    return _ok(sd, mean, "S1", "AM_Range→Wan_S1→GM",
               f"formula=S1;n={n};variant={variant};xi={g:.6f}",
               ["gm_from_mean_sd", "gm_from_range"])
    # 注：S1 用均值 + 范围，同时属于 mean_sd 与 range 两类推断


def wan_s2(n: int, minimum: float, maximum: float, median: float,
           variant: str = WAN_VARIANT_DEFAULT) -> WanResult:
    """S2：n + min + max + Median → SD = (max − min) / ξ"""
    if None in (n, minimum, maximum) or n <= 0:
        return _fail("S2 需要 n / min / max", "Median_Range→Wan_S2→GM")
    if maximum <= minimum:
        return _fail(f"max({maximum}) ≤ min({minimum})，数据可疑", "Median_Range→Wan_S2→GM")
    g = xi(n, variant)
    if g <= 0 or not math.isfinite(g):
        return _fail(f"ξ={g} 退化", "Median_Range→Wan_S2→GM")
    sd = (maximum - minimum) / g
    return _ok(sd, median, "S2", "Median_Range→Wan_S2→GM",
               f"formula=S2;n={n};variant={variant};xi={g:.6f}",
               ["gm_from_median", "gm_from_range"])


def wan_s3(minimum: float, maximum: float, mean: float | None = None,
           median: float | None = None) -> WanResult:
    """S3（缺 n）：SD = (max − min) / (2·q(0.75)·√2) = (max − min) / 1.9079"""
    if None in (minimum, maximum):
        return _fail("S3 需要 min / max", "Min_Max→Wan_S3→GM")
    if maximum <= minimum:
        return _fail(f"max({maximum}) ≤ min({minimum})，数据可疑", "Min_Max→Wan_S3→GM")
    center = mean if mean is not None else median
    if center is None:
        return _fail("S3 需要中心值（Mean 或 Median）", "Min_Max→Wan_S3→GM")
    sd = (maximum - minimum) / TWO_Q75_SQRT2
    warn = ["样本量缺失，已回退到 S3（精度下降）"]
    flags = ["gm_from_range", "no_n_fallback"]
    if mean is not None:
        flags.append("gm_from_mean_sd")
    else:
        flags.append("gm_from_median")
    return _ok(sd, center, "S3", "Min_Max→Wan_S3→GM",
               f"formula=S3;n=NA;denom={TWO_Q75_SQRT2:.4f}",
               flags, warn)


def wan_s4(n: int, q1: float, q3: float, median: float,
           variant: str = WAN_VARIANT_DEFAULT) -> WanResult:
    """S4：n + q1 + q3 + Median → SD = (q3 − q1) / η"""
    if None in (n, q1, q3) or n <= 0:
        return _fail("S4 需要 n / q1 / q3", "Median_IQR→Wan_S4→GM")
    if q3 <= q1:
        return _fail(f"q3({q3}) ≤ q1({q1})，分位矛盾", "Median_IQR→Wan_S4→GM")
    e = eta(n, variant)
    if e <= 0 or not math.isfinite(e):
        return _fail(f"η={e} 退化", "Median_IQR→Wan_S4→GM")
    sd = (q3 - q1) / e
    warn = ["n < 25，Wan 估算误差较大"] if n < 25 else None
    return _ok(sd, median, "S4", "Median_IQR→Wan_S4→GM",
               f"formula=S4;n={n};variant={variant};eta={e:.6f}",
               ["gm_from_median", "gm_from_iqr"], warn)


def wan_s5(n: int, q1: float, q3: float, mean: float,
           variant: str = WAN_VARIANT_DEFAULT) -> WanResult:
    """S5：n + q1 + q3 + Mean → SD = (q3 − q1) / η"""
    if None in (n, q1, q3) or n <= 0:
        return _fail("S5 需要 n / q1 / q3", "AM_IQR→Wan_S5→GM")
    if q3 <= q1:
        return _fail(f"q3({q3}) ≤ q1({q1})，分位矛盾", "AM_IQR→Wan_S5→GM")
    e = eta(n, variant)
    if e <= 0 or not math.isfinite(e):
        return _fail(f"η={e} 退化", "AM_IQR→Wan_S5→GM")
    sd = (q3 - q1) / e
    warn = ["n < 25，Wan 估算误差较大"] if n < 25 else None
    return _ok(sd, mean, "S5", "AM_IQR→Wan_S5→GM",
               f"formula=S5;n={n};variant={variant};eta={e:.6f}",
               ["gm_from_mean_sd", "gm_from_iqr"], warn)


def wan_s6(q1: float, q3: float, median: float | None = None,
           mean: float | None = None) -> WanResult:
    """S6（缺 n）：SD = (q3 − q1) / (2·q(0.75)) = (q3 − q1) / 1.349"""
    if None in (q1, q3):
        return _fail("S6 需要 q1 / q3", "Median_IQR→Wan_S6→GM")
    if q3 <= q1:
        return _fail(f"q3({q3}) ≤ q1({q1})，分位矛盾", "Median_IQR→Wan_S6→GM")
    center = median if median is not None else mean
    if center is None:
        return _fail("S6 需要中心值（Median 或 Mean）", "Median_IQR→Wan_S6→GM")
    sd = (q3 - q1) / TWO_Q75
    flags = ["gm_from_iqr", "no_n_fallback"]
    flags.append("gm_from_median" if median is not None else "gm_from_mean_sd")
    return _ok(sd, center, "S6", "Median_IQR→Wan_S6→GM",
               f"formula=S6;n=NA;denom={TWO_Q75:.4f}",
               flags, ["样本量缺失，已回退到 S6（精度下降）"])


def wan_s7(n: int, q1: float, q3: float, median: float) -> WanResult:
    """S7：n + q1 + q3 + Median → SD = (q3 − q1) / (2·Φ⁻¹((0.75n − 0.125)/(n + 0.25)))"""
    if None in (n, q1, q3) or n <= 0:
        return _fail("S7 需要 n / q1 / q3", "Median_IQR→Wan_S7→GM")
    if q3 <= q1:
        return _fail(f"q3({q3}) ≤ q1({q1})，分位矛盾", "Median_IQR→Wan_S7→GM")
    p = (0.75 * n - 0.125) / (n + 0.25)
    if not (0.0 < p < 1.0):
        return _fail(f"S7 的 Φ⁻¹ 参数越界：{p}", "Median_IQR→Wan_S7→GM")
    denom = 2.0 * phi_inv(p)
    if denom <= 0 or not math.isfinite(denom):
        return _fail(f"S7 分母退化：{denom}", "Median_IQR→Wan_S7→GM")
    sd = (q3 - q1) / denom
    return _ok(sd, median, "S7", "Median_IQR→Wan_S7→GM",
               f"formula=S7;n={n};denom={denom:.6f}",
               ["gm_from_median", "gm_from_iqr"])


# ===========================================================================
# 5. 总入口：按 stat_type 分派（对应 wan2014_formulas.md §5 决策树）
# ===========================================================================

def merge_prior_path(path: str, params: str,
                     prior_path: str = "", prior_params: str = "") -> tuple[str, str]:
    """
    把**先前的单位换算段**前置到统计量换算段之前，并合并参数留痕。

    用途：链式运行时（`convert_units.py` → `wan_convert.py`），输入行已带
    `conversion_path`（如 `ug/L→ICRP89_Adults→ug/g Cr`）；若不保留，
    `derive_flags()` 看不到单位换算段 → 漏报 `unit_converted_creatinine` 等标记
    （链式运行表现为 `FLAG_MISMATCH`）。自查发现并修复。
    """
    p, pr = path, params
    if prior_path:
        head = prior_path[:-len("→GM")] if prior_path.endswith("→GM") else prior_path
        p = f"{head}→{path}"
    if prior_params:
        pr = f"{prior_params};{params}" if params else prior_params
    return p, pr


def merge_prior_flags(res: WanResult, prior_path: str) -> WanResult:
    """
    把**前置单位换算段**应登记的标记合并进 `res.flags`。

    为什么需要：`derive_flags(完整 conversion_path)` 会从 `ICRP89` 段推出
    `unit_converted_creatinine` 等标记；若 `res.flags` 不含它们，链式运行时
    唯一来源断言会报 `FLAG_MISMATCH`（自查发现）。
    注意：`reported_GM` 语义是"原文直接报告 GM、零估算"，此时**不叠加**统计量类标记。
    """
    if not prior_path:
        return res
    prior_flags, _ = derive_flags(prior_path)
    for f in prior_flags:
        if f not in res.flags:
            res.flags.append(f)
    return res


def _convert_record_inner(row: dict, variant: str = WAN_VARIANT_DEFAULT,
                          median_strategy: str = "as_gm") -> WanResult:
    """
    按 stat_type 分派到对应公式，输出 gm_summary / gsd_summary。

    ★ 口径由调用方**显式传入**（A3）：
      `variant`           ← project.yaml → standardization.wan_variant
      `median_strategy`   ← project.yaml → standardization.median_to_gm_strategy
    默认值仅用于自检；正式路径（main）必须从配置注入。

    ★ **链式兼容**：输入行已有的 `conversion_path`（单位换算段）会被保留并前置，
    见 `merge_prior_path()`。
    """
    # ⚠️ 内部实现只输出**自己这一段**（统计量换算段）。
    #   输入行已有的单位换算段由外层 `convert_record()` 统一合并 —— 若在这里
    #   就前置，会生成 `reported_GM→ug/L→ICRP89…` 这种"来源段+单位段"混排，
    #   使 `derive_flags` 的规则匹配错位（自查发现：链式 5 处 FLAG_MISMATCH）。
    prior_path = ""
    prior_params = ""

    def with_prior(path: str, params: str) -> tuple[str, str]:
        # 内部调用点保持签名不变；实际合并由外层完成
        return path, params

    def gv(k):
        v = row.get(k)
        if v is None or str(v).strip() in ("", "NA", "nan", "None", "未报告"):
            return None
        try:
            return float(v)
        except (TypeError, ValueError):
            return None

    st = str(row.get("stat_type") or "").strip()
    n = gv("sample_size")
    n = int(n) if n else None
    gm_in = gv("initial_gm")
    am = gv("initial_mean")
    sd_in = gv("initial_sd")
    med = gv("initial_median")
    p25, p75 = gv("initial_p25"), gv("initial_p75")
    lo, hi = gv("initial_min"), gv("initial_max")
    gsd_in = gv("initial_gsd")

    # --- 首要分支：原文已在对数尺度 → 硬拒绝，不得套 Wan ---
    if st in ("GM_GSD", "GM_only"):
        if gm_in is None:
            return _fail("stat_type 标为 GM 但 initial_gm 为空（S3 交付违约，须回报走 GATE-3 重开）",
                         "reported_GM")
        _p, _pr = with_prior("reported_GM", "source=reported")
        return WanResult(status="SKIP", formula="none", gm=gm_in, gsd=gsd_in,
                         center=gm_in, path=_p,
                         params=_pr, flags=[],
                         messages=["原文已在对数尺度，直接取用，不做估算"])

    # --- AM + SD ---
    if st in ("AM_SD",) or (am is not None and sd_in is not None and st not in ("GM_GSD",)):
        if am is None:
            return _fail("stat_type=AM_SD 但 initial_mean 为空", "AM_SD→AMSD_to_LN→GM")
        if sd_in is None:
            # 退到范围/IQR 法
            pass
        else:
            try:
                gm, gsd = mean_sd_to_gm(am, sd_in)
            except ValueError as exc:
                return _fail(f"AM_SD→GM 失败：{exc}", "AM_SD→AMSD_to_LN→GM")
            cv = sd_in / am
            warn = ["CV > 2，对数正态转换不稳"] if cv > 2.0 else None
            _p, _pr = with_prior("AM_SD→AMSD_to_LN→GM",
                                 f"AM={am};SD={sd_in};CV={cv:.3f}")
            return WanResult(status="WARN" if warn else "OK", formula="AMSD",
                             sd=sd_in, center=am, mean=am, gm=gm, gsd=gsd,
                             path=_p, params=_pr,
                             flags=["gm_from_mean_sd"], messages=list(warn or []))

    # --- Median / IQR / Range 分支 ---
    # ★ 先看配置策略：`as_gm` 表示"直接把中位数当作 GM"（对数正态下中位数 = GM），
    #   对**所有**以中位数为中心值的记录生效（不止缺 n 的情形）。
    #   `via_wan` 才走 Wan 估算。
    def _median_as_gm(med_v: float, path: str, extra_flags: list[str],
                      n_val=None) -> WanResult:
        return WanResult(
            status="SKIP", formula="Median_as_GM", sd=None, center=med_v,
            mean=None, gm=med_v, gsd=None, path=path,
            params=(f"strategy=as_gm;n={'NA' if n_val is None else n_val}"
                    f";configured=standardization.median_to_gm_strategy"),
            flags=["gm_from_median"] + extra_flags,
            messages=["按配置策略直接把中位数作为 GM（零 Wan 估算）；"
                      "如需 GSD，请在 project.yaml 将策略改为 via_wan"])

    if st in ("Median_IQR", "P25_P75") or (med is not None and p25 is not None and p75 is not None):
        if p25 is None or p75 is None:
            return _fail("报告了中位数但无四分位数", "Median_IQR→GM")
        if med is None:
            med = (p25 + p75) / 2.0
        if median_strategy == "as_gm":
            _p, _pr = with_prior(
                "Median_IQR→Median_as_GM→GM",
                f"strategy=as_gm;n={'NA' if n is None else n}"
                f";configured=standardization.median_to_gm_strategy")
            return WanResult(
                status="SKIP", formula="Median_as_GM", sd=None, center=med,
                mean=None, gm=med, gsd=None, path=_p,
                params=_pr,
                flags=["gm_from_median", "gm_from_iqr"],
                messages=["按配置策略直接把中位数作为 GM（零 Wan 估算）；"
                          "如需 GSD，请在 project.yaml 将策略改为 via_wan"])
        res = (wan_s4(n, p25, p75, med, variant) if n
                else wan_s6(p25, p75, median=med))
        return _finish(res,
                          flags_extra=["gm_from_median"] if "gm_from_median" not in res.flags else [],
)

    if st in ("Median_Range",) or (med is not None and lo is not None and hi is not None):
        if lo is None or hi is None:
            return _fail("报告了中位数但无极值", "Median_Range→GM")
        if med is None:
            return _fail("Median_Range 需要中位数", "Median_Range→GM")
        if median_strategy == "as_gm":
            _p2, _pr2 = with_prior(
                "Median_Range→Median_as_GM→GM",
                f"strategy=as_gm;n={'NA' if n is None else n}"
                f";configured=standardization.median_to_gm_strategy")
            return WanResult(
                status="SKIP", formula="Median_as_GM", sd=None, center=med,
                mean=None, gm=med, gsd=None, path=_p2, params=_pr2,
                flags=["gm_from_median", "gm_from_range"],
                messages=["按配置策略直接把中位数作为 GM（零 Wan 估算）；"
                          "如需 GSD，请在 project.yaml 将策略改为 via_wan"])
        res = (wan_s2(n, lo, hi, med, variant) if n
                else wan_s3(lo, hi, median=med))
        return _finish(res)

    if st in ("Min_Max",):
        if am is not None:
            res = (wan_s1(n, lo, hi, am, variant) if n
                else wan_s3(lo, hi, mean=am))
        elif med is not None:
            res = (wan_s2(n, lo, hi, med, variant) if n
                else wan_s3(lo, hi, median=med))
        else:
            return _fail("Min_Max 需要中心值（Mean 或 Median）", "Min_Max→GM")
        return _finish(res)

    if st in ("Median_only",):
        return _fail("只有中位数、无任何离散度信息 → 该记录不可合并",
                     "Median_only→FAILED")

    return _fail(f"无法识别的 stat_type：`{st}` → 交人工", "unknown→FAILED")


def convert_record(row: dict, variant: str = WAN_VARIANT_DEFAULT,
                   median_strategy: str = "as_gm") -> WanResult:
    """
    公开入口：调用内部实现，并**合并前置单位换算段派生的标记**（链式兼容）。
    见 `merge_prior_flags()` 的说明。
    """
    prior_path = str(row.get("conversion_path") or "").strip()
    prior_params = str(row.get("conversion_params") or "").strip()
    res = _convert_record_inner(row, variant=variant,
                                median_strategy=median_strategy)
    # 失败项也合并前置段：一是保留换算痕迹便于追溯，二是使 `derive_flags(完整路径)`
    # 与 `res.flags` 保持一致（否则唯一来源断言在 FAILED 记录上误报）。
    res.path, res.params = merge_prior_path(res.path, res.params,
                                            prior_path, prior_params)
    res = merge_prior_flags(res, prior_path)
    return res


def _finish(res: WanResult, flags_extra: list[str] | None = None) -> WanResult:
    """把 Wan 的 SD + 中心值转成 GM/GSD（只处理统计量段）。"""
    if res.status == "FAILED":
        return res
    if flags_extra:
        for f in flags_extra:
            if f not in res.flags:
                res.flags.append(f)
    center, sd = res.center, res.sd
    if center is None or center <= 0:
        return _fail(f"中心值 {center} ≤ 0，无法取对数", res.path)
    if sd is None or sd <= 0:
        return _fail(f"估算 SD = {sd} ≤ 0，交人工", res.path)
    try:
        gm, gsd = mean_sd_to_gm(center, sd)
    except ValueError as exc:
        return _fail(f"转 GM 失败：{exc}", res.path)
    res.gm, res.gsd = gm, gsd
    if not res.path.endswith("GM"):
        res.path = res.path + "→GM"
    return res


# ===========================================================================
# 6. inferred_flags 反推（按 unit_conversion_playbook.md §6.2 规则表）
# ===========================================================================

RULE_TABLE: list[tuple[str, list[str]]] = [
    # (conversion_path 片段, 登记的标记)
    ("Wan_S1", ["gm_from_mean_sd", "gm_from_range"]),
    ("Wan_S2", ["gm_from_median", "gm_from_range"]),
    ("Wan_S3", ["gm_from_range", "no_n_fallback"]),
    ("Wan_S4", ["gm_from_median", "gm_from_iqr"]),
    ("Wan_S5", ["gm_from_mean_sd", "gm_from_iqr"]),
    ("Wan_S6", ["gm_from_median", "gm_from_iqr", "no_n_fallback"]),
    ("Wan_S7", ["gm_from_median", "gm_from_iqr"]),
    ("AMSD_to_LN", ["gm_from_mean_sd"]),
    # ★ `as_gm` 策略：GM 取自中位数（零 Wan 估算），但**离散度信息仍来自 IQR/范围**，
    #   故需保留对应的 gm_from_iqr / gm_from_range 标记（供敏感性分析分层）。
    ("Median_IQR→Median_as_GM", ["gm_from_median", "gm_from_iqr"]),
    ("Median_Range→Median_as_GM", ["gm_from_median", "gm_from_range"]),
    ("Median_as_GM", ["gm_from_median"]),
    ("sd_from_iqr", ["sd_from_iqr"]),
    ("nmol/L", ["molar_conversion"]),
    ("ICRP89", ["unit_converted_creatinine"]),
    ("time_represented", ["time_represented"]),
    ("blood_matrix_assumed", ["blood_matrix_assumed"]),
    ("species_total_assumed", ["species_total_assumed"]),
]

# ★ 仅"单位换算类"规则（用于 `reported_GM` 早退分支：统计量零推断，但单位仍需留痕）
UNIT_ONLY_RULES: list[tuple[str, list[str]]] = [
    ("ICRP89", ["unit_converted_creatinine"]),
    ("nmol/L", ["molar_conversion"]),
]

# 目标为 ug/L 且来源为 ug/g Cr → 反向换算
REVERSE_VOLUME_HINT = ("ug/g Cr", "ug/L")

CLOSED_FLAG_DICT = CLOSED_FLAG_SET   # 单一来源（shared/inferred_flags.py），不再自建副本


def derive_flags(conversion_path: str) -> tuple[list[str], list[tuple[str, str]]]:
    """
    从 conversion_path 反推应登记的 inferred_flags。
    返回 (标记集合, 命中记录列表[(标记, 命中规则)]) —— 可审计。
    """
    flags: list[str] = []
    hits: list[tuple[str, str]] = []
    path = conversion_path or ""

    # ★ 早退：链式路径以 `reported_GM` 结尾时，语义是"原文直接报告 GM、零估算"。
    #   此时**不叠加**任何统计量类标记（但保留单位换算类，见下方 UNIT_ONLY）。
    ended_with_reported_gm = path.strip().endswith("reported_GM")
    if ended_with_reported_gm:
        # 只保留单位换算类标记（ICRP89 / 反向体积 / 摩尔）
        unit_flags: list[str] = []
        for needle, fl in UNIT_ONLY_RULES:
            if needle in path:
                for f in fl:
                    if f not in unit_flags:
                        unit_flags.append(f)
                        hits.append((f, f"路径含 `{needle}`（零估算，仅单位类）"))
        return unit_flags, hits or [("<none>", "`reported_GM`：原文直接报告，统计量零推断")]

    for needle, fl in RULE_TABLE:
        if needle in path:
            for f in fl:
                if f not in flags:
                    flags.append(f)
                    hits.append((f, f"路径含 `{needle}`"))

    # 反向体积换算
    if "ug/g Cr" in path and path.rstrip().endswith("ug/L"):
        if "unit_converted_volume" not in flags:
            flags.append("unit_converted_volume")
            hits.append(("unit_converted_volume", "来源 ug/g Cr → 目标 ug/L"))

    # 纯"无需换算"标记：无任何推断
    if path.strip() in ("reported_GM", "reported_creatinine_corrected"):
        return [], [("<none>", f"`{path}` 为原文直接报告，零推断")]

    return flags, hits


# ===========================================================================
# 7. self-test
# ===========================================================================

def run_self_test() -> int:
    print("=" * 70)
    print("wan_convert.py --self-test")
    print("=" * 70)

    checks: list[tuple[str, bool, str]] = []

    def check(name: str, ok: bool, detail: str = "") -> None:
        checks.append((name, ok, detail))
        print(f"      {'✅' if ok else '❌'} {name}" + (f"  [{detail}]" if detail else ""))

    def close(a, b, tol=1e-6):
        return a is not None and b is not None and abs(a - b) < tol

    # --- 基础函数 ---
    print("\n[1] 基础函数")
    check("Φ(0) = 0.5", close(phi(0.0), 0.5))
    check("Φ⁻¹(0.75) ≈ 0.6745", close(phi_inv(0.75), 0.6745, 1e-4),
          f"{phi_inv(0.75):.6f}")
    check("常数 2·q(.75)·√2 = 1.9079", close(TWO_Q75_SQRT2, 1.9079, 5e-4),
          f"{TWO_Q75_SQRT2:.6f}")
    check("ξ(n=100) 与 η(n=100) 相等", close(xi(100), eta(100)))

    # --- S1 ---
    print("\n[2] S1：n=100, min=1, max=5, Mean=3")
    r1 = wan_s1(100, 1.0, 5.0, 3.0)
    exp1 = 4.0 / xi(100)
    check("S1 SD 正确", close(r1.sd, exp1), f"SD={r1.sd:.6f} 期望={exp1:.6f}")
    check("S1 标记含 range", "gm_from_range" in r1.flags)

    # --- S2 ---
    print("\n[3] S2：n=100, min=1, max=5, Median=3")
    r2 = wan_s2(100, 1.0, 5.0, 3.0)
    check("S2 SD 与 S1 同式", close(r2.sd, r1.sd), f"SD={r2.sd:.6f}")
    check("S2 中心值用中位数", r2.center == 3.0)

    # --- S3 ---
    print("\n[4] S3（缺 n）：min=1, max=5")
    r3 = wan_s3(1.0, 5.0, median=3.0)
    check("S3 SD = 4/1.9079", close(r3.sd, 4.0 / 1.9079, 5e-4), f"SD={r3.sd:.6f}")
    check("S3 含 no_n_fallback", "no_n_fallback" in r3.flags)
    check("S3 状态为 WARN", r3.status == "WARN")

    # --- S4 ---
    print("\n[5] S4：n=100, q1=2, q3=6, Median=4")
    r4 = wan_s4(100, 2.0, 6.0, 4.0)
    check("S4 SD = 4/η(100)", close(r4.sd, 4.0 / eta(100)), f"SD={r4.sd:.6f} η={eta(100):.6f}")
    check("S4 含 iqr 标记", "gm_from_iqr" in r4.flags)

    # --- S5 ---
    print("\n[6] S5：n=100, q1=2, q3=6, Mean=4")
    r5 = wan_s5(100, 2.0, 6.0, 4.0)
    check("S5 SD 与 S4 同式", close(r5.sd, r4.sd))

    # --- S6 ---
    print("\n[7] S6（缺 n）：q1=2, q3=6")
    r6 = wan_s6(2.0, 6.0, median=4.0)
    check("S6 SD = 4/1.349", close(r6.sd, 4.0 / 1.349), f"SD={r6.sd:.6f}")

    # --- S7 ---
    print("\n[8] S7：n=100, q1=2, q3=6, Median=4")
    r7 = wan_s7(100, 2.0, 6.0, 4.0)
    p7 = (0.75 * 100 - 0.125) / (100 + 0.25)
    check("S7 SD = 4/(2Φ⁻¹(p))", close(r7.sd, 4.0 / (2 * phi_inv(p7))),
          f"SD={r7.sd:.6f}")

    # --- 数学关系（务必理解，否则会误用公式）---
    # ξ(n) = 2Φ⁻¹((n−0.375)/(n+0.25))：n=2 时 ≈1.349，随 n 单调**发散**
    #   → 有 n 时用 S4 得到的 SD 恒 ≤ 用 S6（常数分母 1.349）得到的 SD
    # S7 分母 = 2Φ⁻¹((0.75n−0.125)/(n+0.25))：n=100 时 ≈1.330，**收敛到 1.349**
    #   → 大 n 时 S7 ≈ S6（不是 S4）
    print("\n[9] 分母渐近行为：η 发散、S7→S6、S4 恒小于 S6")
    s4_100 = wan_s4(100, 2.0, 6.0, 4.0)
    s6 = wan_s6(2.0, 6.0, median=4.0)
    s7_big = wan_s7(100000, 2.0, 6.0, 4.0)
    check("η(n=100) ≈ 5.0", close(eta(100), 5.0, 5e-3), f"η={eta(100):.6f}")
    check("η 随 n 单调递增", eta(1000) > eta(100) > eta(10) > eta(2),
          f"η(2)={eta(2):.4f} η(10)={eta(10):.4f} η(100)={eta(100):.4f} η(1000)={eta(1000):.4f}")
    check("η(n) ≥ 1.349 恒成立（n>2）", eta(3) >= 1.349, f"η(3)={eta(3):.6f}")
    check("S4 的 SD 恒 ≤ S6 的 SD", s4_100.sd < s6.sd,
          f"S4={s4_100.sd:.4f} < S6={s6.sd:.4f}")
    check("大 n 时 S7 ≈ S6（相对差 <1%）",
          abs(s7_big.sd - s6.sd) / s6.sd < 0.01,
          f"相对差={abs(s7_big.sd - s6.sd) / s6.sd:.4%}")

    # --- AM_SD → GM ---
    print("\n[10] AM_SD → GM 转换")
    gm, gsd = mean_sd_to_gm(2.72, 0.90)
    check("GM < AM（对数正态）", gm < 2.72, f"GM={gm:.4f} AM=2.72")
    check("GSD > 1", gsd > 1.0, f"GSD={gsd:.4f}")
    check("恒等：SD=0 → GM=AM", close(mean_sd_to_gm(5.0, 0.0)[0], 5.0))
    # 往返一致性
    cv = 0.90 / 2.72
    sig = math.sqrt(math.log(1 + cv ** 2))
    back = gm * math.exp(sig ** 2 / 2)
    check("GM → AM 往返一致", close(back, 2.72, 1e-9), f"AM'={back:.6f}")

    # --- 硬拒绝：GM 记录不得套 Wan ---
    print("\n[11] 硬拒绝：stat_type=GM_GSD 必须 SKIP")
    rg = convert_record({"stat_type": "GM_GSD", "initial_gm": "23.5",
                         "initial_gsd": "2.1", "sample_size": "1200"})
    check("GM_GSD → SKIP", rg.status == "SKIP", f"status={rg.status}")
    check("GM 直接取用", rg.gm == 23.5, f"gm={rg.gm}")
    check("SKIP 记录零推断", rg.flags == [], f"flags={rg.flags}")

    # --- S3 交付违约：标 GM 但无值 ---
    print("\n[12] S3 交付违约检测")
    rv = convert_record({"stat_type": "GM_only", "initial_gm": ""})
    check("GM_only 无值 → FAILED", rv.status == "FAILED")

    # --- 分位矛盾 ---
    print("\n[13] 分位矛盾保护（显式走 via_wan 以触达公式分支）")
    rc = convert_record({"stat_type": "Median_IQR", "initial_median": "55",
                         "initial_p25": "60", "initial_p75": "40",
                         "sample_size": "450"}, median_strategy="via_wan")
    check("q3 < q1 → FAILED", rc.status == "FAILED", f"msg={rc.messages[:1]}")

    # --- 只有中位数 ---
    print("\n[14] 无离散度信息")
    rm = convert_record({"stat_type": "Median_only", "initial_median": "12",
                         "sample_size": "300"})
    check("Median_only → FAILED（不可合并）", rm.status == "FAILED")

    # --- 走完整分派：AM_SD ---
    print("\n[15] 分派：AM_SD 走对数正态路径")
    ra = convert_record({"stat_type": "AM_SD", "initial_mean": "2.72",
                         "initial_sd": "0.90", "sample_size": "800"})
    check("AM_SD → OK", ra.status == "OK", f"status={ra.status}")
    check("path = AM_SD→AMSD_to_LN→GM", ra.path == "AM_SD→AMSD_to_LN→GM", ra.path)
    check("flags 含 gm_from_mean_sd", "gm_from_mean_sd" in ra.flags)

    # --- 走完整分派：Median_IQR ---
    print("\n[16] 分派：Median_IQR（n 缺失 → S6 回退，显式 via_wan）")
    rq = convert_record({"stat_type": "Median_IQR", "initial_median": "7.5",
                         "initial_p25": "5.2", "initial_p75": "9.1",
                         "sample_size": ""}, median_strategy="via_wan")
    check("无 n → 走 S6", "Wan_S6" in rq.path, rq.path)
    check("含 no_n_fallback", "no_n_fallback" in rq.flags)

    # --- inferred_flags 反推可审计性 ---
    print("\n[17] inferred_flags 反推（确定性 + 规则可追溯）")
    f1, h1 = derive_flags("Median_IQR→Wan_S4→ug/L→ICRP89_Adults→ug/g Cr→GM")
    check("含 gm_from_iqr", "gm_from_iqr" in f1, ";".join(f1))
    check("含 unit_converted_creatinine", "unit_converted_creatinine" in f1)
    check("命中规则可追溯", all(r for _, r in h1), f"{len(h1)} 条命中规则")
    f1b, _ = derive_flags("Median_IQR→Wan_S4→ug/L→ICRP89_Adults→ug/g Cr→GM")
    check("确定性：两次结果一致", f1 == f1b)
    f0, _ = derive_flags("reported_GM")
    check("reported_GM → 零标记", f0 == [], f"flags={f0}")
    fv, _ = derive_flags("ug/g Cr→ICRP89_Adults→ug/L")
    check("反向换算 → unit_converted_volume", "unit_converted_volume" in fv)

    # --- 唯一来源规则：路径反推 与 内部 res.flags 必须一致 ---
    print("\n[17b] 唯一来源规则：res.flags 与 derive_flags(path) 必须一致（自检断言）")
    consistency_cases = [
        ({"stat_type": "AM_SD", "initial_mean": "2.72", "initial_sd": "0.90",
          "sample_size": "800"}, "AM_SD 路径"),
        ({"stat_type": "Median_IQR", "initial_median": "7.5", "initial_p25": "5.2",
          "initial_p75": "9.1", "sample_size": "450"}, "Wan_S4 路径"),
        ({"stat_type": "Median_IQR", "initial_median": "7.5", "initial_p25": "5.2",
          "initial_p75": "9.1"}, "Wan_S6 路径（无 n）"),
        ({"stat_type": "GM_GSD", "initial_gm": "23.5", "sample_size": "1200"},
         "reported_GM 零推断路径"),
    ]
    for case, label in consistency_cases:
        rr = convert_record(case, median_strategy="via_wan")
        # derive_flags 是权威来源
        auth, _h = derive_flags(rr.path)
        # 内部 res.flags 在 reported_GM 情形下因 path 无 Wan 段而为空，与 auth 一致
        ok = set(rr.flags) == set(auth)
        check(f"{label}：内部标记 == 反推标记", ok,
              f"res={sorted(rr.flags)} auth={sorted(auth)}")

    # reported_GM 必须零标记（零推断子集完整性）
    rg0 = convert_record({"stat_type": "GM_GSD", "initial_gm": "23.5"})
    auth0, _ = derive_flags(rg0.path)
    check("reported_GM 反推结果为空（零推断）", auth0 == [], f"{auth0}")

    # --- 闭集校验 ---
    print("\n[18] 标记闭集校验")
    all_derived = set()
    for p in ("Median_IQR→Wan_S4→GM", "Min_Max→Wan_S3→GM",
              "AM_SD→AMSD_to_LN→GM", "nmol/L→ug/L→GM",
              "ug/L→ICRP89_Adults→ug/g Cr→GM",
              "ug/g Cr→ICRP89_Adults→ug/L"):
        fl, _ = derive_flags(p)
        all_derived |= set(fl)
    out_of_dict = all_derived - CLOSED_FLAG_DICT
    check("所有反推标记都在闭集内", not out_of_dict, f"越界={out_of_dict}")
    # ★ 单一来源断言：闭集项数 == shared/inferred_flags.py 的注册表项数
    check("闭集项数 == 注册表项数（单一来源）",
          len(CLOSED_FLAG_DICT) == inferred_flag_count(),
          f"{len(CLOSED_FLAG_DICT)} vs {inferred_flag_count()}")
    check("四个新增标记均在闭集内",
          {"no_n_fallback", "molar_conversion", "coarse_age",
           "cordblood_edi_skipped"} <= CLOSED_FLAG_DICT,
          str(sorted({"no_n_fallback", "molar_conversion", "coarse_age",
                      "cordblood_edi_skipped"} - CLOSED_FLAG_DICT)) or "4/4")

    # --- 汇总 ---
    print("\n[19] ★ A3：配置必须真正影响结果（读了就要有用）")
    base = {"stat_type": "Median_IQR", "initial_median": "7.5",
            "initial_p25": "5.2", "initial_p75": "9.1", "sample_size": "450"}
    r_asgm = convert_record(dict(base), variant="minus", median_strategy="as_gm")
    r_wan = convert_record(dict(base), variant="minus", median_strategy="via_wan")
    check("as_gm → 零 Wan 估算（path 含 Median_as_GM）",
          "Median_as_GM" in r_asgm.path, r_asgm.path)
    check("as_gm → GM = 中位数（7.5）", close(r_asgm.gm, 7.5), f"{r_asgm.gm}")
    check("via_wan → 走 Wan（path 含 Wan_S）", "Wan_S" in r_wan.path, r_wan.path)
    check("★ 两种 median_to_gm_strategy 给出不同 GM",
          abs((r_asgm.gm or 0) - (r_wan.gm or 0)) > 1e-6,
          f"as_gm={r_asgm.gm:.6f} via_wan={r_wan.gm:.6f}")
    check("as_gm 策略下 flags 含 gm_from_median",
          "gm_from_median" in r_asgm.flags, str(r_asgm.flags))
    check("as_gm 的 params 记录策略来源",
          "configured=standardization.median_to_gm_strategy" in r_asgm.params,
          r_asgm.params)

    # wan_variant：★ 目前仅实现 minus；plus 必须**显式拒绝**（不得静默等价）
    case = {"stat_type": "Min_Max", "initial_min": "1", "initial_max": "5",
            "initial_mean": "3", "sample_size": "100"}
    r_minus = convert_record(dict(case), variant="minus", median_strategy="via_wan")
    check("minus 变体正常计算", r_minus.sd is not None, f"SD={r_minus.sd}")
    try:
        convert_record(dict(case), variant="plus", median_strategy="via_wan")
        check("★ 未实现的 plus 变体必须拒绝（不得静默等价）", False,
              "未抛错 → 会静默给出与 minus 相同的结果！")
    except NotImplementedError as exc:
        check("★ 未实现的 plus 变体必须拒绝（不得静默等价）", True,
              str(exc)[:40] + "…")
    check("WAN_VARIANTS_IMPLEMENTED 仅含 minus",
          WAN_VARIANTS_IMPLEMENTED == {"minus"}, str(WAN_VARIANTS_IMPLEMENTED))
    check("params 记录 variant",
          "variant=minus" in r_minus.params, r_minus.params)

    # 中位数 + as_gm 时不应产出 GSD（并给出提示）
    check("as_gm 下 gsd 为空且有提示",
          r_asgm.gsd is None and any("via_wan" in m for m in r_asgm.messages),
          str(r_asgm.messages[:1]))

    print("\n[20] ★ 链式兼容：输入已带单位换算段时，标记必须一致")
    # 场景：convert_units.py 的输出作为本脚本输入，输入行的 conversion_path
    # 已含单位换算段（如 `ug/L→ICRP89_Adults→ug/g Cr`）。
    # 若内部实现把它误当作自己的段（生成 `reported_GM→ug/L→ICRP89…` 混排），
    # derive_flags 规则匹配会错位 → FLAG_MISMATCH（自查发现，曾达 7/12）。
    chain_cases = [
        ({"stat_type": "GM_GSD", "initial_gm": "23.5", "initial_gsd": "2.1",
          "conversion_path": "reported_creatinine_corrected",
          "conversion_params": "factor=1;adjusted=Creatinine"},
         "reported_creatinine_corrected→reported_GM", 0),
        ({"stat_type": "GM_only", "initial_gm": "12.66",
          "conversion_path": "ug/L→ICRP89_Adults→ug/g Cr",
          "conversion_params": "group=Adults;CC=0.964"},
         "ug/L→ICRP89_Adults→ug/g Cr→reported_GM", 1),
        ({"stat_type": "AM_SD", "initial_mean": "31.2", "initial_sd": "12.4",
          "conversion_path": "ug/L→ICRP89_Minors→ug/g Cr"},
         "ICRP89_Minors", 2),
        # as_gm 策略：GM 取自中位数，但离散度信息仍来自 IQR → 2 个标记
        ({"stat_type": "Median_IQR", "initial_median": "21.3", "initial_p25": "14.8",
          "initial_p75": "30.1", "conversion_path": "reported_creatinine_corrected"},
         "Median_IQR→Median_as_GM", 2),
        # as_gm 策略 + 前置 ICRP89 单位段 → 3 个标记（median + range + 单位）
        ({"stat_type": "Median_Range", "initial_median": "26.5", "initial_min": "3",
          "initial_max": "88", "conversion_path": "ug/L→ICRP89_Adults→ug/g Cr"},
         "Median_Range→Median_as_GM", 3),
    ]
    for case, expect_path_frag, expect_n_flags in chain_cases:
        rr = convert_record(dict(case), variant="minus", median_strategy="as_gm")
        auth, _ = derive_flags(rr.path)
        same = set(rr.flags) == set(auth)
        check(f"链式 {case['stat_type']}：res.flags == derive_flags(path)", same,
              f"res={sorted(rr.flags)} auth={sorted(auth)}")
        check(f"链式 {case['stat_type']}：路径含 `{expect_path_frag}`",
              expect_path_frag in rr.path, rr.path)
        check(f"链式 {case['stat_type']}：标记数 = {expect_n_flags}",
              len(rr.flags) == expect_n_flags, str(sorted(rr.flags)))
        check(f"链式 {case['stat_type']}：路径不含混排 `reported_GM→`",
              "reported_GM→" not in rr.path, rr.path)

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
# 8. CLI
# ===========================================================================

def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="wan_convert.py",
        description="Wan et al. (2014) S1–S7 统计量换算 + inferred_flags 反推（HBM-Meta-Agent / S4）",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="退出码：0 成功 / 1 存在 FAILED 记录 / 2 前置条件不满足",
    )
    p.add_argument("--input", type=Path, help="主表（.csv / .xlsx）")
    p.add_argument("--config", type=Path, help="project.yaml（必需）")
    p.add_argument("--out", type=Path, help="换算结果输出（.csv）")
    p.add_argument("--trace", type=Path, help="换算痕迹表输出（.csv）")
    p.add_argument("--flags-derivation", type=Path, help="inferred_flags 反推表输出（.csv）")
    p.add_argument("--self-test", action="store_true", help="运行数学校验自检")
    p.add_argument("--version", action="version", version="hbm-meta S4 wan_convert 1.0.0")
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    if args.self_test:
        return run_self_test()

    if not args.input:
        print("错误：需要 --input，或使用 --self-test", file=sys.stderr)
        return 2

    # ★ A3：口径一律来自 project.yaml → standardization（缺失即硬错误）
    try:
        cfg = load_config(args.config, require=["standardization"])
    except ConfigError as exc:
        print(f"错误：{exc}", file=sys.stderr)
        return 2
    std = cfg.standardization
    variant = std["wan_variant"]
    median_strategy = std["median_to_gm_strategy"]
    print(f"配置：{cfg.source_path}  ({cfg.summary()})")
    print(f"标准化口径：wan_variant={variant}  "
          f"median_to_gm_strategy={median_strategy}")

    import csv as _csv

    rows: list[dict] = []
    try:
        if args.input.suffix.lower() == ".csv":
            with args.input.open(encoding="utf-8-sig", newline="") as fh:
                rows = [dict(r) for r in _csv.DictReader(fh)]
        elif args.input.suffix.lower() in (".xlsx", ".xlsm"):
            import openpyxl
            wb = openpyxl.load_workbook(args.input, read_only=True, data_only=True)
            ws = wb[wb.sheetnames[0]]
            it = ws.iter_rows(values_only=True)
            hdr = [str(h).strip() if h is not None else "" for h in next(it)]
            for r in it:
                if r is None or all(v is None for v in r):
                    continue
                rows.append({hdr[i]: (r[i] if i < len(r) else None)
                             for i in range(len(hdr))})
            wb.close()
        else:
            print(f"错误：不支持的文件类型 {args.input.suffix}", file=sys.stderr)
            return 2
    except FileNotFoundError:
        print(f"错误：主表不存在：{args.input}", file=sys.stderr)
        return 2

    out_rows, trace_rows = [], []
    n_failed = n_skip = 0
    n_flag_mismatch = 0
    for row in rows:
        res = convert_record(row, variant=variant, median_strategy=median_strategy)
        if res.status == "FAILED":
            n_failed += 1
        if res.status == "SKIP":
            n_skip += 1

        # === 唯一来源规则（SKILL.md R7）===
        # inferred_flags 的权威来源是 conversion_path → derive_flags()。
        # convert_record() 返回的 res.flags 仅用于自检断言，**不得直接写入主表**。
        authoritative_flags, _hits = derive_flags(res.path)
        if set(res.flags) != set(authoritative_flags):
            n_flag_mismatch += 1
            res.messages.append(
                f"[FLAG_MISMATCH] 内部标记与反推标记不一致："
                f"res.flags={sorted(res.flags)} vs derive_flags={sorted(authoritative_flags)}")

        merged = dict(row)
        merged.update({"conversion_path": res.path,
                       "conversion_params": res.params,
                       "gm_summary": res.gm if res.gm is not None else "",
                       "gsd_summary": res.gsd if res.gsd is not None else "",
                       "inferred_flags": ";".join(authoritative_flags),
                       "wan_status": res.status,
                       "wan_message": " | ".join(res.messages)})
        out_rows.append(merged)
        trace_rows.append({
            "record_id": row.get("record_id", ""),
            "stat_type": row.get("stat_type", ""),
            "value_in": row.get("initial_gm") or row.get("initial_median")
                        or row.get("initial_mean", ""),
            "unit_in": row.get("unit_original", ""),
            "path": res.path, "formula": res.formula,
            "params": res.params,
            "gm_out": res.gm if res.gm is not None else "",
            "gsd_out": res.gsd if res.gsd is not None else "",
            "flags": ";".join(authoritative_flags),
            "status": res.status,
            "message": " | ".join(res.messages),
        })

    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        with args.out.open("w", encoding="utf-8-sig", newline="") as fh:
            w = _csv.DictWriter(fh, fieldnames=list(out_rows[0].keys()))
            w.writeheader()
            w.writerows(out_rows)
        print(f"换算结果：{args.out}")

    if args.trace:
        args.trace.parent.mkdir(parents=True, exist_ok=True)
        with args.trace.open("w", encoding="utf-8-sig", newline="") as fh:
            w = _csv.DictWriter(fh, fieldnames=list(trace_rows[0].keys()))
            w.writeheader()
            w.writerows(trace_rows)
        print(f"换算痕迹：{args.trace}")

    if args.flags_derivation:
        args.flags_derivation.parent.mkdir(parents=True, exist_ok=True)
        with args.flags_derivation.open("w", encoding="utf-8-sig", newline="") as fh:
            w = _csv.writer(fh)
            w.writerow(["record_id", "conversion_path", "flag", "rule_hit", "source_field"])
            for row in rows:
                flags, hits = derive_flags(row.get("conversion_path", ""))
                if not hits:
                    hits = [("<none>", "无命中规则")]
                for f, rule in hits:
                    w.writerow([row.get("record_id", ""), row.get("conversion_path", ""),
                                f, rule, "conversion_path"])
        print(f"标记反推表：{args.flags_derivation}")

    print(f"记录数={len(rows)}  FAILED={n_failed}  SKIP={n_skip}  "
          f"OK/WARN={len(rows) - n_failed - n_skip}"
          + (f"  ⚠️ FLAG_MISMATCH={n_flag_mismatch}" if n_flag_mismatch else ""))
    return 1 if (n_failed or n_flag_mismatch) else 0


if __name__ == "__main__":
    raise SystemExit(main())
