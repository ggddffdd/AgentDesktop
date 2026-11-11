# -*- coding: utf-8 -*-
"""军团「成员发言身份」（v4.292.0）：board_update 接进对话时间线的行为级判据。

背景：legion_worker._board() 早就在每位成员交稿/失败/取消时 emit 了
(pid, node_key, role, wave, status)，但 legion_ui.py 只把它接到状态条，
对话时间线上成员是隐形的。本版在同一信号上再挂一个 5 参槽，只让成员
节点的终态出声；信号签名、引擎、数据结构一律不动。

判据分四组：
  A 行为   —— 四种终态各自成行、running 与生命周期节点零输出
  B 回放   —— _save_history → reload_for_project 后成员发言仍在（守 _ROLE_NAMES 坑）
  C 不变量 —— 零样式、不改信号签名、不碰 on_log/_sys_buffer、v4.269 三条 UX 判据不翻
  D 接线   —— 状态条那条 connect 仍在，新槽直连（不用 lambda）
"""
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

_SBX = tempfile.mkdtemp(prefix="xc_sbx_voice292_")
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
    """调用前后时间线逐字节不变 = 零输出。"""
    before = p.log.toPlainText()
    p.on_member_status(*args)
    return p.log.toPlainText() == before


def test_status_voice():
    p = _fresh()
    txt0 = p.log.toPlainText()

    # A1 done：方案点名的那条基准判据
    p.on_member_status("", "w1_m0", "写手", "2", "done")
    t = p.log.toPlainText()
    check("A1 done 出「写手 · 第 2 波 · 交稿」",
          "写手 · 第 2 波 · 交稿" in t, t)
    check("A1b 成员身份前缀在场", "成员 · " in t, t)
    check("A1c 历史里落的是「成员」role",
          any(m.get("role") == "成员" for m in p.history),
          str(p.history[-1:]))

    # A2 四种终态各自成行、措辞互不相同
    for status, word in (("error", "没跑通"),
                         ("incomplete", "没交上稿"),
                         ("cancelled", "已取消")):
        p.on_member_status("", "w2_m%d" % len(word), "审校", "2", status)
    t = p.log.toPlainText()
    check("A2 error 措辞", "没跑通" in t and t.count("没跑通") == 1, t)
    check("A2 incomplete 措辞", "没交上稿" in t and t.count("没交上稿") == 1, t)
    check("A2 cancelled 措辞", "已取消" in t and t.count("已取消") == 1, t)
    check("A2b 四种终态措辞互不相同",
          len({"交稿", "没跑通", "没交上稿", "已取消"}) == 4)
    check("A2c 每种终态各占一行",
          all(t.count(w) == 1 for w in ("交稿", "没跑通", "没交上稿", "已取消")), t)

    # A3 running 不进时间线（防刷屏）
    check("A3 running 零输出",
          _zero(p, "", "w3_m0", "选品官", "3", "running"), t)

    # A4 生命周期节点 / 计划 / 闸门不产生成员卡
    for key in ("run", "pm_plan", "gate_w2"):
        check("A4 %s 零输出" % key,
              _zero(p, "", key, "写手", "2", "done"))

    # A5 未识别状态静默忽略（不写死分支）
    check("A5 未知状态零输出",
          _zero(p, "", "w4_m0", "写手", "4", "totally_unknown"))

    # A6 空 role / 空 wave 兜底：legion_worker.py:603 的降级 emit 只传 status
    q = _fresh()
    q.on_member_status("", "w1_m0", "", "", "done")
    qt = q.log.toPlainText()
    check("A6 空 role/wave 仍出交稿", "成员 · 交稿" in qt, qt)
    check("A6b 不留空段", "第  波" not in qt and " ·  · " not in qt
          and "成员 ·  · " not in qt, qt)

    # A7 只缺其中一项也要稳
    r = _fresh()
    r.on_member_status("", "w1_m0", "写手", "", "cancelled")
    check("A7 缺 wave 出「写手 · 已取消」", "写手 · 已取消" in r.log.toPlainText(),
          r.log.toPlainText())
    s = _fresh()
    s.on_member_status("", "w1_m0", "", "5", "error")
    check("A7b 缺 role 出「第 5 波 · 没跑通」",
          "第 5 波 · 没跑通" in s.log.toPlainText(), s.log.toPlainText())

    check("A8 初始时间线为空（判据没被前置状态污染）", txt0.strip() == "")
    for w in (p, q, r, s):
        w.deleteLater()


def test_replay_and_style():
    p = _fresh()
    before = p.log.styleSheet()

    # B1 回放：专守「_ROLE_COLOR 漏加成员键 → _load_history():805 静默过滤」
    p._proj_id = "voice292_replay"
    p.on_member_status("", "w2_m0", "写手", "2", "done")
    p.on_member_status("", "w2_m1", "审校", "2", "error")
    p._save_history()
    disk = (Path(_SBX) / "legion_chat" / "voice292_replay.json")
    check("B1 历史已落盘", disk.is_file(), str(disk))
    check("B1b 盘上确有「成员」role",
          '"成员"' in disk.read_text(encoding="utf-8"))

    p.reload_for_project("voice292_other")
    check("B2 切走后时间线清空", "交稿" not in p.log.toPlainText(),
          p.log.toPlainText())
    p.reload_for_project("voice292_replay")
    rt = p.log.toPlainText()
    check("B3 回放：成员发言仍在", "写手 · 第 2 波 · 交稿" in rt, rt)
    check("B3b 回放：失败发言仍在", "审校 · 第 2 波 · 没跑通" in rt, rt)
    check("B3c 回放后 history 未丢成员",
          sum(1 for m in p.history if m.get("role") == "成员") == 2,
          str([m.get("role") for m in p.history]))

    # B4 零样式：改动前后 QSS 字节相等
    check("B4 零样式 styleSheet 字节相等", p.log.styleSheet() == before,
          repr(p.log.styleSheet())[:120])
    p.deleteLater()


def test_static_invariants():
    chat = read("legion_chat.py")
    ui = read("legion_ui.py")
    worker = read("legion_worker.py")

    # C1 白名单登记（回放判据的根）
    from legion_chat import _ROLE_NAMES, _ROLE_COLOR
    check("C1 _ROLE_NAMES 含「成员」", "成员" in _ROLE_NAMES,
          str(sorted(_ROLE_NAMES)))
    check("C1b _ROLE_COLOR 含「成员」", "成员" in _ROLE_COLOR)

    # C2 信号签名没被改（方案「不做清单」第一条）
    check("C2 board_update 仍是 5 参",
          "board_update = Signal(str, str, str, str, str)" in worker)
    check("C2b worker 未新增 emit 字段",
          "_board(self, key, **fields)" in worker)

    # C3 接线：新槽直连，状态条那条没被替换
    check("C3 状态条 connect 仍在",
          "board_update.connect(self.status_strip.on_board)" in ui)
    check("C3b 新槽直连（非 lambda）",
          "board_update.connect(self.chat_panel.on_member_status)" in ui)
    # 只扫「代码行」，别把注释里「不用 lambda」那句话算进来
    _connects = [ln.strip() for ln in ui.splitlines()
                 if "board_update.connect(" in ln]
    check("C3c 恰好两条直连、都不带 lambda",
          len(_connects) == 2
          and all("lambda" not in c for c in _connects)
          and any("chat_panel.on_member_status" in c for c in _connects)
          and any("status_strip.on_board" in c for c in _connects),
          str(_connects))

    # C4 不做清单：不动 on_log / _sys_buffer / fold_log_line
    seg = chat[chat.index("    def on_member_status"):
               chat.index("    def on_log(self, text):")]
    check("C4 新槽不调 on_log", "on_log(" not in seg)
    check("C4b 新槽不碰 _sys_buffer", "_sys_buffer" not in seg)
    check("C4c 新槽不碰 fold_log_line", "fold_log_line" not in seg)
    check("C4d 新槽走现有 _record 通道", 'self._record("成员"' in seg)
    check("C4e running 不进时间线的短路在场", "running" in seg and
          "if not verdict:" in seg)

    # C5 渲染仍是纯文本，零卡片 / 零颜色 / 零 QSS
    render = chat[chat.index("    def _render_card"):
                  chat.index("    def _render_divider")]
    check("C5 _render_card 无 setHtml/setStyleSheet",
          "setHtml" not in render and "setStyleSheet" not in render, render)
    check("C5b _render_card 不取色表", "_ROLE_COLOR" not in render)
    check("C5c prefix 只加了一条纯文本",
          '"成员": "成员 · "' in chat)
    check("C5d _render_card 仍走 _insert", "self._insert(" in render)


def test_ux269_untouched():
    """v4.269 三条 UX 判据不得因新增成员发言翻红。"""
    p = _fresh()
    p.on_log("  🧰 写手 本波工具集：run_command, write_file\n")
    check("D1 技术日志不进主时间线", "本波工具集" not in p.log.toPlainText())
    check("D1b 技术日志进详情", "本波工具集" in p.detail_text())
    p.on_log("  ⚠️ 成员 写手 执行失败（节点 failed）—— 本波交付不完整，日志很长"
             + "。" * 40 + "\n")
    t = p.log.toPlainText()
    check("D2 失败进主时间线", "执行失败" in t, t[:120])
    check("D2b 失败原文在详情", "failed" in p.detail_text())
    check("D3 主时间线没有被失败长文刷屏", ("。" * 12) not in t)

    # 成员发言与系统日志并存时，两条通道都不串
    p.on_member_status("", "w2_m0", "选品官", "2", "done")
    t2 = p.log.toPlainText()
    check("D4 并存：成员发言在场", "选品官 · 第 2 波 · 交稿" in t2, t2[:160])
    check("D4b 并存：失败仍在", "执行失败" in t2, t2[:160])
    check("D4c 并存：技术日志仍未漏出", "本波工具集" not in t2)
    p.deleteLater()


if __name__ == "__main__":
    test_status_voice()
    test_replay_and_style()
    test_static_invariants()
    test_ux269_untouched()
    print("\nPASS=%d FAIL=%d" % (PASS, FAIL))
    sys.exit(1 if FAIL else 0)
