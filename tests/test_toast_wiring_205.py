# -*- coding: utf-8 -*-
"""toast 接线验收（v4.205.0，DESIGN.md §11.7）

上一轮（v4.204.0）只把组件做出来并注册了 host，**没接任何调用点**。
本轮把"保存成功 / 复制成功 / 导出成功 / 删除成功"这类最典型的轻反馈接上。

判据分四类（独立运行：python tests/test_toast_wiring_205.py）：

C 段  挂载点      —— host 必须是 main_stack（挂 chat_col 会在非对话页隐形）
D 段  运行时真跑  —— 不读源码猜，直接调真方法，看它到底弹了什么
E 段  防回退      —— 旧写法（status_label）不许再出现在这几个方法里
F 段  保守边界    —— 该留弹窗的地方（确认框 / 需要复制的路径）不许被换掉

教训（LEARNINGS L178/L181）：判据不能只做"源码里有没有字符串"——
改了字符串判据跟着改就成了恒绿的空判据。所以 D 段一律真调方法。
"""
import ast
import os
import sys
import time
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
sys.path.insert(0, HERE)
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

FAIL = []
CHECKED = 0


def check(name, ok, extra=""):
    global CHECKED
    CHECKED += 1
    tag = "OK  " if ok else "FAIL"
    print(f"  [{tag}] {name}" + (f"  — {extra}" if extra and not ok else ""))
    if not ok:
        FAIL.append(name)


# 崩溃也要把统计打出来（L182：中间崩了汇总就丢了，扰动脚本会误判成"没红"）
def _hook(t, v, tb):
    print(f"\n[!] 未捕获异常：{t.__name__}: {v}")
    print(f"PASS={CHECKED - len(FAIL)} FAIL={len(FAIL)}")
    sys.exit(1)


sys.excepthook = _hook

print("=== v4.205.0 toast 接线验收 ===")

# ---------------------------------------------------------------- C 挂载点
UI_SRC = open(os.path.join(ROOT, "ui.py"), encoding="utf-8").read()
# 行切片取值（不用 ast.get_source_segment）：仓库是 CRLF，ast 内部按 \n 计列，
# 每行少算一个 \r 会累积成偏移，段提取会失准（L170 同类坑）。
UI_LINES = UI_SRC.splitlines()

check("C1 host 挂在 main_stack（所有页面共同的容器）",
      "register_toast_host(self.main_stack" in UI_SRC)
check("C2 不再是 chat_col（对话页专属，切页就隐形）",
      "register_toast_host(self.chat_col" not in UI_SRC)

TREE = ast.parse(UI_SRC)
CW = next((n for n in ast.walk(TREE)
           if isinstance(n, ast.ClassDef) and n.name == "ChatWindow"), None)
check("C3 找到 ChatWindow 类（后续判据的前提）", CW is not None)


def method_src(cls, name):
    """取某个方法的源码文本（按行切片，没有则返回 None）。"""
    n = method_node(cls, name)
    if n is None:
        return None
    return "\n".join(UI_LINES[n.lineno - 1:n.end_lineno])


def _toast_calls_in(node):
    """收集子树里的 toast(...) 调用（只取字面量参数，f-string 记成 <fstring>）。"""
    out = []
    for n in ast.walk(node):
        if not (isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
                and n.func.id == "toast"):
            continue
        d = {"msg": "", "kind": None, "detail": None}
        if n.args:
            a = n.args[0]
            d["msg"] = a.value if isinstance(a, ast.Constant) else "<fstring>"
        for kw in n.keywords:
            if kw.arg in ("kind", "detail") and isinstance(kw.value, ast.Constant):
                d[kw.arg] = kw.value.value
        out.append(d)
    return out


def method_node(cls, name):
    for n in cls.body:
        if isinstance(n, ast.FunctionDef) and n.name == name:
            return n
    return None


mnt_node = method_node(CW, "_mount_toast") if CW else None
check("C4 有独立的 _mount_toast（注册不散在 __init__ 里）", mnt_node is not None)
if mnt_node:
    msrc = method_src(CW, "_mount_toast")
    check("C5 _mount_toast 里带 avoid=input_area（对话页仍不遮输入框）",
          "avoid=self.input_area" in msrc)

# 注：注册发生在 _init_ui（由 __init__ 调用）末尾 —— main_stack 在那儿才建好
init_src = method_src(CW, "_init_ui") if CW else ""
check("C6 _init_ui 末尾调用了 _mount_toast",
      "self._mount_toast()" in (init_src or ""))
# 顺序不能反：main_stack 必须先存在，否则 AttributeError
check("C7 _mount_toast() 位置在 main_stack 创建之后",
      "self.main_stack = " in (init_src or "")
      and (init_src or "x").index("self.main_stack = ")
      < (init_src or "x").index("self._mount_toast()"))

# ------------------------------------------------------------ D 运行时真跑
print("\n-- D 段：真调方法（monkeypatch ui.toast 记录调用）--")

from PySide6.QtWidgets import QApplication, QVBoxLayout, QWidget  # noqa: E402

_app = QApplication.instance() or QApplication(sys.argv)

t0 = time.time()
import ui as UI  # noqa: E402
print(f"  (import ui 耗时 {time.time() - t0:.1f}s)")

import toast as T  # noqa: E402

CALLS = []


def _rec(msg, kind="info", detail=None, duration_ms=None):
    CALLS.append({"msg": msg, "kind": kind, "detail": detail})
    return None


REAL_TOAST = UI.toast
UI.toast = _rec


def reset():
    CALLS.clear()


class _Store:
    def __init__(self, msgs):
        self._m = msgs

    def active(self):
        return type("S", (), {"messages": self._m})()


def _self(**kw):
    """造一个最小 self：只带被测方法要碰到的属性。"""
    from types import SimpleNamespace
    base = dict(cfg={}, _busy=False, store=_Store([]))
    base.update(kw)
    return SimpleNamespace(**base)


# ---- D1/D2 保存 API Key（"保存成功"典型）----
reset()
s = _self(api_key_edit=mock.Mock(**{"text.return_value": "sk-test-123"}),
          _save_cfg=mock.Mock())
UI.ChatWindow._save_api_key(s)
check("D1 保存 API Key → success『API Key 已保存』",
      len(CALLS) == 1 and CALLS[0]["kind"] == "success"
      and CALLS[0]["msg"] == "API Key 已保存", f"实际 {CALLS}")

reset()
s = _self(api_key_edit=mock.Mock(**{"text.return_value": "  "}),
          _save_cfg=mock.Mock())
UI.ChatWindow._save_api_key(s)
check("D2 清空 API Key → warn（不是 success，清空是要注意的事）",
      len(CALLS) == 1 and CALLS[0]["kind"] == "warn", f"实际 {CALLS}")

# ---- D3/D4/D5 复制配对码（"复制成功"典型）----
reset()
UI.ChatWindow._copy_ext_token(_self())
check("D3 配对码为空 → warn",
      len(CALLS) == 1 and CALLS[0]["kind"] == "warn", f"实际 {CALLS}")

_fake = {"t": None}


class _Clip:
    def setText(self, t):
        _fake["t"] = t

    def text(self):
        return _fake["t"]


reset()
with mock.patch.object(QApplication, "clipboard", lambda: _Clip()):
    UI.ChatWindow._copy_ext_token(_self(cfg={"browser_bridge_token": "TOK-9"}))
check("D4 复制成功 → success 且真的写进了剪贴板",
      len(CALLS) == 1 and CALLS[0]["kind"] == "success"
      and _fake["t"] == "TOK-9", f"实际 {CALLS} / 剪贴板 {_fake['t']!r}")


def _boom():
    raise RuntimeError("剪贴板被其它程序占用")


reset()
with mock.patch.object(QApplication, "clipboard", _boom):
    UI.ChatWindow._copy_ext_token(_self(cfg={"browser_bridge_token": "TOK-9"}))
check("D5 复制失败 → error 且带 detail（error 不自动消失，能读完）",
      len(CALLS) == 1 and CALLS[0]["kind"] == "error"
      and CALLS[0]["detail"], f"实际 {CALLS}")

# ---- D6 导出空会话 ----
reset()
UI.ChatWindow.export_session(_self(store=_Store([])))
check("D6 会话无内容 → warn（不是静默返回）",
      len(CALLS) == 1 and CALLS[0]["kind"] == "warn", f"实际 {CALLS}")

# ---- D7 删除会话（忙）----
reset()
UI.ChatWindow._request_delete_session(_self(_busy=True), "sid", "标题")
check("D7 正忙时删会话 → warn",
      len(CALLS) == 1 and CALLS[0]["kind"] == "warn", f"实际 {CALLS}")

# ---- D8 _brief_err：堆栈要压成一行、要截断 ----
long_err = RuntimeError("x" * 300)
b = UI._brief_err(long_err)
check("D8a 长错误被截断（≤160，末尾省略号）", len(b) <= 160 and b.endswith("…"),
      f"len={len(b)}")
check("D8b 多行错误被压成一行（toast 里换行会把卡片撑高）",
      "\n" not in UI._brief_err(RuntimeError("a\nb\nc")))
check("D8c 短错误原样保留（不无谓加工）",
      UI._brief_err(RuntimeError("磁盘满了")) == "磁盘满了")

# ---- D9 端到端：恢复真 toast，看卡片真的出现在界面上 ----
UI.toast = REAL_TOAST
host = QWidget()
host.resize(900, 600)
lay = QVBoxLayout(host)
host.show()
T.register_toast_host(host)


def flush(ms=120):
    end = time.time() + ms / 1000.0
    while time.time() < end:
        QApplication.instance().processEvents()
        time.sleep(0.005)


before = T.visible_count()
UI.ChatWindow._save_api_key(_self(api_key_edit=mock.Mock(
    **{"text.return_value": "sk-e2e"}), _save_cfg=mock.Mock()))
flush(200)
check("D9 端到端：真方法调用后界面上真的多了一条 toast",
      T.visible_count() == before + 1,
      f"before={before} after={T.visible_count()}")
T.unregister_toast_host()

# -------------------------------------------------------------- E 防回退
print("\n-- E 段：旧写法不许回来（status_label 在对话页，设置页看不见）--")

for m in ("_copy_ext_token", "_save_api_key", "export_session"):
    src = method_src(CW, m)
    check(f"E1 {m} 已不再写 status_label", src is not None
          and "status_label" not in src)

for m in ("_delete_active_session", "_request_delete_session"):
    src = method_src(CW, m)
    check(f"E2 {m} 删除结果不再写 status_label", src is not None
          and 'status_label.setText(f"已删除' not in (src or "x"))

n_toast_calls = sum(
    1 for n in ast.walk(TREE)
    if isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and n.func.id == "toast")
check("E3 ui.py 里 toast 调用 ≥ 8 处（本轮接了复制/保存/导出/删除/检查更新）",
      n_toast_calls >= 8, f"实际 {n_toast_calls}")

# ------------------------------------------------------------ F 保守边界
print("\n-- F 段：该留弹窗的必须留（toast 文案不可复制、不阻塞）--")

del_src = method_src(CW, "_delete_active_session") or ""
check("F1 删除会话仍有确认框（不可逆操作，不能只弹个 toast 就删）",
      "QMessageBox.Question" in del_src and "box.exec()" in del_src)

upd_src = method_src(CW, "_check_update") or ""
n_upd_toast = _toast_calls_in(method_node(CW, "_check_update"))
check("F2 检查更新失败仍是 QMessageBox.warning（要能读完/复制原因）",
      "QMessageBox.warning(" in upd_src
      and not any("失败" in c["msg"] for c in n_upd_toast),
      f"toast 调用 {n_upd_toast}")
check("F3 已是最新改走 toast（只一句结论，不值得打断）",
      'toast("已是最新版本"' in upd_src)
# 写死"只许一条"：失败分支若也被换成 toast，这里立刻红
check("F3b _check_update 里只有 1 处 toast（多的就是把失败也换掉了）",
      len(n_upd_toast) == 1, f"实际 {len(n_upd_toast)} 处")

DX = os.path.join(ROOT, "diagnostic_export.py")
dx_src = open(DX, encoding="utf-8").read() if os.path.exists(DX) else ""
check("F4 诊断包导出成功仍走弹窗（路径要发给开发者，toast 抄不走）",
      'QMessageBox.information(parent, "诊断包已导出"' in dx_src)
# 反向判据：这个模块一个 toast 都不许有 —— 加了就红（扰动第 7 条验证过）
check("F4b diagnostic_export.py 里 toast 调用数为 0",
      len(_toast_calls_in(ast.parse(dx_src))) == 0)

print(f"\n=== 汇总：{len(FAIL)} 条失败 ===")
for f in FAIL:
    print("   ✗", f)
print(f"PASS={CHECKED - len(FAIL)} FAIL={len(FAIL)}")
sys.exit(1 if FAIL else 0)
