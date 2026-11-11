# -*- coding: utf-8 -*-
"""v4.290.0：对话列表与新开对话的三处体验修正。

背景（2026-11-11）：
  · 顶部会话下拉框把 `auto_` 自动化会话也列进去 —— 用户视角就是「自动化和
    普通对话串在一起」；且按 dict 插入顺序排列，最新会话反而排最后。
  · 连点「新对话」会堆一串同名空会话，列表里全是「新会话」，找历史更难。
  · `_close_session` 清理上下文摘要时硬编码 `~/Documents/小臭玩AI`，
    而 context_manager 早已跟随 config.USER_DATA_DIR —— 改道环境下删不到文件。

纯标准库 + offscreen Qt。
"""
import os
import sys
import tempfile
from pathlib import Path

_SBX = tempfile.mkdtemp(prefix="xc_290_ui_")
os.environ["XC_USER_DATA_DIR"] = os.path.join(_SBX, "user")
os.environ["XC_LOG_DIR"] = os.path.join(_SBX, "logs")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import automation
import ui as UI

PASS = FAIL = 0


def check(name, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print("  [PASS] " + name)
    else:
        FAIL += 1
        print("  [FAIL] " + name + (("  <- " + str(detail)) if detail else ""))


# v4.290.0：`_new_session` / `_close_session` 已外移到 ui_session_mixin.py
# （ui.py 撞 10000 行红线，按纪律外移）。判据拼两个源扫，要求不变。
_SRC = ((ROOT / "ui_session_mixin.py").read_text(encoding="utf-8-sig")
        + "\n" + (ROOT / "ui.py").read_text(encoding="utf-8-sig"))


class _Sess:
    def __init__(self, sid, title="新会话", created="2026-11-01T10:00:00",
                 pinned=False, messages=None):
        self.sid = sid
        self.title = title
        self.created = created
        self.pinned = pinned
        self.messages = messages if messages is not None else []


class _Combo:
    def __init__(self):
        self.items = []
        self.cur = -1
        self.blocked = None

    def blockSignals(self, b):
        self.blocked = b

    def clear(self):
        self.items = []

    def addItem(self, text, data):
        self.items.append((text, data))

    def setCurrentIndex(self, i):
        self.cur = i


class _Store:
    def __init__(self, sessions, active_sid):
        self.sessions = sessions
        self.active_sid = active_sid
        self.new_calls = 0

    def active(self):
        return self.sessions.get(self.active_sid)

    def new_session(self):
        self.new_calls += 1
        return _Sess("brand-new")


def make_window(store):
    w = UI.ChatWindow.__new__(UI.ChatWindow)
    w.store = store
    w.session_combo = _Combo()
    return w


print("=" * 66)
print("U 组：会话下拉框（过滤 + 排序）")
print("=" * 66)

auto_sid = automation.SESSION_SID_PREFIX + "t9"
store = _Store({
    "old": _Sess("old", "旧对话", "2026-11-01T09:00:00"),
    "new": _Sess("new", "新对话", "2026-11-05T09:00:00"),
    "mid": _Sess("mid", "中间对话", "2026-11-03T09:00:00"),
    "pin": _Sess("pin", "置顶对话", "2026-10-01T09:00:00", pinned=True),
    auto_sid: _Sess(auto_sid, "自动化任务", "2026-11-09T09:00:00"),
}, "new")
w = make_window(store)
w._refresh_session_combo()
_ids = [d for _t, d in w.session_combo.items]

check("U1 自动化会话不出现在用户对话列表",
      auto_sid not in _ids, _ids)
check("U2 置顶排最前", _ids[:1] == ["pin"], _ids)
check("U3 其余按创建时间倒序（new > mid > old）",
      _ids[1:] == ["new", "mid", "old"], _ids)
check("U4 当前会话索引指向 active（new 在第 1 位）",
      w.session_combo.cur == 1, w.session_combo.cur)
check("U5 结束后恢复信号（blockSignals False）",
      w.session_combo.blocked is False, w.session_combo.blocked)

print()
print("=" * 66)
print("N 组：新开对话（空会话复用）")
print("=" * 66)

# N1 当前会话为空 → 复用，不新建
store2 = _Store({"empty": _Sess("empty", messages=[])}, "empty")
w2 = make_window(store2)
w2._render_messages = lambda **k: None
class _Inp:
    def setFocus(self):
        pass
w2.input_box = _Inp()
w2._new_session()
check("N1 当前会话为空 → 不新建（连点不堆空会话）",
      store2.new_calls == 0, store2.new_calls)

# N2 当前会话有消息 → 正常新建
store3 = _Store({"busy_conv": _Sess("busy_conv",
                                    messages=[{"role": "user", "content": "hi"}])},
                "busy_conv")
w3 = make_window(store3)
w3._render_messages = lambda **k: None
w3.input_box = _Inp()
for _m in ("_reset_session_trust", "_sync_product_context",
           "_refresh_session_combo", "_refresh_skill_buttons",
           "_update_skill_bar", "_refresh_deliverables",
           "_refresh_recent_on_welcome"):
    setattr(w3, _m, lambda *a, **k: None)
w3._new_session()
check("N2 当前会话有内容 → 正常新建", store3.new_calls == 1, store3.new_calls)

# N3 当前是自动化会话（空）→ 不该复用，必须新建
store4 = _Store({auto_sid: _Sess(auto_sid, messages=[])}, auto_sid)
w4 = make_window(store4)
w4._render_messages = lambda **k: None
w4.input_box = _Inp()
for _m in ("_reset_session_trust", "_sync_product_context",
           "_refresh_session_combo", "_refresh_skill_buttons",
           "_update_skill_bar", "_refresh_deliverables",
           "_refresh_recent_on_welcome"):
    setattr(w4, _m, lambda *a, **k: None)
w4._new_session()
check("N3 停在空的自动化会话 → 不复用，另开用户会话",
      store4.new_calls == 1, store4.new_calls)

print()
print("=" * 66)
print("P 组：删会话的上下文清理路径")
print("=" * 66)

def _close_body():
    """只取 `_close_session` 的函数体（现在在 ui_session_mixin.py）。

    切片必须限定在同一个文件内：`_SRC` 是「mixin + ui.py」拼接串，
    跨文件切会把 ui.py 里别的方法也算进来（假红）。
    """
    mix = (ROOT / "ui_session_mixin.py").read_text(encoding="utf-8-sig")
    i = mix.index("def _close_session")
    try:
        j = mix.index("\n    def ", i + 10)
    except ValueError:
        j = len(mix)          # 它是文件里最后一个方法
    return mix[i:j]


check("P1 删会话流程不再硬编码用户目录",
      "expanduser(" not in _close_body(), _close_body()[-300:])
check("P2 改走 config.USER_DATA_DIR",
      "_ud = _cfg.USER_DATA_DIR" in _SRC)

print()
print("PASS=%d FAIL=%d" % (PASS, FAIL))
sys.exit(1 if FAIL else 0)
