"""工具调用统一契约（v4.220 重构核心）。

- ToolResult：所有工具最终经由 exec_tool 归一为该结构。字段：
    ok          是否成功（机器可读，替代对中文结果串的脆弱 string-match）
    msg         人类可读结果（保留原中文串，供展示/日志；脱敏在此处生效）
    error       机器可读错误（ok=False 时填）
    data        结构化载荷（如 killed 列表、启动 PID、窗口句柄等）
    evidence    反编造/核验证据（如回收站 before/after 计数）
    deliverables [(rel_path, kind, name), ...]
    schedule    (message, delay_seconds) 或 None
    impact_scope 确认弹窗展示的影响范围文本
  - __iter__ 返回 (msg, deliverables, schedule)，兼容既有 `r, d, s = exec_tool(...)` 解包
  - __str__ 返回 msg，兼容既有把结果当字符串用的地方
- _validate_args：必填/类型/范围/枚举校验，副作用前先拦
- _mask_sensitive / _mask_recursive：路径+用户名+机器名+IP 脱敏
- impact scope 注册表：高危工具登记预检，确认弹窗展示波及面
"""
from __future__ import annotations

import getpass
import os
import re
import socket
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

__all__ = [
    "ToolResult",
    "ToolResultError",
    # v4.223：显式结局契约（根治 string-match）+ 参数校验补全
    "register_outcome",
    "resolve_ok",
    "normalize_timeout",
    "register_tool_schema",
    "validate_for_tool",
    "registered_tool_schemas",
    "TOOL_TIMEOUT_DEFAULT",
    "TOOL_TIMEOUT_CAP",
    "ARG_MAX_LEN_CAP",
    "_validate_args",
    "_mask_sensitive",
    "_mask_recursive",
    "register_impact_scope",
    "compute_impact_scope",
    # v4.224：执行后验证（只降级不升级）
    "register_verifier",
    "registered_verifiers",
    "verify_after",
    "apply_post_verification",
]


class ToolResultError(Exception):
    """校验/归一阶段的可预期失败，调用方据此产出 ToolResult(ok=False)。"""


@dataclass
class ToolResult:
    ok: bool
    msg: str
    error: Optional[str] = None
    data: Dict[str, Any] = field(default_factory=dict)
    evidence: Dict[str, Any] = field(default_factory=dict)
    deliverables: List[Any] = field(default_factory=list)
    schedule: Any = None
    impact_scope: Optional[str] = None
    # v4.223：结构化返回补全——机器可读错误码 / 是否可重试 / 结论是否经过真实验证
    error_code: Optional[str] = None
    retryable: bool = False
    verified: bool = False

    def __iter__(self):
        # 向后兼容：result_str, deliverables, schedule = exec_tool(...)
        return iter((self.msg, self.deliverables, self.schedule))

    def __str__(self):
        return self.msg

    def to_tuple(self):
        return (self.msg, self.deliverables, self.schedule)

    @classmethod
    def from_legacy(cls, val, ok: Optional[bool] = None,
                    name: Optional[str] = None, args=None):
        """归一为 ToolResult。

        v4.223 优先顺序（根除「按中文前缀猜成败」）：
          ① 本身就是 ToolResult → 直接采用（ok 由工具显式给出，权威）；
          ② 调用方显式给 ok → 采用；
          ③ 该工具登记了显式结局契约（register_outcome）→ 契约判定（看真实信号，不看文案）；
          ④ 以上都没有 → 退回 _infer_ok 兜底，并标 verified=False +
             error_code="INFERRED"，把「猜出来的结论」显式暴露给下游，不再冒充已验证。
        """
        if isinstance(val, ToolResult):
            return val
        if isinstance(val, tuple):
            msg = val[0] if len(val) > 0 else ""
            dl = val[1] if len(val) > 1 else []
            sc = val[2] if len(val) > 2 else None
            _ok = ok
            _verified = True
            _ecode = None
            if _ok is None and name:
                _ok = resolve_ok(name, msg, args)
                if _ok is not None:
                    _verified = True
            if _ok is None:
                _ok = _infer_ok(msg)
                _verified = False
                _ecode = "INFERRED" if _ok else "UNVERIFIED_OUTCOME"
            return cls(
                ok=_ok,
                msg=msg,
                deliverables=list(dl) if dl else [],
                schedule=sc,
                error_code=_ecode,
                verified=_verified,
            )
        return cls(ok=False, msg=str(val), verified=False,
                   error_code="BAD_RESULT_TYPE")

    @classmethod
    def fail(cls, msg: str, error: Optional[str] = None,
             error_code: Optional[str] = None, retryable: bool = False, **kw):
        return cls(ok=False, msg=msg, error=error or msg,
                   error_code=error_code or "TOOL_FAILED",
                   retryable=retryable, verified=True, **kw)

    @classmethod
    def ok_result(cls, msg: str, **kw):
        return cls(ok=True, msg=msg, verified=kw.pop("verified", True), **kw)


_SUCCESS_PREFIXES = (
    "已终止", "已启动", "已清空", "已关闭", "已取消", "已修复", "成功",
    "完成", "✅", "已获得", "已切换", "已安装", "已创建", "已保存",
    "已复制", "已移动", "已重命名", "已置顶", "已最小化", "已最大化",
    "已还原", "已读取", "已写入", "已找到", "已聚焦", "已选中", "已截图",
)
_FAILURE_PREFIXES = (
    "失败", "未找到", "错误", "异常", "拒绝", "不存在", "缺少", "不支持",
    "无法", "⏹", "缺", "不正确", "非法", "中止",
)


def _infer_ok(msg: str) -> bool:
    """【legacy 兜底，v4.223 起不用于有契约的工具】

    按中文前缀猜成败。这是 v4.218 审查报告点名的反模式：工具改一句文案就会
    翻转结论（"已终止"判成功、"失败"判失败）。保留仅为兼容尚未登记契约的
    老工具；凡调用它的路径都会被打上 verified=False + error_code="INFERRED"。
    新工具请走 register_outcome 提供真实信号判定。
    """
    if not isinstance(msg, str):
        return False
    s = msg.strip()
    if not s:
        return False
    if s.startswith(_FAILURE_PREFIXES):
        return False
    if s.startswith(_SUCCESS_PREFIXES):
        return True
    return "失败" not in s and "错误" not in s


# ============================================================
# v4.223：显式结局契约（根治 string-match 成败判定）
# ============================================================

_OUTCOME_REGISTRY: Dict[str, Any] = {}


def register_outcome(name: str, fn):
    """登记工具的显式结局判定契约。

    fn(msg, args) -> True / False / None
      · True/False：契约表态，from_legacy 直接采用（不看文案，改文案不会翻结论）
      · None：契约不表态（如缺参数、无法取真信号），交由下一级兜底
    契约必须基于**可观测的真实信号**判定（文件是否落地、进程是否还活着…），
    不得再对中文文案做前缀匹配，否则等于把反模式换个地方复活。
    """
    _OUTCOME_REGISTRY[name] = fn


def resolve_ok(name, msg, args=None) -> Optional[bool]:
    """按登记契约判定成败；无契约/契约不表态/契约异常 → 返回 None（不表态）。"""
    fn = _OUTCOME_REGISTRY.get(name or "")
    if fn is None:
        return None
    try:
        r = fn(msg, args or {})
    except Exception:
        return None
    return r if isinstance(r, bool) else None


def _outcome_write_file(msg, args) -> Optional[bool]:
    """write_file 契约：不看文案，看文件是否真的落地且非空。"""
    p = (args or {}).get("path")
    if not p or not isinstance(p, str):
        return None
    try:
        ap = p
        if not os.path.isabs(ap):
            try:
                import tools as _tools
                ap = os.path.join(_tools.WORKSPACE_DIR, p)
            except Exception:
                ap = os.path.abspath(p)
        return os.path.isfile(ap) and os.path.getsize(ap) > 0
    except Exception:
        return None


register_outcome("write_file", _outcome_write_file)


# ============================================================
# v4.224：执行后验证（post-exec verification）
# ============================================================
# 与「结局契约」的分工：
#   · 结局契约（v4.223）：**判定**这次调用算成功还是失败（基于真实信号）。
#   · 执行后验证（本轮）：调用**已经跑完**，再回头查一次副作用是否真的生效
#     （进程真没了？回收站真空了？），把证据写进 ToolResult。
# 语义硬约束：**验证只降级、不升级** —— 验证不通过必须把 ok 打成 False；
# 验证通过也**不许**把原本失败的调用翻成成功（否则工具不存在/报错也能被"验证"成成功）。
# 无验证器、或验证器查询失败（取不到真信号）→ 返回 None，fail-open 不动结论。

_VERIFY_REGISTRY: Dict[str, Any] = {}


def register_verifier(name: str, fn):
    """登记工具的执行后验证器。

    fn(args, result) -> None / bool / (bool, evidence_str)
      · None：不表态（查不到真信号 / 该调用形态不适用）→ fail-open，结论不动
      · bool 或 (bool, evidence)：表态；False 会把 ok 降级为 False
    """
    if not name or not callable(fn):
        return False
    _VERIFY_REGISTRY[str(name)] = fn
    return True


def registered_verifiers():
    return sorted(_VERIFY_REGISTRY)


def verify_after(name, args=None, result=None):
    """跑执行后验证；无验证器/不表态/异常 → None。否则 (ok: bool, evidence: str)。"""
    fn = _VERIFY_REGISTRY.get(name or "")
    if fn is None:
        return None
    try:
        r = fn(args or {}, result)
    except Exception:
        return None
    if r is None:
        return None
    if isinstance(r, tuple):
        return (bool(r[0]), str(r[1]) if len(r) > 1 else "")
    return (bool(r), "")


def apply_post_verification(tr, name, args=None):
    """把执行后验证结论落到 ToolResult 上。返回是否被降级。

    只降级不升级：验证不通过 → ok=False + error_code + 证据尾巴；
    验证通过 → 只补 verified/evidence，**不动 ok**（原本失败仍失败）。
    """
    if tr is None:
        return False
    v = verify_after(name, args, tr)
    if v is None:
        return False
    vok, ev = v
    try:
        tr.verified = True
    except Exception:
        pass
    if vok:
        # 验证通过：只留证据，绝不把失败翻成成功
        try:
            if ev and not getattr(tr, "evidence", None):
                tr.evidence = ev
        except Exception:
            pass
        return False
    # 验证不通过：证据必留；只有原本「判成功」的才打成失败，并换成
    # POST_VERIFY_FAILED（此刻它就是失败的权威理由）；原本已失败的保留原始错误码，
    # 不让验证结论盖掉工具真正的报错。
    was_ok = bool(getattr(tr, "ok", False))
    try:
        if was_ok:
            tr.ok = False
            tr.error_code = "POST_VERIFY_FAILED"
        if ev:
            tr.msg = (getattr(tr, "msg", "") or "") + "\n[执行后验证未通过] " + ev
    except Exception:
        pass
    return was_ok


# ============================================================
# 参数校验（v4.223 补全：路径标准化 / 长度上限 / 未知字段策略 / 超时限制）
# ============================================================

TOOL_TIMEOUT_DEFAULT = 30
TOOL_TIMEOUT_CAP = 600
ARG_MAX_LEN_CAP = 4_000_000


def normalize_timeout(v, default: int = TOOL_TIMEOUT_DEFAULT,
                      cap: int = TOOL_TIMEOUT_CAP) -> int:
    """把任意入参收敛为合法超时秒数：非法/非正 → default，超出 → cap。"""
    try:
        f = float(v)
    except (TypeError, ValueError):
        return default
    if f <= 0:
        return default
    return int(min(f, cap))


def _normalize_path(p: str) -> str:
    """路径标准化：展开 ~ / 环境变量、折叠 . 与 ..。

    相对路径**保持相对**（只 normpath，不 abspath）——各工具解析相对路径的基准
    目录不同（工作区 / 应用目录 / cwd），此处贸然 abspath 会改变落点。
    """
    try:
        s = os.path.expandvars(os.path.expanduser(str(p)))
    except Exception:
        return str(p)
    if os.path.isabs(s):
        return os.path.abspath(s)
    return os.path.normpath(s)


def _within_base(p: str, base: str) -> bool:
    """p 是否位于 base 目录树内（用于路径越界围栏）。"""
    try:
        ap = os.path.abspath(p)
        ab = os.path.abspath(base)
        return os.path.commonpath([ap, ab]) == ab
    except Exception:
        return False


def _validate_args(args, schema, unknown: str = "ignore"):
    """按 schema 校验工具参数。

    schema: list of dict，每项：
        key       参数名（必填字段）
        type      'int' | 'float' | 'bool' | 'str' | 'path'  (v4.223 增 path)
        required  是否必填
        enum      允许值集合
        min/max   数值范围（type 为 int/float 时）
        allow_empty 字符串是否允许为空（默认不允许）
        max_len   长度上限（v4.223；str 取字符数，其余取 str() 长度）
        pattern   正则约束（v4.223；不匹配即拒）
        base_dir  路径围栏（v4.223；仅 type='path' 生效，越界即拒）
    unknown  (v4.223) 未知字段策略：
        'ignore' 原样放行（默认，保持既有行为，零破坏）
        'strip'  丢弃未在 schema 中声明的字段（防下游误用拼错的参数）
        'reject' 出现未声明字段即拒绝（严格模式，防「参数名写错被静默忽略」）
    返回 (cleaned_args, None) 或 (None, err_msg)。
    """
    if not isinstance(args, dict):
        return None, "参数必须为对象（dict）"
    out = dict(args)
    errs = []
    for spec in schema:
        k = spec["key"]
        raw = out.get(k, None)
        present = raw is not None and raw != ""
        if spec.get("required") and not present:
            errs.append(f"缺少必填参数「{k}」")
            continue
        if not present:
            continue
        v = raw
        t = spec.get("type")
        if t in ("int", "float"):
            try:
                nv = float(v) if t == "float" else int(v)
            except (TypeError, ValueError):
                errs.append(f"参数「{k}」必须是{t}类型")
                continue
            out[k] = nv
            if "min" in spec and nv < spec["min"]:
                errs.append(f"参数「{k}」不能小于 {spec['min']}")
            if "max" in spec and nv > spec["max"]:
                errs.append(f"参数「{k}」不能大于 {spec['max']}")
        elif t == "bool":
            if isinstance(v, bool):
                pass
            elif str(v).lower() in ("true", "1", "yes", "是"):
                out[k] = True
            elif str(v).lower() in ("false", "0", "no", "否"):
                out[k] = False
            else:
                errs.append(f"参数「{k}」必须是布尔值")
        elif t == "str":
            out[k] = str(v)
            if not spec.get("allow_empty") and out[k] == "":
                errs.append(f"参数「{k}」不能为空")
        elif t == "path":
            # v4.223：路径标准化 + 可选越界围栏（相对路径保持相对，不篡改落点基准）
            out[k] = _normalize_path(v)
            if not spec.get("allow_empty") and out[k] == "":
                errs.append(f"参数「{k}」不能为空")
            _base = spec.get("base_dir")
            if _base and not _within_base(out[k], _base):
                errs.append(f"参数「{k}」路径越界（必须位于 {_base} 内）")
        # v4.223：长度上限（防超大 payload 打爆上下文 / 子进程）
        if "max_len" in spec:
            _cur = out.get(k)
            _L = len(_cur) if isinstance(_cur, str) else len(str(_cur))
            if _L > spec["max_len"]:
                errs.append(f"参数「{k}」长度 {_L} 超过上限 {spec['max_len']}")
        # v4.223：正则约束
        if "pattern" in spec:
            _cur = out.get(k)
            try:
                if not re.search(spec["pattern"], str(_cur)):
                    errs.append(f"参数「{k}」不符合格式要求")
            except re.error:
                pass
        if "enum" in spec and out.get(k) not in spec["enum"]:
            errs.append(f"参数「{k}」必须是 {spec['enum']} 之一，收到：{out.get(k)}")
    # v4.223：未知字段策略
    if unknown in ("reject", "strip"):
        _known = {s.get("key") for s in schema if isinstance(s, dict)}
        _extra = [k for k in list(out.keys()) if k not in _known]
        if _extra:
            if unknown == "reject":
                errs.append("未知参数：" + "、".join(sorted(map(str, _extra))))
            else:
                for k in _extra:
                    out.pop(k, None)
    if errs:
        return None, "；".join(errs)
    return out, None


_PATH_RE = re.compile(
    r'([A-Za-z]:\\[^\s"\'，。()（）<>]+)'
    r'|(/home/[^\s"\'，。()（）<>]+)'
    r'|(/Users/[^\s"\'，。()（）<>]+)'
)


def _mask_sensitive(text: str) -> str:
    """对单条文本做脱敏：本地绝对路径、当前用户名、机器名、IPv4。"""
    if not isinstance(text, str) or not text:
        return text
    text = _PATH_RE.sub("<路径已脱敏>", text)
    try:
        uname = getpass.getuser()
    except Exception:
        uname = ""
    if uname:
        text = text.replace(uname, "<用户>")
    try:
        host = socket.gethostname()
    except Exception:
        host = ""
    if host:
        text = re.sub(re.escape(host), "<主机>", text)
    text = re.sub(r"\b(?:\d{1,3}\.){3}\d{1,3}\b", "<IP>", text)
    return text


def _mask_recursive(obj):
    if isinstance(obj, str):
        return _mask_sensitive(obj)
    if isinstance(obj, dict):
        return {k: _mask_recursive(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_mask_recursive(v) for v in obj]
    return obj


# ============================================================
# 影响范围预检注册表
# ============================================================

_IMPACT_REGISTRY: Dict[str, Any] = {}


def register_impact_scope(name: str, fn):
    """登记某工具的「将影响范围」预检函数 fn(args)->str|None。"""
    _IMPACT_REGISTRY[name] = fn


def compute_impact_scope(name: str, args) -> Optional[str]:
    """取工具的确认前影响范围文本；未登记或预检异常返回 None。"""
    fn = _IMPACT_REGISTRY.get(name)
    if fn is None:
        return None
    try:
        return fn(args or {})
    except Exception:
        return None


# ============================================================
# v4.223：工具级 schema 注册表（一处接入，覆盖全部登记工具）
# ============================================================

_TOOL_SCHEMAS: Dict[str, Any] = {}


def register_tool_schema(name: str, schema, unknown: str = "ignore"):
    """登记某工具的参数 schema（含未知字段策略）。"""
    _TOOL_SCHEMAS[name] = (list(schema), unknown)


def validate_for_tool(name: str, args):
    """按登记 schema 校验参数；**未登记 schema 的工具原样放行**（零破坏）。

    返回 (cleaned_args, None) 或 (None, err_msg)。
    """
    ent = _TOOL_SCHEMAS.get(name or "")
    if ent is None:
        return (dict(args) if isinstance(args, dict) else args), None
    return _validate_args(args, ent[0], unknown=ent[1])


def registered_tool_schemas():
    """返回已登记 schema 的工具名集合（供判据/审计用）。"""
    return dict(_TOOL_SCHEMAS)


# 登记：先覆盖「已确认参数名且已有同等校验」的高危工具，避免改变既有行为。
# required 只在原实现已强制必填时才标 True（process_kill/app_kill/app_close）。
register_tool_schema("process_kill", [
    {"key": "name", "type": "str", "required": True, "max_len": 512},
    {"key": "force", "type": "bool"},
])
register_tool_schema("app_kill", [
    {"key": "target", "type": "str", "required": True, "max_len": 512},
])
register_tool_schema("app_close", [
    {"key": "title", "type": "str", "required": True, "max_len": 512},
])
register_tool_schema("clean_recycle_bin", [])
register_tool_schema("read_file", [
    {"key": "path", "type": "path", "max_len": 2000},
    {"key": "offset", "type": "int", "min": 0},
    {"key": "limit", "type": "int", "min": 0},
])
register_tool_schema("write_file", [
    {"key": "path", "type": "path", "max_len": 2000},
    {"key": "content", "type": "str", "max_len": ARG_MAX_LEN_CAP},
])
register_tool_schema("run_command", [
    {"key": "command", "type": "str", "max_len": ARG_MAX_LEN_CAP},
    {"key": "timeout", "type": "int", "min": 1, "max": TOOL_TIMEOUT_CAP},
    {"key": "cwd", "type": "path", "max_len": 2000},
])
