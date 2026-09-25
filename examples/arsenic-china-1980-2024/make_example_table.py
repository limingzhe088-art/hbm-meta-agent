#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
make_example_table.py — 生成脱敏案例主表（examples/arsenic-china-1980-2024/）

★ 脱敏原则（必读）
  保留：**列结构（72 列契约表头）、统计量类型分布、单位写法、基质分布、
       人群分布、时空分布的"形状"** —— 这些是教学价值所在。
  移除：**具体研究标题、作者、DOI、省份级真实数值、任何可识别到单篇研究的信息**。
  做法：标题/作者/DOI 用 `EXAMPLE-*` 占位；浓度值用**合成值**（量级合理但非真实）；
       省份用规范名（行政区划不属个人信息），但**不给出任何真实研究的省份级统计量**。

用法
  python make_example_table.py --write      # 写出 master_table_EXAMPLE.csv
  python make_example_table.py --self-test  # 校验列对齐 + 脱敏规则
"""
from __future__ import annotations

import argparse
import csv
import io
import re
import sys
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except (AttributeError, ValueError):  # pragma: no cover
    pass

HERE = Path(__file__).resolve().parent
OUT = HERE / "03-extraction" / "master_table_EXAMPLE.csv"

# ── 契约 72 列（顺序即 shared/data-contract.md §3）────────────────────
FIELDS = """record_id study_no cohort_id dedup_group title author doi t_publication source_database
time time_start time_end country province city county region latitude longitude
population population_group recruit_crowd population_desc classification_rule age age_mean height weight bmi male_n female_n gender
flag_occupational flag_disease flag_endemic_area flag_mixed_occupational inclusion_decision exclusion_reason
sample_type blood_matrix flag_blood_matrix analyte analyte_species detection_method qc_reported
stat_type initial_gm initial_mean initial_sd initial_median initial_p25 initial_p75 initial_p05 initial_p95 initial_min initial_max initial_gsd unit_original sample_size
adjusted unit_final gm_summary gsd_summary conversion_path conversion_params inferred_flags notes
extracted_by verified verified_by snapshot_tag row_hash""".split()

assert len(FIELDS) == 72, f"契约应为 72 列，实际 {len(FIELDS)}"


def row(**kw) -> list[str]:
    unknown = set(kw) - set(FIELDS)
    assert not unknown, f"未知字段：{unknown}"
    return [str(kw.get(f, "")) for f in FIELDS]


# ── 10 条脱敏示例记录 ────────────────────────────────────────────────
# 设计意图：覆盖 **全部关键分叉**，让使用者一眼看到"长什么样才算合规"
ROWS: list[dict] = [
    # 1) 尿样 + 直接报告 GM/GSD（零推断：reported_GM）
    dict(record_id="R00001", study_no="S001", title="EXAMPLE-Study-001 urinary arsenic in adults",
         author="EXAMPLE-Author-A", doi="10.xxxx/EXAMPLE-001", t_publication="2020",
         source_database="PubMed", time="2018", time_start="2017", time_end="2019",
         country="China", province="云南省", city="示例市", region="Southwest",
         population="一般成年人", population_group="Adults", recruit_crowd="社区",
         population_desc="多阶段分层抽样", classification_rule="18 岁及以上常住居民",
         age="18-75", male_n="600", female_n="600", gender="Both",
         flag_occupational="FALSE", flag_disease="FALSE", flag_endemic_area="FALSE",
         flag_mixed_occupational="FALSE", inclusion_decision="Include",
         sample_type="Urine", analyte="As", analyte_species="Total",
         detection_method="ICP-MS", qc_reported="TRUE",
         stat_type="GM_GSD", initial_gm="23.50", initial_gsd="2.10",
         unit_original="ug/g Cr", sample_size="1200",
         adjusted="Creatinine", unit_final="ug/g Cr", gm_summary="23.50", gsd_summary="2.10",
         conversion_path="reported_GM",
         notes="原文直接报告 GM 与 GSD（零推断子集的成员）",
         extracted_by="AI+Human", verified="TRUE", verified_by="EXAMPLE-Verifier",
         snapshot_tag="As_EXAMPLE", row_hash="EX0001"),
    # 2) 尿样 + 原文只给 AM+SD → 经对数正态转换
    dict(record_id="R00002", study_no="S002", title="EXAMPLE-Study-002 urinary metals, school children",
         author="EXAMPLE-Author-B", doi="10.xxxx/EXAMPLE-002", t_publication="2019",
         source_database="CNKI", time="2016", time_start="2016", time_end="2017",
         country="China", province="贵州省", city="示例市", region="Southwest",
         population="学龄儿童", population_group="Minors", recruit_crowd="学校",
         population_desc="整班抽样的在校学生", classification_rule="8-12 岁在校学生",
         age="8-12", male_n="220", female_n="205", gender="Both",
         flag_occupational="FALSE", flag_disease="FALSE", flag_endemic_area="TRUE",
         flag_mixed_occupational="FALSE", inclusion_decision="Include",
         sample_type="Urine", analyte="As", analyte_species="Total",
         detection_method="HG-AAS", qc_reported="FALSE",
         stat_type="AM_SD", initial_mean="31.20", initial_sd="12.40",
         unit_original="ug/L", sample_size="450",
         adjusted="No", unit_final="ug/g Cr", gm_summary="28.86", gsd_summary="1.44",
         conversion_path="AM_SD→AMSD_to_LN→ug/L→ICRP89_Minors→ug/g Cr→GM",
         conversion_params="AM=31.20;SD=12.40;CV=0.397;group=Minors;CE=0.925;V=1.05;CC=0.815;path=CC_direct",
         inferred_flags="gm_from_mean_sd;unit_converted_creatinine;coarse_age",
         notes="原文为 μg/L 未校正；地方病区人群，已进 GATE-2 疑似清单（示例）",
         extracted_by="AI+Human", verified="TRUE", verified_by="EXAMPLE-Verifier",
         snapshot_tag="As_EXAMPLE", row_hash="EX0002"),
    # 3) 尿样 + 中位数+IQR，缺 n → Wan S6 回退
    dict(record_id="R00003", study_no="S003", title="EXAMPLE-Study-003 urinary arsenic, cross-sectional",
         author="EXAMPLE-Author-C", doi="10.xxxx/EXAMPLE-003", t_publication="2018",
         source_database="Wanfang", time="2015", country="China",
         province="山东省", city="示例市", region="East",
         population="成年人", population_group="Adults", recruit_crowd="医院体检",
         age="20-60", gender="Both",
         flag_occupational="FALSE", flag_disease="FALSE", flag_endemic_area="FALSE",
         flag_mixed_occupational="FALSE", inclusion_decision="Include",
         sample_type="Urine", analyte="As", analyte_species="Total",
         detection_method="ICP-MS", qc_reported="TRUE",
         stat_type="Median_IQR", initial_median="18.40", initial_p25="11.20",
         initial_p75="29.60", unit_original="ug/g Cr", sample_size="",
         adjusted="Creatinine", unit_final="ug/g Cr", gm_summary="18.40",
         conversion_path="Median_IQR→Median_as_GM→GM",
         conversion_params="strategy=as_gm;n=NA;configured=standardization.median_to_gm_strategy",
         inferred_flags="gm_from_median;gm_from_iqr",
         notes="原文未给亚组样本量（仅报告总样本量）→ sample_size 留空",
         extracted_by="AI+Human", verified="TRUE", verified_by="EXAMPLE-Verifier",
         snapshot_tag="As_EXAMPLE", row_hash="EX0003"),
    # 4) 尿样 + 比重校正（不套 ICRP，进分层）
    dict(record_id="R00004", study_no="S004", title="EXAMPLE-Study-004 urinary metals, specific gravity",
         author="EXAMPLE-Author-D", doi="10.xxxx/EXAMPLE-004", t_publication="2017",
         source_database="PubMed", time="2014", country="China",
         province="广东省", city="示例市", region="South",
         population="成年人", population_group="Adults", recruit_crowd="社区",
         age="18-70", gender="Both",
         flag_occupational="FALSE", flag_disease="FALSE", flag_endemic_area="FALSE",
         flag_mixed_occupational="FALSE", inclusion_decision="Include",
         sample_type="Urine", analyte="As", analyte_species="Total",
         detection_method="ICP-MS", qc_reported="TRUE",
         stat_type="GM_GSD", initial_gm="12.66", initial_gsd="1.90",
         unit_original="ug/L", sample_size="800",
         adjusted="SpecificGravity", unit_final="ug/L", gm_summary="12.66", gsd_summary="1.90",
         conversion_path="specific_gravity_reported",
         notes="原文用比重校正（未套 ICRP）→ 进『校正方式』敏感性分层",
         extracted_by="AI+Human", verified="TRUE", verified_by="EXAMPLE-Verifier",
         snapshot_tag="As_EXAMPLE", row_hash="EX0004"),
    # 5) 血样 + 全血
    dict(record_id="R00005", study_no="S005", title="EXAMPLE-Study-005 blood arsenic in adults",
         author="EXAMPLE-Author-E", doi="10.xxxx/EXAMPLE-005", t_publication="2021",
         source_database="PubMed", time="2019", country="China",
         province="北京市", city="示例市", region="North",
         population="成年人", population_group="Adults", recruit_crowd="医院体检",
         age="25-65", gender="Both",
         flag_occupational="FALSE", flag_disease="FALSE", flag_endemic_area="FALSE",
         flag_mixed_occupational="FALSE", inclusion_decision="Include",
         sample_type="Blood", blood_matrix="WholeBlood", analyte="As",
         analyte_species="Total", detection_method="ICP-MS", qc_reported="TRUE",
         stat_type="AM_SD", initial_mean="2.72", initial_sd="0.90",
         unit_original="ug/L", sample_size="800",
         adjusted="NotApplicable", unit_final="ug/L", gm_summary="2.58", gsd_summary="1.38",
         conversion_path="AM_SD→AMSD_to_LN→GM",
         conversion_params="AM=2.72;SD=0.90;CV=0.331",
         inferred_flags="gm_from_mean_sd",
         extracted_by="AI+Human", verified="TRUE", verified_by="EXAMPLE-Verifier",
         snapshot_tag="As_EXAMPLE", row_hash="EX0005"),
    # 6) 血样 + 标题提示 serum（基质存疑，必须打 flag）
    dict(record_id="R00006", study_no="S006", title="EXAMPLE-Study-006 serum arsenic, pregnant women",
         author="EXAMPLE-Author-F", doi="10.xxxx/EXAMPLE-006", t_publication="2020",
         source_database="CNKI", time="2018", country="China",
         province="江苏省", city="示例市", region="East",
         population="孕妇", population_group="Pregnant", recruit_crowd="医院体检",
         population_desc="产检孕妇", classification_rule="单胎妊娠 12-16 周",
         age_mean="28.5", height="162.0", weight="60.0", bmi="22.9",
         female_n="400", gender="Female",
         flag_occupational="FALSE", flag_disease="FALSE", flag_endemic_area="FALSE",
         flag_mixed_occupational="FALSE", inclusion_decision="Pending",
         sample_type="Blood", blood_matrix="Serum", flag_blood_matrix="TRUE",
         analyte="As", analyte_species="Total", detection_method="ICP-MS",
         qc_reported="TRUE",
         stat_type="Median_IQR", initial_median="1.85", initial_p25="1.20",
         initial_p75="2.80", unit_original="ug/L", sample_size="400",
         adjusted="NotApplicable", unit_final="ug/L", gm_summary="1.85",
         conversion_path="Median_IQR→Median_as_GM→GM",
         inferred_flags="gm_from_median;gm_from_iqr;blood_matrix_assumed",
         notes="标题含 serum，正文未明确全血 → flag_blood_matrix=TRUE，待 GATE-2 裁决",
         extracted_by="AI+Human", verified="TRUE", verified_by="EXAMPLE-Verifier",
         snapshot_tag="As_EXAMPLE", row_hash="EX0006"),
    # 7) 脐血
    dict(record_id="R00007", study_no="S007", title="EXAMPLE-Study-007 cord blood arsenic, newborns",
         author="EXAMPLE-Author-G", doi="10.xxxx/EXAMPLE-007", t_publication="2019",
         source_database="PubMed", time="2016", country="China",
         province="上海市", city="示例市", region="East",
         population="新生儿", population_group="Minors", recruit_crowd="出生队列",
         population_desc="出生队列新生儿", age="0", gender="Both",
         flag_occupational="FALSE", flag_disease="FALSE", flag_endemic_area="FALSE",
         flag_mixed_occupational="FALSE", inclusion_decision="Include",
         sample_type="CordBlood", analyte="As", analyte_species="Total",
         detection_method="ICP-MS", qc_reported="TRUE",
         stat_type="GM_only", initial_gm="4.89", unit_original="ug/L", sample_size="500",
         adjusted="NotApplicable", unit_final="ug/L", gm_summary="4.89",
         conversion_path="reported_GM",
         notes="脐血记录：EDI 不计算（方案 A，标 cordblood_edi_skipped）",
         extracted_by="AI+Human", verified="TRUE", verified_by="EXAMPLE-Verifier",
         snapshot_tag="As_EXAMPLE", row_hash="EX0007"),
    # 8) 尿样 + 摩尔单位换算（nmol/L → μg/L，引入形态假设）
    dict(record_id="R00008", study_no="S008", title="EXAMPLE-Study-008 urinary arsenic, nmol/L reported",
         author="EXAMPLE-Author-H", doi="10.xxxx/EXAMPLE-008", t_publication="2016",
         source_database="WebOfScience", time="2013", country="China",
         province="湖北省", city="示例市", region="Central",
         population="成年人", population_group="Adults", recruit_crowd="社区",
         age="18-80", gender="Both",
         flag_occupational="FALSE", flag_disease="FALSE", flag_endemic_area="FALSE",
         flag_mixed_occupational="FALSE", inclusion_decision="Include",
         sample_type="Urine", analyte="As", analyte_species="Total",
         detection_method="ICP-MS", qc_reported="TRUE",
         stat_type="GM_only", initial_gm="267.0", unit_original="nmol/L", sample_size="600",
         adjusted="No", unit_final="ug/g Cr", gm_summary="15.27",
         conversion_path="nmol/L→ug/L→ICRP89_Adults→ug/g Cr",
         conversion_params="MW=74.92;formula=value*MW/1000;factor=0.07492;group=Adults;CC=0.964",
         inferred_flags="molar_conversion;unit_converted_creatinine",
         notes="原文用摩尔单位，换算引入形态/摩尔质量假设（MW=74.92 按元素砷）",
         extracted_by="AI+Human", verified="TRUE", verified_by="EXAMPLE-Verifier",
         snapshot_tag="As_EXAMPLE", row_hash="EX0008"),
    # 9) 疑似职业暴露（GATE-2 待裁决）
    dict(record_id="R00009", study_no="S009", title="EXAMPLE-Study-009 urinary metals in smelter workers",
         author="EXAMPLE-Author-I", doi="10.xxxx/EXAMPLE-009", t_publication="2015",
         source_database="CNKI", time="2012", country="China",
         province="湖南省", city="示例市", region="Central",
         population="冶炼厂工人", population_group="Adults", recruit_crowd="职业人群",
         age="20-55", gender="Both",
         flag_occupational="TRUE", flag_disease="FALSE", flag_endemic_area="FALSE",
         flag_mixed_occupational="FALSE", inclusion_decision="Pending",
         sample_type="Urine", analyte="As", analyte_species="Total",
         detection_method="HG-AAS", qc_reported="FALSE",
         stat_type="GM_GSD", initial_gm="58.30", initial_gsd="2.40",
         unit_original="ug/g Cr", sample_size="320",
         adjusted="Creatinine", unit_final="ug/g Cr", gm_summary="58.30", gsd_summary="2.40",
         conversion_path="reported_GM",
         notes="疑似职业暴露 → flag_occupational=TRUE，inclusion_decision=Pending（AI 不得自行排除）",
         extracted_by="AI+Human", verified="TRUE", verified_by="EXAMPLE-Verifier",
         snapshot_tag="As_EXAMPLE", row_hash="EX0009"),
    # 10) 仅有中位数、无离散度 → 不可合并（FAILED 示例）
    dict(record_id="R00010", study_no="S010", title="EXAMPLE-Study-010 urinary arsenic, median only",
         author="EXAMPLE-Author-J", doi="NO_DOI", t_publication="2014",
         source_database="Wanfang", time="2011", country="China",
         province="吉林省", city="示例市", region="Northeast",
         population="成年人", population_group="Adults", recruit_crowd="疾控监测",
         age="18-70", gender="Both",
         flag_occupational="FALSE", flag_disease="FALSE", flag_endemic_area="FALSE",
         flag_mixed_occupational="FALSE", inclusion_decision="Include",
         sample_type="Urine", analyte="As", analyte_species="Total",
         detection_method="Unknown", qc_reported="FALSE",
         stat_type="Median_only", initial_median="18.89", unit_original="ug/L",
         sample_size="450", adjusted="No", unit_final="", gm_summary="",
         conversion_path="Median_only→FAILED",
         notes="只有中位数、无任何离散度信息 → 该记录本质上不可合并；须计入局限说明",
         extracted_by="AI+Human", verified="TRUE", verified_by="EXAMPLE-Verifier",
         snapshot_tag="As_EXAMPLE", row_hash="EX0010"),
    # 11) 尿样 + via_wan 策略（中位数 + IQR → Wan S4 估 SD 再转 GM）
    #     说明：当 project.yaml 的 median_to_gm_strategy=via_wan 时走此路径；
    #           as_gm 策略下同一记录会走 Median_as_GM（见 R00003）。
    dict(record_id="R00011", study_no="S011",
         title="EXAMPLE-Study-011 urinary arsenic, IQR reported (via_wan path)",
         author="EXAMPLE-Author-K", doi="10.xxxx/EXAMPLE-011", t_publication="2022",
         source_database="PubMed", time="2020", country="China",
         province="四川省", city="示例市", region="Southwest",
         population="成年人", population_group="Adults", recruit_crowd="社区",
         age="18-75", gender="Both",
         flag_occupational="FALSE", flag_disease="FALSE", flag_endemic_area="FALSE",
         flag_mixed_occupational="FALSE", inclusion_decision="Include",
         sample_type="Urine", analyte="As", analyte_species="Total",
         detection_method="ICP-MS", qc_reported="TRUE",
         stat_type="Median_IQR", initial_median="21.30", initial_p25="14.80",
         initial_p75="30.10", unit_original="ug/g Cr", sample_size="420",
         adjusted="Creatinine", unit_final="ug/g Cr", gm_summary="21.08", gsd_summary="1.35",
         conversion_path="Median_IQR→Wan_S4→GM",
         conversion_params="formula=S4;n=420;variant=minus;eta=5.63",
         inferred_flags="gm_from_median;gm_from_iqr",
         notes="演示 Wan 2014 S4 路径（median_to_gm_strategy=via_wan 时启用）",
         extracted_by="AI+Human", verified="TRUE", verified_by="EXAMPLE-Verifier",
         snapshot_tag="As_EXAMPLE", row_hash="EX0011"),
    # 12) 尿样 + 只有极值（Median + Range → Wan S2；无 n 退化 S3）
    dict(record_id="R00012", study_no="S012",
         title="EXAMPLE-Study-012 urinary metals, range reported",
         author="EXAMPLE-Author-L", doi="10.xxxx/EXAMPLE-012", t_publication="2013",
         source_database="CNKI", time="2010", country="China",
         province="新疆维吾尔自治区", city="示例市", region="Northwest",
         population="成年人", population_group="Adults", recruit_crowd="疾控监测",
         age="18-70", gender="Both",
         flag_occupational="FALSE", flag_disease="FALSE", flag_endemic_area="FALSE",
         flag_mixed_occupational="FALSE", inclusion_decision="Include",
         sample_type="Urine", analyte="As", analyte_species="Total",
         detection_method="HG-AAS", qc_reported="FALSE",
         stat_type="Median_Range", initial_median="26.50", initial_min="3.00",
         initial_max="88.00", unit_original="ug/L", sample_size="",
         adjusted="No", unit_final="ug/g Cr", gm_summary="26.50",
         conversion_path="Median_Range→Wan_S3→ICRP89_Adults→ug/g Cr",
         conversion_params="formula=S3;n=NA;denom=1.9079;group=Adults;CC=0.964",
         inferred_flags="gm_from_median;gm_from_range;no_n_fallback;unit_converted_creatinine",
         notes="原文未给 n，Wan 回退到 S3（no_n_fallback），精度下降须标注",
         extracted_by="AI+Human", verified="TRUE", verified_by="EXAMPLE-Verifier",
         snapshot_tag="As_EXAMPLE", row_hash="EX0012"),
]


def write_table() -> None:
    OUT.parent.mkdir(parents=True, exist_ok=True)
    with io.open(OUT, "w", encoding="utf-8", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(FIELDS)
        for r in ROWS:
            w.writerow(row(**r))
    print(f"已写出：{OUT}（{len(ROWS)} 行 + 表头）")


# ── 脱敏规则检查 ────────────────────────────────────────────────────
# 禁止出现的内容（真实可识别信息）
FORBIDDEN_PATTERNS = [
    (r"\b10\.\d{4,9}/[a-z0-9.\-]+", "真实 DOI 形态（应使用 10.xxxx/EXAMPLE-*）"),
    (r"\b(PMID|pmid)\s*[:：]?\s*\d+", "PMID"),
    (r"\b(19|20)\d{2};\s*\d+\s*[:：]", "期刊卷期页引用形态"),
    (r"(?:等|et al\.)\s*[（(]\s*(19|20)\d{2}", "作者-年份引用形态"),
]
# 允许的占位写法
ALLOWED_PLACEHOLDER = re.compile(r"EXAMPLE-|10\.xxxx/|NO_DOI|示例")


def self_test() -> int:
    print("=" * 70)
    print("make_example_table.py --self-test（脱敏案例主表）")
    print("=" * 70)
    checks: list[tuple[str, bool, str]] = []

    def check(name, ok, detail=""):
        checks.append((name, ok, detail))
        print(f"      {'✅' if ok else '❌'} {name}" + (f"  [{detail}]" if detail else ""))

    print("\n[1] 列结构")
    check("契约列数 = 72", len(FIELDS) == 72, str(len(FIELDS)))
    check("列名唯一", len(FIELDS) == len(set(FIELDS)))
    for must in ("record_id", "study_no", "stat_type", "initial_gm", "unit_original",
                 "sample_size", "adjusted", "unit_final", "gm_summary",
                 "conversion_path", "inferred_flags", "verified"):
        check(f"含必需列 `{must}`", must in FIELDS)

    print("\n[2] 行结构（每行 72 列、与表头对齐）")
    check("行数 = 12", len(ROWS) == 12, str(len(ROWS)))
    all_len_ok = True
    for i, r in enumerate(ROWS, start=1):
        vals = row(**r)
        if len(vals) != 72:
            all_len_ok = False
            print(f"        第 {i} 行列数 {len(vals)}")
    check("所有行均为 72 列", all_len_ok)
    ids = [r["record_id"] for r in ROWS]
    check("record_id 唯一", len(ids) == len(set(ids)))
    snos = [r["study_no"] for r in ROWS]
    check("study_no 唯一（本案例示例）", len(snos) == len(set(snos)))

    print("\n[3] ★ 脱敏：禁止出现可识别信息")
    blob = "\n".join(",".join(row(**r)) for r in ROWS)
    for pat, desc in FORBIDDEN_PATTERNS:
        hits = re.findall(pat, blob)
        check(f"无{desc}", not hits, str(hits[:3]) if hits else "无命中")
    # 标题/作者/DOI 必须是占位
    for r in ROWS:
        for field in ("title", "author", "doi"):
            v = str(r.get(field, ""))
            if field == "doi" and v == "NO_DOI":
                continue
            if not ALLOWED_PLACEHOLDER.search(v):
                check(f"{r['record_id']}.{field} 为占位写法", False, f"实际 {v!r}")
                break
        else:
            continue
        break
    else:
        check("title / author / doi 全部为 EXAMPLE 占位", True)

    print("\n[4] ★ 教学价值：关键分叉必须被覆盖")
    stat_types = {r.get("stat_type") for r in ROWS}
    for st in ("GM_GSD", "GM_only", "AM_SD", "Median_IQR", "Median_only"):
        check(f"覆盖 stat_type={st}", st in stat_types, str(sorted(stat_types)))
    matrices = {r.get("sample_type") for r in ROWS}
    check("覆盖 Urine / Blood / CordBlood",
          {"Urine", "Blood", "CordBlood"} <= matrices, str(sorted(matrices)))
    adjs = {r.get("adjusted") for r in ROWS}
    check("覆盖校正方式 Creatinine / SpecificGravity / No",
          {"Creatinine", "SpecificGravity", "No"} <= adjs, str(sorted(adjs)))
    paths = " ".join(str(r.get("conversion_path", "")) for r in ROWS)
    for frag in ("reported_GM", "Median_as_GM", "Wan", "ICRP89", "nmol/L",
                 "specific_gravity", "FAILED"):
        check(f"覆盖换算路径片段 `{frag}`", frag in paths)
    flags = " ".join(str(r.get("inferred_flags", "")) for r in ROWS)
    for f in ("gm_from_median", "gm_from_iqr", "gm_from_mean_sd",
              "unit_converted_creatinine", "molar_conversion", "coarse_age",
              "blood_matrix_assumed"):
        check(f"覆盖标记 `{f}`", f in flags)
    decisions = {r.get("inclusion_decision") for r in ROWS}
    check("覆盖 Include / Pending（GATE-2 裁决示例）",
          {"Include", "Pending"} <= decisions, str(sorted(decisions)))

    print("\n[5] 与已写出文件一致（若存在）")
    if OUT.exists():
        with io.open(OUT, encoding="utf-8-sig", newline="") as fh:
            back = list(csv.reader(fh))
        check("表头与契约一致", back[0] == FIELDS)
        check("数据行数一致", len(back) - 1 == len(ROWS), f"{len(back)-1}")
        bad = [(i + 1, len(r)) for i, r in enumerate(back) if len(r) != 72]
        check("文件内所有行列数一致", not bad, str(bad) or "全部 72 列")
    else:
        check("文件存在（可跑 --write 生成）", False, "未生成")

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
        prog="make_example_table.py",
        description="生成脱敏案例主表（examples/arsenic-china-1980-2024/）")
    ap.add_argument("--write", action="store_true", help="写出 CSV")
    ap.add_argument("--self-test", action="store_true", help="校验列对齐与脱敏规则")
    args = ap.parse_args(argv)

    if args.self_test:
        if not OUT.exists():
            print("（未发现已写出文件，先生成）")
            write_table()
        return self_test()
    if args.write:
        write_table()
        return 0
    ap.print_help()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
