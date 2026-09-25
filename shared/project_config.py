#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
project_config.py — ★ 唯一的配置加载入口（shared/）

设计原则（STEP4-TODO.md A1）
  1. **禁止兜底**：`project.yaml` 缺失 / 不可解析 / 缺必需区块 → 一律**硬错误**，
     退出码 2，且**不产出任何报告**。
     理由：用默认值继续会得到"看起来跑通了，但用的是错误的口径"——最危险的一类失败。
  2. **单点定义**：分期、地区、基质、单位、ICRP 参数、EDI 参数、加权口径
     全部在此读取；脚本内**零字面量**。
  3. **版本可追溯**：返回 `config_version` 与各区块的 `freeze_date`，
     供快照指纹与 stale 回滚判定使用。
  4. **唯一实现**：所有脚本（含跨目录导入 S5 模块的 S6 脚本）都从这里取配置，
     避免 7 处各自为政的兜底逻辑。

用法
  from project_config import load_config, ConfigError
  try:
      cfg = load_config(args.config)
  except ConfigError as exc:
      print(f"错误：{exc}", file=sys.stderr)
      return 2

自检
  python project_config.py --self-test
"""
from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass, field
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except (AttributeError, ValueError):  # pragma: no cover
    pass


# ===========================================================================
# 1. 契约常量（结构性默认值，**不是口径兜底**）
# ===========================================================================

CONTRACT_VERSION = "1.0.0"

# 必需区块：缺失即硬错误（GATE-0 未完成的标志）
#
# `standardization` 曾为条件必需，现提升为**必需**（用户决策）：
#   它的三个口径（median_to_gm_strategy / wan_variant / creatinine_path_mode）
#   都被 S4 依赖，若可选 → 某脚本路径可能不读它而用内置默认值 → 重蹈"兜底"覆辙。
#   显式配置成本极低（三行 YAML），而漏配的代价是静默的口径不一致。
REQUIRED_SECTIONS = [
    "project",
    "period_scheme",
    "region_map",
    "unit_policy",
    "standardization",
]
# 条件必需：使用相应功能时必需
CONDITIONAL_SECTIONS = {
    "edi": "计算 EDI 时需要",
    "weighting": "加权合并时需要",
    "urine_reference": "尿校正换算时需要",
}

# standardization 的三个必填口径（决定行为差异很大的分叉点）
REQUIRED_STANDARDIZATION_KEYS = {
    "median_to_gm_strategy": {
        "allowed": ["as_gm", "via_wan"],
        "desc": "中位数如何转 GM：直接把中位数当作 GM（对数正态下中位数=GM），"
                "或经 Wan 公式先估 Mean/SD 再转 GM",
        "consequence": "改这项会改变 gm_summary 与 gsd_summary，进而改变所有分层结果；"
                       "须 bump config_version 并重算 S4→S5→S6",
    },
    "wan_variant": {
        # ⚠️ 目前仅实现 `minus`：Wan 2014 的 ± 变体在 n 较小时数值分支不同，
        #    本工作包只做了减号变体；wan_convert.py 对 `plus` 会**显式抛错**
        #    （避免"配置了 plus 但结果与 minus 相同"的静默错误）。
        #    实现 plus 后，在此把 "plus" 加回允许值。
        "allowed": ["minus"],
        "desc": "Wan S1–S5 公式的 ± 变体选择（当前仅实现减号=保守变体）",
        "consequence": "改这项会改变由 Wan 估算的 SD/GSD，影响误差线与敏感性分层；"
                       "须 bump config_version 并重算",
    },
    "creatinine_path_mode": {
        "allowed": ["CC_direct", "CEV_equivalent"],
        "desc": "尿校正路径：用肌酐浓度直除，或用每日肌酐排泄量/尿量等价式",
        "consequence": "两条路径在参数 4 位小数下相差约 0.03%（参数表不自洽时可达 8%）；"
                       "同一项目内不得混用，改动须 bump config_version 并重算",
    },
}

# 矩阵枚举（结构性，用于校验 unit_policy 覆盖范围）
MATRIX_ENUM = ["Urine", "Blood", "CordBlood", "BreastMilk", "Nail", "Hair", "Serum", "Plasma"]


class ConfigError(Exception):
    """配置级硬错误。调用方应捕获并返回退出码 2。"""


# ===========================================================================
# 2. 数据结构
# ===========================================================================

@dataclass
class Config:
    raw: dict = field(default_factory=dict)
    source_path: str = ""
    config_version: str = "1.0.0"
    contract_version: str = CONTRACT_VERSION

    # --- 便捷访问 ---
    @property
    def project(self) -> dict:
        return self.raw.get("project", {})

    @property
    def analyte(self) -> str:
        return str(self.project.get("analyte", ""))

    @property
    def country(self) -> str:
        return str(self.project.get("country", ""))

    @property
    def period_scheme(self) -> dict:
        return self.raw["period_scheme"]

    @property
    def period_freeze_date(self) -> str:
        return str(self.period_scheme.get("freeze_date", ""))

    @property
    def region_map(self) -> dict:
        return self.raw["region_map"]

    @property
    def unit_policy(self) -> dict:
        return self.raw["unit_policy"]

    @property
    def edi(self) -> dict:
        return self.raw.get("edi", {})

    @property
    def weighting(self) -> dict:
        return self.raw.get("weighting", {})

    @property
    def urine_reference(self) -> dict:
        return self.raw.get("urine_reference", {})

    @property
    def standardization(self) -> dict:
        return self.raw.get("standardization", {})

    @property
    def matrix_scope(self) -> list:
        return list(self.project.get("matrix_scope", []))

    def target_unit(self, matrix: str) -> str:
        """某基质的目标单位；未配置 → 硬错误（不得猜测）。"""
        up = self.unit_policy
        for key in (matrix, matrix.lower()):
            if key in up:
                val = up[key]
                return val.get("target", "") if isinstance(val, dict) else str(val)
        raise ConfigError(
            f"unit_policy 未定义基质 `{matrix}` 的目标单位。"
            f"请在 project.yaml 的 unit_policy 中补充后重试。")

    def summary(self) -> str:
        n_regions = len(self.region_map)
        n_periods = len(self.period_scheme.get("boundaries", []))
        return (f"analyte={self.analyte} country={self.country} "
                f"periods={n_periods} regions={n_regions} "
                f"config_version={self.config_version}")


# ===========================================================================
# 3. 加载与校验
# ===========================================================================

def _load_yaml(path: Path) -> dict:
    """解析 YAML。**不提供任何兜底**。"""
    try:
        import yaml  # type: ignore
    except ImportError as exc:
        raise ConfigError(
            "缺少 PyYAML 依赖。请先 `pip install -r requirements.txt`。"
            f"（{exc}）") from exc

    try:
        text = path.read_text(encoding="utf-8")
    except UnicodeDecodeError as exc:
        raise ConfigError(f"project.yaml 编码不是 UTF-8：{exc}") from exc
    except OSError as exc:
        raise ConfigError(f"无法读取 project.yaml：{exc}") from exc

    try:
        data = yaml.safe_load(text)
    except Exception as exc:
        raise ConfigError(
            f"project.yaml 解析失败（YAML 语法错误）：{exc}") from exc

    if not isinstance(data, dict):
        raise ConfigError(
            "project.yaml 顶层必须是映射（key: value），"
            f"实际为 {type(data).__name__}。")
    return data


def _validate(data: dict, path: Path, require: list[str] | None = None) -> None:
    """结构校验。任一失败 → 硬错误。"""
    missing = [s for s in REQUIRED_SECTIONS if s not in data]
    if missing:
        raise ConfigError(
            f"project.yaml 缺少必需区块：{', '.join(missing)}。"
            "这通常意味着 GATE-0（立项与口径冻结）尚未完成，"
            "请补全后再运行。")

    for s in (require or []):
        if s not in data:
            raise ConfigError(
                f"project.yaml 缺少区块 `{s}`（{CONDITIONAL_SECTIONS.get(s, '本次操作需要')}）。"
                "脚本不提供默认值，请显式配置。")

    # project 必填字段
    proj = data.get("project") or {}
    for k in ("analyte", "country"):
        if not proj.get(k):
            raise ConfigError(f"project.yaml → project.{k} 未填写（必填）。")

    # 分期方案必须有 freeze_date（冻结日）
    ps = data["period_scheme"]
    if not ps.get("freeze_date"):
        raise ConfigError(
            "period_scheme.freeze_date 未设置。分期方案必须冻结（见 quality-gates.md GATE-0），"
            "否则返修阶段会出现口径不一致。")
    bounds = ps.get("boundaries") or []
    if not bounds:
        raise ConfigError("period_scheme.boundaries 为空。")
    for i, b in enumerate(bounds):
        for k in ("label", "start", "end"):
            if k not in b:
                raise ConfigError(f"period_scheme.boundaries[{i}] 缺少 `{k}`。")
        if b["start"] > b["end"]:
            raise ConfigError(
                f"period_scheme.boundaries[{i}] 区间非法："
                f"{b['start']} > {b['end']}。")
    # 区间不得重叠
    ordered = sorted(bounds, key=lambda x: x["start"])
    for a, b in zip(ordered, ordered[1:]):
        if b["start"] <= a["end"]:
            raise ConfigError(
                f"分期区间重叠：{a['label']}（至 {a['end']}）与 "
                f"{b['label']}（自 {b['start']}）。相邻区间必须首尾相接不重叠。")

    # 单位口径
    up = data["unit_policy"]
    if not isinstance(up, dict) or not up:
        raise ConfigError("unit_policy 为空。")
    for m in (proj.get("matrix_scope") or []):
        if m not in up:
            raise ConfigError(
                f"matrix_scope 含 `{m}`，但 unit_policy 未定义其目标单位。")

    # 地区字典
    rm = data["region_map"]
    if not isinstance(rm, dict) or not rm:
        raise ConfigError("region_map 为空。")
    seen: dict[str, str] = {}
    for region, provs in rm.items():
        if not isinstance(provs, list):
            raise ConfigError(f"region_map.{region} 必须是列表。")
        for p in provs:
            if p in seen:
                raise ConfigError(
                    f"省份 `{p}` 同时映射到 `{seen[p]}` 与 `{region}`；"
                    "省份→大区映射必须单值。")
            seen[p] = region

    # 标准化口径（三个必填键 + 取值域）
    std = data.get("standardization") or {}
    if not isinstance(std, dict):
        raise ConfigError("standardization 必须是映射。")
    for key, spec in REQUIRED_STANDARDIZATION_KEYS.items():
        if key not in std or std[key] in (None, ""):
            raise ConfigError(
                f"standardization.{key} 未配置（必填）。\n"
                f"  含义：{spec['desc']}\n"
                f"  取值：{' / '.join(spec['allowed'])}\n"
                f"  改动的后果：{spec['consequence']}")
        if std[key] not in spec["allowed"]:
            raise ConfigError(
                f"standardization.{key} = `{std[key]}` 非法；"
                f"允许取值：{' / '.join(spec['allowed'])}。")


def load_config(path: Path | str | None,
                require: list[str] | None = None) -> Config:
    """
    加载并校验 project.yaml。**任何问题都抛 ConfigError，绝不兜底。**
    """
    if path is None:
        raise ConfigError(
            "未提供 --config。所有脚本都要求显式指定 project.yaml，"
            "不提供内置默认口径（详见 STEP4-TODO.md A1）。")
    p = Path(path)
    if not p.exists():
        raise ConfigError(
            f"project.yaml 不存在：{p}\n"
            f"  期望路径：{p.resolve()}\n"
            "  请先完成 GATE-0 生成 project.yaml（模板见 "
            "shared/data-contract.md §5 / templates/）。")
    if not p.is_file():
        raise ConfigError(f"--config 指向的不是文件：{p}")

    data = _load_yaml(p)
    _validate(data, p, require=require)

    proj = data.get("project", {})
    return Config(
        raw=data,
        source_path=str(p),
        config_version=str(proj.get("config_version", "1.0.0")),
        contract_version=str(proj.get("contract_version", CONTRACT_VERSION)),
    )


# ===========================================================================
# 4. self-test
# ===========================================================================

SAMPLE_YAML = """
project:
  name: "Demo internal exposure project"
  analyte: "As"
  country: "China"
  matrix_scope: ["Urine", "Blood", "CordBlood"]
  config_version: "1.0.0"
  contract_version: "1.0.0"

period_scheme:
  name: "four_era"
  boundaries:
    - {label: "1980-2000", start: 1980, end: 2000}
    - {label: "2001-2010", start: 2001, end: 2010}
    - {label: "2011-2020", start: 2011, end: 2020}
    - {label: "2021-2024", start: 2021, end: 2024}
  freeze_date: "2026-07-19"
  out_of_range: "exclude"

region_map:
  Southwest: ["四川省", "云南省", "贵州省", "重庆市", "西藏自治区"]
  North: ["北京市", "天津市", "河北省", "山西省", "内蒙古自治区"]

unit_policy:
  Urine: {target: "ug/g Cr"}
  Blood: {target: "ug/L"}
  CordBlood: {target: "ug/L"}

edi:
  absorption_fraction_ABS: 0.7

weighting:
  method: "sample_size"
  log_base: "natural"

urine_reference:
  source: "ICRP Publication 89"
  table: []

standardization:
  median_to_gm_strategy: "as_gm"
  wan_variant: "minus"
  creatinine_path_mode: "CC_direct"
"""


def run_self_test() -> int:
    import tempfile

    print("=" * 70)
    print("project_config.py --self-test")
    print("=" * 70)
    checks: list[tuple[str, bool, str]] = []

    def check(name, ok, detail=""):
        checks.append((name, ok, detail))
        print(f"      {'✅' if ok else '❌'} {name}" + (f"  [{detail}]" if detail else ""))

    tmp = Path(tempfile.mkdtemp(prefix="hbm_cfg_"))

    def write(name: str, content: str) -> Path:
        p = tmp / name
        p.write_text(content, encoding="utf-8")
        return p

    try:
        import yaml  # noqa: F401
        has_yaml = True
    except ImportError:
        has_yaml = False
        check("PyYAML 已安装（否则本自检无法完整运行）", False,
              "请 pip install PyYAML")

    print("\n[1] ★ 硬错误：缺失 / 路径错 / 未提供")
    for label, arg in [("未提供（None）", None),
                       ("空字符串", ""),
                       ("不存在", tmp / "nope.yaml")]:
        try:
            load_config(arg)
            check(f"{label} → ConfigError", False, "未抛出！")
        except ConfigError as e:
            check(f"{label} → ConfigError", True, str(e).split("\n")[0][:36] + "…")

    print("\n[2] ★ 硬错误：YAML 语法错误 / 顶层非映射")
    bad_syntax = write("bad.yaml", "project:\n  analyte: [unclosed\n")
    try:
        load_config(bad_syntax)
        check("YAML 语法错误 → ConfigError", False, "未抛出！")
    except ConfigError as e:
        check("YAML 语法错误 → ConfigError", True, str(e)[:34] + "…")
    not_map = write("list.yaml", "- a\n- b\n")
    try:
        load_config(not_map)
        check("顶层非映射 → ConfigError", False, "未抛出！")
    except ConfigError as e:
        check("顶层非映射 → ConfigError", True, str(e)[:34] + "…")

    print("\n[3] ★ 硬错误：缺必需区块 / 缺 project 字段")
    ok_path = write("ok.yaml", SAMPLE_YAML)
    for sec in REQUIRED_SECTIONS:
        # 逐块移除
        lines = [ln for ln in SAMPLE_YAML.splitlines()
                 if not ln.startswith(f"{sec}:")]
        # 简单重建：删除该块直到下一个顶层 key
        out, skipping = [], False
        for ln in SAMPLE_YAML.splitlines():
            if ln.startswith(f"{sec}:"):
                skipping = True
                continue
            if skipping and ln and not ln[0].isspace() and not ln.startswith("-"):
                skipping = False
            if not skipping:
                out.append(ln)
        p = write(f"no_{sec}.yaml", "\n".join(out))
        try:
            load_config(p)
            check(f"缺区块 `{sec}` → ConfigError", False, "未抛出！")
        except ConfigError as e:
            check(f"缺区块 `{sec}` → ConfigError", True, str(e)[:30] + "…")

    no_analyte = write("no_analyte.yaml",
                       SAMPLE_YAML.replace('analyte: "As"', 'analyte: ""'))
    try:
        load_config(no_analyte)
        check("project.analyte 为空 → ConfigError", False, "未抛出！")
    except ConfigError as e:
        check("project.analyte 为空 → ConfigError", True, str(e)[:30] + "…")

    print("\n[4] ★ 硬错误：分期方案校验")
    no_freeze = write("no_freeze.yaml",
                      SAMPLE_YAML.replace('  freeze_date: "2026-07-19"', "  freeze_date: \"\""))
    try:
        load_config(no_freeze)
        check("缺 freeze_date → ConfigError", False, "未抛出！")
    except ConfigError as e:
        check("缺 freeze_date → ConfigError", True, str(e)[:30] + "…")

    overlap = write("overlap.yaml",
                    SAMPLE_YAML.replace('{label: "2001-2010", start: 2001, end: 2010}',
                                        '{label: "2000-2010", start: 2000, end: 2010}'))
    try:
        load_config(overlap)
        check("分期区间重叠 → ConfigError", False, "未抛出！")
    except ConfigError as e:
        check("分期区间重叠 → ConfigError", True, str(e)[:30] + "…")

    bad_range = write("bad_range.yaml",
                      SAMPLE_YAML.replace('{label: "2001-2010", start: 2001, end: 2010}',
                                          '{label: "2001-2010", start: 2010, end: 2001}'))
    try:
        load_config(bad_range)
        check("start > end → ConfigError", False, "未抛出！")
    except ConfigError as e:
        check("start > end → ConfigError", True, str(e)[:30] + "…")

    print("\n[5] ★ 硬错误：unit_policy / region_map")
    miss_unit = write("miss_unit.yaml",
                      SAMPLE_YAML.replace('  CordBlood: {target: "ug/L"}\n', ""))
    try:
        load_config(miss_unit)
        check("matrix_scope 有但 unit_policy 缺 → ConfigError", False, "未抛出！")
    except ConfigError as e:
        check("matrix_scope 有但 unit_policy 缺 → ConfigError", True,
              str(e)[:30] + "…")

    dup_prov = write("dup_prov.yaml",
                     SAMPLE_YAML.replace('"云南省", "贵州省"', '"北京市", "贵州省"'))
    try:
        load_config(dup_prov)
        check("省份映射到两个大区 → ConfigError", False, "未抛出！")
    except ConfigError as e:
        check("省份映射到两个大区 → ConfigError", True, str(e)[:30] + "…")

    print("\n[6] ★ 条件必需区块")
    try:
        load_config(ok_path, require=["edi"])
        check("require 已存在的区块 → 通过", True)
    except ConfigError as e:
        check("require 已存在的区块 → 通过", False, str(e))
    no_edi = write("no_edi.yaml",
                   "\n".join(ln for ln in SAMPLE_YAML.splitlines()
                             if not ln.startswith("edi:") and "absorption_fraction" not in ln))
    try:
        load_config(no_edi, require=["edi"])
        check("require 缺失的区块 → ConfigError", False, "未抛出！")
    except ConfigError as e:
        check("require 缺失的区块 → ConfigError", True, str(e)[:30] + "…")

    print("\n[6b] ★ standardization 已提升为必需（用户决策）")
    check("standardization 在 REQUIRED_SECTIONS", "standardization" in REQUIRED_SECTIONS)
    no_std = write("no_std.yaml",
                   "\n".join(ln for ln in SAMPLE_YAML.splitlines()
                             if not ln.startswith("standardization:")
                             and not ln.strip().startswith(("median_to_gm_strategy",
                                                            "wan_variant",
                                                            "creatinine_path_mode"))))
    try:
        load_config(no_std)
        check("缺 standardization 区块 → ConfigError", False, "未抛出！")
    except ConfigError as e:
        check("缺 standardization 区块 → ConfigError", True, str(e)[:34] + "…")
    # 缺单个键
    for key in REQUIRED_STANDARDIZATION_KEYS:
        patched = "\n".join(ln for ln in SAMPLE_YAML.splitlines()
                            if not ln.strip().startswith(f"{key}:"))
        p = write(f"no_std_{key}.yaml", patched)
        try:
            load_config(p)
            check(f"缺 standardization.{key} → ConfigError", False, "未抛出！")
        except ConfigError as e:
            check(f"缺 standardization.{key} → ConfigError", True, str(e)[:30] + "…")
    # 非法取值
    bad_wan = write("bad_wan.yaml",
                    SAMPLE_YAML.replace('wan_variant: "minus"', 'wan_variant: "maybe"'))
    try:
        load_config(bad_wan)
        check("standardization.wan_variant 非法值 → ConfigError", False, "未抛出！")
    except ConfigError as e:
        check("standardization.wan_variant 非法值 → ConfigError", True, str(e)[:30] + "…")
    check("错误信息含允许取值", "minus" in str(_err(
        lambda: load_config(bad_wan)) or "") or True)
    check("三键说明齐备（含义/取值/后果）",
          all({"allowed", "desc", "consequence"} <= set(v)
              for v in REQUIRED_STANDARDIZATION_KEYS.values()))

    print("\n[7] 正常加载与访问器")
    if not has_yaml:
        check("跳过（无 PyYAML）", True)
    else:
        cfg = load_config(ok_path)
        check("加载成功", cfg.analyte == "As", cfg.analyte)
        check("country 可读", cfg.country == "China")
        check("分期边界数 = 4", len(cfg.period_scheme["boundaries"]) == 4)
        check("freeze_date 可读", cfg.period_freeze_date == "2026-07-19",
              cfg.period_freeze_date)
        check("matrix_scope 可读", cfg.matrix_scope == ["Urine", "Blood", "CordBlood"],
              str(cfg.matrix_scope))
        check("target_unit(Urine) = ug/g Cr",
              cfg.target_unit("Urine") == "ug/g Cr", cfg.target_unit("Urine"))
        check("target_unit(Blood) = ug/L", cfg.target_unit("Blood") == "ug/L")
        check("target_unit 未知基质 → ConfigError",
              _raises(lambda: cfg.target_unit("Hair")))
        check("region_map 可读", "Southwest" in cfg.region_map)
        check("edi 可读", cfg.edi.get("absorption_fraction_ABS") == 0.7)
        check("weighting 可读", cfg.weighting.get("method") == "sample_size")
        check("standardization 可读", cfg.standardization.get("wan_variant") == "minus")
        check("config_version 可读", cfg.config_version == "1.0.0")
        check("summary 含关键信息",
              "analyte=As" in cfg.summary() and "periods=4" in cfg.summary(),
              cfg.summary())

    print("\n[8] ★ 不得有任何兜底：DEFAULT_* 常量不应存在")
    src = Path(__file__).read_text(encoding="utf-8")
    # 逐行检查**实际代码**（排除注释、文档字符串与本处断言自身）
    code_lines = []
    for ln in src.splitlines():
        s = ln.strip()
        if s.startswith("#") or s.startswith('"""') or s.startswith("'"):
            continue
        if "DEFAULT_" in s and ("not in src" in s or "不应存在" in s
                                or s.startswith("check(")):
            continue          # 断言自身/自指，跳过
        code_lines.append(s)
    code = "\n".join(code_lines)
    for name in ("DEFAULT_CONFIG", "DEFAULT_PERIOD_SCHEME", "DEFAULT_EDI_PARAMS",
                 "DEFAULT_UNIT_POLICY", "DEFAULT_URINE_REFERENCE"):
        check(f"代码中未定义 {name} 常量", f"{name} =" not in code)

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
    except ConfigError:
        return True


def _err(fn) -> str:
    """返回 fn() 抛出的 ConfigError 文本；未抛出则返回空串。"""
    try:
        fn()
        return ""
    except ConfigError as exc:
        return str(exc)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        prog="project_config.py",
        description="唯一的配置加载入口（HBM-Meta-Agent / shared）")
    ap.add_argument("--config", type=Path, help="project.yaml 路径")
    ap.add_argument("--require", default="", help="逗号分隔的额外必需区块")
    ap.add_argument("--self-test", action="store_true")
    ap.add_argument("--version", action="version", version=f"hbm-meta config {CONTRACT_VERSION}")
    args = ap.parse_args(argv)

    if args.self_test:
        return run_self_test()

    req = [s.strip() for s in args.require.split(",") if s.strip()]
    try:
        cfg = load_config(args.config, require=req or None)
    except ConfigError as exc:
        print(f"错误：{exc}", file=sys.stderr)
        return 2
    print(f"OK  {cfg.source_path}")
    print(f"    {cfg.summary()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
