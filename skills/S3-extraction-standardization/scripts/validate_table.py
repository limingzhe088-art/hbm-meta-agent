#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
validate_table.py — 主表契约校验（S3 / S4 门禁）

阶段状态：第三步骨架版
  ✅ 完整 CLI（--input / --config / --out）
  ✅ --self-test（用自带脱敏样例跑通）
  ✅ 核心校验逻辑（8 条，对应 data-contract.md §7）
  ✅ 可疑值模式扫描（对应 missing_field_watchlist.md 清单 B）
  ⏳ 第四步待办：口径已改为从 project.yaml 读取（A1 已完成）；快照指纹（A4）待接入

用法
  python validate_table.py --input 主表.xlsx --config project.yaml --out 报告.md
  python validate_table.py --input 主表.csv --self-test        # 自检模式
  python validate_table.py --self-test                          # 仅跑内置夹具

退出码
  0 无 ERROR        1 存在 ERROR        2 前置条件不满足（闸门未过 / 文件缺失）
"""
from __future__ import annotations

import argparse
import csv
import json
import sys
from pathlib import Path

# Windows 控制台默认 GBK 无法输出 ✅/❌ 等字符；尽量切到 UTF-8
try:  # pragma: no cover - 环境相关
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except (AttributeError, ValueError):  # Python < 3.7 或非 TTY
    pass

# ★ 唯一配置入口（shared/project_config.py）——禁止本脚本自带口径默认值
_SHARED = Path(__file__).resolve().parent.parent.parent.parent / "shared"
if str(_SHARED) not in sys.path:
    sys.path.insert(0, str(_SHARED))
try:
    from project_config import Config, ConfigError, load_config  # noqa: E402
    from inferred_flags import (  # noqa: E402
        CLOSED_FLAG_SET, CONTRACT_DOCUMENTED, NO_IMPUTE_FIELDS,
        inferred_flag_count, unknown_flags,
    )
except ImportError as _exc:  # pragma: no cover
    print(f"错误：无法导入 shared/ 下模块（{_exc}）", file=sys.stderr)
    raise SystemExit(2)

# ---------------------------------------------------------------------------
# 契约常量（第四步将改为从 project.yaml 读取）
# ---------------------------------------------------------------------------

CONTRACT_VERSION = "1.0.0"

# data-contract.md §3 —— 必填列
REQUIRED_COLUMNS = [
    # 来源标识
    "record_id", "study_no", "title", "author", "doi", "t_publication",
    # 时空标识
    "time", "country", "province", "region",
    # 人群标识
    "population", "population_group",
    # 边界风险标记
    "flag_occupational", "flag_disease", "flag_endemic_area", "flag_mixed_occupational",
    "inclusion_decision",
    # 基质与检测
    "sample_type", "analyte",
    # 原文统计量
    "stat_type", "unit_original", "sample_size",
    # 审计元数据
    "extracted_by", "verified", "snapshot_tag", "row_hash",
]

# 条件必填（列, 触发列, 触发值, 必填列清单）
CONDITIONAL_REQUIRED = [
    ("sample_type", "Blood", ["blood_matrix"]),
    ("sample_type", "Urine", ["adjusted"]),
    ("inclusion_decision", "Exclude", ["exclusion_reason"]),
]

# 枚举白名单（data-contract.md §3）
ENUMS = {
    "sample_type": ["Urine", "Blood", "CordBlood", "BreastMilk", "Nail", "Hair",
                    "Serum", "Plasma", "Other"],
    "population_group": ["Adults", "Minors", "Pregnant", "Elderly", "Mixed", "Unknown"],
    "gender": ["Male", "Female", "Both", "Unknown"],
    "stat_type": ["GM_GSD", "GM_only", "AM_SD", "Median_IQR", "Median_Range",
                  "Median_only", "Min_Max", "P25_P75", "Other"],
    "adjusted": ["No", "Creatinine", "SpecificGravity", "ReferenceConversion",
                 "NotApplicable"],
    "blood_matrix": ["WholeBlood", "Serum", "Plasma", "Unspecified"],
    "analyte_species": ["Total", "iAs", "iAs+MMA+DMA", "MeHg", "Unknown"],
    "inclusion_decision": ["Include", "Exclude", "Pending"],
    "extracted_by": ["AI", "Human", "AI+Human"],
    "region": ["North", "Northeast", "East", "Central", "South", "Southwest",
               "Northwest", "UNKNOWN"],
}

# 禁止插补的四个核心字段：★ 由 shared/inferred_flags.py 导入（上方 import），不在此重复定义。

# 推断标记字典：★ 单一来源（shared/inferred_flags.py），不再自建副本。
# 教训：本文件曾自建一份 16 项列表，19→20 项扩充时漏同步，导致闭集校验形同虚设。
INFERRED_FLAG_DICT = sorted(CLOSED_FLAG_SET)

# ⚠️ 口径一律来自 project.yaml；**本脚本不提供任何默认口径**（STEP4-TODO.md A1）。
#    仅便于自检的内置夹具配置，通过 --self-test 使用，绝不用于正式分析。
FIXTURE_CONFIG = {
    "unit_policy": {"Urine": "ug/g Cr", "Blood": "ug/L", "CordBlood": "ug/L"},
    "period_scheme": {"source": "self-test fixture"},
    "verified_ratio_threshold": 0.90,
    "_fixture": True,
}

MISSING = {"", "na", "n/a", "nan", "none", "null", "-", "—", "未报告"}


# ---------------------------------------------------------------------------
# 读写
# ---------------------------------------------------------------------------

def read_table(path: Path) -> tuple[list[dict], list[str]]:
    """读取主表（.csv / .xlsx），返回 (rows, columns)。"""
    if not path.exists():
        raise FileNotFoundError(f"主表不存在：{path}")
    suffix = path.suffix.lower()
    if suffix == ".csv":
        with path.open(encoding="utf-8-sig", newline="") as fh:
            reader = csv.DictReader(fh)
            rows = [dict(r) for r in reader]
            return rows, list(reader.fieldnames or [])
    if suffix in (".xlsx", ".xlsm"):
        try:
            import openpyxl
        except ImportError as exc:  # pragma: no cover
            raise SystemExit("需要 openpyxl：pip install openpyxl") from exc
        wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
        ws = wb[wb.sheetnames[0]]
        it = ws.iter_rows(values_only=True)
        header = [str(h).strip() if h is not None else "" for h in next(it)]
        rows = []
        for r in it:
            if r is None or all(v is None for v in r):
                continue
            rows.append({header[i]: (r[i] if i < len(r) else None)
                         for i in range(len(header))})
        wb.close()
        return rows, header
    if suffix == ".xls":
        raise SystemExit("不支持旧版 .xls，请先另存为 .xlsx 或 .csv")
    raise SystemExit(f"不支持的文件类型：{suffix}")


def read_config(path: Path | None) -> dict:
    """
    ★ 从 project.yaml 读取口径。**缺失或解析失败 → ConfigError（硬错误）**。
    返回值是供校验器使用的扁平口径字典。

    ⚠️ `unit_policy` 必须**归一化为「基质 → 目标单位字符串」**。
    此前直接把 `cfg.raw["unit_policy"]`（值为 `{target: ...}` 的嵌套字典）传出，
    导致规则 5（单位口径一致性）拿嵌套字典与字符串比较，**永远报 ERROR** —— 自查发现。
    """
    cfg = load_config(path, require=["unit_policy"])
    out = dict(cfg.raw)
    # 归一化单位口径：{'Urine': {'target': 'ug/g Cr'}} → {'Urine': 'ug/g Cr'}
    out["unit_policy"] = {m: cfg.target_unit(m) for m in cfg.unit_policy}
    th = (cfg.raw.get("validation") or {}).get("verified_ratio_threshold")
    out["verified_ratio_threshold"] = 0.90 if th is None else float(th)
    return out


# ---------------------------------------------------------------------------
# 工具
# ---------------------------------------------------------------------------

def is_missing(v) -> bool:
    if v is None:
        return True
    return str(v).strip().lower() in MISSING


def as_float(v):
    if is_missing(v):
        return None
    try:
        return float(str(v).strip())
    except (TypeError, ValueError):
        return None


def as_bool(v) -> bool:
    if isinstance(v, bool):
        return v
    return str(v).strip().lower() in {"true", "1", "yes", "y", "是"}


class Report:
    """收集 ERROR / WARN 并生成 Markdown 报告。"""

    def __init__(self) -> None:
        self.errors: list[dict] = []
        self.warns: list[dict] = []

    def err(self, rule: str, record: str, field: str, detail: str) -> None:
        self.errors.append({"rule": rule, "record": record, "field": field, "detail": detail})

    def warn(self, rule: str, record: str, field: str, detail: str) -> None:
        self.warns.append({"rule": rule, "record": record, "field": field, "detail": detail})

    @property
    def ok(self) -> bool:
        return not self.errors

    def to_markdown(self, source: str, n_rows: int, cfg: dict) -> str:
        lines = [
            "# 主表契约校验报告",
            "",
            f"- 数据源：`{source}`",
            f"- 记录数：{n_rows}",
            f"- 契约版本：`{CONTRACT_VERSION}`",
            f"- ERROR：**{len(self.errors)}**　WARN：{len(self.warns)}",
            f"- 结论：{'✅ 通过' if self.ok else '❌ 未通过（存在 ERROR，流程阻断）'}",
            "",
        ]
        if cfg.get("_yaml_warning"):
            lines += [f"> ⚠️ {cfg['_yaml_warning']}", ""]

        lines += ["## ERROR（阻断）", ""]
        if self.errors:
            lines += ["| 规则 | record_id | 字段 | 详情 |", "|---|---|---|---|"]
            for e in self.errors:
                lines.append(f"| {e['rule']} | {e['record']} | `{e['field']}` | {e['detail']} |")
        else:
            lines.append("无。")

        lines += ["", "## WARN（提示，不阻断）", ""]
        if self.warns:
            lines += ["| 规则 | record_id | 字段 | 详情 |", "|---|---|---|---|"]
            for w in self.warns:
                lines.append(f"| {w['rule']} | {w['record']} | `{w['field']}` | {w['detail']} |")
        else:
            lines.append("无。")

        lines += [
            "",
            "---",
            "",
            "> 规则来源：`shared/data-contract.md` §7（8 条校验）与 "
            "`skills/S3-extraction-standardization/references/missing_field_watchlist.md`（清单 B 可疑值模式）。",
            "",
        ]
        return "\n".join(lines)


# ---------------------------------------------------------------------------
# 校验主体
# ---------------------------------------------------------------------------

def validate(rows: list[dict], columns: list[str], cfg: dict) -> Report:
    rep = Report()
    colset = set(columns)

    # --- 规则 1：列存在性 ---
    missing_cols = [c for c in REQUIRED_COLUMNS if c not in colset]
    if missing_cols:
        rep.err("1-列存在性", "-", ",".join(missing_cols[:8]),
                f"缺少 {len(missing_cols)} 个必填列；契约要求见 data-contract.md §3")

    for i, row in enumerate(rows, start=1):
        rid = str(row.get("record_id") or f"#row{i}").strip()

        # --- 规则 2：枚举值 ---
        for col, allowed in ENUMS.items():
            if col not in colset:
                continue
            v = row.get(col)
            if is_missing(v):
                continue
            if str(v).strip() not in allowed:
                rep.err("2-枚举", rid, col,
                        f"值 `{v}` 不在枚举内：{'/'.join(allowed)}")

        # --- 规则 3：条件必填 ---
        for trigger_col, trigger_val, needs in CONDITIONAL_REQUIRED:
            if str(row.get(trigger_col) or "").strip() == trigger_val:
                for need in needs:
                    if need not in colset or is_missing(row.get(need)):
                        rep.err("3-条件必填", rid, need,
                                f"当 {trigger_col}={trigger_val} 时必填")

        # --- 规则 4：数值合法性 ---
        ss = as_float(row.get("sample_size"))
        if ss is not None and ss <= 0:
            rep.err("4-数值", rid, "sample_size", f"必须 > 0，当前 {ss}")
        t = as_float(row.get("time"))
        if t is not None and not (1900 <= t <= 2100):
            rep.err("4-数值", rid, "time", f"越界：{t}")
        for gm_col in ("initial_gm", "gm_summary", "initial_gsd", "gsd_summary"):
            if gm_col in colset:
                g = as_float(row.get(gm_col))
                if g is not None and g <= 0:
                    rep.err("4-数值", rid, gm_col, f"必须 > 0，当前 {g}")

        # --- 规则 6：推断标记完备性 ---
        path_v = str(row.get("conversion_path") or "").strip()
        flags_v = str(row.get("inferred_flags") or "").strip()
        if path_v and ("Wan_" in path_v or "unit_converted" in path_v) and not flags_v:
            rep.err("6-推断标记", rid, "inferred_flags",
                    f"conversion_path=`{path_v}` 含推断换算，但 inferred_flags 为空")

        # --- 规则 7：重复检测（同研究同基质同人群同时间）---
        # 由 build_duplicate_index 统一处理，见下

        # === 清单 B：可疑值模式 ===
        gm = as_float(row.get("initial_gm"))
        am = as_float(row.get("initial_mean"))
        med = as_float(row.get("initial_median"))
        p25 = as_float(row.get("initial_p25"))
        p75 = as_float(row.get("initial_p75"))
        lo = as_float(row.get("initial_min"))
        hi = as_float(row.get("initial_max"))
        gsd = as_float(row.get("initial_gsd"))

        if gm is not None and am is not None and am > 0 and gm > am * 1.5:
            rep.warn("B2-GM>AM", rid, "initial_gm",
                     f"GM={gm} 比 AM={am} 高 50% 以上，疑似提取错位")
        if med is not None and p25 is not None and p25 > med:
            rep.err("B2-分位矛盾", rid, "initial_p25", f"P25={p25} > 中位数={med}")
        if med is not None and p75 is not None and p75 < med:
            rep.err("B2-分位矛盾", rid, "initial_p75", f"P75={p75} < 中位数={med}")
        if lo is not None and hi is not None and lo > hi:
            rep.err("B2-极值矛盾", rid, "initial_min", f"min={lo} > max={hi}")
        if gsd is not None and gsd < 1.0:
            rep.warn("B2-GSD<1", rid, "initial_gsd", f"GSD={gsd} < 1.0，疑似填了算术 SD")
        if gm is not None and lo is not None and gm < lo:
            rep.warn("B2-GM<min", rid, "initial_gm", f"GM={gm} < min={lo}")
        if gm is not None and hi is not None and gm > hi:
            rep.warn("B2-GM>max", rid, "initial_gm", f"GM={gm} > max={hi}")

        # 时间 vs 发表年（本项目最高频错误）
        # 注：采样年晚于发表年是硬矛盾；相隔 0–3 年为正常发表滞后，不告警。
        pub = as_float(row.get("t_publication"))
        if t is not None and pub is not None and t > pub:
            rep.err("B4-时间", rid, "time",
                    f"采样年 {t:.0f} 晚于发表年 {pub:.0f}，逻辑矛盾（疑似把发表年填进了 time）")
        if t is not None and pub is not None and t == pub:
            rep.warn("B4-时间", rid, "time",
                     f"采样年与发表年相同（{t:.0f}），疑似误填发表年份，请回原文确认")

        # 尿样量级
        st = str(row.get("sample_type") or "").strip()
        if st == "Urine":
            g = gm if gm is not None else as_float(row.get("gm_summary"))
            if g is not None and (g > 1000 or g < 0.1):
                rep.warn("B1-量级", rid, "unit_original",
                         f"尿样浓度 {g} 量级异常（常见 1–100 μg/g Cr），先怀疑单位")

        # 规则 5：单位口径一致性（骨架版：仅在有 unit_final 时检查）
        if "unit_final" in colset and not is_missing(row.get("unit_final")):
            expect = cfg.get("unit_policy", {}).get(st)
            # 归一化：兼容 {'target': '...'} 形式的配置（防"永远报错"）
            if isinstance(expect, dict):
                expect = expect.get("target", "")
            cur = str(row["unit_final"]).strip()
            adj = str(row.get("adjusted") or "").strip()
            if expect and cur != expect:
                # 例外：比重校正记录**保留原文单位**（不套 ICRP）—— 属预期行为，
                # 降为 WARN 并提示进『校正方式』分层（见 S4 unit_conversion_playbook §4.3）
                if adj == "SpecificGravity":
                    rep.warn("5-单位口径", rid, "unit_final",
                             f"比重校正记录保留 `{cur}`（原文单位），未套 ICRP；"
                             f"该基质目标单位为 `{expect}` → 进『校正方式』分层")
                else:
                    rep.err("5-单位口径", rid, "unit_final",
                            f"应为 `{expect}`，当前 `{cur}`")

        # V-04：核心字段禁止插补
        # 支持精确形式 `value_interpolated_<field>`（精确定位是哪个字段被插补）
        for f in NO_IMPUTE_FIELDS:
            if f"value_interpolated_{f}" in flags_v:
                rep.err("V-04-禁止插补", rid, f,
                        "核心字段被标记为插补（严重违规，需人工复核）")
        if "value_interpolated" in flags_v and "value_interpolated_" not in flags_v:
            for f in NO_IMPUTE_FIELDS:
                if f in colset and not is_missing(row.get(f)):
                    rep.err("V-04-禁止插补", rid, f,
                            "核心字段被标记为插补（严重违规，需人工复核）")
                    break

    # --- 规则 7：组合重复 ---
    seen: dict[tuple, str] = {}
    key_cols = ["study_no", "sample_type", "population", "time"]
    if all(c in colset for c in key_cols):
        for i, row in enumerate(rows, start=1):
            rid = str(row.get("record_id") or f"#row{i}").strip()
            key = tuple(str(row.get(c) or "").strip() for c in key_cols)
            if key in seen:
                rep.warn("7-疑似重复", rid, ",".join(key_cols),
                         f"与 {seen[key]} 的四元组完全相同，请确认是否为不同记录")
            else:
                seen[key] = rid

    # --- 规则 8：快照冻结校验（verified 比例）---
    if rows and "verified" in colset:
        n = sum(1 for r in rows if as_bool(r.get("verified")))
        ratio = n / len(rows)
        thr = float(cfg.get("verified_ratio_threshold", 0.90))
        if ratio < thr:
            rep.err("8-冻结校验", "-", "verified",
                    f"verified 比例 {ratio:.0%} < 阈值 {thr:.0%}（S5 主结论要求 100%）")
        else:
            rep.warn("8-冻结校验", "-", "verified", f"verified 比例 {ratio:.0%}（达标，进入 S5 前须达 100%）")

    # --- record_id 唯一性 ---
    if "record_id" in colset:
        ids = [str(r.get("record_id") or "").strip() for r in rows]
        dup = {x for x in ids if x and ids.count(x) > 1}
        for d in dup:
            rep.err("7-唯一性", d, "record_id", "record_id 重复，违反契约 §2 行标识规则")

    return rep


# ---------------------------------------------------------------------------
# self-test
# ---------------------------------------------------------------------------

FIXTURE_HEADER = [
    "record_id", "study_no", "title", "author", "doi", "t_publication",
    "time", "country", "province", "city", "region",
    "population", "population_group", "gender",
    "flag_occupational", "flag_disease", "flag_endemic_area",
    "flag_mixed_occupational", "inclusion_decision", "exclusion_reason",
    "sample_type", "blood_matrix", "analyte", "analyte_species",
    "detection_method", "qc_reported",
    "stat_type", "initial_gm", "initial_mean", "initial_sd", "initial_median",
    "initial_p25", "initial_p75", "initial_min", "initial_max", "initial_gsd",
    "unit_original", "sample_size",
    "adjusted", "unit_final", "gm_summary", "gsd_summary",
    "conversion_path", "inferred_flags",
    "extracted_by", "verified", "snapshot_tag", "row_hash",
]

FIXTURE_ROWS = [
    # R00001 正常尿样记录
    ["R00001", "S001", "Urinary arsenic in Chinese adults", "Zhang", "10.xxxx/a",
     "2020", "2018", "China", "云南省", "昆明市", "Southwest",
     "Adults", "Adults", "Both",
     "FALSE", "FALSE", "FALSE", "FALSE", "Include", "",
     "Urine", "", "As", "Total", "ICP-MS", "TRUE",
     "GM_GSD", "23.50", "", "", "", "", "", "", "", "2.1",
     "ug/g Cr", "1200",
     "Creatinine", "ug/g Cr", "23.50", "2.1",
     "reported_GM", "", "AI+Human", "TRUE", "DEMO_2026-01-01", "h1"],
    # R00002 血样 + serum 基质存疑：缺 blood_matrix → 条件必填 ERROR
    ["R00002", "S002", "Serum arsenic among pregnant women", "Li", "10.xxxx/b",
     "2021", "2019", "China", "北京市", "北京市", "North",
     "Pregnant women", "Pregnant", "Female",
     "FALSE", "FALSE", "FALSE", "FALSE", "Pending", "",
     "Blood", "", "As", "Total", "ICP-MS", "TRUE",
     "AM_SD", "", "2.72", "0.90", "", "", "", "", "", "",
     "ug/L", "800",
     "", "", "", "",
     "", "", "AI", "FALSE", "DEMO_2026-01-01", "h2"],
    # R00003 时间晚于发表年 → B4 ERROR；枚举非法 → 规则2 ERROR
    ["R00003", "S003", "Cord blood arsenic review", "Wang", "10.xxxx/c",
     "2015", "2020", "China", "江苏省", "南京市", "East",
     "Infants", "Newborn", "Both",
     "FALSE", "FALSE", "FALSE", "FALSE", "Include", "",
     "CordBlood", "", "As", "Total", "Unknown", "FALSE",
     "Median_IQR", "", "", "", "4.89", "3.10", "6.80", "", "", "",
     "μg/L", "300",
     "", "", "", "",
     "", "", "AI", "FALSE", "DEMO_2026-01-01", "h3"],
    # R00004 分位矛盾 + GSD<1 + 核心字段插补 → 多条 ERROR/WARN
    ["R00004", "S004", "Urinary arsenic children", "Chen", "NO_DOI",
     "2019", "2019", "China", "贵州省", "贵阳市", "Southwest",
     "Children", "Minors", "Both",
     "FALSE", "FALSE", "TRUE", "FALSE", "Include", "",
     "Urine", "", "As", "Total", "HG-AAS", "FALSE",
     "Median_IQR", "", "", "", "55.33", "60.00", "40.00", "5.00", "200.00", "0.80",
     "ug/L", "450",
     "ReferenceConversion", "ug/g Cr", "55.33", "0.80",
     "median_to_GM", "gm_from_iqr;value_interpolated_sample_size", "AI", "FALSE",
     "DEMO_2026-01-01", "h4"],
]


def write_fixture(tmpdir: Path) -> Path:
    """写出脱敏自检夹具（含故意植入的错误，用于验证校验器能抓到）。"""
    tmpdir.mkdir(parents=True, exist_ok=True)
    path = tmpdir / "selftest_master_table.csv"
    with path.open("w", encoding="utf-8-sig", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(FIXTURE_HEADER)
        w.writerows(FIXTURE_ROWS)
    return path


def run_self_test() -> int:
    """自检：夹具 → 期望命中 → 断言。"""
    import tempfile

    print("=" * 68)
    print("validate_table.py --self-test")
    print("=" * 68)

    tmp = Path(tempfile.mkdtemp(prefix="hbm_selftest_"))
    fixture = write_fixture(tmp)
    print(f"[1/4] 夹具已生成：{fixture}")

    rows, cols = read_table(fixture)
    print(f"[2/4] 读取成功：{len(rows)} 行 × {len(cols)} 列")

    cfg = dict(FIXTURE_CONFIG)   # 自检专用夹具口径（非正式分析路径）
    cfg["verified_ratio_threshold"] = 0.10  # 夹具中 verified 仅 1/4，放宽以免掩盖其他断言
    rep = validate(rows, cols, cfg)
    print(f"[3/4] 校验完成：ERROR={len(rep.errors)}  WARN={len(rep.warns)}")

    got_err = {(e["record"], e["field"], e["rule"]) for e in rep.errors}
    got_warn = {(w["record"], w["field"], w["rule"]) for w in rep.warns}

    expectations = [
        # (类型, record, field, 说明)
        ("err", "R00002", "blood_matrix", "sample_type=Blood 未填 blood_matrix"),
        ("err", "R00003", "time", "采样年晚于发表年"),
        ("err", "R00003", "population_group", "枚举非法 Newborn"),
        ("err", "R00004", "initial_p25", "P25 > 中位数"),
        ("err", "R00004", "initial_p75", "P75 < 中位数"),
        ("err", "R00004", "sample_size", "核心字段 value_interpolated_sample_size"),
        ("warn", "R00004", "initial_gsd", "GSD < 1.0"),
        ("warn", "R00004", "time", "采样年与发表年相同"),
    ]

    failures = []
    for kind, rid, field, desc in expectations:
        pool = got_err if kind == "err" else got_warn
        hit = any(r == rid and f == field for (r, f, _) in pool)
        mark = "✅" if hit else "❌"
        print(f"      {mark} [{kind.upper():4}] {rid} / {field} — {desc}")
        if not hit:
            failures.append(f"{rid}/{field} ({desc})")

    report_md = rep.to_markdown(str(fixture), len(rows), cfg)
    report_path = tmp / "validate_report.md"
    report_path.write_text(report_md, encoding="utf-8")
    print(f"[4/4] 报告已写出：{report_path}")

    print("-" * 68)
    if failures:
        print(f"self-test 失败：{len(failures)} 项期望未命中")
        for f in failures:
            print(f"  - {f}")
        return 1
    print(f"self-test 通过：{len(expectations)} 项期望全部命中")
    print(f"（夹具含故意植入的错误，校验器应全部捕获；临时目录 {tmp}）")

    # ★ 单一来源一致性（用户要求）：字典项数 == 注册表项数 == 契约清单覆盖项数
    print()
    print("── 单一来源一致性 ──")
    n_registry = inferred_flag_count()
    n_local = len(INFERRED_FLAG_DICT)
    n_contract = len(CONTRACT_DOCUMENTED)
    ok1 = n_local == n_registry
    print(f"      {'✅' if ok1 else '❌'} 本地闭集项数({n_local}) == 注册表项数({n_registry})")
    ok2 = n_contract <= n_registry
    print(f"      {'✅' if ok2 else '❌'} 契约清单({n_contract}) ⊆ 注册表({n_registry})")
    ok3 = not unknown_flags(INFERRED_FLAG_DICT)
    print(f"      {'✅' if ok3 else '❌'} 本地闭集无越界项")

    # ★ unit_policy 归一化（防"嵌套字典与字符串比较 → 永远报错"）
    print()
    print("── unit_policy 归一化 ──")
    import tempfile as _tf
    from pathlib import Path as _P
    _d = _P(_tf.mkdtemp(prefix="hbm_ut_"))
    _cfg = _d / "project.yaml"
    _cfg.write_text(
        "project:\n  analyte: As\n  country: China\n  matrix_scope: [Urine, Blood]\n"
        "period_scheme:\n  boundaries:\n    - {label: '1980-2000', start: 1980, end: 2000}\n"
        "  freeze_date: '2026-07-19'\n"
        "region_map:\n  Southwest: [云南省]\n"
        "unit_policy:\n  Urine: {target: 'ug/g Cr'}\n  Blood: {target: 'ug/L'}\n"
        "standardization:\n  median_to_gm_strategy: as_gm\n  wan_variant: minus\n"
        "  creatinine_path_mode: CC_direct\n", encoding="utf-8")
    try:
        _c = read_config(_cfg)
        up = _c.get("unit_policy", {})
        flat = all(isinstance(v, str) for v in up.values())
        print(f"      {'✅' if flat else '❌'} unit_policy 已归一化为字符串映射：{up}")
        print(f"      {'✅' if up.get('Urine') == 'ug/g Cr' else '❌'} "
              f"Urine 目标单位 = {up.get('Urine')!r}")
        print(f"      {'✅' if up.get('Blood') == 'ug/L' else '❌'} "
              f"Blood 目标单位 = {up.get('Blood')!r}")
        # 用归一化后的口径跑一条记录，规则 5 不应报错
        _rows = [{"record_id": "R1", "study_no": "S1", "title": "t", "author": "a",
                  "doi": "NO_DOI", "t_publication": "2020", "time": "1995",
                  "country": "China", "province": "云南省", "region": "Southwest",
                  "population": "成人", "population_group": "Adults",
                  "flag_occupational": "FALSE", "flag_disease": "FALSE",
                  "flag_endemic_area": "FALSE", "flag_mixed_occupational": "FALSE",
                  "inclusion_decision": "Include", "sample_type": "Urine",
                  "analyte": "As", "stat_type": "GM_GSD", "unit_original": "ug/g Cr",
                  "sample_size": "100", "unit_final": "ug/g Cr",
                  "extracted_by": "AI+Human", "verified": "TRUE",
                  "snapshot_tag": "X", "row_hash": "h"}]
        _rep = validate(_rows, list(_rows[0].keys()), {**_c,
                                                       "verified_ratio_threshold": 0.5})
        _unit_errs = [e for e in _rep.errors if e["rule"] == "5-单位口径"]
        print(f"      {'✅' if not _unit_errs else '❌'} "
              f"单位口径规则不再误报（ERROR 数 = {len(_unit_errs)}）")
        ok4 = flat and not _unit_errs
    except Exception as exc:
        print(f"      ❌ unit_policy 归一化自检抛异常：{exc}")
        ok4 = False
    return 0 if (ok1 and ok2 and ok3 and ok4) else 1


def _raises_any(fn) -> bool:
    try:
        fn()
        return False
    except Exception:
        return True


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="validate_table.py",
        description="主表契约校验（HBM-Meta-Agent / S3 门禁）",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="退出码：0 无 ERROR / 1 存在 ERROR / 2 前置条件不满足",
    )
    p.add_argument("--input", type=Path, help="主表文件（.csv / .xlsx）")
    p.add_argument("--config", type=Path, help="project.yaml 路径（可选，骨架版缺失则用内置默认值）")
    p.add_argument("--out", type=Path, help="校验报告输出路径（.md）")
    p.add_argument("--self-test", action="store_true", help="运行内置脱敏夹具自检")
    p.add_argument("--quiet", action="store_true", help="仅输出结论行")
    p.add_argument("--version", action="version", version=f"hbm-meta contract {CONTRACT_VERSION}")
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    if args.self_test:
        return run_self_test()

    if not args.input:
        print("错误：需要 --input，或使用 --self-test", file=sys.stderr)
        return 2

    try:
        rows, cols = read_table(args.input)
    except (FileNotFoundError, SystemExit) as exc:
        print(f"错误：{exc}", file=sys.stderr)
        return 2

    # ★ A1：配置缺失/解析失败/缺必需区块 → 硬错误退出码 2，**不产出报告**
    try:
        cfg = read_config(args.config)
    except ConfigError as exc:
        print(f"错误：{exc}", file=sys.stderr)
        return 2
    rep = validate(rows, cols, cfg)

    report_md = rep.to_markdown(str(args.input), len(rows), cfg)
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(report_md, encoding="utf-8")

    if not args.quiet:
        if not args.out:
            print(report_md)
        else:
            print(f"报告已写出：{args.out}")
    print(f"记录数={len(rows)}  ERROR={len(rep.errors)}  WARN={len(rep.warns)}  "
          f"结论={'PASS' if rep.ok else 'FAIL'}")
    return 0 if rep.ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
