#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
convert_units.py — 单位换算 + 尿校正（ICRP 89）

阶段状态：第三步骨架版
  ✅ 完整 CLI（--input / --config / --out / --trace）
  ✅ --self-test（质量单位 / 摩尔换算 / ICRP 参数选择 / 往返一致性 / 退化保护）
  ✅ 核心逻辑：质量单位归一、摩尔换算、尿校正（CC 直除 + CE/V 等价式）、
       ICRP 89 参数决策树、conversion_path 编码、inferred_flags 反推
  ⏳ 第四步待办：口径全部从 project.yaml 读取（禁止兜底，见 STEP4-TODO.md A1）、
       输出带快照指纹、与 wan_convert.py 串联成完整 S4 流程、CI 集成

保真声明
  参数表与换算公式来自：
    - ICRP Publication 89（尿量/肌酐参考值）
    - 原项目 R 脚本 EDI计算_run.R / 描述性分析_run.R（Adults/Pregnant/Minors 三档）
  算法未做任何改动。Minors 行 CC 与 CE/V 的不自洽**原样保留**并在输出中标注（见 icrp89 §2 注）。

用法
  python convert_units.py --self-test
  python convert_units.py --input 主表.xlsx --config project.yaml --out 换算后.csv --trace trace.csv

退出码
  0 成功   1 存在 FAILED 记录   2 前置条件不满足
"""
from __future__ import annotations

import argparse
import csv
import math
import sys
from dataclasses import dataclass, field, asdict
from pathlib import Path

try:  # Windows 控制台 GBK 兼容
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except (AttributeError, ValueError):  # pragma: no cover
    pass

# ★ 唯一配置入口（shared/project_config.py）——禁止本脚本自带口径默认值
_SHARED = Path(__file__).resolve().parent.parent.parent.parent / "shared"
if str(_SHARED) not in sys.path:
    sys.path.insert(0, str(_SHARED))
try:
    from project_config import ConfigError, load_config  # noqa: E402
    from snapshot import SnapshotError, append_snapshot_registry, build_snapshot  # noqa: E402
    from inferred_flags import unknown_flags  # noqa: E402  ★ 标记字典单一来源
except ImportError as _exc:  # pragma: no cover
    print(f"错误：无法导入 shared/ 下模块（{_exc}）", file=sys.stderr)
    raise SystemExit(2)


# ===========================================================================
# 1. 常量表
# ===========================================================================

# --- 质量单位 → μg/L 的乘数（μg/L 为基准）---
MASS_TO_UG_PER_L: dict[str, float] = {
    "ug/l": 1.0,
    "µg/l": 1.0,
    "μg/l": 1.0,
    "ng/ml": 1.0,          # 1 ng/mL = 1 μg/L
    "mg/l": 1000.0,
    "ug/dl": 10.0,
    "µg/dl": 10.0,
    "μg/dl": 10.0,
    "ng/l": 0.001,
    "pg/ml": 0.001,
    "ug/ml": 1000.0,       # 1 μg/mL = 1000 μg/L
    "mg/ml": 1_000_000.0,
}

# --- 尿校正单位（以 μg/g Cr 为基准）---
CREATININE_UNITS: dict[str, float] = {
    "ug/g cr": 1.0,
    "µg/g cr": 1.0,
    "μg/g cr": 1.0,
    "ug/g creatinine": 1.0,
    "mg/g cr": 1000.0,
}

# --- 组织/其他基准（不可与 μg/g Cr 互换）---
TISSUE_UNITS: set[str] = {"ug/g", "µg/g", "μg/g", "mg/kg", "ug/kg", "ng/g"}

# --- 摩尔质量（g/mol），按报告形态 ---
MOLAR_MASS: dict[str, float] = {
    "As": 74.92,
    "As2O3": 197.84,
    "Cd": 112.41,
    "Cr": 52.00,
    "Pb": 207.20,
    "Hg": 200.59,
    "MeHg": 215.63,
    "Ni": 58.69,
    "Ti": 47.87,
    "V": 50.94,
    "PFOA": 414.07,
    "PFOS": 500.13,
}

# ⚠️ 正式分析的口径一律来自 project.yaml（STEP4-TODO.md A1）。
#    下表是 ICRP 89 的**参数实现**（算法保真基准），而**目标单位/矩阵范围**必须来自配置。
#    自检通过 --self-test 使用内置夹具，绝不用于正式分析。

# --- ICRP 89 尿量/肌酐参考值表（见 references/icrp89_urine_reference.md §2）---
ICRP89_TABLE: list[dict] = [
    {"group": "Newborn",    "age": 0,   "sex": None,     "CE": 0.05,  "V": 0.3,  "CC": 0.167},
    {"group": "Infant",     "age": 1,   "sex": None,     "CE": 0.11,  "V": 0.4,  "CC": 0.275},
    {"group": "Toddler",    "age": 5,   "sex": None,     "CE": 0.33,  "V": 0.5,  "CC": 0.66},
    {"group": "Child",      "age": 10,  "sex": None,     "CE": 0.65,  "V": 0.7,  "CC": 0.929},
    {"group": "Adolescent", "age": 15,  "sex": None,     "CE": 1.2,   "V": 1.4,  "CC": 0.857},
    {"group": "Adolescent_Male",   "age": 15, "sex": "Male",   "CE": 1.4, "V": 1.6, "CC": 0.875},
    {"group": "Adolescent_Female", "age": 15, "sex": "Female", "CE": 1.0, "V": 1.2, "CC": 0.833},
    # 原项目沿用值（10–15 区间）；CC 与 CE/V 不自洽，原样保留
    {"group": "Minors",     "age": None, "sex": None,    "CE": 0.925, "V": 1.05, "CC": 0.815,
     "inconsistent": True},
    {"group": "Adults",     "age": 18,  "sex": None,     "CE": 1.35,  "V": 1.4,  "CC": 0.964},
    {"group": "Adult_Male",   "age": 18, "sex": "Male",   "CE": 1.7, "V": 1.6, "CC": 1.063},
    {"group": "Adult_Female", "age": 18, "sex": "Female", "CE": 1.0, "V": 1.2, "CC": 0.833},
    {"group": "Pregnant",   "age": None, "sex": "Female", "CE": 1.275, "V": 2.0, "CC": 0.638,
     "source": "NHANES"},
]

# 自检专用夹具口径（**仅 --self-test 使用**，正式分析必须来自 project.yaml）
FIXTURE_UNIT_POLICY: dict[str, str] = {
    "Urine": "ug/g Cr",
    "Blood": "ug/L",
    "CordBlood": "ug/L",
}


# ===========================================================================
# 2. 单位解析
# ===========================================================================

def normalize_unit(u: str) -> str:
    """单位字符串归一化（仅内部使用；unit_original 永不被覆盖）。"""
    if u is None:
        return ""
    s = str(u).strip()
    s = s.replace("μ", "u").replace("µ", "u").replace("μ", "u")
    s = s.replace("μ", "u")
    s = s.replace(" ", " ")
    s = s.replace("per ", "/")
    # 常见同义写法
    s = s.replace("ug/creatinine", "ug/g cr")
    s = s.lower()
    s = " ".join(s.split())
    return s


def classify_unit(u: str) -> str:
    """分类：mass_volume / creatinine / tissue / molar / unknown"""
    s = normalize_unit(u)
    if s in MASS_TO_UG_PER_L:
        return "mass_volume"
    if s in CREATININE_UNITS:
        return "creatinine"
    if "nmol/l" in s or "umol/l" in s or "mol/l" in s:
        return "molar"
    if s in TISSUE_UNITS or s.endswith("/g") or "/kg" in s:
        return "tissue"
    return "unknown"


# ===========================================================================
# 3. 结果容器
# ===========================================================================

@dataclass
class ConvResult:
    status: str = "OK"              # OK / SKIP / FAILED / WARN
    value_out: float | None = None
    unit_out: str = ""
    path: str = ""
    params: str = ""
    flags: list[str] = field(default_factory=list)
    messages: list[str] = field(default_factory=list)
    group_used: str = ""

    def to_dict(self) -> dict:
        d = asdict(self)
        d["flags"] = ";".join(self.flags)
        d["messages"] = " | ".join(self.messages)
        return d


def _fail(msg, path="", params=""):
    return ConvResult(status="FAILED", path=path, params=params, messages=[msg])


# ===========================================================================
# 4. ICRP 89 参数选择（按 icrp89_urine_reference.md §3 决策树）
# ===========================================================================

def select_icrp_group(population_group: str, sex: str | None = None,
                      age: float | None = None) -> tuple[dict | None, list[str]]:
    """
    参数选择优先级：孕妇 > 年龄段 > 性别。
    返回 (参数行, 告警列表)。
    """
    warns: list[str] = []
    pg = (population_group or "").strip()
    sx = (sex or "").strip()
    if sx in ("", "Both", "Unknown", "None"):
        sx = ""

    # 1) 孕妇优先（覆盖年龄与性别）
    if pg == "Pregnant" or (sex == "Female" and pg == "Pregnant"):
        row = next(r for r in ICRP89_TABLE if r["group"] == "Pregnant")
        return row, warns

    # 2) 按年龄段
    if age is not None:
        if age < 0.5:
            return next(r for r in ICRP89_TABLE if r["group"] == "Newborn"), warns
        if age < 4:
            return next(r for r in ICRP89_TABLE if r["group"] == "Infant"), warns
        if age < 8:
            return next(r for r in ICRP89_TABLE if r["group"] == "Toddler"), warns
        if age < 13:
            return next(r for r in ICRP89_TABLE if r["group"] == "Child"), warns
        if age < 18:
            key = {"Male": "Adolescent_Male", "Female": "Adolescent_Female"}.get(sx, "Adolescent")
            return next(r for r in ICRP89_TABLE if r["group"] == key), warns
        # 成人
        key = {"Male": "Adult_Male", "Female": "Adult_Female"}.get(sx, "Adults")
        return next(r for r in ICRP89_TABLE if r["group"] == key), warns

    # 3) 按人群归类
    if pg in ("Adults", "Elderly"):
        key = {"Male": "Adult_Male", "Female": "Adult_Female"}.get(sx, "Adults")
        return next(r for r in ICRP89_TABLE if r["group"] == key), warns
    if pg == "Minors":
        warns.append("使用 Minors 粗年龄段参数（CC 与 CE/V 不自洽，原项目沿用值）")
        return next(r for r in ICRP89_TABLE if r["group"] == "Minors"), warns

    warns.append(f"未知 population_group=`{pg}`，回退到成人通用参数")
    return next(r for r in ICRP89_TABLE if r["group"] == "Adults"), warns


# ===========================================================================
# 5. 核心换算
# ===========================================================================

def to_ug_per_l(value: float, unit: str, analyte: str | None = None,
                species: str | None = None) -> ConvResult:
    """任意质量/摩尔浓度 → μg/L。"""
    kind = classify_unit(unit)
    s = normalize_unit(unit)

    if kind == "mass_volume":
        factor = MASS_TO_UG_PER_L[s]
        out = value * factor
        msg = []
        if abs(factor - 1.0) > 1e-12:
            msg.append(f"质量单位换算 ×{factor:g}（{unit} → ug/L）")
        return ConvResult(value_out=out, unit_out="ug/L",
                          path=f"{unit}→ug/L", params=f"factor={factor:g}",
                          messages=msg)

    if kind == "molar":
        mass = MOLAR_MASS.get((species or analyte or "").strip())
        if mass is None:
            return _fail(f"摩尔换算需要摩尔质量，但 `{species or analyte}` 不在表中",
                         f"{unit}→ug/L")
        # nmol/L → μg/L： × MW / 1000
        if "nmol/l" in s:
            factor = mass / 1000.0
        elif "umol/l" in s:
            factor = mass
        elif "mol/l" in s:
            factor = mass * 1000.0
        else:
            return _fail(f"无法识别的摩尔单位：{unit}", f"{unit}→ug/L")
        out = value * factor
        return ConvResult(value_out=out, unit_out="ug/L",
                          path=f"{unit}→ug/L",
                          params=f"MW={mass};formula=value*MW/1000;factor={factor:g}",
                          flags=["molar_conversion"],
                          messages=[f"摩尔换算（MW={mass}）"])

    if kind == "creatinine":
        # 需要在调用方先转成 ug/L；此处不接受
        return _fail("肌酐校正单位需走 creatinine_correct() 路径", f"{unit}→ug/L")

    if kind == "tissue":
        return _fail(f"`{unit}` 是组织干重/湿重基准，不可换算为 μg/L 或 μg/g Cr",
                     f"{unit}→FAILED")

    return _fail(f"无法识别的单位：`{unit}`", f"{unit}→FAILED")


def creatinine_correct(value_ug_per_l: float, group_row: dict,
                       mode: str = "CC_direct") -> ConvResult:
    """
    μg/L → μg/g Cr
      CC_direct : 值 / CC
      CEV_equivalent : 值 × V / CE
    """
    if group_row is None:
        return _fail("缺少 ICRP 89 参数行", "ug/L→ug/g Cr")
    CC, CE, V = group_row["CC"], group_row["CE"], group_row["V"]
    if mode == "CEV_equivalent":
        if CE <= 0:
            return _fail(f"CE={CE} 非法", "ug/L→ug/g Cr")
        out = value_ug_per_l * V / CE
        params = f"group={group_row['group']};CE={CE};V={V};path=CEV_equivalent"
    else:
        if CC <= 0:
            return _fail(f"CC={CC} 非法", "ug/L→ug/g Cr")
        out = value_ug_per_l / CC
        params = f"group={group_row['group']};CC={CC};path=CC_direct"
    src = group_row.get("source", "ICRP89")
    params += f";source={src}"
    flags = ["unit_converted_creatinine"]
    msgs = []
    if group_row.get("inconsistent"):
        msgs.append("该人群档的 CC 与 CE/V 不自洽（原项目沿用值，已留痕）")
    return ConvResult(value_out=out, unit_out="ug/g Cr",
                      path=f"ug/L→ICRP89_{group_row['group']}→ug/g Cr",
                      params=params, flags=flags, messages=msgs,
                      group_used=group_row["group"])


def creatinine_to_ug_per_l(value_ug_per_g_cr: float, group_row: dict) -> ConvResult:
    """μg/g Cr → μg/L（反向换算）"""
    if group_row is None:
        return _fail("缺少 ICRP 89 参数行", "ug/g Cr→ug/L")
    CC = group_row["CC"]
    if CC <= 0:
        return _fail(f"CC={CC} 非法", "ug/g Cr→ug/L")
    return ConvResult(value_out=value_ug_per_g_cr * CC, unit_out="ug/L",
                      path=f"ug/g Cr→ICRP89_{group_row['group']}→ug/L",
                      params=f"group={group_row['group']};CC={CC};path=reverse",
                      flags=["unit_converted_volume"],
                      group_used=group_row["group"])


def convert_urine(value: float, unit_original: str, target_unit: str,
                  population_group: str, sex: str | None = None,
                  age: float | None = None, mode: str = "CC_direct",
                  adjusted_hint: str | None = None,
                  analyte: str | None = None,
                  species: str | None = None) -> ConvResult:
    """尿样统一入口：任意单位 → 目标单位（默认 ug/g Cr）。"""
    kind = classify_unit(unit_original)
    row, warns = select_icrp_group(population_group, sex, age)

    # 原文已肌酐校正
    if kind == "creatinine":
        factor = CREATININE_UNITS[normalize_unit(unit_original)]
        out = value * factor
        return ConvResult(status="WARN" if warns else "SKIP",
                          value_out=out, unit_out="ug/g Cr",
                          path="reported_creatinine_corrected",
                          params=f"factor={factor:g};adjusted=Creatinine",
                          messages=["原文已肌酐校正，不做换算"] + warns)

    # 体积浓度 → μg/L → μg/g Cr
    if kind in ("mass_volume", "molar"):
        step1 = to_ug_per_l(value, unit_original, analyte, species)
        if step1.status == "FAILED":
            return step1
        step2 = creatinine_correct(step1.value_out, row, mode)
        if step2.status == "FAILED":
            return step2
        if target_unit == "ug/L":
            # 目标就是 ug/L：只需单位归一
            return ConvResult(value_out=step1.value_out, unit_out="ug/L",
                              path=step1.path, params=step1.params,
                              flags=step1.flags,
                              messages=step1.messages + warns,
                              group_used=step2.group_used)
        # 拼接路径：避免出现 `ug/L→ug/L` 冗余段
        head = step1.path[:-len("→ug/L")] if step1.path.endswith("→ug/L") else step1.path
        full_path = f"{head}→ICRP89_{row['group']}→ug/g Cr"
        return ConvResult(status="WARN" if warns else step2.status,
                          value_out=step2.value_out, unit_out="ug/g Cr",
                          path=full_path,
                          params=step2.params, flags=step1.flags + step2.flags,
                          messages=step1.messages + step2.messages + warns,
                          group_used=row["group"])

    if kind == "tissue":
        return _fail(f"`{unit_original}` 是组织基准，不能作为尿样校正单位",
                     f"{unit_original}→FAILED")

    return _fail(f"尿样单位无法识别：`{unit_original}`", f"{unit_original}→FAILED")


def convert_generic(value: float, unit_original: str, sample_type: str,
                    target_unit: str, population_group: str = "Adults",
                    sex: str | None = None, age: float | None = None,
                    mode: str = "CC_direct", analyte: str | None = None,
                    species: str | None = None) -> ConvResult:
    """按基质分派的统一入口。"""
    st = (sample_type or "").strip()
    if st == "Urine":
        return convert_urine(value, unit_original, target_unit,
                             population_group, sex, age, mode, analyte, species)
    if st in ("Blood", "CordBlood", "Serum", "Plasma"):
        # 血样：仅做质量/摩尔归一，不走 ICRP
        if classify_unit(unit_original) == "creatinine":
            return _fail("血样不应使用肌酐校正单位", f"{unit_original}→FAILED")
        return to_ug_per_l(value, unit_original, analyte, species)
    # 其他基质（母乳/指甲/头发）：由配置决定目标单位，此处仅归一
    return to_ug_per_l(value, unit_original, analyte, species)


def check_input_contract(rows: list[dict], cols: list[str]) -> tuple[bool, str]:
    """
    ★ S4 输入前检：必须是 S3 的**原始提取表**（含 `initial_*` 列）。

    判据：`initial_gm` / `initial_median` / `initial_mean` / `initial_min`
    **至少一列存在且至少有一条记录非空**。

    为什么需要：S4 的输出（含 `gm_summary`）与 S5 的输出（含 `stratum`/`gm`）
    都不含 `initial_*`。若误把它们当输入再跑 S4，会构成"用输出再跑一遍"的
    循环错误——典型表现是全部记录 `FAILED`，或因误取 `gm_summary` 而跳过换算。

    返回 (ok, 错误信息)。
    """
    initial_cols = ["initial_gm", "initial_median", "initial_mean", "initial_min"]
    present = [c for c in initial_cols if c in cols]
    if not present:
        return False, (
            "输入表缺 initial_* 列。这通常是 S3 的原始提取表被 S4 输出覆盖，"
            "或误把 S4/S5 的输出当作输入。请确认输入是 S3 的产物"
            "（含 initial_*、unit_original、stat_type）。\n"
            f"  当前列名（前 20 个）：{', '.join(cols[:20])}\n"
            "  说明见 skills/S4-unit-statistic-conversion/SKILL.md §2.1")
    n_nonempty = sum(
        1 for r in rows
        if any(str(r.get(c) or "").strip() not in ("", "NA", "nan", "None", "未报告")
               for c in present)
    )
    if n_nonempty == 0:
        return False, (
            f"输入表有 {', '.join(present)} 列，但**所有记录该列均为空**。"
            "这通常说明输入是 S4/S5 的输出（已标准化/已合并），"
            "而非 S3 的原始提取表。请确认输入是 S3 的产物"
            "（含 initial_*、unit_original、stat_type）。\n"
            "  说明见 skills/S4-unit-statistic-conversion/SKILL.md §2.1")
    return True, ""


def _read_table(path: Path) -> tuple[list[dict], list[str]]:
    """读取主表，返回 (rows, columns)。"""
    if path.suffix.lower() == ".csv":
        with path.open(encoding="utf-8-sig", newline="") as fh:
            rd = csv.DictReader(fh)
            rows = [dict(r) for r in rd]
            return rows, list(rd.fieldnames or [])
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
        return rows, hdr
    raise ValueError(f"不支持的文件类型：{path.suffix}")


# ===========================================================================
# 6. self-test
# ===========================================================================

def run_self_test() -> int:
    print("=" * 70)
    print("convert_units.py --self-test")
    print("=" * 70)
    checks: list[tuple[str, bool, str]] = []

    def check(name, ok, detail=""):
        checks.append((name, ok, detail))
        print(f"      {'✅' if ok else '❌'} {name}" + (f"  [{detail}]" if detail else ""))

    def close(a, b, tol=1e-9):
        return a is not None and b is not None and abs(a - b) <= tol

    # --- 单位归一 ---
    print("\n[1] 单位字符串归一化")
    check("µg/L 与 ug/L 归一相同", normalize_unit("µg/L") == normalize_unit("ug/L"),
          normalize_unit("µg/L"))
    check("μG/L（大写）归一", normalize_unit("μG/L") == "ug/l", normalize_unit("μG/L"))
    check("ng/mL 分类为 mass_volume", classify_unit("ng/mL") == "mass_volume")
    check("μg/g Cr 分类为 creatinine", classify_unit("μg/g Cr") == "creatinine")
    check("nmol/L 分类为 molar", classify_unit("nmol/L") == "molar")
    check("μg/g 分类为 tissue", classify_unit("μg/g") == "tissue")

    # --- 质量单位换算 ---
    print("\n[2] 质量单位换算 → μg/L")
    check("ng/mL → μg/L 数值不变",
          close(to_ug_per_l(5.0, "ng/mL").value_out, 5.0))
    check("mg/L → μg/L ×1000",
          close(to_ug_per_l(1.0, "mg/L").value_out, 1000.0))
    check("μg/dL → μg/L ×10",
          close(to_ug_per_l(2.0, "μg/dL").value_out, 20.0))
    check("ng/L → μg/L ÷1000",
          close(to_ug_per_l(500.0, "ng/L").value_out, 0.5))
    check("pg/mL → μg/L ÷1000",
          close(to_ug_per_l(300.0, "pg/mL").value_out, 0.3))

    # --- 摩尔换算（验证 As 的已知值）---
    print("\n[3] 摩尔换算（As, MW=74.92）")
    r = to_ug_per_l(13.35, "nmol/L", analyte="As")
    check("13.35 nmol/L As = 1 μg/L",
          close(r.value_out, 13.35 * 74.92 / 1000, 1e-9), f"{r.value_out:.6f}")
    check("登记 molar_conversion 标记", "molar_conversion" in r.flags)
    check("留痕 MW", "MW=74.92" in r.params, r.params)
    check("未知物质 → FAILED", to_ug_per_l(1.0, "nmol/L", analyte="Xx").status == "FAILED")

    # --- ICRP 参数选择 ---
    print("\n[4] ICRP 89 参数选择")
    row, w = select_icrp_group("Adults")
    check("成人未分性别 → CC=0.964", row["CC"] == 0.964, f"group={row['group']}")
    check("成人未分性别不默认男性", row["group"] == "Adults")
    row_m, _ = select_icrp_group("Adults", sex="Male")
    check("成人男 → CC=1.063", row_m["CC"] == 1.063, row_m["group"])
    row_f, _ = select_icrp_group("Adults", sex="Female")
    check("成人女 → CC=0.833", row_f["CC"] == 0.833, row_f["group"])
    row_p, _ = select_icrp_group("Pregnant")
    check("孕妇 → CC=0.638（NHANES）", row_p["CC"] == 0.638, row_p["group"])
    check("孕妇来源标注 NHANES", row_p.get("source") == "NHANES")
    row_mi, w_mi = select_icrp_group("Minors")
    check("Minors → CC=0.815", row_mi["CC"] == 0.815)
    check("Minors 触发不自洽告警", any("不自洽" in x for x in w_mi), str(w_mi))
    row_c, _ = select_icrp_group("Unknown", age=8.0)
    check("年龄 8 → Child（CC=0.929）", row_c["CC"] == 0.929, row_c["group"])
    row_t, _ = select_icrp_group("Unknown", age=0.2)
    check("年龄 0.2 → Newborn（CC=0.167）", row_t["CC"] == 0.167, row_t["group"])

    # --- ICRP 表内部一致性（CE/V = CC）---
    print("\n[5] ICRP 表内部一致性校验（CC = CE/V）")
    tol = 0.002
    for r_ in ICRP89_TABLE:
        if r_.get("inconsistent"):
            check(f"{r_['group']} 已知不自洽（跳过，已标注）", True,
                  f"CC={r_['CC']} vs CE/V={r_['CE']/r_['V']:.3f}")
            continue
        calc = r_["CE"] / r_["V"]
        ok = abs(calc - r_["CC"]) < tol
        check(f"{r_['group']}: CE/V={calc:.3f} ≈ CC={r_['CC']}", ok)

    # --- 尿校正两条路径必须一致 ---
    print("\n[6] 尿校正：CC 直除 与 CE/V 等价式必须一致（相对容差）")
    row_a, _ = select_icrp_group("Adults")
    v = 23.5
    a = creatinine_correct(v, row_a, mode="CC_direct")
    b = creatinine_correct(v, row_a, mode="CEV_equivalent")
    # 注：表中 CC=0.964 为 4 位小数舍入值，CE/V=1.35/1.4=0.964286。
    #     两路径差异约 0.03%，属舍入而非逻辑错误；保留表值以保真。
    rel = abs(a.value_out - b.value_out) / a.value_out
    check("Adults 两路径相对差 <0.1%", rel < 1e-3,
          f"CC={a.value_out:.6f} CEV={b.value_out:.6f} 相对差={rel:.4%}")
    check("CC 与 CE/V 的差异仅来自舍入",
          abs(row_a["CC"] - row_a["CE"] / row_a["V"]) < 3e-4,
          f"CC={row_a['CC']} CE/V={row_a['CE']/row_a['V']:.6f}")
    check("路径编码区分两种模式", "CC_direct" in a.params and "CEV_equivalent" in b.params)

    # --- 完整尿样换算 ---
    print("\n[7] 尿样完整换算 ug/L → ug/g Cr")
    u = convert_urine(12.0, "ug/L", "ug/g Cr", "Adults")
    expect = 12.0 / 0.964
    check("12 ug/L / 0.964 = 12.4481 ug/g Cr",
          close(u.value_out, expect, 1e-6), f"{u.value_out:.6f}")
    check("目标单位正确", u.unit_out == "ug/g Cr")
    check("路径含 ICRP89_Adults", "ICRP89_Adults" in u.path, u.path)
    check("登记 unit_converted_creatinine", "unit_converted_creatinine" in u.flags)

    # --- 已校正记录不换算 ---
    print("\n[8] 已肌酐校正记录：SKIP")
    sk = convert_urine(23.5, "ug/g Cr", "ug/g Cr", "Adults")
    check("状态 SKIP", sk.status == "SKIP", sk.status)
    check("数值不变", close(sk.value_out, 23.5))
    check("路径 reported_creatinine_corrected", sk.path == "reported_creatinine_corrected")
    check("零标记", sk.flags == [], str(sk.flags))

    # --- mg/g Cr 换算 ---
    print("\n[9] mg/g Cr → μg/g Cr ×1000")
    mg = convert_urine(0.0235, "mg/g Cr", "ug/g Cr", "Adults")
    check("0.0235 mg/g Cr = 23.5 μg/g Cr", close(mg.value_out, 23.5, 1e-9))

    # --- 反向换算 ---
    print("\n[10] 反向换算 μg/g Cr → μg/L")
    rev = creatinine_to_ug_per_l(23.5, row_a)
    check("23.5 × 0.964 = 22.654", close(rev.value_out, 23.5 * 0.964, 1e-9),
          f"{rev.value_out:.6f}")
    check("登记 unit_converted_volume", "unit_converted_volume" in rev.flags)

    # --- 往返一致性 ---
    print("\n[11] 往返一致性（ug/L → ug/g Cr → ug/L）")
    fwd = convert_urine(12.0, "ug/L", "ug/g Cr", "Adults")
    back = creatinine_to_ug_per_l(fwd.value_out, row_a)
    check("往返回到 12.0", close(back.value_out, 12.0, 1e-9), f"{back.value_out:.9f}")

    # --- 组织基准保护 ---
    print("\n[12] 组织基准不可当肌酐单位")
    tis = convert_urine(5.0, "μg/g", "ug/g Cr", "Adults")
    check("μg/g → FAILED", tis.status == "FAILED", tis.messages[0] if tis.messages else "")

    # --- 血样不走 ICRP ---
    print("\n[13] 血样不走尿校正")
    bl = convert_generic(2.72, "ug/L", "Blood", "ug/L", "Adults")
    check("血样 ug/L 直通", close(bl.value_out, 2.72))
    check("血样无肌酐标记", "unit_converted_creatinine" not in bl.flags, str(bl.flags))
    bl2 = convert_generic(2.72, "μg/g Cr", "Blood", "ug/L", "Adults")
    check("血样用肌酐单位 → FAILED", bl2.status == "FAILED")

    # --- 未知单位 ---
    print("\n[14] 未知单位保护")
    un = convert_generic(1.0, "furlongs/fortnight", "Urine", "ug/g Cr", "Adults")
    check("未知单位 → FAILED", un.status == "FAILED")

    # --- Minors 告警透传 ---
    print("\n[15] Minors 记录告警透传")
    mn = convert_urine(5.0, "ug/L", "ug/g Cr", "Minors")
    check("Minors 换算成功", mn.status in ("OK", "WARN"), mn.status)
    check("告警含不自洽提示",
          any("不自洽" in m for m in mn.messages), str(mn.messages[:1]))

    print("\n[16] ★ A4：快照指纹块（data-contract.md §4 格式）")
    import tempfile
    from pathlib import Path as _P
    tmpd = _P(tempfile.mkdtemp(prefix="hbm_cu_snap_"))
    tbl = tmpd / "As_2026-06-05.csv"
    tbl.write_text("record_id,study_no\nR1,S001\nR2,S001\nR3,S002\n", encoding="utf-8")
    try:
        snap = build_snapshot(tbl, analyte="As", generator="convert_units.py")
        check("build_snapshot 成功", snap.sha256 != "", snap.fingerprint())
        check("tag 格式 = As_<date>", snap.tag.startswith("As_"), snap.tag)
        check("record_count=3 / study_count=2",
              (snap.record_count, snap.study_count) == (3, 2),
              f"{snap.record_count}/{snap.study_count}")
        blk = snap.yaml_block()
        check("YAML 块含 snapshot: 头", blk.startswith("snapshot:"))
        for k in ("sha256", "generated_at", "config_version", "contract_version",
                  "record_count", "study_count", "generator"):
            check(f"指纹块含 `{k}`", f"{k}:" in blk)
        check("sha256 为 64 位", len(snap.sha256) == 64)
        check("short sha 为 12 位", len(snap.short_sha) == 12)
        # 字节级敏感性
        tbl.write_text(tbl.read_text(encoding="utf-8") + "\n", encoding="utf-8")
        snap2 = build_snapshot(tbl, analyte="As")
        check("★ 字节改动 → 哈希改变（漂移可检出）",
              snap.sha256 != snap2.sha256)
        check("★ 漂移判定：is_stale_against 为 True",
              snap.is_stale_against(snap2))
    except SnapshotError as exc:
        check("build_snapshot 成功", False, str(exc))

    print("\n[17] ★ S4 输入契约前检（防止误把 S4/S5 输出当输入）")
    base_cols = ["record_id", "study_no", "sample_type", "time", "unit_original",
                 "stat_type", "initial_gm"]
    ok_rows = [{"record_id": "R1", "study_no": "S001", "sample_type": "Urine",
                "time": "1995", "unit_original": "ug/L", "stat_type": "GM_only",
                "initial_gm": "32.58"}]
    ok, msg = check_input_contract(ok_rows, base_cols)
    check("含 initial_gm 且有值 → 通过", ok, msg or "OK")

    # 情形 A：完全无 initial_* 列（典型：误用 S5 输出）
    out_cols = ["stratum", "sample_type", "period", "gm", "gsd", "record_count"]
    out_rows = [{"stratum": "Urine | 1980-2000", "gm": "32.58", "gsd": "1.8"}]
    ok_a, msg_a = check_input_contract(out_rows, out_cols)
    check("★ 无 initial_* 列 → 拒绝", not ok_a)
    check("★ 错误信息含 s3/s4/s5", "S4/S5" in msg_a, msg_a.split("\n")[0][:50] + "…")
    check("★ 错误信息含 initial_*", "initial_*" in msg_a)
    check("★ 错误信息含 SKILL.md 指引", "SKILL.md" in msg_a)

    # 情形 B：有 initial_* 列但全空（典型：误用 S4 输出覆盖后残留表头）
    empty_rows = [{"record_id": "R1", "initial_gm": "", "initial_median": "",
                   "initial_mean": "", "initial_min": ""}]
    empty_cols = ["record_id", "initial_gm", "initial_median", "initial_mean",
                  "initial_min", "gm_summary"]
    ok_b, msg_b = check_input_contract(empty_rows, empty_cols)
    check("★ initial_* 列全空 → 拒绝", not ok_b)
    check("★ 提示'已标准化/已合并'", "已标准化" in msg_b or "已合并" in msg_b,
          msg_b.split("\n")[0][:50] + "…")

    # 情形 C：只有 initial_median 有值（合法：报告的是中位数）
    med_rows = [{"record_id": "R1", "initial_median": "7.5", "initial_gm": ""}]
    med_cols = ["record_id", "initial_median", "initial_gm", "unit_original",
                "stat_type"]
    ok_c, _ = check_input_contract(med_rows, med_cols)
    check("仅 initial_median 有值 → 通过（合法输入）", ok_c)
    # 情形 D：只有 initial_min 有值
    ok_d, _ = check_input_contract([{"initial_min": "2.0"}], ["initial_min"])
    check("仅 initial_min 有值 → 通过", ok_d)
    # 情形 E：全为 '未报告' 占位
    nr_rows = [{"initial_gm": "未报告", "initial_median": "NA"}]
    ok_e, _ = check_input_contract(nr_rows, ["initial_gm", "initial_median"])
    check("占位值（未报告/NA）视为空 → 拒绝", not ok_e)

    print("\n[18] ★ 标记闭集校验（单一来源 shared/inferred_flags.py）")
    # 收集本脚本各路径产出的全部标记
    emitted: set[str] = set()
    probes = [
        to_ug_per_l(13.35, "nmol/L", analyte="As"),          # molar_conversion
        convert_urine(12.0, "ug/L", "ug/g Cr", "Adults"),    # unit_converted_creatinine
        convert_urine(23.5, "ug/g Cr", "ug/g Cr", "Adults"), # reported_creatinine_corrected（零标记）
        creatinine_to_ug_per_l(23.5, select_icrp_group("Adults")[0]),  # unit_converted_volume
        convert_urine(5.0, "ug/L", "ug/g Cr", "Minors"),     # + coarse_age（经告警）
    ]
    for r in probes:
        emitted |= set(r.flags)
    bad = unknown_flags(sorted(emitted))
    check("本脚本产出的全部标记都在闭集内", not bad,
          f"越界={bad}" if bad else f"{len(emitted)} 个标记：{sorted(emitted)}")
    check("含 molar_conversion（摩尔换算路径）", "molar_conversion" in emitted)
    check("含 unit_converted_creatinine（尿校正路径）",
          "unit_converted_creatinine" in emitted)
    check("含 unit_converted_volume（反向换算路径）",
          "unit_converted_volume" in emitted)
    # 已肌酐校正的记录必须零标记（零推断子集完整性前提）
    zero = convert_urine(23.5, "ug/g Cr", "ug/g Cr", "Adults")
    check("reported_creatinine_corrected → 零标记", zero.flags == [], str(zero.flags))

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
# 7. CLI
# ===========================================================================

def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="convert_units.py",
        description="单位换算 + 尿校正（ICRP 89）（HBM-Meta-Agent / S4）",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="退出码：0 成功 / 1 存在 FAILED 记录 / 2 前置条件不满足",
    )
    p.add_argument("--input", type=Path, help="主表（.csv / .xlsx）")
    p.add_argument("--config", type=Path, help="project.yaml（骨架版可缺省；第四步改为必需）")
    p.add_argument("--out", type=Path, help="换算后主表（.csv）")
    p.add_argument("--trace", type=Path, help="换算痕迹表（.csv）")
    p.add_argument("--report", type=Path,
                   help="标准化报告（.md，头部含快照指纹块）；缺省时派生自 --out 同目录")
    p.add_argument("--snapshot-registry", type=Path,
                   help="快照登记表（如 _state/snapshots.csv，追加不覆盖）")
    p.add_argument("--sample-type", help="仅处理指定基质（Urine / Blood / …）")
    p.add_argument("--self-test", action="store_true", help="运行换算逻辑自检")
    p.add_argument("--version", action="version", version="hbm-meta S4 convert_units 1.0.0")
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    if args.self_test:
        return run_self_test()

    if not args.input:
        print("错误：需要 --input，或使用 --self-test", file=sys.stderr)
        return 2

    # ★ S4 输入前检：读取并校验输入的**表类型正确**
    try:
        rows, cols = _read_table(args.input)
    except FileNotFoundError:
        print(f"错误：主表不存在：{args.input}", file=sys.stderr)
        return 2
    except ValueError as exc:
        print(f"错误：{exc}", file=sys.stderr)
        return 2

    ok_contract, contract_msg = check_input_contract(rows, cols)
    if not ok_contract:
        print(f"错误：{contract_msg}", file=sys.stderr)
        return 2

    # ★ A1+A3：配置缺失/解析失败/缺区块 → 硬错误退出码 2；口径一律来自配置
    try:
        cfg = load_config(args.config,
                          require=["unit_policy", "urine_reference", "standardization"])
    except ConfigError as exc:
        print(f"错误：{exc}", file=sys.stderr)
        return 2
    unit_policy = {m: cfg.target_unit(m) for m in cfg.unit_policy if isinstance(cfg.unit_policy[m], dict)}
    if not unit_policy:
        print("错误：project.yaml → unit_policy 未解析出任何目标单位。", file=sys.stderr)
        return 2
    # ★ 尿校正路径由配置决定（不得硬编码）
    creat_mode = cfg.standardization["creatinine_path_mode"]
    print(f"配置：{cfg.source_path}  ({cfg.summary()})")
    print(f"标准化口径：creatinine_path_mode={creat_mode}")

    # ★ A4：构建快照指纹（本脚本是最上游生成快照的地方）
    try:
        snap = build_snapshot(args.input, analyte=cfg.analyte,
                              config_version=cfg.config_version,
                              generator="convert_units.py")
    except SnapshotError as exc:
        print(f"错误：无法构建快照指纹：{exc}", file=sys.stderr)
        return 2
    print(f"快照指纹：{snap.fingerprint()}（记录 {snap.record_count} / 研究 {snap.study_count}）")
    if args.snapshot_registry:
        append_snapshot_registry(args.snapshot_registry, snap,
                                 note=f"standardized by convert_units.py; "
                                      f"creatinine_path_mode={creat_mode}")
        print(f"已登记快照：{args.snapshot_registry}")

    out_rows, trace_rows = [], []
    n_failed = 0
    for row in rows:
        if args.sample_type and str(row.get("sample_type", "")).strip() != args.sample_type:
            continue
        st = str(row.get("sample_type", "")).strip()
        target = unit_policy.get(st, "ug/L")
        # ★ 单位换算是**线性缩放**，对原文报告的任何浓度点估计都适用：
        #   优先 initial_gm → initial_median → initial_mean。
        #   （此前只取 initial_gm，导致仅报告中位数/均值的记录被误判为"缺值" —— 自查发现）
        raw, value_field = None, ""
        for cand in ("initial_gm", "initial_median", "initial_mean"):
            v = str(row.get(cand) or "").strip()
            if v not in ("", "NA", "nan", "None", "未报告"):
                raw, value_field = v, cand
                break
        try:
            val = float(raw)
        except (TypeError, ValueError):
            val = None
        if val is None:
            res = _fail("缺少可换算的浓度值（initial_gm / initial_median / initial_mean 均为空；"
                        "若原文仅报告 IQR 或极值，先由 S4 的统计量换算步骤求点估计）",
                        "no_value→FAILED")
            value_field = "（无）"
        else:
            res = convert_generic(val, str(row.get("unit_original", "")), st, target,
                                  str(row.get("population_group", "") or "Adults"),
                                  str(row.get("gender", "")) or None,
                                  None, creat_mode,   # ★ 路径来自配置
                                  str(row.get("analyte", "")) or None,
                                  str(row.get("analyte_species", "")) or None)
        if res.status == "FAILED":
            n_failed += 1
        out_rows.append({**row, "unit_final": res.unit_out or row.get("unit_final", ""),
                         "conversion_path": res.path,
                         "conversion_params": res.params,
                         "unit_status": res.status,
                         "unit_message": " | ".join(res.messages)})
        trace_rows.append({
            "record_id": row.get("record_id", ""),
            "sample_type": st,
            "value_field": value_field,
            "value_in": raw if raw is not None else "",
            "unit_in": row.get("unit_original", ""),
            "unit_out": res.unit_out, "value_out": res.value_out,
            "path": res.path, "params": res.params,
            "flags": ";".join(res.flags), "status": res.status,
            "message": " | ".join(res.messages),
        })

    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        if out_rows:
            with args.out.open("w", encoding="utf-8-sig", newline="") as fh:
                w = csv.DictWriter(fh, fieldnames=list(out_rows[0].keys()))
                w.writeheader()
                w.writerows(out_rows)
            print(f"换算后主表：{args.out}")

    if args.trace:
        args.trace.parent.mkdir(parents=True, exist_ok=True)
        if trace_rows:
            with args.trace.open("w", encoding="utf-8-sig", newline="") as fh:
                w = csv.DictWriter(fh, fieldnames=list(trace_rows[0].keys()))
                w.writeheader()
                w.writerows(trace_rows)
            print(f"换算痕迹：{args.trace}")

    # ★ A4：标准化报告（头部带快照指纹块，格式见 data-contract.md §4）
    report_path = args.report
    if report_path is None and args.out is not None:
        report_path = args.out.with_name("standardization_report.md")
    if report_path is not None:
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text(
            render_standardization_report(snap, cfg, creat_mode, unit_policy,
                                          out_rows, trace_rows, n_failed),
            encoding="utf-8")
        print(f"标准化报告：{report_path}")

    print(f"处理记录数={len(out_rows)}  FAILED={n_failed}")
    return 1 if n_failed else 0


def render_standardization_report(snap, cfg, creat_mode: str, unit_policy: dict,
                                  out_rows: list, trace_rows: list,
                                  n_failed: int) -> str:
    """
    生成标准化报告（Markdown），**头部为快照指纹块**。
    用途：GATE-4 待裁决包的正文 + 快照冻结依据。
    """
    from collections import Counter
    status_c = Counter(r.get("unit_status", "") for r in out_rows)
    path_c = Counter(r.get("conversion_path", "") for r in trace_rows)
    unit_c = Counter(str(r.get("unit_final", "")) for r in out_rows)

    lines = [
        "# 标准化报告（Standardization Report）",
        "",
        "## 0. 快照指纹",
        "",
        "> 由脚本自动生成；人工不得修改。任何数字都可追溯到该快照。",
        "",
        snap.markdown_block(),
        "",
        "## 1. 配置与口径（GATE-0 冻结值，此处仅复核）",
        "",
        "| 项 | 值 | 来源 |",
        "|---|---|---|",
        f"| `analyte` | {cfg.analyte} | `project.yaml` |",
        f"| `country` | {cfg.country} | `project.yaml` |",
        f"| `matrix_scope` | {', '.join(cfg.matrix_scope) or '（未声明）'} | `project.yaml` |",
        f"| 矩阵目标单位 | {', '.join(f'{k}→{v}' for k, v in unit_policy.items())} | `unit_policy` |",
        f"| 分期方案 | {cfg.period_scheme.get('name', 'unnamed')} | `period_scheme`（冻结日 {cfg.period_freeze_date}） |",
        f"| 中位数→GM 策略 | {cfg.standardization.get('median_to_gm_strategy')} | `standardization` |",
        f"| Wan 变体 | {cfg.standardization.get('wan_variant')} | `standardization` |",
        f"| 尿校正路径 | {creat_mode} | `standardization` |",
        f"| 配置版本 | {cfg.config_version} | `project.yaml` |",
        "",
        "> ⚠️ 若上表任一项与 GATE-0 冻结值不一致 → 停止，不得继续。",
        "",
        "## 2. 换算结果概况",
        "",
        f"- 输入记录数：{snap.record_count}（研究数 {snap.study_count}）",
        f"- 处理记录数：{len(out_rows)}",
        f"- `FAILED`（不可换算，不得进入合并）：**{n_failed}**",
        "",
        "### 2.1 状态分布",
        "",
        "| 状态 | 记录数 | 处置 |",
        "|---|---|---|",
    ]
    meaning = {"OK": "进入合并池", "SKIP": "无需换算，进入合并池",
               "WARN": "进入合并池，进敏感性候选",
               "FAILED": "★ **不得进入合并**，须计入局限说明"}
    for st in ("OK", "SKIP", "WARN", "FAILED"):
        if status_c.get(st):
            lines.append(f"| `{st}` | {status_c[st]} | {meaning.get(st, '')} |")

    lines += ["", "### 2.2 目标单位分布", "", "| `unit_final` | 记录数 |", "|---|---|"]
    for u, n in unit_c.most_common():
        lines.append(f"| `{u or '（空）'}` | {n} |")

    lines += ["", "### 2.3 换算路径分布", "", "| `conversion_path` | 记录数 |", "|---|---|"]
    for p, n in path_c.most_common(20):
        lines.append(f"| `{p or '（空）'}` | {n} |")

    lines += ["", "## 3. 交付产物", "",
              "| 产物 | 说明 |", "|---|---|",
              "| 标准化主表（`--out`） | 含 `unit_final` / `conversion_path` / `conversion_params` |",
              "| 换算痕迹表（`--trace`） | 每条记录的完整换算链 |",
              "| 本报告 | GATE-4 待裁决包正文 + 快照冻结依据 |",
              "",
              "## 4. GATE-4 出口自检", "",
              "- [ ] 所有记录 `conversion_path` 非空（`FAILED` 亦须有失败路径）",
              "- [ ] 所有记录 `unit_final` 等于 `unit_policy` 对应目标单位",
              "- [ ] `FAILED` 清单已产出并提交人工",
              "- [ ] 量级合理性检查已完成（尿 1–100 ug/g Cr；血 0.1–10 ug/L）",
              "- [ ] **快照指纹已记录**（见 §0）",
              "- [ ] **人工批准冻结**（AI 不得自行开启 GATE-4）",
              "", "---", "",
              "> 快照指纹由 `shared/snapshot.py` 生成，格式见 `shared/data-contract.md` §4。",
              ""]
    return "\n".join(lines)


if __name__ == "__main__":
    raise SystemExit(main())
