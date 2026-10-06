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
import re
import socket
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

__all__ = [
    "ToolResult",
    "ToolResultError",
    "_validate_args",
    "_mask_sensitive",
    "_mask_recursive",
    "register_impact_scope",
    "compute_impact_scope",
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

    def __iter__(self):
        # 向后兼容：result_str, deliverables, schedule = exec_tool(...)
        return iter((self.msg, self.deliverables, self.schedule))

    def __str__(self):
        return self.msg

    def to_tuple(self):
        return (self.msg, self.deliverables, self.schedule)

    @classmethod
    def from_legacy(cls, val, ok: Optional[bool] = None):
        if isinstance(val, ToolResult):
            return val
        if isinstance(val, tuple):
            msg = val[0] if len(val) > 0 else ""
            dl = val[1] if len(val) > 1 else []
            sc = val[2] if len(val) > 2 else None
            return cls(
                ok=_infer_ok(msg) if ok is None else ok,
                msg=msg,
                deliverables=list(dl) if dl else [],
                schedule=sc,
            )
        return cls(ok=False, msg=str(val))

    @classmethod
    def fail(cls, msg: str, error: Optional[str] = None, **kw):
        return cls(ok=False, msg=msg, error=error or msg, **kw)

    @classmethod
    def ok_result(cls, msg: str, **kw):
        return cls(ok=True, msg=msg, **kw)


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


def _validate_args(args, schema):
    """按 schema 校验工具参数。

    schema: list of dict，每项：
        key       参数名（必填字段）
        type      'int' | 'float' | 'bool' | 'str'
        required  是否必填
        enum      允许值集合
        min/max   数值范围（type 为 int/float 时）
        allow_empty 字符串是否允许为空（默认不允许）
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
        if "enum" in spec and out.get(k) not in spec["enum"]:
            errs.append(f"参数「{k}」必须是 {spec['enum']} 之一，收到：{out.get(k)}")
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
