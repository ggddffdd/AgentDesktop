# -*- coding: utf-8 -*-
"""toast 组件验收（DESIGN.md §11.9 的 6 条判据，逐条做成可执行）

独立运行：python tests/test_toast_204.py
"""
import os
import re
import sys
import threading
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
sys.path.insert(0, HERE)
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QEvent, QTimer  # noqa: E402
from PySide6.QtWidgets import (  # noqa: E402
    QApplication,
    QLabel,
    QVBoxLayout,
    QWidget,
)

import toast as T  # noqa: E402

FAIL = []
CHECKED = 0


def check(name, ok, extra=""):
    global CHECKED
    CHECKED += 1
    tag = "OK  " if ok else "FAIL"
    print(f"  [{tag}] {name}" + (f"  — {extra}" if extra and not ok else ""))
    if not ok:
        FAIL.append(name)


def flush(ms=30, rounds=4):
    """推进事件循环，让 queued 信号落地（跨线程 emit 必须靠它投递）。"""
    app = QApplication.instance()
    end = time.time() + ms / 1000.0
    for _ in range(rounds):
        app.processEvents()
        time.sleep(0.005)
    while time.time() < end:
        app.processEvents()
        time.sleep(0.005)


def wait(ms):
    """按墙钟等待并持续推进事件循环（供 QTimer 超时用）。"""
    app = QApplication.instance()
    end = time.time() + ms / 1000.0
    while time.time() < end:
        app.processEvents()
        time.sleep(0.005)


app = QApplication.instance() or QApplication(sys.argv)


def _on_crash(etype, e, tb):
    """任何一步崩了都要把汇总打出来。

    判据红了但后续代码崩掉 → 汇总行没打印 → run_all 看到空输出判 [EMPT]，
    而失败名单为空又看不出红在哪。失败的报告必须比失败本身更健壮。
    """
    import traceback
    traceback.print_exception(etype, e, tb)
    print(f"\n=== 测试中断：{e!r} ===")
    print(f"PASS={CHECKED - len(FAIL)} FAIL={len(FAIL) + 1}")
    sys.exit(1)


sys.excepthook = _on_crash

# ---------------------------------------------------------------
print("=== 判据 6 未注册 host 时调用不抛异常（降级写日志）===")
# 降级路径的可观测点是「写了日志」而不是「没抛异常」——
# 渲染跑在 Qt 事件处理里，异常根本传不到 toast() 调用处（扰动 ⑥ 验证过）。
import logging                                # noqa: E402

_records = []


class _Cap(logging.Handler):
    def emit(self, record):
        _records.append(record.getMessage())


logging.getLogger("toast").addHandler(_Cap())
logging.getLogger("toast").setLevel(logging.INFO)

T.unregister_toast_host()
raised = None
try:
    T.toast("未注册时的提示", kind="success")
    T.toast("未注册时的错误", kind="error", detail="detail")
    flush(80)
except Exception as e:                       # noqa: BLE001
    raised = e
check("6a 未注册时 toast() 不抛异常", raised is None, repr(raised))
check("6b 未注册时 visible_count()==0", T.visible_count() == 0)
bad_kind = None
try:
    T.toast("x", kind="no-such-kind")
except ValueError:
    bad_kind = "ok"
check("6c kind 非法时明确抛 ValueError（参数错误不该静默）", bad_kind == "ok")
check("6d 未注册时降级写日志（降级路径真的被执行）",
      any("[toast:success]" in r and "未注册时的提示" in r for r in _records),
      f"捕获到 {len(_records)} 条日志")

# ---------------------------------------------------------------
print("\n=== 建立测试 host ===")
host = QWidget()
host.resize(900, 600)
hl = QVBoxLayout(host)
hl.setContentsMargins(0, 0, 0, 0)
filler = QWidget()
filler.setMinimumHeight(480)
hl.addWidget(filler)
avoid = QWidget()          # 模拟聊天输入区（底部）
avoid.setMinimumHeight(100)
hl.addWidget(avoid)
host.show()

T.register_toast_host(host, avoid=avoid)
flush(60)
check("0a 注册后图层建立", T._MGR._layer is not None)
check("0b 注册返回图层挂在 host 上", T._MGR._layer.parent() is host)

# ---------------------------------------------------------------
print("\n=== 判据 3 同内容 2s 内合并 ===")
for i in range(5):
    T.toast("保存成功", kind="success", duration_ms=2000)
    flush(40)
check("3a 连弹 5 次相同内容 → 界面上只有 1 条", T.visible_count() == 1,
      f"实测 {T.visible_count()}")

# 不同内容应当新增
T.toast("另一条提示", kind="info", duration_ms=2000)
flush(40)
check("3b 不同内容不合并 → 2 条", T.visible_count() == 2, f"实测 {T.visible_count()}")
# 同 kind 同文案但不同 detail 也不该合并
T.toast("保存成功", kind="success", detail="a.md", duration_ms=2000)
flush(40)
check("3c 同文案不同 detail 不合并 → 3 条", T.visible_count() == 3,
      f"实测 {T.visible_count()}")

# ---------------------------------------------------------------
print("\n=== 判据 4 同时最多 3 条（第 4 条丢弃最旧，不排队）===")
T.unregister_toast_host()
T.register_toast_host(host, avoid=avoid)
flush(40)
for i in range(6):
    T.toast(f"第 {i} 条", kind="info", duration_ms=5000)
    flush(40)
check("4a 连弹 6 条 → 稳定后 3 条", T.visible_count() == 3, f"实测 {T.visible_count()}")
cards = T._MGR._layer.cards()
texts = [[l.text() for l in c.findChildren(QLabel)] for c in cards]
check("4b 保留的是最新的 3 条（第 3/4/5 条），最旧的被丢弃",
      any("第 5 条" in t for t in texts) and all("第 0 条" not in t for t in texts),
      f"实测 {texts}")

# ---------------------------------------------------------------
print("\n=== 判据 5 悬停暂停 / 移出重新计时 ===")
T.unregister_toast_host()
T.register_toast_host(host, avoid=avoid)
flush(40)
T.toast("悬停测试", kind="info", duration_ms=400)
flush(40)
card = T._MGR._layer.cards()[0]
check("5a 计时器在跑", card._timer.isActive())
app.sendEvent(card, QEvent(QEvent.Enter))
flush(20)
check("5b 鼠标进入 → 计时暂停", not card._timer.isActive())
check("5c 悬停时显示关闭按钮（非 error 也显示）", card._close_btn.isVisible())
app.sendEvent(card, QEvent(QEvent.Leave))
flush(20)
check("5d 鼠标移出 → 重新计时", card._timer.isActive())
check("5e 移出后非 error 的关闭按钮重新隐藏", not card._close_btn.isVisible())
remain = card._timer.remainingTime()
check("5f 移出是重新计时而非续算（剩余≈完整时长）", 300 <= remain <= 400,
      f"remaining={remain}")

# ---------------------------------------------------------------
print("\n=== 判据 2 任意线程调用不崩（3 线程并发弹 50 次）===")
T.unregister_toast_host()
T.register_toast_host(host, avoid=avoid)
flush(40)
errors = []


def worker(n):
    try:
        for i in range(50):
            T.toast(f"线程{n}-{i}", kind=["success", "info", "warn", "error"][i % 4],
                    duration_ms=3000)
    except Exception as e:                   # noqa: BLE001
        errors.append(repr(e))


ths = [threading.Thread(target=worker, args=(n,)) for n in range(3)]
for t in ths:
    t.start()
for t in ths:
    t.join(timeout=20)
flush(300, rounds=10)
check("2a 工作线程内 emit 未抛异常", not errors, f"{errors[:2]}")
check("2b 全部请求处理完仍不超过 3 条", T.visible_count() <= 3,
      f"实测 {T.visible_count()}")
check("2c 后台请求确实被渲染（不是全丢弃）", T.visible_count() >= 1,
      f"实测 {T.visible_count()}")

# ---------------------------------------------------------------
print("\n=== 判据 1 颜色/圆角/字号/间距全走 token（不写死）===")
tq_src = open(os.path.join(ROOT, "theme_qss.py"), encoding="utf-8").read()
toast_src = open(os.path.join(ROOT, "toast.py"), encoding="utf-8").read()
hex_pat = re.compile(r"#[0-9A-Fa-f]{6}\b")
tq_seg = tq_src[tq_src.index("TOAST_KIND_COLOR"):]
check("1a theme_qss 的 toast 段无裸 hex", not hex_pat.search(tq_seg),
      f"{hex_pat.findall(tq_seg)[:3]}")
check("1b toast.py 无裸 hex", not hex_pat.search(toast_src),
      f"{hex_pat.findall(toast_src)[:3]}")
check("1c toast.py 无写死 px 圆角（走 toast_card）",
      "border-radius" not in toast_src.replace("toast_card", ""))
for fn in ("toast_card", "toast_bar", "toast_dot", "toast_close_btn",
           "TOAST_KIND_COLOR"):
    check(f"1d theme_qss 提供 {fn}", f"def {fn}" in tq_src or f"{fn} = " in tq_src)

# ---------------------------------------------------------------
print("\n=== 行为补充：error 不自动消失 / 自动消失 / 避让输入区 ===")
T.unregister_toast_host()
T.register_toast_host(host, avoid=avoid)
flush(40)
T.toast("导出失败", kind="error", detail="磁盘空间不足")
flush(60)
check("B1 error 条已显示", T.visible_count() == 1)
# 等 1500ms 才够：扰动 ③ 把 error 改成 800ms 自动消失，必须能被这条抓到
wait(1500)
check("B2 error 1500ms 后仍不自动消失（设计：要能读完/复制）",
      T.visible_count() == 1, f"实测 {T.visible_count()}")
_cards = T._MGR._layer.cards()          # 防御取值：B2 若失败这里不能连带崩
ecard = _cards[0] if _cards else None
check("B3 error 常显关闭按钮", ecard is not None and ecard._close_btn.isVisible())

T.unregister_toast_host()
T.register_toast_host(host, avoid=avoid)
flush(40)
T.toast("两秒后消失", kind="success", duration_ms=200)
flush(60)
check("B4 success 条已显示", T.visible_count() == 1)
wait(800)
check("B5 success 超时后自动消失", T.visible_count() == 0, f"实测 {T.visible_count()}")

# 避让：toast 底边应在 avoid 上边缘之上
T.unregister_toast_host()
T.register_toast_host(host, avoid=avoid)
flush(40)
T.toast("避让测试", kind="info", duration_ms=4000)
flush(80)
layer = T._MGR._layer
geo = layer.geometry()
avoid_top_in_host = avoid.mapTo(host, avoid.rect().topLeft()).y()
check("B6 浮层底边在输入区上边缘之上（不遮输入框）",
      geo.bottom() <= avoid_top_in_host, f"toast底={geo.bottom()} 输入区顶={avoid_top_in_host}")
# 基准值写死为设计规格，不取 T.EDGE_MARGIN / T.MIN_W ——
# 拿被测常量当基准，常量一改判据跟着改，就成了永远绿的空判据（扰动 ⑤ 验证过）
check("B7 浮层靠右（右边缘距 host 右侧 24）",
      abs(host.width() - geo.right() - 24) <= 2,
      f"right={geo.right()} host_w={host.width()}")
check("B8 浮层宽度夹在 [280,420]", 280 <= geo.width() <= 420,
      f"w={geo.width()}")

# 宿主尺寸变化后要跟着走（窗口缩放时 toast 不能停在旧位置）
host.resize(700, 520)
flush(60)
geo2 = layer.geometry()
check("B9 宿主 resize 后重新定位（右下角跟随）",
      abs(host.width() - geo2.right() - 24) <= 2 and geo2.bottom() <= host.height(),
      f"right={geo2.right()} bottom={geo2.bottom()} host={host.width()}x{host.height()}")

print(f"\n=== 汇总：{len(FAIL)} 条失败 ===")
for f in FAIL:
    print("   ✗", f)
print(f"PASS={CHECKED - len(FAIL)} FAIL={len(FAIL)}")
sys.exit(1 if FAIL else 0)
