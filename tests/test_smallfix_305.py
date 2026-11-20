# -*- coding: utf-8 -*-
"""v4.305.0：第二批三项小改（F-04 / F-05 / F-08）的回归判据。

判据分两层：
  A 纯函数层——smallfix_guard_305 的决策逻辑，确定性、不碰真实用户数据；
  B 接线层——config / tools / ui 真实调用护栏（源码级 + 真实函数行为）。

旧代码（v4.304.0）没有 smallfix_guard_305 模块、没有接线 → 探测式调用返回
哨兵、源码判据读不到新标记，对应判据翻红；新代码全绿。
"""
import io
import os
import sys
import tempfile
import json
from pathlib import Path

_SBX = tempfile.mkdtemp(prefix="xc_305_smallfix_")
os.environ["XC_USER_DATA_DIR"] = os.path.join(_SBX, "user")
os.environ["XC_WORKSPACE_DIR"] = os.path.join(_SBX, "ws")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

PASS = FAIL = 0


class _Sentinel:
    """旧代码缺函数时的替代值：任何与真实期望值的比较都为假 → 干净翻红。"""
    def __getitem__(self, k):
        return self

    def __eq__(self, other):
        return False

    def __ne__(self, other):
        return True

    def __bool__(self):
        return False

    def __iter__(self):
        return iter((self, self))

    def __len__(self):
        return 2

    def __repr__(self):
        return "<OLD-CODE-SENTINEL>"


_SENTINEL = _Sentinel()


def check(name, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print("  [PASS] " + name)
    else:
        FAIL += 1
        print("  [FAIL] " + name + (("  <- " + str(detail)) if detail else ""))


def probe(mod, fn, *args, **kwargs):
    """旧代码缺模块/缺函数时返回唯一哨兵，避免「两个 None 相等」假绿。"""
    if mod is None:
        return _SENTINEL
    f = getattr(mod, fn, None)
    if f is None:
        return _SENTINEL
    try:
        return f(*args, **kwargs)
    except Exception:
        return _SENTINEL


try:
    import smallfix_guard_305 as G
except Exception:
    G = None


def read_src(name):
    p = ROOT / name
    try:
        return p.read_text(encoding="utf-8")
    except Exception:
        return ""


# ==================== A 纯函数层 ====================
print("=== A 纯函数层 smallfix_guard_305 ===")


def _norm(s):
    """判据内置归一化口径（与 skill_loader 一致），避免判据依赖被测模块的内部实现。"""
    import re
    if not s:
        return ""
    s = str(s).strip()
    s = re.sub(r"[\U0001F000-\U0001FAFF\u2600-\u27BF\uFE0F\u200D]", "", s).strip()
    s = s.replace(" ", "-")
    return s.lower()


check("A 模块可导入", G is not None)
# 注意：A 层全部走 probe —— 旧代码 G=None 时返回哨兵，判据干净翻红而非静默跳过。
# F-04 skill_enabled_decision：归一化注入，顺序全禁用→名空→白名单→比对
check("A1 全禁用优先", probe(G, "skill_enabled_decision", "any", [], True, _norm) is False)
check("A2 空名拒绝", probe(G, "skill_enabled_decision", "   ", ["my-skill"], False, _norm) is False)
check("A3 白名单空=全放行", probe(G, "skill_enabled_decision", "anything", [], False, _norm) is True)
check("A4 归一化命中(大小写)", probe(G, "skill_enabled_decision", "MY SKILL", ["my-skill"], False, _norm) is True)
check("A5 归一化命中(空格)", probe(G, "skill_enabled_decision", "  my skill ", ["my-skill"], False, _norm) is True)
check("A6 归一化命中(emoji)", probe(G, "skill_enabled_decision", "emoji🔥 skill", ["emoji-skill"], False, _norm) is True)
check("A7 白名单侧也归一化", probe(G, "skill_enabled_decision", "emoji-skill", ["emoji🔥 skill"], False, _norm) is True)
check("A8 不在白名单拒绝", probe(G, "skill_enabled_decision", "other", ["my-skill"], False, _norm) is False)
# F-05 artifact_snapshot_root：恒等于 run_cwd，与 script_dir 无关
check("A9 快照根=cwd", probe(G, "artifact_snapshot_root", "C:/data", "C:/data/workspace") == "C:/data")
check("A10 快照根与script_dir异根时仍=cwd",
      probe(G, "artifact_snapshot_root", "C:/cwd", "D:/elsewhere") == "C:/cwd")
check("A11 快照根忽略None script_dir", probe(G, "artifact_snapshot_root", "C:/cwd", None) == "C:/cwd")
# F-08 count_event_filter_defs
one = "class A:\n    def eventFilter(self, o, e):\n        return 1\n"
two = "class A:\n    def eventFilter(self, o, e):\n        return 1\n    def eventFilter(self, o, e):\n        return 2\n"
none = "class A:\n    def other(self):\n        pass\n"
check("A12 单个eventFilter计1", probe(G, "count_event_filter_defs", one, "A") == 1)
check("A13 两个eventFilter计2", probe(G, "count_event_filter_defs", two, "A") == 2)
check("A14 无eventFilter计0", probe(G, "count_event_filter_defs", none, "A") == 0)
check("A15 类不存在返回-1", probe(G, "count_event_filter_defs", one, "B") == -1)


# ==================== B 接线层 ====================
print("=== B 接线层 ===")
cfg_src = read_src("config.py")
tools_src = read_src("tools.py")
ui_src = read_src("ui.py")

# F-04 config 接线
check("B1 config 不用吞错的 strip 回退", "n = (name or \"\").strip()" not in cfg_src)
check("B2 config 调用 skill_enabled_decision", "skill_enabled_decision(" in cfg_src)
check("B3 config 导入 smallfix_guard_305", "from smallfix_guard_305 import skill_enabled_decision" in cfg_src)
check("B4 config 仍导入真实归一化函数", "from skill_loader import normalize_skill_name" in cfg_src)

# B-F04 行为：真实 config.is_skill_enabled 归一化
import config
_cfg = tempfile.mkdtemp(prefix="xc_305_cfg_")
_saved_dir, _saved_path = config.USER_DATA_DIR, config.CONFIG_PATH
config.USER_DATA_DIR = _cfg
config.CONFIG_PATH = os.path.join(_cfg, "config.json")
Path(config.CONFIG_PATH).write_text(json.dumps({
    "enabled_skills": ["emoji🔥 skill", "My Skill"], "skills_disabled_all": False,
}, ensure_ascii=False), encoding="utf-8")
try:
    check("B5 真实调用:输入归一化命中", config.is_skill_enabled("my-skill") is True)
    check("B6 真实调用:emoji+大小写命中", config.is_skill_enabled("  EMOJI🔥 SKILL ") is True)
    check("B7 真实调用:不在白名单拒绝", config.is_skill_enabled("nope") is False)
finally:
    config.USER_DATA_DIR, config.CONFIG_PATH = _saved_dir, _saved_path

# F-05 tools 接线
check("B8 tools 导入 artifact_snapshot_root", "from smallfix_guard_305 import artifact_snapshot_root" in tools_src)
check("B9 tools 定义 snap_root", "snap_root = artifact_snapshot_root(run_cwd, ws)" in tools_src)
check("B10 before 快照用 snap_root", "before = snapshot_workspace(snap_root)" in tools_src)
check("B11 after 快照用 snap_root", "after = snapshot_workspace(snap_root)" in tools_src)
check("B12 deliverable 拼接用 snap_root", "full = os.path.join(snap_root, nf)" in tools_src)
check("B13 主路径不再用 ws 快照", "snapshot_workspace(ws)" not in tools_src)
# B13b：snap_root 依赖 run_cwd，定义必须在其后（否则 UnboundLocalError，实测撞过）。
_i_run = tools_src.find("    run_cwd = WORKSPACE_DIR")
_i_snap = tools_src.find("snap_root = artifact_snapshot_root(run_cwd, ws)")
check("B13b snap_root 定义在 run_cwd 之后", _i_run >= 0 and _i_snap > _i_run,
      ("run_cwd@%d" % _i_run, "snap@%d" % _i_snap))
# B13c：真实调用 tool_run_python 不因接线顺序抛异常（end-to-end 护栏）
try:
    import tools as _tools
    _r, _d = _tools.tool_run_python(None, "print('ok305')", cfg={"python_exe": sys.executable})
    check("B13c tool_run_python 真实执行不报接线异常",
          "UnboundLocalError" not in str(_r) and "run_cwd" not in str(_r), str(_r)[:120])
except Exception as _e:
    check("B13c tool_run_python 真实执行不报接线异常", False, repr(_e))

# F-08 ui 接线（AST）：同类只一个 eventFilter
check("B14 ChatWindow 仅一个 eventFilter",
      probe(G, "count_event_filter_defs", ui_src, "ChatWindow") == 1,
      probe(G, "count_event_filter_defs", ui_src, "ChatWindow"))
check("B15 合并定义含焦点联动", "QEvent.FocusIn" in ui_src and "QEvent.FocusOut" in ui_src)
check("B16 合并定义含搜索键处理", "Qt.Key_Return" in ui_src and "Qt.Key_Escape" in ui_src)
check("B17 注释声明唯一定义", "唯一的事件过滤器" in ui_src)

# B-F08 行为：真实 Qt 焦点联动（合并前是死代码）
try:
    from PySide6.QtWidgets import QApplication
    from PySide6.QtCore import QEvent
    app = QApplication.instance() or QApplication([])
    import ui as _ui
    _w = _ui.ChatWindow(config.load_config())
    _ib, _frame = getattr(_w, "input_box", None), getattr(_w, "_input_frame", None)
    if _ib is not None and _frame is not None:
        _before = _frame.styleSheet()
        _w.eventFilter(_ib, QEvent(QEvent.FocusIn))
        _after = _frame.styleSheet()
        check("B18 真实Qt:FocusIn改变边框", _after != _before)
        _w.eventFilter(_ib, QEvent(QEvent.FocusOut))
        check("B19 真实Qt:FocusOut还原边框", _frame.styleSheet() == _before)
    else:
        check("B18 真实Qt:FocusIn改变边框", False, "input_box/frame 缺失")
        check("B19 真实Qt:FocusOut还原边框", False, "input_box/frame 缺失")
    _w.close()
    app.processEvents()
except Exception as e:
    check("B18 真实Qt:FocusIn改变边框", False, repr(e))
    check("B19 真实Qt:FocusOut还原边框", False, repr(e))

print("=== 汇总 === PASS=%d FAIL=%d ===" % (PASS, FAIL))
sys.exit(1 if FAIL else 0)
