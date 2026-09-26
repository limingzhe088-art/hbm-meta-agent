#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
verify_structure.py — 结构完整性校验（CI 用）

核验内容
  S1  SKILL.md frontmatter：`name` 存在、`description` 非空
  S2  子技能 frontmatter：`metadata.parent_skill` 指向有效的父技能
  S3  目录结构：6 个子技能均含 SKILL.md + references/ + scripts/ + templates/
  S4  子技能 `name` 与目录名一致
  S5  `description` 足够长（避免空泛触发词；阈值 80 字符）
  S6  必备顶层文件/目录存在（SKILL.md / agent/routing.md / shared/ / tests/ …）

用法
  python tests/verify_structure.py
  python tests/verify_structure.py --json
"""
from __future__ import annotations

import argparse
import io
import json
import re
import sys
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except (AttributeError, ValueError):  # pragma: no cover
    pass

ROOT = Path(__file__).resolve().parent.parent

SUB_SKILLS = [
    "S1-search-strategy",
    "S2-screening-quality",
    "S3-extraction-standardization",
    "S4-unit-statistic-conversion",
    "S5-weighted-pooling-edi",
    "S6-qc-audit-revision",
]

# ★ 已实现的子技能（含 SKILL.md）。S2 目前仅有目录骨架，
#   属**规划中**：其检查降级为 WARN（不使 CI 变红），完成实现后移入本列表即可。
IMPLEMENTED_SUB_SKILLS = [
    "S1-search-strategy",
    "S3-extraction-standardization",
    "S4-unit-statistic-conversion",
    "S5-weighted-pooling-edi",
    "S6-qc-audit-revision",
]

PLANNED_SUB_SKILLS = [s for s in SUB_SKILLS if s not in IMPLEMENTED_SUB_SKILLS]

REQUIRED_SUBDIRS = ["references", "scripts", "templates"]

TOP_LEVEL_REQUIRED = [
    "README.md",
    "LICENSE",
    "CITATION.cff",
    "CONTRIBUTING.md",
    "CHANGELOG.md",
    "SKILL.md",
    "agent/routing.md",
    "shared/data-contract.md",
    "shared/quality-gates.md",
    "shared/project_config.py",
    "shared/inferred_flags.py",
    "shared/snapshot.py",
    "templates/project.yaml",
    "requirements.txt",
    ".gitignore",
    "STEP4-TODO.md",
    "tests/verify_consistency.py",
    "tests/verify_structure.py",
    "tests/ci_local.py",
    "tests/inject_failure.py",
    ".github/workflows/validate.yml",
]

MIN_DESCRIPTION_LEN = 80


# ===========================================================================
# frontmatter 解析（不引入 YAML 依赖之外的东西；此处用轻量解析以在
# PyYAML 缺失时也能报错，而非崩溃）
# ===========================================================================

_FM_RE = re.compile(r"^---\s*\n(.*?)\n---\s*\n", re.DOTALL)


def read_frontmatter(path: Path) -> tuple[dict, str]:
    """
    返回 (frontmatter_dict, 错误信息)。
    轻量解析：只取顶层 `key: value` 与 `description:` 的多行字符串形式，
    足够校验 name/description/metadata.parent_skill/length。
    """
    if not path.exists():
        return {}, f"{path} 不存在"
    text = io.open(path, encoding="utf-8", errors="replace").read()
    m = _FM_RE.match(text)
    if not m:
        return {}, f"{path} 缺 frontmatter（应以 `---` 包裹）"
    body = m.group(1)
    # 优先用 PyYAML（更准确）
    try:
        import yaml  # type: ignore
        data = yaml.safe_load(body)
        if isinstance(data, dict):
            return data, ""
    except ImportError:
        pass
    except Exception as exc:
        return {}, f"{path} frontmatter YAML 解析失败：{exc}"
    # 退化解析
    data: dict = {}
    key = None
    buf: list[str] = []
    for line in body.splitlines():
        if re.match(r"^[A-Za-z_][\w-]*\s*:", line):
            if key:
                data[key] = " ".join(buf).strip()
            key = line.split(":", 1)[0].strip()
            val = line.split(":", 1)[1].strip().strip('"').strip("'")
            buf = [val] if val else []
        elif key and (line.startswith((" ", "\t")) or line.strip()):
            buf.append(line.strip())
    if key:
        data[key] = " ".join(buf).strip()
    data["__fallback_parser__"] = True
    return data, ""


# ===========================================================================
# 各项核验
# ===========================================================================

def check_s1_root_frontmatter() -> tuple[bool, list[str]]:
    problems: list[str] = []
    fm, err = read_frontmatter(ROOT / "SKILL.md")
    if err:
        return False, [err]
    name = str(fm.get("name") or "").strip()
    desc = str(fm.get("description") or "").strip()
    if not name:
        problems.append("SKILL.md frontmatter 缺 `name`")
    if not desc:
        problems.append("SKILL.md frontmatter 缺 `description`")
    return not problems, problems + [f"name={name!r} description 长度={len(desc)}"]


def check_s2_parent_skill() -> tuple[bool, list[str]]:
    problems: list[str] = []
    notes: list[str] = []
    root_fm, _ = read_frontmatter(ROOT / "SKILL.md")
    root_name = str(root_fm.get("name") or "").strip()
    if not root_name:
        return False, ["无法确定父技能名（SKILL.md 缺 name）"]
    for sub in IMPLEMENTED_SUB_SKILLS:
        p = ROOT / "skills" / sub / "SKILL.md"
        fm, err = read_frontmatter(p)
        if err:
            problems.append(f"{sub}: {err}")
            continue
        meta = fm.get("metadata") or {}
        ps = str((meta or {}).get("parent_skill") or "").strip() if isinstance(meta, dict) else ""
        if not ps:
            problems.append(f"{sub}: metadata.parent_skill 缺失")
        elif ps != root_name:
            problems.append(
                f"{sub}: metadata.parent_skill={ps!r} 与父技能 {root_name!r} 不符")
        else:
            notes.append(f"{sub}: parent_skill={ps}")
    notes.append(f"（规划中未校验：{', '.join(PLANNED_SUB_SKILLS)}）")
    return not problems, problems + notes


def check_s3_dirs() -> tuple[bool, list[str]]:
    problems: list[str] = []
    for sub in IMPLEMENTED_SUB_SKILLS:
        base = ROOT / "skills" / sub
        if not base.exists():
            problems.append(f"{sub}: 目录不存在")
            continue
        if not (base / "SKILL.md").exists():
            problems.append(f"{sub}: 缺 SKILL.md")
        for d in REQUIRED_SUBDIRS:
            if not (base / d).is_dir():
                problems.append(f"{sub}: 缺 {d}/ 目录")
    return not problems, problems + [
        f"已核对 {len(IMPLEMENTED_SUB_SKILLS)} 个已实现子技能"
        f"（另有 {len(PLANNED_SUB_SKILLS)} 个规划中：{', '.join(PLANNED_SUB_SKILLS)}）"]


def check_s4_name_matches_dir() -> tuple[bool, list[str]]:
    """
    子技能 `name` 应等于 `hbm-<目录名去掉 S<n>- 前缀>`。
    例：目录 `S4-unit-statistic-conversion` → name `hbm-unit-statistic-conversion`。
    """
    problems: list[str] = []
    notes: list[str] = []
    for sub in IMPLEMENTED_SUB_SKILLS:
        fm, err = read_frontmatter(ROOT / "skills" / sub / "SKILL.md")
        if err:
            problems.append(f"{sub}: {err}")
            continue
        name = str(fm.get("name") or "").strip()
        # 去掉 `S<n>-` 前缀后加 `hbm-`
        expect = "hbm-" + sub.split("-", 1)[1]
        if name != expect:
            problems.append(f"{sub}: name={name!r}，应为 {expect!r}")
        else:
            notes.append(f"{sub}: name={name}")
    return not problems, problems + notes


def check_s5_description_length() -> tuple[bool, list[str]]:
    problems: list[str] = []
    lengths: list[str] = []
    pairs = [("(root)", ROOT / "SKILL.md")] + [
        (s, ROOT / "skills" / s / "SKILL.md") for s in IMPLEMENTED_SUB_SKILLS]
    for label, p in pairs:
        fm, err = read_frontmatter(p)
        if err:
            problems.append(f"{label}: {err}")
            continue
        desc = str(fm.get("description") or "").strip()
        if len(desc) < MIN_DESCRIPTION_LEN:
            problems.append(
                f"{label}: description 仅 {len(desc)} 字符（< {MIN_DESCRIPTION_LEN}），"
                "触发词可能不足")
        lengths.append(f"{label}={len(desc)}")
    return not problems, problems + [" ".join(lengths)]


def check_s6_top_level() -> tuple[bool, list[str]]:
    problems: list[str] = []
    for rel in TOP_LEVEL_REQUIRED:
        if not (ROOT / rel).exists():
            problems.append(f"缺 {rel}")
    return not problems, problems + [f"已核对 {len(TOP_LEVEL_REQUIRED)} 项"]


CHECKS = [
    ("S1", "根 SKILL.md frontmatter（name / description）", check_s1_root_frontmatter),
    ("S2", "子技能 metadata.parent_skill 指向有效父技能", check_s2_parent_skill),
    ("S3", "6 个子技能目录结构完整", check_s3_dirs),
    ("S4", "子技能 name 与目录名一致", check_s4_name_matches_dir),
    ("S5", "description 长度充足（触发词足够）", check_s5_description_length),
    ("S6", "必备顶层文件/目录存在", check_s6_top_level),
]


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        prog="verify_structure.py",
        description="结构完整性校验（HBM-Meta-Agent，CI 用）")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)

    results = []
    print("=" * 72)
    print("verify_structure.py — 结构完整性校验")
    print("=" * 72)
    for cid, desc, fn in CHECKS:
        ok, notes = fn()
        results.append({"id": cid, "desc": desc, "ok": ok, "notes": notes})
        print(f"\n[{cid}] {desc}")
        for n in notes:
            print(f"      {'✅' if ok else '❌'} {n}")

    n_ok = sum(1 for r in results if r["ok"])
    print("\n" + "-" * 72)
    print(f"结构校验：{n_ok}/{len(results)} 项通过")
    if args.json:
        print(json.dumps({"ok": n_ok == len(results), "results": results},
                         ensure_ascii=False, indent=2))
    return 0 if n_ok == len(results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
