#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
inferred_flags.py — ★ inferred_flags 标记字典的**唯一来源**（shared/）

动机（来自一次真实缺陷）
  标记字典曾在 5 处各写一份：`data-contract.md` §6、`field_dictionary.md` §7、
  `unit_conversion_playbook.md` §6.2、`wan_convert.py::CLOSED_FLAG_DICT`、
  `validate_table.py::INFERRED_FLAG_DICT`。
  每次新增标记（16 → 19 → 20）都要手工同步 5 处，实测**漏了 2 处**
  （`validate_table.py` 仍为 16 项；`wan_convert.py` 漏 `cordblood_edi_skipped`
  与 `exploratory_reconstruction`），导致闭集校验形同虚设。

  本模块把字典收敛为**单一来源**；各脚本从此导入，不再各自维护副本。
  新增标记只需改这里 + 同步 `data-contract.md` §6，其余位置由派生得到。

用法
  from inferred_flags import (
      INFERRED_FLAGS, CLOSED_FLAG_SET, is_known, unknown_flags,
      registry_self_check,
  )

自检
  python inferred_flags.py --self-test
"""
from __future__ import annotations

import argparse
import sys

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except (AttributeError, ValueError):  # pragma: no cover
    pass


CONTRACT_VERSION = "1.0.0"

# ===========================================================================
# 单一来源：全部标记（顺序即 data-contract.md §6 的顺序，便于逐项对照）
# ===========================================================================

INFERRED_FLAGS: list[dict] = [
    # --- 统计量换算类（S4 登记）---
    {"name": "gm_from_mean_sd", "group": "statistic",
     "meaning": "GM 由 AM+SD 经 Wan 公式估算",
     "trigger": "stat_type=AM_SD",
     "impact": "进敏感性子集「仅直接报告 GM」"},
    {"name": "gm_from_median", "group": "statistic",
     "meaning": "GM 由中位数估算",
     "trigger": "stat_type=Median_*",
     "impact": "同上"},
    {"name": "gm_from_iqr", "group": "statistic",
     "meaning": "GM 由 IQR/P25–P75 估算",
     "trigger": "stat_type=Median_IQR",
     "impact": "同上"},
    {"name": "gm_from_range", "group": "statistic",
     "meaning": "GM 由 Min–Max 估算",
     "trigger": "stat_type=Min_Max",
     "impact": "同上，估算误差最大（S3 分母 1.9079 与 ξ(n) 差距最大）"},
    {"name": "sd_from_iqr", "group": "statistic",
     "meaning": "SD 由 IQR 反算",
     "trigger": "原文只给 IQR",
     "impact": "—"},
    # --- 单位换算类（S4 登记）---
    {"name": "unit_converted_creatinine", "group": "unit",
     "meaning": "用 ICRP 89 参考值把 μg/L 换成 μg/g Cr",
     "trigger": "adjusted=ReferenceConversion",
     "impact": "进「校正方式」分层敏感性"},
    {"name": "unit_converted_volume", "group": "unit",
     "meaning": "μg/g Cr 反算为 μg/L",
     "trigger": "反向换算",
     "impact": "—"},
    {"name": "molar_conversion", "group": "unit",
     "meaning": "摩尔单位换算，引入形态/摩尔质量假设",
     "trigger": "nmol/L → μg/L",
     "impact": "必须在 conversion_params 记录 MW 与形态"},
    # --- 时空与基质类（S3 登记）---
    {"name": "time_represented", "group": "temporal",
     "meaning": "多年采样区间取了代表年",
     "trigger": "time_start ≠ time_end",
     "impact": "记录取法于 notes"},
    {"name": "blood_matrix_assumed", "group": "matrix",
     "meaning": "假定为全血",
     "trigger": "原文未说明血基质",
     "impact": "必须同时 flag_blood_matrix=TRUE"},
    {"name": "species_total_assumed", "group": "analyte",
     "meaning": "假定为总砷",
     "trigger": "原文未做形态分析",
     "impact": "SI 表登记"},
    {"name": "coord_random", "group": "spatial",
     "meaning": "经纬度为随机/推算生成",
     "trigger": "无实测坐标",
     "impact": "**不得用于正式空间结论**"},
    # --- 数据质量类 ---
    {"name": "value_interpolated", "group": "quality",
     "meaning": "缺失值线性插补",
     "trigger": "宏观解释变量",
     "impact": "★ **禁止用于主表浓度字段**（V-04）"},
    {"name": "value_estimated_from_figure", "group": "quality",
     "meaning": "从图中读数估算",
     "trigger": "仅图无表",
     "impact": "必须人工复核"},
    {"name": "sample_size_estimated", "group": "quality",
     "meaning": "样本量由文中推算",
     "trigger": "分段给出",
     "impact": "进人工复核清单"},
    # --- 记录拆分类 ---
    {"name": "record_split_from_study", "group": "record",
     "meaning": "同一研究按性别/年龄/地区/时段拆成多行记录",
     "trigger": "一篇论文报告 3 个年龄组",
     "impact": "权重按各拆分行样本量分配；须确认样本量未重复计入"},
    {"name": "subgroup_selected", "group": "record",
     "meaning": "混合人群中仅取了符合纳入标准的亚组",
     "trigger": "职业与非职业混合调查仅取非职业亚组",
     "impact": "须与 GATE-2 裁决一致"},
    # --- S4 补充（缺 n / 粗年龄段）---
    {"name": "no_n_fallback", "group": "statistic",
     "meaning": "因缺少样本量而回退到不需 n 的公式",
     "trigger": "只有 IQR/Range 无 n → 走 Wan S3/S6",
     "impact": "精度下降；进敏感性分析分层"},
    {"name": "coarse_age", "group": "parameter",
     "meaning": "使用粗年龄段参数",
     "trigger": "原文只写「儿童」→ 用 Minors 统一值",
     "impact": "参数差异大（CC 0.275–0.929）；进敏感性分析分层"},
    # --- S5 补充（脐血方案 A）---
    {"name": "cordblood_edi_skipped", "group": "edi",
     "meaning": "脐带血未计算 EDI",
     "trigger": "缺乏胎儿/新生儿药代动力学参数（方案 A）",
     "impact": "必须在结果表计数并写入局限说明"},
    # --- S5/脚本内部使用（不属于"推断"语义，但需在闭集内以便校验）---
    {"name": "exploratory_reconstruction", "group": "edi",
     "meaning": "结果为探索性重建（血路 EDI）",
     "trigger": "blood_route 计算",
     "impact": "结果表与图必须标注 exploratory reconstruction"},
    {"name": "inconsistent_param", "group": "parameter",
     "meaning": "参数表不自洽（CC ≠ CE/V）",
     "trigger": "Minors 档或双路径相对差 > 3%",
     "impact": "进敏感性分层，写入局限"},
    {"name": "bw_unused", "group": "edi",
     "meaning": "血路公式不使用体重",
     "trigger": "Vd 已按体重标准化",
     "impact": "参数留痕用"},
]

FLAG_NAMES: list[str] = [f["name"] for f in INFERRED_FLAGS]
CLOSED_FLAG_SET: set[str] = set(FLAG_NAMES)

# 定义处应位于契约 §6 的"推断标记"（其余为脚本内部标记，需在契约中另作说明）
CONTRACT_DOCUMENTED: list[str] = [
    "gm_from_mean_sd", "gm_from_median", "gm_from_iqr", "gm_from_range",
    "sd_from_iqr", "unit_converted_creatinine", "unit_converted_volume",
    "time_represented", "blood_matrix_assumed", "species_total_assumed",
    "coord_random", "value_interpolated", "value_estimated_from_figure",
    "sample_size_estimated", "record_split_from_study", "subgroup_selected",
    "no_n_fallback", "molar_conversion", "coarse_age", "cordblood_edi_skipped",
]

# 脚本内部标记（非"推断"语义，但需在闭集内）
SCRIPT_INTERNAL: list[str] = [
    "exploratory_reconstruction", "inconsistent_param", "bw_unused",
]

# 禁止插补的四个核心字段（data-contract.md §6 硬约束）
NO_IMPUTE_FIELDS = ["gm_summary", "sample_size", "time", "sample_type"]


# ===========================================================================
# 文档项数一致性（防止"字典扩充了但文档仍写旧项数"）
# ===========================================================================
#
# 历史问题：字典 16 → 19 → 20 时，有 7 处文档仍写旧项数
# （field_dictionary §7 写 16、SKILL.md 写 16、S4/SKILL.md 写 19、
#   unit_conversion_playbook 写"待办 16→19"、proofing_rules 写 16 等）。
# 本检查在自检时扫描文档，凡出现"N 项字典 / N 项标记"且与注册表不符即报错。

# 需要核对的文档（相对 {skill_root}）
COUNT_DOCS = [
    "SKILL.md",
    "shared/data-contract.md",
    "skills/S3-extraction-standardization/references/field_dictionary.md",
    "skills/S3-extraction-standardization/references/proofing_rules.md",
    "skills/S4-unit-statistic-conversion/SKILL.md",
    "skills/S4-unit-statistic-conversion/references/unit_conversion_playbook.md",
]

# 匹配"N 项字典"、"标记字典（N 项"、"**N 项**文档化标记"等表述
import re as _re

_COUNT_PATTERNS = [
    _re.compile(r"(\d+)\s*项字典"),
    _re.compile(r"标记字典[（(]\s*\*{0,2}(\d+)\s*项"),
    _re.compile(r"\*{0,2}(\d+)\s*项\*{0,2}文档化标记"),
]


def doc_count_issues(root=None) -> list[str]:
    """
    扫描文档中的"项数表述"，返回与注册表不一致的清单（空 = 通过）。
    只校验"文档化标记"的项数（脚本内部标记不计入面向用户的表述）。
    """
    import io
    from pathlib import Path
    base = Path(root) if root else Path(__file__).resolve().parent.parent
    expected = len(CONTRACT_DOCUMENTED)
    issues: list[str] = []
    for rel in COUNT_DOCS:
        p = base / rel
        if not p.exists():
            issues.append(f"{rel} 不存在")
            continue
        text = io.open(p, encoding="utf-8", errors="replace").read()
        for pat in _COUNT_PATTERNS:
            for m in pat.finditer(text):
                got = int(m.group(1))
                if got != expected:
                    line_no = text[:m.start()].count("\n") + 1
                    issues.append(
                        f"{rel}:{line_no} 写「{m.group(0)}」，应为 {expected} 项")
    return issues


# ===========================================================================
# 查询接口
# ===========================================================================

def is_known(flag: str) -> bool:
    """标记是否在闭集内。"""
    return flag in CLOSED_FLAG_SET


def unknown_flags(flags: str | list[str]) -> list[str]:
    """返回越界标记（不在闭集内）。"""
    items = flags.split(";") if isinstance(flags, str) else list(flags)
    return sorted({f.strip() for f in items if f.strip() and f.strip() not in CLOSED_FLAG_SET})


def by_group(group: str) -> list[str]:
    return [f["name"] for f in INFERRED_FLAGS if f["group"] == group]


def inferred_flag_count() -> int:
    """闭集项数（供各脚本在自检中断言"定义处 == 使用处"）。"""
    return len(INFERRED_FLAGS)


def registry_self_check() -> list[str]:
    """
    自检：字典内部一致性 + 与契约清单一一对应。返回问题列表（空 = 通过）。
    """
    problems: list[str] = []

    # 1) 名称唯一
    if len(FLAG_NAMES) != len(set(FLAG_NAMES)):
        dup = [n for n in FLAG_NAMES if FLAG_NAMES.count(n) > 1]
        problems.append(f"标记名重复：{sorted(set(dup))}")

    # 2) 必需字段齐全
    for f in INFERRED_FLAGS:
        for k in ("name", "group", "meaning", "trigger", "impact"):
            if not f.get(k):
                problems.append(f"`{f.get('name')}` 缺字段 `{k}`")

    # 3) 契约清单 ⊆ 注册表
    extra_in_registry = set(FLAG_NAMES) - set(CONTRACT_DOCUMENTED) - set(SCRIPT_INTERNAL)
    if extra_in_registry:
        problems.append(f"注册表中有未归类的标记：{sorted(extra_in_registry)}")

    # 4) 契约清单 ⊆ 注册表
    missing_in_registry = set(CONTRACT_DOCUMENTED) - CLOSED_FLAG_SET
    if missing_in_registry:
        problems.append(f"契约清单中的标记不在注册表：{sorted(missing_in_registry)}")

    # 5) 脚本内部标记 ⊆ 注册表
    missing_internal = set(SCRIPT_INTERNAL) - CLOSED_FLAG_SET
    if missing_internal:
        problems.append(f"脚本内部标记不在注册表：{sorted(missing_internal)}")

    # 6) 计数一致性
    if len(CONTRACT_DOCUMENTED) + len(SCRIPT_INTERNAL) != len(INFERRED_FLAGS):
        problems.append(
            f"计数不一致：契约 {len(CONTRACT_DOCUMENTED)} + 内部 {len(SCRIPT_INTERNAL)} "
            f"≠ 注册表 {len(INFERRED_FLAGS)}")

    return problems


def summary() -> str:
    groups: dict[str, int] = {}
    for f in INFERRED_FLAGS:
        groups[f["group"]] = groups.get(f["group"], 0) + 1
    gs = ", ".join(f"{k}={v}" for k, v in sorted(groups.items()))
    return (f"共 {len(INFERRED_FLAGS)} 项（契约文档化 {len(CONTRACT_DOCUMENTED)} + "
            f"脚本内部 {len(SCRIPT_INTERNAL)}）｜分组：{gs}")


# ===========================================================================
# self-test
# ===========================================================================

# 用于"使用处覆盖"校验的已知使用者（脚本 + 文档），由 CI/自检核对
KNOWN_CONSUMERS = {
    "shared/data-contract.md": "契约 §6 字典本体",
    "skills/S3-extraction-standardization/references/field_dictionary.md": "字段字典 §7",
    "skills/S4-unit-statistic-conversion/references/unit_conversion_playbook.md": "反推规则表 §6.2",
    "skills/S4-unit-statistic-conversion/scripts/wan_convert.py": "CLOSED_FLAG_DICT（改为导入）",
    "skills/S3-extraction-standardization/scripts/validate_table.py": "闭集校验（改为导入）",
}


def run_self_test() -> int:
    print("=" * 70)
    print("inferred_flags.py --self-test（标记字典单一来源）")
    print("=" * 70)
    checks: list[tuple[str, bool, str]] = []

    def check(name, ok, detail=""):
        checks.append((name, ok, detail))
        print(f"      {'✅' if ok else '❌'} {name}" + (f"  [{detail}]" if detail else ""))

    print("\n[1] 注册表内部一致性")
    problems = registry_self_check()
    check("registry_self_check 无问题", not problems, "; ".join(problems) or "通过")
    check("标记名唯一", len(FLAG_NAMES) == len(set(FLAG_NAMES)))
    check(f"共 {len(INFERRED_FLAGS)} 项", len(INFERRED_FLAGS) == 23,
          f"{len(INFERRED_FLAGS)} 项（契约 20 + 内部 3）")
    check("契约文档化 20 项", len(CONTRACT_DOCUMENTED) == 20,
          f"{len(CONTRACT_DOCUMENTED)}")
    check("脚本内部 3 项", len(SCRIPT_INTERNAL) == 3, f"{len(SCRIPT_INTERNAL)}")
    print(f"      · {summary()}")

    print("\n[2] 四个新增标记必须在册")
    for name in ("no_n_fallback", "molar_conversion", "coarse_age", "cordblood_edi_skipped"):
        check(f"`{name}` 在册", is_known(name))
        check(f"`{name}` 有契约归属", name in CONTRACT_DOCUMENTED)

    print("\n[3] 闭集校验接口")
    check("已知标记 → 无越界", unknown_flags("gm_from_iqr;coarse_age") == [])
    check("未知标记 → 报出", unknown_flags("gm_from_iqr;not_a_flag") == ["not_a_flag"])
    check("空串 → 无越界", unknown_flags("") == [])
    check("分号分隔解析正确", unknown_flags("a;b;c") == ["a", "b", "c"])
    check("列表输入支持", unknown_flags(["gm_from_iqr", "xx"]) == ["xx"])
    check("去重后返回", unknown_flags("zz;zz") == ["zz"])

    print("\n[4] 分组查询")
    check("statistic 组含 gm_from_iqr",
          "gm_from_iqr" in by_group("statistic"), str(by_group("statistic")))
    check("edi 组含 cordblood_edi_skipped",
          "cordblood_edi_skipped" in by_group("edi"), str(by_group("edi")))
    check("parameter 组含 coarse_age 与 inconsistent_param",
          {"coarse_age", "inconsistent_param"} <= set(by_group("parameter")),
          str(by_group("parameter")))

    print("\n[5] 禁止插补字段")
    check("四字段齐全",
          NO_IMPUTE_FIELDS == ["gm_summary", "sample_size", "time", "sample_type"],
          str(NO_IMPUTE_FIELDS))

    print("\n[6] ★ 使用处覆盖核对（字典项数 == 使用处覆盖项数）")
    import io
    from pathlib import Path
    root = Path(__file__).resolve().parent.parent

    def read(rel: str) -> str:
        p = root / rel
        return io.open(p, encoding="utf-8", errors="replace").read() if p.exists() else ""

    # 契约 §6 应含全部 CONTRACT_DOCUMENTED
    contract = read("shared/data-contract.md")
    missing = [n for n in CONTRACT_DOCUMENTED if f"`{n}`" not in contract]
    check("契约 §6 覆盖全部文档化标记", not missing, str(missing) or "20/20")

    # 字段字典应含全部文档化标记
    fd = read("skills/S3-extraction-standardization/references/field_dictionary.md")
    missing_fd = [n for n in CONTRACT_DOCUMENTED if n not in fd]
    check("字段字典覆盖全部文档化标记", not missing_fd, str(missing_fd) or "20/20")

    # 反推规则表（playbook）应含全部 S4 登记的标记
    pb = read("skills/S4-unit-statistic-conversion/references/unit_conversion_playbook.md")
    s4_flags = [n for n in FLAG_NAMES
                if n in ("gm_from_mean_sd", "gm_from_median", "gm_from_iqr", "gm_from_range",
                         "sd_from_iqr", "unit_converted_creatinine", "unit_converted_volume",
                         "molar_conversion", "no_n_fallback", "time_represented",
                         "blood_matrix_assumed", "species_total_assumed")]
    missing_pb = [n for n in s4_flags if n not in pb]
    check("反推规则表覆盖全部 S4 标记", not missing_pb, str(missing_pb) or f"{len(s4_flags)}/{len(s4_flags)}")

    # 两个脚本必须导入本模块（而非自建副本）
    for rel in ("skills/S4-unit-statistic-conversion/scripts/wan_convert.py",
                "skills/S3-extraction-standardization/scripts/validate_table.py"):
        src = read(rel)
        imported = "from inferred_flags import" in src or "import inferred_flags" in src
        check(f"{Path(rel).name} 已导入单一来源", imported, "导入" if imported else "仍自建副本")

    print("\n[7] 定义处与使用处数量一一对应")
    check(f"契约清单（{len(CONTRACT_DOCUMENTED)}）+ 内部（{len(SCRIPT_INTERNAL)}）"
          f"== 注册表（{len(INFERRED_FLAGS)}）",
          len(CONTRACT_DOCUMENTED) + len(SCRIPT_INTERNAL) == len(INFERRED_FLAGS))

    print("\n[8] ★ 文档项数表述一致性（防止扩充后文档仍写旧项数）")
    issues = doc_count_issues()
    check("所有文档的项数表述 == 注册表项数", not issues,
          "; ".join(issues) if issues else f"全部为 {len(CONTRACT_DOCUMENTED)} 项")
    check("核对文档数 == 6", len(COUNT_DOCS) == 6, str(len(COUNT_DOCS)))
    # 反向验证：故意注入错误项数应被检出
    import tempfile as _tf, io as _io
    from pathlib import Path as _P
    tmp = _P(_tf.mkdtemp(prefix="hbm_flagcnt_"))
    (tmp / "SKILL.md").write_text("其余任何非原文直接值必须登记（13 项字典）。",
                                  encoding="utf-8")
    bad = doc_count_issues(root=tmp)
    check("★ 注入错误项数能被检出（检查有效）",
          any("13" in b for b in bad), str(bad[:1]))

    print("\n" + "-" * 70)
    failed = [c for c in checks if not c[1]]
    if failed:
        print(f"self-test 失败：{len(failed)}/{len(checks)} 项未通过")
        for name, _, detail in failed:
            print(f"  - {name}  {detail}")
        return 1
    print(f"self-test 通过：{len(checks)}/{len(checks)} 项全部通过")
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        prog="inferred_flags.py",
        description="inferred_flags 标记字典的单一来源（HBM-Meta-Agent / shared）")
    ap.add_argument("--self-test", action="store_true")
    ap.add_argument("--list", action="store_true", help="列出全部标记")
    ap.add_argument("--check", help="校验逗号分隔的标记是否在闭集内")
    args = ap.parse_args(argv)

    if args.self_test:
        return run_self_test()

    if args.list:
        print(summary())
        for f in INFERRED_FLAGS:
            tag = "契约" if f["name"] in CONTRACT_DOCUMENTED else "内部"
            print(f"  [{tag}] {f['name']:28s} ({f['group']})  {f['meaning']}")
        return 0

    if args.check is not None:
        bad = unknown_flags(args.check.replace(",", ";"))
        if bad:
            print(f"越界标记：{bad}", file=sys.stderr)
            return 1
        print("全部在闭集内")
        return 0

    ap.print_help()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
