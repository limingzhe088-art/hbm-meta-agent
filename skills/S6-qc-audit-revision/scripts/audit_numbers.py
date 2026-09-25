#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
audit_numbers.py — 数值审计：稿件数字 vs 快照重算

阶段状态：第三步骨架版
  ✅ 完整 CLI（--input 稿件 / --config / --out）
  ✅ --self-test（四类状态均可被检出：OK / MISMATCH / STALE_MANUSCRIPT / NO_SOURCE）
  ✅ 核心逻辑：
       ① 快照指纹读取与漂移判定（STALE_MANUSCRIPT）
       ② 从稿件按"占位符"或"上下文线索"提取待审数值
       ③ 用 weighted_gm / edi_calculation 从快照重算
       ④ 逐项比对 → 四状态分类 + 容差分级
       ⑤ 连带修改定位（所在整句 + 比较级/趋势词检测）
  ⏳ 第四步待办：project.yaml 缺失改为硬错误、真实 docx/md 解析器接入、CI 集成

设计说明
  稿件取值有两种模式：
    A. 占位符模式（推荐，确定性最强）：稿件写 {{period_GM:Urine:1980-2000}}
    B. 上下文线索模式（用于既有稿件）：正则匹配"数字 + 单位"并按线索词归类
  本骨架两种都支持；线索模式用于真实稿件需按目标期刊表述调整正则。

用法
  python audit_numbers.py --self-test
  python audit_numbers.py --input 稿件.md --config project.yaml --out 对照表.md
"""
from __future__ import annotations

import argparse
import csv
import json
import math
import re
import sys
from dataclasses import dataclass, field, asdict
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except (AttributeError, ValueError):  # pragma: no cover
    pass

sys.path.insert(0, str(Path(__file__).resolve().parent))

# 跨技能导入：S6 依赖 S5 的加权合并与 EDI 实现（避免复制算法，保证单一实现）
_S5 = Path(__file__).resolve().parent.parent.parent / "S5-weighted-pooling-edi" / "scripts"
if _S5.exists():
    sys.path.insert(0, str(_S5))
try:
    import weighted_gm as wgm  # noqa: E402
    import edi_calculation as edi  # noqa: E402
except ImportError as _exc:  # pragma: no cover
    print(f"错误：无法导入 S5 模块（{_exc}）。请确认 S5-weighted-pooling-edi/scripts 存在。",
          file=sys.stderr)
    raise SystemExit(2)

# ★ 唯一配置入口（shared/project_config.py）
_SHARED = Path(__file__).resolve().parent.parent.parent.parent / "shared"
if str(_SHARED) not in sys.path:
    sys.path.insert(0, str(_SHARED))
try:
    from project_config import ConfigError, load_config  # noqa: E402
    from snapshot import Snapshot, detect_stale  # noqa: E402
except ImportError as _exc:  # pragma: no cover
    print(f"错误：无法导入 shared/ 下模块（{_exc}）", file=sys.stderr)
    raise SystemExit(2)


def load_edi_config_from(cfg) -> dict:
    """
    从已加载的 Config 派生 EDI 参数（复用 S5 的 load_edi_config，
    保证与 S5 的配置来源**完全一致**，不另立解析逻辑）。
    """
    import importlib
    m = importlib.import_module("edi_calculation")
    return m.load_edi_config(cfg.source_path)["edi"]


# ===========================================================================
# 1. 常量
# ===========================================================================

STATUS_OK = "OK"
STATUS_MISMATCH = "MISMATCH"
STATUS_STALE = "STALE_MANUSCRIPT"
STATUS_NO_SOURCE = "NO_SOURCE"

# 容差（相对）
TOLERANCE = {
    "period_GM": 0.005,
    "region_GM": 0.005,
    "province_GM": 0.005,
    "population_GM": 0.005,
    "edi": 0.01,
    "correlation": 0.001,   # 绝对
    "percentage": 0.005,     # 绝对（百分点）
    "count": 0.0,            # 精确
}

# 连带修改触发词（含比较级/趋势/最高级表述的句子需复审）
LINKED_CHANGE_HINTS = [
    "declin", "decreas", "reduc", "rebound", "increas", "stabil",
    "higher", "lower", "highest", "lowest", "greater", "less than",
    "no data", "not available", "consistently", "throughout",
    "下降", "上升", "回升", "稳定", "高于", "低于", "最高", "最低", "无数据",
]

PLACEHOLDER_RE = re.compile(r"\{\{\s*([a-zA-Z_]+)\s*:\s*([^}]+?)\s*\}\}")

# 线索模式：数字 + 单位（宽松）
# 注：μ/µ/u 前缀必须可选（`ug/g Cr` 与 `μg/g Cr` 都要匹配）；
#     此前把 `μ?` 写成必需导致裸 `ug/g Cr` 漏匹配（自查发现）。
NUMBER_WITH_UNIT_RE = re.compile(
    r"(?P<value>\d+(?:\.\d+)?)\s*"
    r"(?P<unit>[μµu]?g\s*/\s*(?:g\s*Cr|L)|[μµu]g\s*/\s*kg\s*bw\s*/\s*day)",
    re.IGNORECASE)

# ★ 百分比：**不自动重算**。降幅的分母选择不唯一（相对基期？相对峰值？），
#   自动重算会引入"假确定感"；人工核对两个分层值的降幅成本极低。
#   决策见 STEP4-TODO.md；识别到百分比时标记为 unresolved → NO_SOURCE。
PERCENT_RE = re.compile(r"\d+(?:\.\d+)?\s*%")
PERCENT_ADVICE = ("降幅百分比需由两个分层值算出，须人工核对"
                  "（分母选择不唯一：相对基期、相对峰值或其他）")


# ===========================================================================
# 2. 数据结构
# ===========================================================================

@dataclass
class AuditItem:
    index: int
    value_type: str            # period_GM / region_GM / province_GM / population_GM / edi / count / …
    stratum: str               # 分层条件（人读）
    manuscript_value: float | None
    recomputed_value: float | None
    diff: float | None
    rel_diff: float | None
    status: str = STATUS_OK
    source: str = ""            # 快照标签 + 生成脚本
    linked_change: str = ""     # 连带需修改的句子
    note: str = ""
    unit: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


# ===========================================================================
# 3. 快照指纹
# ===========================================================================
# ★ Snapshot / detect_stale 已迁移到**单一来源** `shared/snapshot.py`
#   （原为本文件内的本地类，仅 4 个字段；共享版含 §4 全部 9 个字段）。
#   本文件只保留"从快照登记表/JSON 读取元数据"这一读取辅助。

def read_snapshot_meta(path: Path) -> dict:
    """
    从 `_state/snapshots.csv` 或伴随 json 读取快照元数据。
    CSV 取**最后一行**（登记表为追加式，最后一行即最新快照）。
    """
    if path is None or not path.exists():
        return {}
    if path.suffix.lower() == ".json":
        return json.loads(path.read_text(encoding="utf-8"))
    if path.suffix.lower() == ".csv":
        with path.open(encoding="utf-8-sig", newline="") as fh:
            rows = list(csv.DictReader(fh))
        return rows[-1] if rows else {}
    return {}


def snapshot_from_meta(meta: dict) -> Snapshot:
    """
    由快照登记表/JSON 的元数据构造共享版 `Snapshot`。
    兼容两种列名：`snapshot_tag`（登记表）与 `tag`（JSON）。
    """
    def _int(v) -> int:
        try:
            return int(str(v).strip())
        except (TypeError, ValueError):
            return 0

    return Snapshot(
        tag=str(meta.get("snapshot_tag") or meta.get("tag") or "unknown"),
        sha256=str(meta.get("sha256") or ""),
        record_count=_int(meta.get("record_count", 0)),
        study_count=_int(meta.get("study_count", 0)),
        source_table=str(meta.get("source_table") or ""),
        generated_at=str(meta.get("created_at") or meta.get("generated_at") or ""),
        config_version=str(meta.get("config_version") or "1.0.0"),
        generator="audit_numbers.py",
    )


# ===========================================================================
# 4. 从稿件提取待审数值
# ===========================================================================

def extract_placeholders(text: str) -> list[dict]:
    """
    占位符模式：{{period_GM:Urine:1980-2000}}
    返回 [{'type': 'period_GM', 'args': ['Urine','1980-2000'], 'raw': ...}]
    """
    out = []
    for m in PLACEHOLDER_RE.finditer(text):
        out.append({"type": m.group(1), "args": [a.strip() for a in m.group(2).split(":")],
                    "raw": m.group(0)})
    return out


def mask_placeholders(text: str, keep: str | None = None) -> str:
    """
    把占位符替换为空串后再抽取线索数字。
    否则占位符里的年份（如 `{{period_GM:Urine:1980-2000}}`）会被误当作带单位数值。

    `keep`：保留指定的那一个占位符原文（用于定位它所在的句子），其余仍遮蔽。
    """
    if keep is None:
        return PLACEHOLDER_RE.sub(" ", text)

    def _rep(m):
        return m.group(0) if m.group(0) == keep else " "

    return PLACEHOLDER_RE.sub(_rep, text)


def split_sentences(paragraph: str) -> list[str]:
    """
    把段落切成句子，供线索模式使用。
    ★ 必要性：若按段落整块推断分层，同一段内后一句的分期/地区线索会污染前一句的数值
      （实测：`...32.58 ... 1980-2000 and blood arsenic ... in North China.` 曾把血值
      误判为 `period=1980-2000`，地区推断失效）。先切句再推断即可避免。
    同时处理中英文句号、分号与换行。
    """
    parts = re.split(r"(?<=[.!?。！？])\s+|\n+", paragraph or "")
    return [p.strip() for p in parts if p.strip()]


def extract_context_numbers(text: str) -> list[dict]:
    """
    上下文线索模式：数字 + 单位 → 记录所在**句子**与线索词。
    会先遮蔽占位符，避免两种模式互相污染；并按句切分以避免跨句线索污染。
    """
    text = mask_placeholders(text)
    out = []
    for sentence in split_sentences(text):
        for m in NUMBER_WITH_UNIT_RE.finditer(sentence):
            out.append({"value": float(m.group("value")), "unit": m.group("unit"),
                        "sentence": sentence})
    return out


def sentence_needs_linked_change(sentence: str) -> bool:
    low = sentence.lower()
    return any(h in low for h in LINKED_CHANGE_HINTS)


# ===========================================================================
# 4b. 从线索推断分层（P0：让 audit_numbers 能真正重算）
# ===========================================================================

MATRIX_TOKENS = {
    "umbilical": "CordBlood", "cord blood": "CordBlood", "cordblood": "CordBlood",
    "cord": "CordBlood", "脐": "CordBlood", "脐带血": "CordBlood",
    "urine": "Urine", "urinary": "Urine", "尿": "Urine",
    "blood": "Blood", "血": "Blood", "serum": "Blood", "plasma": "Blood",
}
REGION_TOKENS = {
    "northwest": "Northwest", "northeast": "Northeast", "southwest": "Southwest",
    "southeast": "Southeast", "north": "North", "south": "South",
    "east": "East", "central": "Central", "west": "West",
    "西北": "Northwest", "东北": "Northeast", "西南": "Southwest",
    "华北": "North", "华南": "South", "华东": "East", "华中": "Central",
    "中国东部": "East", "中国西部": "West",
}
POPULATION_TOKENS = {
    "adults": "Adults", "adult": "Adults", "成人": "Adults", "elderly": "Elderly",
    "minors": "Minors", "minor": "Minors", "children": "Minors", "child": "Minors",
    "儿童": "Minors", "pregnant": "Pregnant", "pregnancy": "Pregnant", "孕": "Pregnant",
}
PERIOD_RE = re.compile(r"(?:19|20)\d{2}\s*[-–—~to]+\s*(?:19|20)\d{2}")

_WORD_RE_CACHE: dict[str, re.Pattern] = {}


def _has_token(text: str, token: str) -> bool:
    """
    词边界匹配（避免 `east` 命中 `Northeast` 之类的子串误配）。
    含 CJK 或空格的 token 退化为子串匹配（中文无词边界）。
    """
    if not token:
        return False
    if any("\u4e00" <= ch <= "\u9fff" for ch in token) or " " in token:
        return token in text
    pat = _WORD_RE_CACHE.get(token)
    if pat is None:
        pat = re.compile(r"(?<![a-z])" + re.escape(token) + r"(?![a-z])")
        _WORD_RE_CACHE[token] = pat
    return bool(pat.search(text))


def infer_stratum(sentence: str, scheme: dict) -> dict:
    """
    从句子线索推断该数字属于哪个分层。**最具体优先**：
        时段（句中最具体的时间限定）> 地区 > 人群
    返回 {'matrix':…, 'dim':…, 'key':…}；无法判定时 matrix/dim/key 为 None。
    """
    low = (sentence or "").lower()

    matrix = None
    for tok in sorted(MATRIX_TOKENS, key=len, reverse=True):
        if _has_token(low, tok):
            matrix = MATRIX_TOKENS[tok]
            break
    if matrix is None:
        return {"matrix": None, "dim": None, "key": None}

    # 1) 时段：句中显式年份区间，且能落到某一期
    for m in PERIOD_RE.finditer(sentence or ""):
        nums = re.findall(r"(?:19|20)\d{2}", m.group(0))
        if len(nums) == 2:
            start, end = int(nums[0]), int(nums[1])
            lab_start, lab_end = (wgm.assign_period(start, scheme),
                                  wgm.assign_period(end, scheme))
            if lab_start and lab_start == lab_end:
                return {"matrix": matrix, "dim": "period", "key": lab_start}

    # 2) 地区（长 token 优先，避免 `north` 抢占 `northwest`）
    for tok in sorted(REGION_TOKENS, key=len, reverse=True):
        if _has_token(low, tok):
            return {"matrix": matrix, "dim": "region", "key": REGION_TOKENS[tok]}

    # 3) 人群
    for tok in sorted(POPULATION_TOKENS, key=len, reverse=True):
        if _has_token(low, tok):
            return {"matrix": matrix, "dim": "population_group",
                    "key": POPULATION_TOKENS[tok]}

    return {"matrix": matrix, "dim": None, "key": None}


def recompute_stratum(rows: list[dict], scheme: dict, matrix: str,
                      dim: str, key: str) -> float | None:
    """按分层从主表重算加权 GM。"""
    vals, ws = [], []
    for r in rows:
        if str(r.get("sample_type") or "").strip() != matrix:
            continue
        if dim == "period":
            if wgm.assign_period(_f(r.get("time")), scheme) != key:
                continue
        elif dim == "region":
            if str(r.get("region") or "").strip() != key:
                continue
        elif dim == "province":
            if str(r.get("province") or "").strip() != key:
                continue
        elif dim == "population_group":
            if str(r.get("population_group") or "").strip() != key:
                continue
        else:
            continue
        g, w = _f(r.get("gm_summary")), _f(r.get("sample_size"))
        if g and w and g > 0 and w > 0:
            vals.append(g); ws.append(w)
    return wgm.weighted_gm(vals, ws) if vals else None


def build_items_from_text(text: str, scheme: dict) -> list[dict]:
    """
    线索模式 → 待审项（含推断出的分层），使真实稿件可直接审计。
    未能推断出分层的项标 `unresolved`，产出为 NO_SOURCE 并附原因。

    ★ 百分比（降幅等）一律标 `unresolved`：不自动重算（决策见 PERCENT_RE 注释）。
    """
    items: list[dict] = []
    covered_sentences: list[str] = []

    # ① 占位符模式优先：记录其覆盖的句子，供线索模式排除
    for ph in extract_placeholders(text):
        t = ph["type"]
        args = ph["args"]
        if t in ("period_GM", "region_GM", "province_GM", "population_GM") and len(args) >= 2:
            items.append({"type": t, "args": args, "value": None, "sentence": "",
                          "origin": "placeholder"})
            # 找到占位符所在句子并入"已覆盖"集合
            for sent in split_sentences(mask_placeholders(text, keep=ph["raw"])):
                if ph["raw"] in sent:
                    covered_sentences.append(sent.strip())

    # ② 线索模式：仅审计**未被占位符覆盖**的句子
    for n in extract_context_numbers(text):
        sent = n.get("sentence", "")
        if any(sent.strip() == cs for cs in covered_sentences):
            continue                      # 该句已由占位符覆盖 → 跳过，避免重复审计
        # ★ 百分比 → 一律 unresolved，不自动重算
        if PERCENT_RE.search(sent or ""):
            items.append({"type": "unresolved", "args": [], "value": n["value"],
                          "unit": n["unit"], "sentence": sent, "origin": "context",
                          "reason": PERCENT_ADVICE})
            continue
        info = infer_stratum(sent, scheme)
        if info["matrix"] and info["dim"] and info["key"]:
            t = {"period": "period_GM", "region": "region_GM",
                 "province": "province_GM",
                 "population_group": "population_GM"}[info["dim"]]
            items.append({"type": t, "args": [info["matrix"], info["key"]],
                          "value": n["value"], "unit": n["unit"], "sentence": sent,
                          "origin": "context"})
        else:
            items.append({"type": "unresolved", "args": [], "value": n["value"],
                          "unit": n["unit"], "sentence": sent, "origin": "context",
                          "reason": (f"无法从上下文推断分层"
                                     f"（matrix={info['matrix']}, dim={info['dim']}）")})
    return items


def read_master_table(path: Path) -> list[dict]:
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
    raise ValueError(f"不支持的主表类型：{path.suffix}")


# ===========================================================================
# 5. 核心审计
# ===========================================================================

def classify(type_: str, ms_val: float | None, calc_val: float | None) -> tuple[str, float | None, float | None]:
    """返回 (status, abs_diff, rel_diff)。"""
    if ms_val is None:
        return STATUS_NO_SOURCE, None, None
    if calc_val is None or ms_val is None:
        return STATUS_NO_SOURCE, None, None
    diff = ms_val - calc_val
    rel = abs(diff) / abs(calc_val) if calc_val else (0.0 if diff == 0 else float("inf"))
    tol = TOLERANCE.get(type_, 0.005)
    if type_ in ("correlation", "percentage"):
        ok = abs(diff) <= tol
    elif type_ == "count":
        ok = diff == 0
    else:
        ok = rel <= tol
    return (STATUS_OK if ok else STATUS_MISMATCH), diff, rel


def audit_period_gm(rows: list[dict], scheme: dict, matrix: str,
                    period: str) -> float | None:
    sub = [r for r in rows
           if str(r.get("sample_type", "")).strip() == matrix
           and wgm.assign_period(_f(r.get("time")), scheme) == period]
    if not sub:
        return None
    vals, ws = [], []
    for r in sub:
        try:
            g, w = float(r["gm_summary"]), float(r["sample_size"])
        except (KeyError, TypeError, ValueError):
            continue
        if g > 0 and w > 0:
            vals.append(g); ws.append(w)
    if not vals:
        return None
    return wgm.weighted_gm(vals, ws)


def _f(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def run_audit(rows: list[dict], scheme: dict, items: list[dict],
              current_snapshot: Snapshot,
              manuscript_snapshot: Snapshot | None = None,
              manuscript_text: str = "",
              edi_cfg: dict | None = None) -> list[AuditItem]:
    """
    执行审计。

    ★ 口径必须显式传入：`scheme` 来自 `project.yaml → period_scheme`；
    `edi_cfg` 在审计 EDI 项时必需（来自 `project.yaml → edi`）。
    **本函数不提供任何口径默认值**（A3：口径一律来自配置）。
    """
    stale = detect_stale(manuscript_snapshot, current_snapshot)
    out: list[AuditItem] = []

    for i, it in enumerate(items, start=1):
        t = it["type"]
        args = it.get("args", [])
        ms_val = it.get("value")
        calc = None
        stratum = ""
        source = f"snapshot={current_snapshot.fingerprint()};script=audit_numbers.py"

        if t in ("period_GM", "region_GM", "province_GM", "population_GM"):
            if len(args) >= 2:
                matrix, key = args[0], args[1]
                dim = {"period_GM": "period", "region_GM": "region",
                       "province_GM": "province",
                       "population_GM": "population_group"}[t]
                calc = recompute_stratum(rows, scheme, matrix, dim, key)
                stratum = f"{matrix} | {key}" if dim == "period" else f"{matrix} | {dim}={key}"
        elif t == "unresolved":
            out.append(AuditItem(
                i, t, "unknown", ms_val, None, None, None, STATUS_NO_SOURCE,
                source, "", it.get("reason", "无法推断分层"), it.get("unit", "")))
            continue
        elif t == "edi":
            if len(args) >= 2:
                matrix, pg = args[0], args[1]
                cfg = edi_cfg
                if cfg is None:
                    out.append(AuditItem(
                        i, t, f"{matrix} | {pg}", ms_val, None, None, None,
                        STATUS_NO_SOURCE, source, "",
                        "未提供 EDI 口径（project.yaml → edi）→ 无法重算", ""))
                    continue
                if matrix == "Urine":
                    vals, ws = [], []
                    for r in rows:
                        if str(r.get("sample_type", "")).strip() != "Urine":
                            continue
                        if str(r.get("population_group", "")).strip() != pg:
                            continue
                        g, w = _f(r.get("gm_summary")), _f(r.get("sample_size"))
                        if not g or not w:
                            continue
                        res = edi.compute_urine_edi(g, pg, cfg)
                        if res.edi_primary:
                            vals.append(res.edi_primary); ws.append(w)
                    calc = wgm.weighted_gm(vals, ws) if vals else None
                    stratum = f"Urine EDI | {pg}"
        elif t == "count":
            calc = float(len(rows)) if args[:1] == ["records"] else None
            stratum = "records"
        else:
            out.append(AuditItem(i, t, "unknown", ms_val, None, None, None,
                                 STATUS_NO_SOURCE, source, "", f"未识别类型 {t}", it.get("unit", "")))
            continue

        status, diff, rel = classify(t, ms_val, calc)
        if stale and status != STATUS_NO_SOURCE:
            status = STATUS_STALE
        linked = ""
        if status in (STATUS_MISMATCH, STATUS_STALE) and it.get("sentence"):
            if sentence_needs_linked_change(it["sentence"]):
                linked = it["sentence"]
        if calc is None and status != STATUS_NO_SOURCE:
            status = STATUS_NO_SOURCE

        out.append(AuditItem(i, t, stratum, ms_val, calc, diff, rel,
                             status, source, linked, "", it.get("unit", "")))
    return out


# ===========================================================================
# 6. 报告
# ===========================================================================

def render_report(items: list[AuditItem], snapshot: Snapshot,
                  manuscript_snapshot: Snapshot | None) -> str:
    counts = {s: sum(1 for x in items if x.status == s)
              for s in (STATUS_OK, STATUS_MISMATCH, STATUS_STALE, STATUS_NO_SOURCE)}
    lines = [
        "# 数值审计对照表",
        "",
        f"- 当前快照：`{snapshot.fingerprint()}`（记录 {snapshot.record_count} / 研究 {snapshot.study_count}）",
        f"- 稿件绑定快照：`{manuscript_snapshot.fingerprint() if manuscript_snapshot else '未声明'}`",
        f"- 审计项：{len(items)}",
        f"- **OK** {counts[STATUS_OK]}　**MISMATCH** {counts[STATUS_MISMATCH]}　"
        f"**STALE_MANUSCRIPT** {counts[STATUS_STALE]}　**NO_SOURCE** {counts[STATUS_NO_SOURCE]}",
        "",
    ]
    if counts[STATUS_STALE]:
        lines += ["> ⚠️ **检测到快照漂移**：稿件绑定的快照与当前快照哈希不一致。",
                  "> 全部相关数字标记 `STALE_MANUSCRIPT`，应**全量重算**而非逐个人工核对。", ""]

    lines += ["## 明细", "",
              "| # | 类型 | 分层 | 稿件原值 | 重算值 | 相对差 | 状态 | 来源 | 连带修改 |",
              "|---|---|---|---|---|---|---|---|---|"]
    for x in items:
        ms = "" if x.manuscript_value is None else f"{x.manuscript_value:g}"
        cv = "" if x.recomputed_value is None else f"{x.recomputed_value:.4f}"
        rd = "" if x.rel_diff is None else f"{x.rel_diff:.4%}"
        lc = (x.linked_change[:60] + "…") if len(x.linked_change) > 60 else x.linked_change
        lines.append(f"| {x.index} | `{x.value_type}` | {x.stratum} | {ms}{x.unit} | {cv} | "
                     f"{rd} | **{x.status}** | {x.source} | {lc} |")

    lines += ["", "## 待人工裁决", ""]
    pending = [x for x in items if x.status in (STATUS_MISMATCH, STATUS_STALE, STATUS_NO_SOURCE)]
    if not pending:
        lines.append("无。全部 `OK`，可定稿。")
    else:
        lines.append("| # | 分层 | 稿件原值 | 重算值 | 状态 | 请裁决 |")
        lines.append("|---|---|---|---|---|---|")
        for x in pending:
            lines.append(f"| {x.index} | {x.stratum} | "
                         f"{'' if x.manuscript_value is None else f'{x.manuscript_value:g}'} | "
                         f"{'' if x.recomputed_value is None else f'{x.recomputed_value:.4f}'} | "
                         f"**{x.status}** | ☐ 采信重算值 ☐ 修正换算 ☐ 保留并注明 ☐ 补来源 |")
    lines += ["", "---", "",
              "> AI 只产出差异与证据；`MISMATCH` / `NO_SOURCE` 的处置由人工裁决（见 `references/audit_protocol.md` §8）。",
              ""]
    return "\n".join(lines)


# ===========================================================================
# 7. self-test
# ===========================================================================

SELFTEST_ROWS = [
    # Urine 1980-2000：等值 32.58 → 加权 GM 必为 32.58
    {"record_id": "R1", "sample_type": "Urine", "time": "1995", "region": "Southwest",
     "province": "云南省", "population_group": "Adults",
     "gm_summary": "32.58", "sample_size": "1000", "study_no": "S001"},
    {"record_id": "R2", "sample_type": "Urine", "time": "1998", "region": "Southwest",
     "province": "贵州省", "population_group": "Adults",
     "gm_summary": "32.58", "sample_size": "500", "study_no": "S002"},
    # Urine 2001-2010：等值 12.45
    {"record_id": "R3", "sample_type": "Urine", "time": "2005", "region": "East",
     "province": "山东省", "population_group": "Adults",
     "gm_summary": "12.45", "sample_size": "800", "study_no": "S003"},
    # Blood 2001-2010：等值 2.72
    {"record_id": "R4", "sample_type": "Blood", "time": "2005", "region": "North",
     "province": "北京市", "population_group": "Adults",
     "gm_summary": "2.72", "sample_size": "600", "study_no": "S004"},
    # Blood 2011-2020：等值 4.24（供线索模式重算验证）
    {"record_id": "R5", "sample_type": "Blood", "time": "2015", "region": "North",
     "province": "北京市", "population_group": "Adults",
     "gm_summary": "4.24", "sample_size": "400", "study_no": "S005"},
]

SELFTEST_TEXT = """
The weighted GM of urinary arsenic decreased from {{period_GM:Urine:1980-2000}} ug/g Cr
in 1980-2000 to 12.4500 ug/g Cr in 2001-2010, a decline of 61.79%.
Blood arsenic declined to {{period_GM:Blood:2001-2010}}, and the pattern was consistent.
""".strip()


def run_self_test(scheme: dict | None = None,
                  edi_cfg: dict | None = None) -> int:
    """
    自检。`scheme` / `edi_cfg` 由调用方**显式传入**（CLI 的 --self-test 传夹具，
    正式路径不应调用本函数）。这样自检不再隐式耦合模块级夹具。
    """
    print("=" * 70)
    print("audit_numbers.py --self-test")
    print("=" * 70)
    checks: list[tuple[str, bool, str]] = []

    def check(name, ok, detail=""):
        checks.append((name, ok, detail))
        print(f"      {'✅' if ok else '❌'} {name}" + (f"  [{detail}]" if detail else ""))

    scheme = scheme or wgm.FIXTURE_PERIOD_SCHEME
    edi_cfg = edi_cfg or edi.FIXTURE_EDI_PARAMS
    snap = Snapshot("As_2026-06-05", "3f1a9c0d1e2f", record_count=688, study_count=384)
    snap_old = Snapshot("As_2026-03-29", "aaaabbbbcccc", record_count=650, study_count=370)

    print("\n[1] 重算函数")
    g = audit_period_gm(SELFTEST_ROWS, scheme, "Urine", "1980-2000")
    check("Urine 1980-2000 重算 = 32.58", abs(g - 32.58) < 1e-6, f"{g:.6f}")
    check("无匹配层返回 None",
          audit_period_gm(SELFTEST_ROWS, scheme, "Urine", "2021-2024") is None)

    print("\n[2] 状态分类：OK")
    items = [
        {"type": "period_GM", "args": ["Urine", "1980-2000"], "value": 32.58},
    ]
    res = run_audit(SELFTEST_ROWS, scheme, items, snap)
    check("完全一致 → OK", res[0].status == STATUS_OK, res[0].status)

    print("\n[3] 状态分类：MISMATCH")
    items = [
        {"type": "period_GM", "args": ["Urine", "1980-2000"], "value": 38.10},
    ]
    res = run_audit(SELFTEST_ROWS, scheme, items, snap)
    check("值不同 → MISMATCH", res[0].status == STATUS_MISMATCH, res[0].status)
    check("记录差值", res[0].diff is not None and abs(res[0].diff - (38.10 - 32.58)) < 1e-6,
          f"diff={res[0].diff:.4f}")
    check("记录相对差", res[0].rel_diff > 0.1, f"{res[0].rel_diff:.4%}")

    print("\n[4] 容差边界")
    # 32.58 * 1.004 = 32.71（0.4% < 0.5% 容差）→ OK
    res = run_audit(SELFTEST_ROWS, scheme,
                    [{"type": "period_GM", "args": ["Urine", "1980-2000"], "value": 32.71}],
                    snap)
    check("0.4% 差 → OK（容差内）", res[0].status == STATUS_OK, f"{res[0].rel_diff:.4%}")
    # 32.58 * 1.01 = 32.91（1% > 0.5%）→ MISMATCH
    res = run_audit(SELFTEST_ROWS, scheme,
                    [{"type": "period_GM", "args": ["Urine", "1980-2000"], "value": 32.91}],
                    snap)
    check("1% 差 → MISMATCH（超容差）", res[0].status == STATUS_MISMATCH, f"{res[0].rel_diff:.4%}")

    print("\n[5] ★ STALE_MANUSCRIPT（快照漂移）")
    check("哈希不同 → 判定漂移", detect_stale(snap_old, snap))
    check("哈希相同 → 不漂移", not detect_stale(snap, snap))
    check("未声明绑定快照 → 不判漂移（但无保护）", not detect_stale(None, snap))
    res = run_audit(SELFTEST_ROWS, scheme,
                    [{"type": "period_GM", "args": ["Urine", "1980-2000"], "value": 32.58}],
                    snap, manuscript_snapshot=snap_old)
    check("值本应 OK，但快照漂移 → STALE_MANUSCRIPT",
          res[0].status == STATUS_STALE, res[0].status)
    res_same = run_audit(SELFTEST_ROWS, scheme,
                         [{"type": "period_GM", "args": ["Urine", "1980-2000"], "value": 32.58}],
                         snap, manuscript_snapshot=snap)
    check("快照一致 → 仍为 OK", res_same[0].status == STATUS_OK, res_same[0].status)

    print("\n[6] 状态分类：NO_SOURCE")
    res = run_audit(SELFTEST_ROWS, scheme,
                    [{"type": "period_GM", "args": ["Urine", "2021-2024"], "value": 24.05}],
                    snap)
    check("分层无数据 → NO_SOURCE", res[0].status == STATUS_NO_SOURCE, res[0].status)
    res = run_audit(SELFTEST_ROWS, scheme,
                    [{"type": "period_GM", "args": ["Urine", "1980-2000"], "value": None}],
                    snap)
    check("稿件无值 → NO_SOURCE", res[0].status == STATUS_NO_SOURCE, res[0].status)

    print("\n[7] 占位符提取")
    ph = extract_placeholders(SELFTEST_TEXT)
    check("提取到 2 个占位符", len(ph) == 2, str([p["type"] for p in ph]))
    check("解析类型 period_GM", ph[0]["type"] == "period_GM")
    check("解析参数", ph[0]["args"] == ["Urine", "1980-2000"], str(ph[0]["args"]))

    print("\n[8] 线索模式提取（须与占位符模式隔离）")
    nums = extract_context_numbers(SELFTEST_TEXT)
    # SELFTEST_TEXT 中 2 个占位符被遮蔽，只剩 1 个真实带单位数字（12.4500 ug/g Cr）
    check("提取到 1 个真实数字（占位符已遮蔽）", len(nums) == 1,
          f"{len(nums)} 个: {[n['value'] for n in nums]}")
    check("数值解析正确", any(abs(n["value"] - 12.45) < 1e-9 for n in nums),
          str([n["value"] for n in nums]))
    # 无占位符时应提取全部
    nums2 = extract_context_numbers("Arsenic was 32.58 ug/g Cr and blood was 2.72 ug/L.")
    check("无占位符时提取 2 个", len(nums2) == 2, str([n["value"] for n in nums2]))
    check("★ 占位符内的年份未被误提取",
          not any(n["value"] in (1980.0, 2000.0, 2001.0, 2010.0) for n in nums),
          str([n["value"] for n in nums]))
    check("遮蔽函数生效", mask_placeholders("a {{x:1}} b") == "a   b",
          repr(mask_placeholders("a {{x:1}} b")))
    # 裸 ug/g Cr（无 μ）必须能匹配
    bare = extract_context_numbers("urinary arsenic was 23.50 ug/g Cr in adults.")
    check("裸 ug/g Cr 可匹配", len(bare) == 1 and abs(bare[0]["value"] - 23.50) < 1e-9,
          str([(n["value"], n["unit"]) for n in bare]))

    print("\n[9] 连带修改定位")
    check("'decline' 触发连带复审",
          sentence_needs_linked_change("urinary arsenic decreased from 32.58"))
    check("'highest' 触发", sentence_needs_linked_change("the highest level was in Southwest"))
    check("'no data' 触发", sentence_needs_linked_change("there was no data for that period"))
    check("中文'下降'触发", sentence_needs_linked_change("尿砷水平下降"))
    check("中性句不触发",
          not sentence_needs_linked_change("The study included 384 studies."))

    print("\n[10] 报告渲染")
    items = [
        {"type": "period_GM", "args": ["Urine", "1980-2000"], "value": 38.10,
         "sentence": "urinary arsenic decreased from 38.10 ug/g Cr"},
        {"type": "period_GM", "args": ["Urine", "2001-2010"], "value": 12.45},
        {"type": "period_GM", "args": ["Blood", "2001-2010"], "value": 2.72},
    ]
    res = run_audit(SELFTEST_ROWS, scheme, items, snap)
    md = render_report(res, snap, None)
    check("报告含四状态计数", "MISMATCH" in md and "OK" in md)
    check("报告含待人工裁决表", "待人工裁决" in md)
    check("报告含连带修改", "urinary arsenic decreased" in md or "连带修改" in md)
    check("MISMATCH 项进入待裁决", any(x.status == STATUS_MISMATCH for x in res))
    check("连带句被捕获", any(x.linked_change for x in res),
          str([x.linked_change[:30] for x in res if x.linked_change]))

    print("\n[11] 全 OK 时可定稿")
    res_allok = run_audit(SELFTEST_ROWS, scheme,
                          [{"type": "period_GM", "args": ["Urine", "1980-2000"], "value": 32.58},
                           {"type": "period_GM", "args": ["Urine", "2001-2010"], "value": 12.45},
                           {"type": "period_GM", "args": ["Blood", "2001-2010"], "value": 2.72}],
                          snap)
    md = render_report(res_allok, snap, None)
    check("全 OK 报告写明可定稿", "可定稿" in md)
    check("全 OK 无待裁决项", all(x.status == STATUS_OK for x in res_allok))

    print("\n[12] ★ P0：--master-table 端到端重算（线索模式 → 分层推断 → 重算）")
    # 用 SELFTEST_ROWS 作"主表"，构造一段真实风格稿件文本
    ms_text = (
        "The weighted GM of urinary arsenic in Southwest China was 39.03 ug/g Cr "
        "during 2011-2020. Blood arsenic in North China reached 4.24 ug/L. "
        "Urinary arsenic among children was 55.33 ug/g Cr. "
        "The overall sample comprised 500 records."
    )
    built = build_items_from_text(ms_text, scheme)
    check("从线索构建出待审项", len(built) >= 3, f"{len(built)} 项")
    kinds = [b["type"] for b in built]
    check("识别出 period_GM（分期优先）", "period_GM" in kinds, str(kinds))
    check("识别出 region_GM（句中无分期时）", "region_GM" in kinds, str(kinds))
    check("识别出 population_GM", "population_GM" in kinds, str(kinds))
    # 分层推断正确性
    p_items = [b for b in built if b["type"] == "period_GM"]
    check("时段推断 = 2011-2020",
          any(b["args"][1] == "2011-2020" for b in p_items),
          str([b["args"] for b in p_items]))
    r_items = [b for b in built if b["type"] == "region_GM"]
    check("地区推断 = North（Blood 句）",
          any(b["args"][1] == "North" for b in r_items),
          str([b["args"] for b in r_items]))

    # 用主表重算（SELFTEST_ROWS 中 Urine 2011-2020 无数据 → NO_SOURCE；Blood 有）
    audit_items = [{"type": b["type"], "args": b["args"], "value": b["value"],
                    "unit": b.get("unit", ""), "sentence": b.get("sentence", "")}
                   for b in built if b["type"] != "unresolved"]
    res_ctx = run_audit(SELFTEST_ROWS, scheme, audit_items, snap)
    check("重算产出结果", len(res_ctx) == len(audit_items), str(len(res_ctx)))
    check("有数据的层得到重算值",
          any(x.recomputed_value is not None for x in res_ctx),
          str([(x.stratum, x.recomputed_value) for x in res_ctx]))
    check("无数据的层标 NO_SOURCE",
          any(x.status == STATUS_NO_SOURCE for x in res_ctx),
          str([(x.stratum, x.status) for x in res_ctx]))

    # 占位符模式仍可重算（与既有自检 [2]–[5] 一致）
    ph_text = "urinary arsenic was {{period_GM:Urine:1980-2000}} ug/g Cr in that period."
    built_ph = build_items_from_text(ph_text, scheme)
    check("占位符模式构建项", any(b["type"] == "period_GM" for b in built_ph),
          str([b["type"] for b in built_ph]))
    ph_items = [{"type": b["type"], "args": b["args"], "value": 32.58,
                 "unit": b.get("unit", ""), "sentence": b.get("sentence", "")}
                for b in built_ph if b["type"] == "period_GM"]
    res_ph = run_audit(SELFTEST_ROWS, scheme, ph_items, snap)
    check("占位符项重算一致 → OK", res_ph[0].status == STATUS_OK,
          f"{res_ph[0].status}（重算 {res_ph[0].recomputed_value}）")

    # 无法推断分层 → unresolved → NO_SOURCE
    unresolved = [b for b in built if b["type"] == "unresolved"]
    if unresolved:
        check("无法推断分层的项标 unresolved", True, f"{len(unresolved)} 项")
        res_un = run_audit(SELFTEST_ROWS, scheme,
                           [{"type": "unresolved", "args": [], "value": 500.0,
                             "reason": "无法推断分层"}], snap)
        check("unresolved → NO_SOURCE", res_un[0].status == STATUS_NO_SOURCE,
              res_un[0].status)
        check("NO_SOURCE 带原因", "无法推断" in res_un[0].note, res_un[0].note)

    print("\n[13] infer_stratum 单元验证")
    check("Urine + 年份区间",
          infer_stratum("urinary arsenic during 2011-2020", scheme)["key"] == "2011-2020",
          str(infer_stratum("urinary arsenic during 2011-2020", scheme)))
    check("Blood + 地区",
          infer_stratum("blood arsenic in North China", scheme)["key"] == "North",
          str(infer_stratum("blood arsenic in North China", scheme)))
    check("Urine + 人群（儿童）",
          infer_stratum("urinary arsenic among children", scheme)["key"] == "Minors",
          str(infer_stratum("urinary arsenic among children", scheme)))
    check("脐带血识别",
          infer_stratum("cord blood arsenic", scheme)["matrix"] == "CordBlood")
    check("中文地区识别",
          infer_stratum("尿砷在西南地区", scheme)["key"] == "Southwest",
          str(infer_stratum("尿砷在西南地区", scheme)))
    check("无基质线索 → matrix=None",
          infer_stratum("the sample comprised 500 records", scheme)["matrix"] is None)
    check("分期优先于地区",
          infer_stratum("urinary arsenic in Southwest during 2011-2020", scheme)["dim"]
          == "period")

    print("\n[14] 报告渲染（含 unresolved）")
    md2 = render_report(res_ctx, snap, None)
    check("报告含 NO_SOURCE 项", "NO_SOURCE" in md2)
    check("报告含待人工裁决", "待人工裁决" in md2)

    print("\n[15] ★ 按句切分（避免跨句线索污染）")
    para = ("Urinary arsenic declined from 32.58 ug/g Cr in 1980-2000 and blood arsenic "
            "reached 5.99 ug/L in North China.")
    sents = split_sentences(para)
    check("整段被切为 1 句（无句号分隔）", len(sents) == 1, str(len(sents)))
    multi = split_sentences("Urine was 32.58 ug/g Cr in 1980-2000. Blood was 5.99 ug/L in North.")
    check("多句被正确切分", len(multi) == 2, str(multi))
    # 关键：第二句不得继承第一句的 period 线索
    info2 = infer_stratum(multi[1], scheme)
    check("★ 第二句不继承第一句的 period 线索",
          info2["dim"] != "period", str(info2))
    check("第二句正确推断为 region=North",
          info2["dim"] == "region" and info2["key"] == "North", str(info2))
    nums = extract_context_numbers(para)
    check("同一句内两个数值都在该句上下文中", len(nums) == 2,
          str([(n["value"], n["sentence"][:28]) for n in nums]))
    nums2 = extract_context_numbers("Urine was 1.0 ug/g Cr. Blood was 2.0 ug/L.")
    check("跨句数值各归各句", len(nums2) == 2 and
          "Urine" in nums2[0]["sentence"] and "Blood" in nums2[1]["sentence"],
          str([n["sentence"] for n in nums2]))

    print("\n[16] ★ 降幅百分比不自动重算（决策：分母不唯一 → 假确定感）")
    # 情形 A：**纯百分比**（无浓度单位）→ 数字提取器本就不抓，天然不会误判为浓度
    pct_only = "Urinary arsenic showed a decline of 61.79% between the two periods."
    built_only = build_items_from_text(pct_only, scheme)
    check("纯百分比不被当作浓度值（提不到项）",
          len(built_only) == 0, str([b["type"] for b in built_only]))
    check("PERCENT_RE 仍能识别百分比句式", bool(PERCENT_RE.search(pct_only)))

    # 情形 B：**同句含浓度 + 百分比** → 该句标 unresolved，不自动重算
    pct_text = ("Urinary arsenic in Southwest China declined to 12.45 ug/g Cr, "
                "a decline of 61.79%.")
    built_pct = build_items_from_text(pct_text, scheme)
    check("含百分比的句子 → 产出 unresolved（而非 period_GM/region_GM）",
          len(built_pct) == 1 and built_pct[0]["type"] == "unresolved",
          str([(b["type"], b.get("reason", "")[:20]) for b in built_pct]))
    check("unresolved 原因写明'须人工核对'",
          "人工核对" in built_pct[0].get("reason", ""), built_pct[0].get("reason", ""))
    check("原因含分母不唯一的说明",
          "分母" in built_pct[0].get("reason", ""), built_pct[0].get("reason", ""))
    check("原因提到'降幅百分比'",
          "降幅百分比" in built_pct[0].get("reason", ""), built_pct[0].get("reason", ""))
    res_pct = run_audit(SELFTEST_ROWS, scheme, [
        {"type": b["type"], "args": b["args"], "value": b["value"],
         "sentence": b.get("sentence", ""), "reason": b.get("reason", "")}
        for b in built_pct], snap)
    check("百分比项 → NO_SOURCE", res_pct[0].status == STATUS_NO_SOURCE,
          res_pct[0].status)
    check("报告提示人工核对降幅",
          "降幅" in res_pct[0].note and "人工核对" in res_pct[0].note, res_pct[0].note)

    print("\n[17] ★ 占位符与线索模式互斥（占位符优先）")
    mixed_doc = ("Urinary arsenic was {{period_GM:Urine:1980-2000}} ug/g Cr in 1980-2000. "
                 "Blood arsenic reached 5.99 ug/L in North China.")
    built_mix = build_items_from_text(mixed_doc, scheme)
    ph_items = [b for b in built_mix if b["origin"] == "placeholder"]
    ctx_items = [b for b in built_mix if b["origin"] == "context"]
    check("占位符项被识别", len(ph_items) == 1, str(len(ph_items)))
    check("占位符所在句不被线索模式重复审计",
          all("1980-2000" not in (b.get("sentence") or "") for b in ctx_items),
          str([b.get("sentence", "")[:40] for b in ctx_items]))
    check("未被占位符覆盖的句仍被线索审计",
          any("North China" in (b.get("sentence") or "") for b in ctx_items),
          str([b.get("sentence", "")[:40] for b in ctx_items]))

    print("\n[18] ★ A5：Snapshot 迁移到 shared 后行为一致性")
    check("Snapshot 来自 shared.snapshot（非本文件本地类）",
          Snapshot.__module__ == "snapshot", Snapshot.__module__)
    check("detect_stale 来自 shared.snapshot",
          detect_stale.__module__ == "snapshot", detect_stale.__module__)
    # 逐行检查实际代码（排除注释与本处断言自身），确认已无本地 Snapshot 定义
    src_lines = []
    for ln in Path(__file__).read_text(encoding="utf-8").splitlines():
        t = ln.strip()
        if t.startswith("#") or t.startswith('"""'):
            continue
        if "class Snapshot" in t and ("not in" in t or "不再定义" in t):
            continue          # 断言自身，跳过
        src_lines.append(t)
    check("本文件不再定义本地 Snapshot 类（逐行核对代码）",
          not any(t.startswith("class Snapshot") for t in src_lines),
          "已无本地类" )
    # 字段兼容：共享版多 6 个字段，构造与访问不得报错
    s_shared = Snapshot(tag="As_2026-06-05", sha256="a" * 64,
                        record_count=688, study_count=384)
    check("4 个原字段可构造", s_shared.tag == "As_2026-06-05"
          and s_shared.record_count == 688 and s_shared.study_count == 384)
    check("fingerprint 语义一致（tag@前12位）",
          s_shared.fingerprint() == "As_2026-06-05@" + "a" * 12,
          s_shared.fingerprint())
    check("新增字段有默认值（不破坏原调用）",
          s_shared.source_table == "" and s_shared.generated_at == ""
          and s_shared.config_version == "1.0.0"
          and s_shared.contract_version == "1.0.0"
          and s_shared.git_commit == "")
    # detect_stale 语义等价（与迁移前一致）
    s_old = Snapshot(tag="As_2026-03-29", sha256="b" * 64)
    check("哈希不同 → detect_stale 为 True",
          detect_stale(s_old, s_shared) is True)
    check("哈希相同 → detect_stale 为 False",
          detect_stale(s_shared, s_shared) is False)
    check("None → False", detect_stale(None, s_shared) is False)
    check("空哈希 → False",
          detect_stale(Snapshot("x", ""), s_shared) is False)

    # 登记表 → Snapshot 的元数据映射（列名兼容）
    meta_csv = {"snapshot_tag": "As_2026-06-05", "sha256": "c" * 64,
                "created_at": "2026-07-19T10:00:00+08:00",
                "record_count": "688", "study_count": "384"}
    sm = snapshot_from_meta(meta_csv)
    check("登记表列名 snapshot_tag 可映射", sm.tag == "As_2026-06-05", sm.tag)
    check("created_at → generated_at",
          sm.generated_at == "2026-07-19T10:00:00+08:00", sm.generated_at)
    check("计数从字符串解析", (sm.record_count, sm.study_count) == (688, 384),
          f"{sm.record_count}/{sm.study_count}")
    meta_json = {"tag": "PFOA_2027-01-20", "sha256": "d" * 64}
    sj = snapshot_from_meta(meta_json)
    check("JSON 列名 tag 也兼容", sj.tag == "PFOA_2027-01-20", sj.tag)
    check("缺字段时用安全默认",
          snapshot_from_meta({}).tag == "unknown"
          and snapshot_from_meta({}).record_count == 0)

    print("\n[19] ★ 迁移回归：同一输入产出相同的四状态分类")
    # 迁移前用本地 Snapshot（4 字段）、迁移后用共享版（9 字段）；
    # 同一组 (rows, items) 必须产出完全相同的状态序列。
    cases = [
        ([{"type": "period_GM", "args": ["Urine", "1980-2000"], "value": 32.58},
          {"type": "period_GM", "args": ["Urine", "2001-2010"], "value": 12.45},
          {"type": "period_GM", "args": ["Blood", "2001-2010"], "value": 2.72}],
         snap, None, STATUS_OK),
        ([{"type": "period_GM", "args": ["Urine", "1980-2000"], "value": 38.10}],
         snap, None, STATUS_MISMATCH),
        ([{"type": "period_GM", "args": ["Urine", "2021-2024"], "value": 24.05}],
         snap, None, STATUS_NO_SOURCE),
        ([{"type": "period_GM", "args": ["Urine", "1980-2000"], "value": 32.58}],
         snap, snap_old, STATUS_STALE),
    ]
    for items_c, cur, ms, label in cases:
        got = run_audit(SELFTEST_ROWS, scheme, items_c, cur,
                        manuscript_snapshot=ms, edi_cfg=edi_cfg)
        check(f"{label}：状态可复现", got[0].status == label,
              f"{got[0].status}（期望 {label}）")
    # 四个状态覆盖齐全
    all_status = set()
    for items_c, cur, ms, _ in cases:
        all_status |= {x.status for x in run_audit(
            SELFTEST_ROWS, scheme, items_c, cur, manuscript_snapshot=ms,
            edi_cfg=edi_cfg)}
    check("四状态均有覆盖",
          all_status == {STATUS_OK, STATUS_MISMATCH, STATUS_NO_SOURCE, STATUS_STALE},
          str(sorted(all_status)))

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

def build_parser():
    p = argparse.ArgumentParser(
        prog="audit_numbers.py",
        description="数值审计：稿件 vs 快照重算（HBM-Meta-Agent / S6）",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="退出码：0 全部 OK / 1 存在差异 / 2 前置条件不满足")
    p.add_argument("--input", type=Path, help="稿件文件（.md / .txt）")
    p.add_argument("--master-table", type=Path,
                   help="★ 主表快照（.csv / .xlsx）——提供后即可真正重算；缺失则只提取待审项清单")
    p.add_argument("--snapshot", type=Path, help="快照元数据（snapshots.csv 或 json）")
    p.add_argument("--config", type=Path, help="project.yaml")
    p.add_argument("--out", type=Path, help="审计报告输出（.md）")
    p.add_argument("--self-test", action="store_true")
    p.add_argument("--version", action="version", version="hbm-meta S6 audit_numbers 1.0.0")
    return p


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    if args.self_test:
        # 自检：显式传入夹具（正式路径不调用本函数）
        return run_self_test(wgm.FIXTURE_PERIOD_SCHEME, edi.FIXTURE_EDI_PARAMS)

    if not args.input or not args.input.exists():
        print("错误：需要存在的 --input 稿件文件", file=sys.stderr)
        return 2

    # ★ A3：口径一律来自 project.yaml（禁止任何默认值）
    try:
        cfg = load_config(args.config, require=["period_scheme"])
    except ConfigError as exc:
        print(f"错误：{exc}", file=sys.stderr)
        return 2
    scheme = cfg.period_scheme
    edi_cfg = None
    if "edi" in cfg.raw:
        try:
            edi_cfg = load_edi_config_from(cfg)
        except ConfigError as exc:
            print(f"警告：EDI 口径不可用（{exc}）；EDI 项将标 NO_SOURCE", file=sys.stderr)
    print(f"配置：{cfg.source_path}  ({cfg.summary()})")

    text = args.input.read_text(encoding="utf-8")
    meta = read_snapshot_meta(args.snapshot) if args.snapshot else {}
    snap = snapshot_from_meta(meta)      # ★ 共享版 Snapshot（单一来源）

    # 构建待审项（占位符 + 线索两种模式，线索项含推断出的分层）
    items_raw = build_items_from_text(text, scheme)
    print(f"提取到 {len(items_raw)} 个待审数值项")

    # ★ 有主表 → 真正重算；无主表 → 仅输出待审项清单（骨架降级模式）
    if args.master_table:
        try:
            rows = read_master_table(args.master_table)
        except (FileNotFoundError, ValueError) as exc:
            print(f"错误：{exc}", file=sys.stderr)
            return 2
        snap.record_count = snap.record_count or len(rows)
        snap.study_count = snap.study_count or len(
            {str(r.get("study_no") or "") for r in rows})
        print(f"主表：{args.master_table}（{len(rows)} 条记录）")

        audit_items = [{"type": b["type"], "args": b["args"], "value": b["value"],
                        "unit": b.get("unit", ""), "sentence": b.get("sentence", ""),
                        "reason": b.get("reason", "")}
                       for b in items_raw]
        results = run_audit(rows, scheme, audit_items, snap,
                            manuscript_snapshot=None, edi_cfg=edi_cfg)
        md = render_report(results, snap, None)
        counts = {s: sum(1 for x in results if x.status == s)
                  for s in (STATUS_OK, STATUS_MISMATCH, STATUS_STALE, STATUS_NO_SOURCE)}
        n_bad = counts[STATUS_MISMATCH] + counts[STATUS_STALE] + counts[STATUS_NO_SOURCE]
        if args.out:
            args.out.parent.mkdir(parents=True, exist_ok=True)
            args.out.write_text(md, encoding="utf-8")
            print(f"审计报告：{args.out}")
        else:
            print(md)
        print(f"OK={counts[STATUS_OK]}  MISMATCH={counts[STATUS_MISMATCH]}  "
              f"STALE={counts[STATUS_STALE]}  NO_SOURCE={counts[STATUS_NO_SOURCE]}")
        return 1 if n_bad else 0

    # 降级模式：无主表，只能列出待审项
    print("⚠️ 未提供 --master-table：仅提取待审项清单，**无法重算**")
    print("   请加 `--master-table 主表.xlsx` 以启用真正的数值审计。")
    lines = ["# 数值审计（待审项清单）", "",
             f"- 稿件：`{args.input}`",
             f"- 提取到 {len(items_raw)} 个待审数值项",
             "", "| # | 类型 | 分层 | 稿件值 | 单位 | 所在句子 |",
             "|---|---|---|---|---|---|"]
    for i, b in enumerate(items_raw, start=1):
        layer = " | ".join(str(a) for a in b.get("args", [])) or "—"
        sv = "" if b.get("value") is None else f"{b['value']:g}"
        lines.append(f"| {i} | `{b['type']}` | {layer} | {sv} | {b.get('unit', '')} | "
                     f"{(b.get('sentence') or '')[:60]} |")
    md = "\n".join(lines)
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(md, encoding="utf-8")
        print(f"已写出：{args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
