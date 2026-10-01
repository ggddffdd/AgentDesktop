# -*- coding: utf-8 -*-
"""防编造三层机制回归（v4.189 批①②③）。

背景（2026-09-30 小臭 CHANGELOG 编造事件）：模型对账时把记忆里的真素材
（其他版本的功能 + 日志日期 + HTTP 4xx）拼装成「文件里的条目」煞有介事
对账，两次拆穿才承认。本探针钉死三层防线：
  [A] 批① 提示层——AGENT_SYS_APPEND 引用纪律（三件套/禁记忆补全）
  [B] 批② 工作流层——对账意图检测（双条件防误伤）+ 纪律注入接线
  [C] 批③ 解析器——read_file args 提路径 / 否定语境豁免
  [D] 批③ 端到端——编造版本号被标红 / 真引用不误伤 / 否定报告豁免 /
      读失败不作真值源 / 本轮边界 / 无引用语境不验
  [E] 渲染分支——audit_warn 四处专用渲染（主重放/导出 HTML/MD/DOCX）
  [G] 批④ 附件必读——agent 注入接线 + 行为回验（声称已读但没调 read_file 标红）
  [H] 拖拽添加附件——窗口级 dragEnter/drop + _ingest_attachment 统一入口
  [J] 批⑥ 引用纪律下放 tool result——read_file 首尾钉纪律 + 空/失败结果显式化
  [K] 批⑦ 读取区间空洞检测——乱序多窗口留缝漏读也能查出（v4.192）
  [F] 三文件语法编译
"""
import inspect
import json
import os
import re
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

_p = _f = 0


def check(name, cond, detail=""):
    global _p, _f
    if cond:
        _p += 1
        print("  PASS %s %s" % (name, ("-> " + str(detail)[:70]) if detail else ""))
    else:
        _f += 1
        print("  FAIL %s %s" % (name, ("-> " + str(detail)[:70]) if detail else ""))


def main():
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    os.chdir(root)
    tmp = tempfile.mkdtemp(prefix="af189_")

    print("=== [A] 批① AGENT_SYS_APPEND 引用纪律 ===")
    import config as C
    _ap = C.AGENT_SYS_APPEND
    check("A1 含「引用文件必带凭证」", "引用文件必带凭证" in _ap)
    check("A2 要求三件套（路径+行号+原文）",
          ("路径+行号+原文" in _ap) and ("三件套" in _ap))
    check("A3 禁止记忆补全（训练知识不算数）",
          ("凭记忆" in _ap) and ("不算数" in _ap) and ("从记忆补全" in _ap))
    check("A4 要求先 read_file 真读", "read_file" in _ap)

    print("=== [B] 批② 对账意图检测 + 注入接线 ===")
    import agent as AG

    W = AG.AgentWorker.__new__(AG.AgentWorker)  # 轻量实例，只调实例方法
    need = W._audit_ref_needed
    check("B1 「核对这份CHANGELOG」命中", need("帮我核对这份CHANGELOG", ""))
    check("B2 「对账更新日志」命中", need("对账一下更新日志", ""))
    check("B3 「核对这个文件的版本记录」命中", need("核对这个文件的版本记录", ""))
    check("B4 prev 句命中也触发", need("帮我看看", "刚才那份文件帮我核对一下"))
    check("B5 「核对这道题」不误伤（无文件语境）", not need("核对一下这道题的答案", ""))
    check("B6 普通聊天零触发", not need("今天天气怎么样", ""))
    check("B7 「查证 bug」不误伤（无文件语境）", not need("这个bug帮我查证一下", ""))
    check("B8 纪律常量四要素",
          ("read_file" in AG.AgentWorker._AUDIT_REF_INSTRUCTION)
          and ("结构清单" in AG.AgentWorker._AUDIT_REF_INSTRUCTION)
          and ("行号+原文摘录" in AG.AgentWorker._AUDIT_REF_INSTRUCTION)
          and ("禁止从你的记忆补全" in AG.AgentWorker._AUDIT_REF_INSTRUCTION))
    with open(os.path.join(root, "agent.py"), encoding="utf-8") as fh:
        _asrc = fh.read()
    check("B9 run() 注入接线（_audit_ref_needed 调用 + _internal 注入 + 查重）",
          ("self._audit_ref_needed(_cur_user, _prev_user)" in _asrc)
          and ('"content": self._AUDIT_REF_INSTRUCTION' in _asrc)
          and ("核对/对账文件内容" in _asrc))

    print("=== [C] 批③ 解析器：路径提取 / 否定豁免 ===")
    import ui as UI

    MW = UI.ChatWindow.__new__(UI.ChatWindow)
    xp = MW._extract_read_path
    check("C1 JSON dict path 提取",
          xp('{"path": "C:/x/CHANGELOG.md"}') == "C:/x/CHANGELOG.md")
    check("C2 JSON dict file 键提取", xp('{"file": "notes/a.md"}') == "notes/a.md")
    check("C3 裸路径字符串兜底",
          xp("C:/Users/xyb/Desktop/CHANGELOG.md") == "C:/Users/xyb/Desktop/CHANGELOG.md")
    _trunc = '{"path": "C:/very/long/path/' + "x" * 400 + '.md", "offset": 0, "limit"'
    check("C4 截断 JSON 正则兜底", xp(_trunc).startswith("C:/very/long/path/"))
    check("C5 空输入安全", xp("") == "" and xp(None) == "")
    ng = MW._negation_near
    check("C6 否定豁免：『没有v4.126』", ng("文件里没有v4.126这条记录", "v4.126"))
    check("C7 否定豁免：『不存在 v4.113』", ng("日志里不存在 v4.113 的条目", "v4.113"))
    check("C8 肯定引用不豁免：『v4.126 最可疑』", not ng("v4.126 最可疑", "v4.126"))
    check("C9 肯定引用不豁免：『记录了 v4.188.0』",
          not ng("CHANGELOG 里记录了 v4.188.0", "v4.188.0"))

    print("=== [D] 批③ 端到端：回验器行为 ===")
    # 真值文件：真版本 v4.188.0 在内，v4.126 不在
    truth = os.path.join(tmp, "CHANGELOG.md")
    with open(truth, "w", encoding="utf-8") as fh:
        fh.write("# 更新日志\n## v4.188.0 — 2026-09-30\n- 修复 A\n## v4.136 — 2026-09-10\n")

    class _Sess:
        def __init__(self, msgs):
            self.messages = msgs

    class _Store:
        def __init__(self, msgs):
            self._s = _Sess(msgs)
            self.active = lambda: self._s

    class _Chat:
        def __init__(self):
            self.items = []
            self.append = self.items.append

    def _mkmw(msgs):
        w = UI.ChatWindow.__new__(UI.ChatWindow)
        w.store = _Store(msgs)
        w.chat_view = _Chat()
        w._save_throttled = lambda: None
        return w

    def _tl_read(path, result="成功读取内容……"):
        return {"role": "tool_log", "name": "read_file",
                "args": json.dumps({"path": path}), "result": result}

    # D1 编造被标红
    w1 = _mkmw([
        {"role": "user", "content": "帮我核对这份更新日志"},
        _tl_read(truth),
        {"role": "assistant",
         "content": "文件里写着：v4.126 是 401 自动切换，v4.188.0 修复了 A。"},
    ])
    w1._audit_reply_citations()
    _warns1 = [m for m in w1.store.active().messages if m.get("role") == "audit_warn"]
    check("D1 编造版本号被标红", len(_warns1) == 1 and "v4.126" in _warns1[0]["content"],
          _warns1[0]["content"][:60] if _warns1 else "无警示")
    # D1b（v4.189 实战回归）：警示**只落 store**，不手动插 DOM——
    # 手动 chat_view.append 不推进 _rendered_msg_count，紧接着的 _flush_render
    # 会再渲染同一条 → 同一张警示卡出现两遍（用户实测截图两条一模一样的警示）。
    check("D1b 警示只落 store 不直插 DOM（防重复卡）",
          w1.chat_view.items == [],
          str(w1.chat_view.items)[:60])
    _od = inspect.getsource(UI.ChatWindow._on_agent_done)
    check("D1c _on_agent_done 末尾有 _flush_render 统一渲染",
          "_flush_render()" in _od
          and _od.index("_audit_reply_citations()") < _od.index("_flush_render()"))
    _acomb = (inspect.getsource(UI.ChatWindow._audit_reply_citations)
              + inspect.getsource(UI.ChatWindow._audit_attachment_reads))
    check("D1d 两个校验器内均无 chat_view.append（重复卡根因）",
          "self.chat_view.append(" not in _acomb)

    # D3b（v4.189 实战误报回归）：承认编造时说的是
    # 「v4.126、v4.113、v4.104、v4.128-130 我全都没在这个文件里见到」——
    # 口语否定式「没在…见到」原词表没覆盖 → 如实报告被标红。
    w3b = _mkmw([
        {"role": "user", "content": "核对这份更新日志"},
        _tl_read(truth),
        {"role": "assistant",
         "content": "v4.126、v4.113、v4.104、v4.128-130 这些版本号，"
                    "我全都没在这个文件里见到，是我凭记忆编造的。"},
    ])
    w3b._audit_reply_citations()
    check("D3b 「全都没在这个文件里见到」如实承认不误报",
          not any(m.get("role") == "audit_warn"
                  for m in w3b.store.active().messages))

    # D2 真引用零误伤
    w2 = _mkmw([
        {"role": "user", "content": "帮我核对这份更新日志"},
        _tl_read(truth),
        {"role": "assistant",
         "content": "文件里最新一节是 v4.188.0（2026-09-30），修复了 A。"},
    ])
    w2._audit_reply_citations()
    check("D2 真引用不误伤",
          not any(m.get("role") == "audit_warn"
                  for m in w2.store.active().messages))

    # D3 否定语境豁免（如实报告不存在）
    w3 = _mkmw([
        {"role": "user", "content": "有没有 v4.126 这条？核对一下"},
        _tl_read(truth),
        {"role": "assistant", "content": "文件里没有 v4.126 这条记录。"},
    ])
    w3._audit_reply_citations()
    check("D3 「没有v4.126」如实报告不被标红",
          not any(m.get("role") == "audit_warn"
                  for m in w3.store.active().messages))

    # D4 无引用语境（一般知识版本号）不验
    w4 = _mkmw([
        {"role": "user", "content": "读一下这个配置帮我看看"},
        _tl_read(truth),
        {"role": "assistant",
         "content": "建议升级到 v2.0 框架配合 Python 3.12 使用。"},
    ])
    w4._audit_reply_citations()
    check("D4 无引用语境（v2.0 一般知识）不触发",
          not any(m.get("role") == "audit_warn"
                  for m in w4.store.active().messages))

    # D5 read_file 失败不作真值源
    w5 = _mkmw([
        {"role": "user", "content": "核对更新日志"},
        _tl_read("C:/不存在.md", result="文件不存在：C:/不存在.md"),
        {"role": "assistant",
         "content": "文件里写着 v4.126 是 401 自动切换。"},
    ])
    w5._audit_reply_citations()
    check("D5 读失败文件不作真值源（不误标也不漏标）",
          not any(m.get("role") == "audit_warn"
                  for m in w5.store.active().messages))

    # D6 本轮边界：read_file 属上一轮（user 之前）→ 不算本轮真值源
    w6 = _mkmw([
        _tl_read(truth),
        {"role": "user", "content": "再核对一下更新日志"},
        {"role": "assistant",
         "content": "文件里写着 v4.126 是 401 自动切换。"},
    ])
    w6._audit_reply_citations()
    check("D6 上一轮的 read_file 不算本轮真值源",
          not any(m.get("role") == "audit_warn"
                  for m in w6.store.active().messages))

    # D7 _resolve_read_path：绝对路径直读 / 不存在返回空
    check("D7 路径解析：绝对路径命中", MW._resolve_read_path(truth) == truth)
    check("D7b 路径解析：不存在返回空串",
          MW._resolve_read_path(os.path.join(tmp, "nope.md")) == "")

    print("=== [E] audit_warn 渲染分支（四处） ===")
    with open(os.path.join(root, "ui.py"), encoding="utf-8") as fh:
        _usrc = fh.read()
    check("E1 主重放 _fmt_single_message 分支",
          'm.get("role") == "audit_warn"' in _usrc)
    check("E2 导出 HTML/MD/DOCX 三处 elif 分支",
          _usrc.count('elif m.get("role") == "audit_warn"') >= 3)
    check("E3 _on_agent_done 接线回验器", "self._audit_reply_citations()" in _usrc)

    print("=== [G] 批④ 附件必读 + 行为回验 ===")
    check("G1 纪律常量四要素",
          ("read_file" in AG.AgentWorker._ATTACH_READ_INSTRUCTION)
          and ("offset" in AG.AgentWorker._ATTACH_READ_INSTRUCTION)
          and ("禁止声称" in AG.AgentWorker._ATTACH_READ_INSTRUCTION)
          and ("属于编造" in AG.AgentWorker._ATTACH_READ_INSTRUCTION))
    _re_att = re.compile(AG.AgentWorker._ATTACH_MARK_RE_STR)
    check("G2 匹配 [非图片文件: incoming/CHANGELOG.md]",
          _re_att.search("[非图片文件: incoming/CHANGELOG.md]").group(1)
          == "incoming/CHANGELOG.md")
    check("G3 匹配 [文件: x] 与 [file: x]",
          bool(_re_att.search("[文件: incoming/notes.md]"))
          and bool(_re_att.search("[file: cfg.json]")))
    check("G4 不匹配 [文件不存在: x]",
          not _re_att.search("[文件不存在: incoming/x.md]"))
    check("G5 run() 注入接线（_ATTACH_READ_INSTRUCTION 注入 + 查重）",
          ('"content": self._ATTACH_READ_INSTRUCTION' in _asrc)
          and ("用户本轮发来了附件" in _asrc))

    # 行为回验端到端（复用 D 组桩）
    def _mkw4(msgs):
        return _mkmw(msgs)

    _asst_read_claim = ("读完 CHANGELOG，给您一份交叉核验："
                        "v4.126 是 401 自动切换。")
    # G6 声称已读但零调用 → 标红（本次事件原景重现）
    wA = _mkw4([
        {"role": "user", "content": "帮我核对一下\n[非图片文件: incoming/CHANGELOG.md]"},
        {"role": "assistant", "content": _asst_read_claim},
    ])
    wA._audit_attachment_reads()
    _wa = [m for m in wA.store.active().messages if m.get("role") == "audit_warn"]
    check("G6 声称已读+零调用 → 标红", len(_wa) == 1
          and "CHANGELOG.md" in _wa[0]["content"] and "疑似未读编造" in _wa[0]["content"],
          _wa[0]["content"][:50] if _wa else "无警示")
    # G7 本轮真调 read_file → 不标红
    wB = _mkw4([
        {"role": "user", "content": "[非图片文件: incoming/CHANGELOG.md]"},
        {"role": "tool_log", "name": "read_file",
         "args": json.dumps({"path": "incoming/CHANGELOG.md"}),
         "result": "# 更新日志\n## v4.188.0"},
        {"role": "assistant", "content": _asst_read_claim},
    ])
    wB._audit_attachment_reads()
    check("G7 真调 read_file → 不标红",
          not any(m.get("role") == "audit_warn"
                  for m in wB.store.active().messages))
    # G8 run_python 读也算真读
    wC = _mkw4([
        {"role": "user", "content": "[非图片文件: incoming/CHANGELOG.md]"},
        {"role": "tool_log", "name": "run_python",
         "args": json.dumps({"code": "open('incoming/CHANGELOG.md', encoding='utf-8').read()"}),
         "result": "..."},
        {"role": "assistant", "content": _asst_read_claim},
    ])
    wC._audit_attachment_reads()
    check("G8 run_python 读文件 → 不标红",
          not any(m.get("role") == "audit_warn"
                  for m in wC.store.active().messages))
    # G9 assistant.tool_calls 通道也算真读（args 截断不影响）
    wD = _mkw4([
        {"role": "user", "content": "[非图片文件: incoming/CHANGELOG.md]"},
        {"role": "assistant", "tool_calls": [
            {"function": {"name": "read_file",
                          "arguments": json.dumps({"path": "incoming/CHANGELOG.md"})}}]},
        {"role": "assistant", "content": _asst_read_claim},
    ])
    wD._audit_attachment_reads()
    check("G9 tool_calls 完整 arguments 通道 → 不标红",
          not any(m.get("role") == "audit_warn"
                  for m in wD.store.active().messages))
    # G10 不声称已读 → 不标红（交给批③版本回验兜底）
    wE = _mkw4([
        {"role": "user", "content": "[非图片文件: incoming/CHANGELOG.md]"},
        {"role": "assistant", "content": "我无法读取该文件，请确认格式。"},
    ])
    wE._audit_attachment_reads()
    check("G10 未声称已读 → 不标红",
          not any(m.get("role") == "audit_warn"
                  for m in wE.store.active().messages))
    # G11 无附件标记 → 直接返回
    wF = _mkw4([
        {"role": "user", "content": "普通问题"},
        {"role": "assistant", "content": "已读完那本书。"},
    ])
    wF._audit_attachment_reads()
    check("G11 无附件标记 → 不触发",
          not any(m.get("role") == "audit_warn"
                  for m in wF.store.active().messages))
    # G12 行为回验接线 _on_agent_done
    check("G12 _on_agent_done 接线", "self._audit_attachment_reads()" in _usrc)

    print("=== [H] 拖拽添加附件 ===")
    check("H1 ChatWindow 三方法存在",
          all(hasattr(UI.ChatWindow, m) for m in
              ("dragEnterEvent", "dropEvent", "_ingest_attachment")))
    check("H2 窗口开接收/子控件关吞事件（三处 setAcceptDrops）",
          ("self.setAcceptDrops(True)" in _usrc)
          and ("self.chat_view.setAcceptDrops(False)" in _usrc)
          and ("self.input_box.setAcceptDrops(False)" in _usrc))
    check("H3 加号与拖拽共用统一入口",
          "self._ingest_attachment(p)" in _usrc.replace(" ", "").replace(
              "self._ingest_attachment(p)", "self._ingest_attachment(p)")
          and _usrc.count("self._ingest_attachment(") >= 2)
    # 行为级：offscreen QApplication + 真 QDropEvent
    try:
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        from PySide6.QtWidgets import QApplication
        from PySide6.QtCore import QMimeData, QUrl, QPoint, Qt
        from PySide6.QtGui import QDragEnterEvent, QDropEvent
        _app = QApplication.instance() or QApplication([])
        # 临时文件当拖入目标
        dragfile = os.path.join(tmp, "拖拽测试.md")
        with open(dragfile, "w", encoding="utf-8") as fh:
            fh.write("# 拖入\n## v4.189.0\n")

        class _IB:
            def __init__(self):
                self.text = ""
            def insertPlainText(self, t):
                self.text += str(t)

        wH = UI.ChatWindow.__new__(UI.ChatWindow)
        wH._ingested = []
        wH._ingest_attachment = lambda p: wH._ingested.append(p)
        wH.input_box = _IB()
        wH._on_input_changed = lambda: None

        md = QMimeData()
        md.setUrls([QUrl.fromLocalFile(dragfile)])
        ent = QDragEnterEvent(QPoint(10, 10), Qt.CopyAction, md,
                              Qt.NoButton, Qt.NoModifier)
        wH.dragEnterEvent(ent)
        check("H4 dragEnter(文件URL) 接受", ent.isAccepted())
        drop = QDropEvent(QPoint(10, 10), Qt.CopyAction, md,
                          Qt.NoButton, Qt.NoModifier)
        wH.dropEvent(drop)
        _norm = lambda x: [os.path.normpath(i) for i in x]
        check("H5 drop(文件URL) → 统一入口收到路径",
              _norm(wH._ingested) == _norm([dragfile]) and drop.isAccepted(),
              str(wH._ingested))
        md2 = QMimeData()
        md2.setText("拖入的文本")
        ent2 = QDragEnterEvent(QPoint(5, 5), Qt.CopyAction, md2,
                               Qt.NoButton, Qt.NoModifier)
        wH.dragEnterEvent(ent2)
        drop2 = QDropEvent(QPoint(5, 5), Qt.CopyAction, md2,
                           Qt.NoButton, Qt.NoModifier)
        wH.dropEvent(drop2)
        check("H6 drop(文本) → 插入输入框不进附件",
              wH.input_box.text == "拖入的文本"
              and len(wH._ingested) == 1)
        md3 = QMimeData()
        md3.setUrls([QUrl("https://example.com/x.md")])
        drop3 = QDropEvent(QPoint(1, 1), Qt.CopyAction, md3,
                           Qt.NoButton, Qt.NoModifier)
        wH.dropEvent(drop3)
        check("H7 drop(远程URL) → 不当本地附件（isfile 过滤）",
              len(wH._ingested) == 1)
    except Exception as e:
        check("H4-H7 拖拽行为级", False, str(e)[:80])

    # ================= [I] 批⑤：部分读取回验 =================
    print("=== [I] 批⑤ 部分读取回验（读了 X% 却声称读全） ===")
    try:
        # 构造真实文件：49050 字符（复刻实战规模）
        big = os.path.join(tmp, "BIGLOG.md")
        with open(big, "w", encoding="utf-8") as f:
            f.write("头部说明\n" + ("版本条目行内容\n" * 6132))
        _total_len = len(open(big, encoding="utf-8").read())
        check("I0 构造大文件成功（>40000 字符）", _total_len > 40000,
              str(_total_len))

        def _tl_read_off(path, offset):
            return {"role": "tool_log", "name": "read_file",
                    "args": json.dumps({"path": path, "offset": offset}),
                    "result": "…内容…"}

        def _tl_read_range(path, offset, limit):
            return {"role": "tool_log", "name": "read_file",
                    "args": json.dumps({"path": path, "offset": offset,
                                        "limit": limit}),
                    "result": "…内容…"}

        # I1 原景重现：只读 0~8000（+ 续读到 20000）却说「整份读完」
        wI1 = _mkmw([
            {"role": "user", "content": "读完再跟我说"},
            _tl_read_off(big, 0),
            _tl_read_off(big, 8000),
            _tl_read_off(big, 16000),
            {"role": "assistant",
             "content": f"读完了，整份读完 {os.path.basename(big)}，"
                        "给您只基于真实内容的对账。"},
        ])
        wI1._audit_partial_reads()
        _wI1 = [m for m in wI1.store.active().messages
                if m.get("role") == "audit_warn"]
        check("I1 只读 41% 却声称整份读完 → 标红", len(_wI1) == 1,
              _wI1[0]["content"][:70] if _wI1 else "无警示")
        check("I1b 警示指出未覆盖区间（批⑦：不再是单一百分比）",
              bool(_wI1) and "未覆盖区间" in _wI1[0]["content"]
              and "~" in _wI1[0]["content"],
              _wI1[0]["content"][-60:] if _wI1 else "")

        # I2 真的读全了（连续覆盖到末尾，无洞）→ 不误伤
        wI2 = _mkmw([
            {"role": "user", "content": "读完再跟我说"},
            _tl_read_off(big, 0),
            _tl_read_off(big, 8000),
            _tl_read_off(big, 16000),
            _tl_read_off(big, 24000),
            _tl_read_off(big, 32000),
            _tl_read_off(big, 40000),
            _tl_read_range(big, 48000, _total_len - 48000),  # 覆盖到文件末尾
            {"role": "assistant", "content": "已整份读完 BIGLOG.md，清单如下。"},
        ])
        wI2._audit_partial_reads()
        check("I2 连续读全（无洞）→ 不误伤",
              not any(m.get("role") == "audit_warn"
                      for m in wI2.store.active().messages))

        # I2b 批⑦核心用例：乱序跳到末尾但中间有洞（旧 max 算法会漏报）
        wI2b = _mkmw([
            {"role": "user", "content": "读完再跟我说"},
            _tl_read_off(big, 0),
            _tl_read_off(big, _total_len - 2000),  # 直接跳读末尾
            {"role": "assistant", "content": "已整份读完 BIGLOG.md，清单如下。"},
        ])
        wI2b._audit_partial_reads()
        _wI2b = [m for m in wI2b.store.active().messages
                 if m.get("role") == "audit_warn"]
        check("I2b 批⑦：首尾读+中间大洞 → 仍标红（旧 max 算法会漏报）",
              len(_wI2b) == 1,
              _wI2b[0]["content"][:70] if _wI2b else "无警示（漏报=旧算法缺陷）")

        # I3 如实声明只读了一部分 → 豁免
        wI3 = _mkmw([
            {"role": "user", "content": "先看一部分"},
            _tl_read_off(big, 0),
            {"role": "assistant",
             "content": "我只读了前 8000 字符，还没读全 BIGLOG.md，"
                        "先给您部分结论。"},
        ])
        wI3._audit_partial_reads()
        check("I3 「只读了前 8000 字符，还没读全」如实声明 → 豁免",
              not any(m.get("role") == "audit_warn"
                      for m in wI3.store.active().messages))

        # I4 没声称读全（只说「看了一眼」）→ 不触发
        wI4 = _mkmw([
            {"role": "user", "content": "看一眼"},
            _tl_read_off(big, 0),
            {"role": "assistant", "content": "BIGLOG.md 开头是头部说明。"},
        ])
        wI4._audit_partial_reads()
        check("I4 未声称读全 → 不触发",
              not any(m.get("role") == "audit_warn"
                      for m in wI4.store.active().messages))

        # I5 小文件一次读完（无 offset）→ 不误伤
        small = os.path.join(tmp, "SMALL.md")
        with open(small, "w", encoding="utf-8") as f:
            f.write("短文件。")
        wI5 = _mkmw([
            {"role": "user", "content": "读它"},
            {"role": "tool_log", "name": "read_file",
             "args": json.dumps({"path": small}), "result": "短文件。"},
            {"role": "assistant", "content": "已整份读完 SMALL.md。"},
        ])
        wI5._audit_partial_reads()
        check("I5 小文件一次读完 → 不误伤",
              not any(m.get("role") == "audit_warn"
                      for m in wI5.store.active().messages))

        # I6 文件已被删除 → fail-open 不误报
        wI6 = _mkmw([
            {"role": "user", "content": "读它"},
            _tl_read_off(os.path.join(tmp, "GONE_不存在.md"), 0),
            {"role": "assistant", "content": "已整份读完 GONE_不存在.md。"},
        ])
        wI6._audit_partial_reads()
        check("I6 文件不存在 → fail-open 不误报",
              not any(m.get("role") == "audit_warn"
                      for m in wI6.store.active().messages))

        # I7 本轮只一个文件、用「这份文件」指代（不出现文件名）→ 仍标红
        wI7 = _mkmw([
            {"role": "user", "content": "读完再答"},
            _tl_read_off(big, 0),
            {"role": "assistant", "content": "这份文件我已整份读完，结论如下。"},
        ])
        wI7._audit_partial_reads()
        check("I7 「这份文件」指代（唯一文件）→ 仍标红",
              any(m.get("role") == "audit_warn"
                  for m in wI7.store.active().messages))

        _ui_src = inspect.getsource(UI.ChatWindow._on_agent_done)
        check("I8 _on_agent_done 已挂 _audit_partial_reads",
              "_audit_partial_reads()" in _ui_src)
    except Exception as e:
        check("I 组批⑤探针", False, str(e)[:120])

    print("=== [J] 批⑥ 引用纪律下放 tool result（v4.191） ===")
    try:
        import tools as TS
        _orig_ws = TS.WORKSPACE_DIR
        try:
            TS.WORKSPACE_DIR = tmp
            jf = os.path.join(tmp, "J_LOG.md")
            body = "".join("行%05d：内容填充。\n" % i for i in range(2000))
            with open(jf, "w", encoding="utf-8") as f:
                f.write(body)
            _n = len(body)

            # J1~J4 分段读（未读完）：首尾各钉一行纪律，尾部带未读百分比
            r1 = TS.tool_read_file(C.APP_DIR, jf, offset=0, limit=8000)
            check("J1 分段读：开头钉【读取纪律】", r1.startswith("[读取纪律]"), r1[:40])
            check("J2 分段读：原文未失真（紧跟纪律之后）",
                  (body[:200] in r1) and (r1.index(body[:200]) < 400))
            check("J3 分段读：尾部标未读百分比 + 续读 offset + 禁声称读全",
                  ("还剩" in r1) and ("offset=8000" in r1)
                  and ("禁止声称已读完整个文件" in r1))
            check("J4 分段读：真读到 → 不含 NOT FOUND", "RESULT NOT FOUND" not in r1)

            # J5 续读到末尾
            r2 = TS.tool_read_file(C.APP_DIR, jf, offset=_n - 1000, limit=8000)
            check("J5 续读到末尾：标「已读到文件末尾」+「全文已读全」",
                  ("已读到文件末尾" in r2) and ("全文已读全" in r2))

            # J6 小文件一次读全（原先无任何标记，v4.191 补上）
            small = os.path.join(tmp, "J_SMALL.md")
            with open(small, "w", encoding="utf-8") as f:
                f.write("短文件。")
            r3 = TS.tool_read_file(C.APP_DIR, small)
            check("J6 小文件一次读全：补标「本次已全部读入」",
                  ("本次已全部读入" in r3) and ("短文件。" in r3))

            # J7~J8 文件不存在 → NOT FOUND，但前缀不变（ui.py 批③判据不破）
            r4 = TS.tool_read_file(C.APP_DIR, os.path.join(tmp, "NOPE_不存在.md"))
            check("J7 文件不存在：带 RESULT NOT FOUND", "RESULT NOT FOUND" in r4, r4[-60:])
            check("J8 失败前缀不变（_READ_FAIL_PREFIX 判据仍成立）",
                  r4.startswith("文件不存在"), r4[:20])

            # J9 offset 超出 → 不再只是泛化错误
            r5 = TS.tool_read_file(C.APP_DIR, small, offset=99999)
            check("J9 offset 超出：带 RESULT NOT FOUND 且前缀不变",
                  ("RESULT NOT FOUND" in r5) and r5.startswith("offset="))

            # J10 越界路径
            r6 = TS.tool_read_file(C.APP_DIR,
                                   os.path.join(tempfile.gettempdir(), "evil_j.txt"))
            check("J10 越界拒绝：带 RESULT NOT FOUND 且前缀不变",
                  ("RESULT NOT FOUND" in r6) and r6.startswith("拒绝："), r6[:20])

            # J11 未提供路径
            check("J11 未提供路径：带 RESULT NOT FOUND",
                  "RESULT NOT FOUND" in TS.tool_read_file(C.APP_DIR, ""))
        finally:
            TS.WORKSPACE_DIR = _orig_ws
    except Exception as e:
        check("J 组批⑥探针", False, str(e)[:120])

    # ================= [K] 批⑦：读取区间空洞检测 =================
    print("=== [K] 批⑦ 读取区间空洞检测（v4.192） ===")
    try:
        # K1-K6 单元级：区间合并 + 缺口算法（直接测 ChatWindow 静态方法）
        from ui import ChatWindow as _CW
        _mg = _CW._merge_ranges
        _gp = _CW._find_gaps
        check("K1 相邻区间合并",
              _mg([(0, 100), (100, 200)]) == [(0, 200)])
        check("K2 重叠区间合并",
              _mg([(0, 150), (100, 200)]) == [(0, 200)])
        check("K3 乱序区间合并",
              _mg([(200, 300), (0, 100), (50, 250)]) == [(0, 300)])
        check("K4 无洞：连续覆盖",
              _gp([(0, 100), (100, 200)], 200) == [])
        check("K5 首尾读+中间大洞 → 检出中间缺口",
              _gp([(0, 100), (900, 1000)], 1000) == [(100, 900)])
        check("K6 未读到末尾 → 尾部缺口",
              _gp([(0, 100)], 1000) == [(100, 1000)])

        # K7-K9 行为级：用真实文件走 _audit_partial_reads
        k7 = os.path.join(tmp, "HOLE.md")
        with open(k7, "w", encoding="utf-8") as f:
            f.write(("内容段落行\n" * 2000))  # ≈12000 字符
        _l7 = len(open(k7, encoding="utf-8").read())
        _kb = os.path.basename(k7)

        def _tlk(path, off, lim):
            return {"role": "tool_log", "name": "read_file",
                    "args": json.dumps({"path": path, "offset": off,
                                        "limit": lim}),
                    "result": "…内容…"}

        # K7 乱序多窗口留缝（复刻本次实战：0~5000、8000~12000 漏了 5000~8000）
        wK7 = _mkmw([
            {"role": "user", "content": "读完再下结论"},
            _tlk(k7, 0, 5000),
            _tlk(k7, 8000, 4000),
            {"role": "assistant", "content": f"整份读完 {_kb}，结论如下。"},
        ])
        wK7._audit_partial_reads()
        _wK7 = [m for m in wK7.store.active().messages
                if m.get("role") == "audit_warn"]
        check("K7 乱序多窗口留缝漏读 → 标红并指出缺口区间",
              len(_wK7) == 1 and "5000~8000" in _wK7[0]["content"],
              _wK7[0]["content"][:80] if _wK7 else "无警示")

        # K8 有洞但如实声明「只读了部分」→ 豁免（不误伤诚实者）
        wK8 = _mkmw([
            {"role": "user", "content": "先看一部分"},
            _tlk(k7, 0, 5000),
            {"role": "assistant", "content": f"我只读了 {_kb} 前 5000 字符，还没读全。"},
        ])
        wK8._audit_partial_reads()
        check("K8 有洞但如实声明部分读 → 豁免",
              not any(m.get("role") == "audit_warn"
                      for m in wK8.store.active().messages))

        # K9 小洞（<200 字符）不报（防对齐误差误伤）
        wK9 = _mkmw([
            {"role": "user", "content": "读完再说"},
            _tlk(k7, 0, 5050),
            _tlk(k7, 5150, _l7),  # 缺口 5050~5150 = 100 字符
            {"role": "assistant", "content": f"整份读完 {_kb}。"},
        ])
        wK9._audit_partial_reads()
        check("K9 缺口 <200 字符 → 不误伤",
              not any(m.get("role") == "audit_warn"
                      for m in wK9.store.active().messages))
    except Exception as e:
        check("K 组批⑦探针", False, str(e)[:120])

    print("=== [F] 三文件语法编译 ===")
    import py_compile
    for f in ("config.py", "agent.py", "ui.py"):
        try:
            py_compile.compile(os.path.join(root, f), doraise=True)
            check("F %s 编译通过" % f, True)
        except Exception as e:
            check("F %s 编译通过" % f, False, str(e)[:60])

    print(f"\n=== ALL_{'_OK' if _f == 0 else 'FAIL'} === PASS={_p} FAIL={_f}")
    sys.exit(0 if _f == 0 else 1)


if __name__ == "__main__":
    main()
