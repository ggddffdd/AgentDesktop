# -*- coding: utf-8 -*-
"""军团「成员说话带产出首句」（v4.295.0）：现有对话模块的行为级判据。

背景：v4.292.0 让成员在时间线有了发言身份，但只会播报「交稿 / 没跑通」，
像打卡机不像群成员。实地核查发现产出首句其实早就在盘上：
legion_worker._board() 先调 legion.board_update() 把 summary 写进
legion_board/<pid>.json 的 nodes[node_key].summary，之后才 emit 5 参信号。
所以本版只让 on_member_status 拿到 (pid, key) 去读盘拼一句，
**信号签名、worker、状态条、调度、QSS 一律不动** —— 属现有模块优化。

判据分四组：
  A 数据层 —— 首句读取、清洗、40 字截断、读盘失败/坏档降级、判词去重
  B 行为   —— 时间线真的出现首句、长文不刷屏、过滤口径与空段兜底不翻
  C 不变量 —— 零样式、回放仍在、v4.292 接线与信号签名不变、引擎零改动
  D 时序   —— 先写盘再 emit（本方案成立的前提，钉死不许被改坏）
"""
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

_SBX = tempfile.mkdtemp(prefix="xc_sbx_sum295_")
os.environ["XC_LEGION_DIR"] = _SBX
os.environ.setdefault("XC_USER_DATA_DIR", os.path.join(_SBX, "userdata"))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

PASS = 0
FAIL = 0


def check(name, cond, extra=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print("[PASS] %s" % name)
    else:
        FAIL += 1
        print("[FAIL] %s %s" % (name, extra))


def read(rel):
    return (ROOT / rel).read_text(encoding="utf-8")


def _app():
    from PySide6.QtWidgets import QApplication
    return QApplication.instance() or QApplication([])


def _fresh():
    _app()
    from legion_chat import LegionChatPanel
    return LegionChatPanel()


def _zero(p, *args):
    before = p.log.toPlainText()
    p.on_member_status(*args)
    return p.log.toPlainText() == before


def _seed(pid, key, summary, **fields):
    """按 worker 的真实写入路径造节点（board_update 会把 summary 落 nodes）。"""
    import legion
    legion.board_update(pid, key, summary=summary, **fields)
    return legion.board_get(pid)["nodes"].get(key, {})


# ---------------------------------------------------------------- A 数据层
def test_summary_fn():
    import legion_chat
    _impl = getattr(legion_chat, "member_line_summary", None)
    check("A1 legion_chat 暴露 member_line_summary（本版新增的纯函数）",
          callable(_impl))

    def call(*a):
        # 旧源码没有该函数时逐条跑红，不短路 —— 判据要显示改前的真实缺口。
        return _impl(*a) if callable(_impl) else ""

    _seed("pa", "w1_m0", "主角是个在旧书店打工的姑娘，她能听见书里的人说话。")
    r = call("pa", "w1_m0", "交稿")
    check("A2 读到产出首句", "旧书店打工" in r, r)

    long_one = "。" * 70 + "结尾"
    _seed("pb", "w2_m0", long_one)
    r2 = call("pb", "w2_m0", "交稿")
    check("A3 长首句截断到 40 字以内", len(r2) <= 41, "len=%d %r" % (len(r2), r2))
    check("A3b 截断带省略号", r2.endswith("…"), r2)
    check("A3c 截断后短于原串且长度受控",
          len(r2) < len(long_one) and len(r2) <= 41, "len=%d" % len(r2))

    _seed("pc", "w1_m0", "第一行\n\n第二行\t\t第三行   第四行")
    r3 = call("pc", "w1_m0", "交稿")
    check("A4 换行/制表/连续空格被清洗", "\n" not in r3 and "\t" not in r3
          and "    " not in r3, r3)
    check("A4b 清洗后内容仍在", "第一行" in r3 and "第四行" in r3, r3)

    _seed("pd", "w1_m0", "")
    check("A5 空 summary 返回空串", call("pd", "w1_m0", "交稿") == "",
          repr(call("pd", "w1_m0", "交稿")))
    check("A6 节点不存在返回空串", call("pd", "w9_m9", "交稿") == "",
          repr(call("pd", "w9_m9", "交稿")))
    check("A6b 不存在的 pid 返回空串", call("no_such_pid", "w1_m0", "交稿") == "",
          repr(call("no_such_pid", "w1_m0", "交稿")))

    # 坏档：board_load 内部吞异常返回空板，纯函数不得抛
    bad = Path(_SBX) / "legion_board" / "pbad.json"
    bad.parent.mkdir(parents=True, exist_ok=True)
    bad.write_text("{ this is not json", encoding="utf-8")
    try:
        v = call("pbad", "w1_m0", "交稿")
        check("A7 坏档不抛异常且降级为空串", v == "", repr(v))
    except Exception as e:
        check("A7 坏档不抛异常且降级为空串", False, repr(e))

    _seed("pe", "w3_m0", "已取消：大哥点了停止")
    r5 = call("pe", "w3_m0", "已取消")
    check("A8 剥掉与判词重复的前缀", "已取消" not in r5 and "点了停止" in r5, r5)

    _seed("pf", "w4_m0", "ConnectionResetError: 远端断了")
    r6 = call("pf", "w4_m0", "没跑通")
    check("A9 失败原因也进首句", "远端断了" in r6, r6)

    check("A10 截断常量是 40", getattr(legion_chat, "_MEMBER_SUMMARY_MAX", None) == 40,
          repr(getattr(legion_chat, "_MEMBER_SUMMARY_MAX", None)))


# ---------------------------------------------------------------- B 行为
def test_timeline():
    p = _fresh()
    _seed("b1", "w1_m0", "先把主角设定成旧书店的姑娘，再铺三条线索。",
          role="写手", wave="2", status="done")
    p.on_member_status("b1", "w1_m0", "写手", "2", "done")
    t = p.log.toPlainText()
    check("B1 时间线出现产出首句", "旧书店的姑娘" in t, t[-240:])
    check("B2 原有身份格式不变（成员 · 写手 · 第 2 波 · 交稿）",
          "成员 · 写手 · 第 2 波 · 交稿" in t, t[-240:])
    _member_lines = [ln for ln in t.splitlines() if "成员 · " in ln]
    check("B2b 首句接在判词后面、同一行且只有一条成员消息",
          "交稿 · " in t and len(_member_lines) == 1
          and "旧书店的姑娘" in _member_lines[0], t[-240:])

    # 长文不刷屏：本版拼的是 40 字，不能把 70 个句号灌进来
    _seed("b2", "w2_m0", "。" * 70, role="审校", wave="2", status="incomplete")
    q = _fresh()
    q.on_member_status("b2", "w2_m0", "审校", "2", "incomplete")
    qt = q.log.toPlainText()
    _b3_lines = [ln for ln in qt.splitlines() if "成员 · " in ln]
    check("B3 长文不刷屏（成员单行有上限、原 70 字符号没整段灌入）",
          len(_b3_lines) == 1 and len(_b3_lines[0]) < 90
          and qt.count("。") < 70, qt[-160:])
    check("B3b 该成员仍有发言", "没交上稿" in qt, qt[-160:])

    check("B4 running 有 summary 也零输出",
          _zero(p, "b1", "w3_m0", "选品官", "3", "running"))
    for key in ("run", "pm_plan", "gate_w2"):
        check("B4 %s 零输出" % key, _zero(p, "b1", key, "写手", "2", "done"))
    check("B4b 未知状态零输出", _zero(p, "b1", "w4_m0", "写手", "4", "unknown"))

    # 降级 emit：role/wave 空串，且盘上没节点 → 仍出「交稿」、不留空段
    s = _fresh()
    s.on_member_status("no_board", "w1_m0", "", "", "done")
    st = s.log.toPlainText()
    check("B5 读盘失败仍出「成员 · 交稿」", "成员 · 交稿" in st, st)
    check("B5b 不留空段", " ·  · " not in st and "第  波" not in st, st)

    # 四种终态各自成行
    u = _fresh()
    for i, (status, key) in enumerate((("done", "a"), ("error", "b"),
                                      ("incomplete", "c"), ("cancelled", "d"))):
        _seed("b3", "w5_m%d" % i, "第%d句%s" % (i, "内容" if status != "cancelled"
                                              else "已取消：收工"), status=status)
        u.on_member_status("b3", key, "写手", "5", status)
    ut = u.log.toPlainText()
    check("B6 四种终态仍各自成行",
          all(w in ut for w in ("交稿", "没跑通", "没交上稿", "已取消")), ut[-260:])

    for w in (p, q, s, u):
        w.deleteLater()


# ---------------------------------------------------------------- C 不变量
def test_invariants():
    import legion_chat
    p = _fresh()
    before = p.log.styleSheet()
    _seed("c1", "w1_m0", "回放用的一句产出。", role="写手", wave="2", status="done")
    p.on_member_status("c1", "w1_m0", "写手", "2", "done")
    check("C1 零样式：styleSheet 前后逐字节相等", p.log.styleSheet() == before)

    check("C2 _ROLE_NAMES 仍含「成员」（回放不被静默过滤）",
          "成员" in legion_chat._ROLE_NAMES, str(legion_chat._ROLE_NAMES))

    p._proj_id = "sum295_replay"
    p._save_history()
    p.reload_for_project("sum295_other")
    check("C3 切走后清空", "回放用的一句产出" not in p.log.toPlainText())
    p.reload_for_project("sum295_replay")
    check("C3b 回放：带首句的发言仍在",
          "回放用的一句产出" in p.log.toPlainText(), p.log.toPlainText()[-200:])
    p.deleteLater()

    worker = read("legion_worker.py")
    chat = read("legion_chat.py")
    ui = read("legion_ui.py")
    check("C4 board_update 仍是 5 参签名",
          "Signal(str, str, str, str, str)" in worker)
    check("C4b worker 的 emit 仍是 5 个实参",
          "self.board_update.emit(" in worker and "str(fields.get(\"status\", \"\"))" in worker)
    check("C5 v4.292 的 connect 仍在",
          "self.worker.board_update.connect(self.chat_panel.on_member_status)" in ui)
    check("C5b 直连不用 lambda", "lambda" not in
          "".join(l for l in ui.splitlines() if "on_member_status" in l))
    check("C6 首句逻辑只在对话页（引擎/状态条零改动）",
          "member_line_summary" not in worker
          and "member_line_summary" not in read("legion_status_widget.py"))
    check("C6b 对话页没引入 @ 寻址 / 成员常驻（本轮不做）",
          "to_member" not in chat and "mention" not in chat)
    check("C7 on_log / _sys_buffer 未被改写（v4.269 判据不翻）",
          "def on_log(self, text)" in chat and "_sys_buffer" in chat)


# ---------------------------------------------------------------- D 时序
def test_write_before_emit():
    worker = read("legion_worker.py")
    start = worker.find("    def _board(self, key, **fields):")
    assert start >= 0, "找不到 _board"
    seg = worker[start:start + 900]
    iw = seg.find("legion.board_update(")
    ie = seg.find("self.board_update.emit(")
    check("D1 _board 里先写盘再 emit（本方案成立的前提）",
          iw >= 0 and ie > iw, "iw=%d ie=%d" % (iw, ie))
    check("D2 写盘时确实传了 summary 字段", "summary=" in worker)
    check("D3 legion.board_update 把 summary 落进 nodes",
          "node.update({k: v for k, v in fields.items() if v is not None})"
          in read("legion.py"))


if __name__ == "__main__":
    test_summary_fn()
    test_timeline()
    test_invariants()
    test_write_before_emit()
    print("")
    print("PASS=%d FAIL=%d" % (PASS, FAIL))
    sys.exit(1 if FAIL else 0)
