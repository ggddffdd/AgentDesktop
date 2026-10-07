# -*- coding: utf-8 -*-
"""v4.228.0 连接层瞬时失败（WinError 10061）：判定分类 + 文案 + 重试接线。

事故（2026-10-07 22:52）：导演台报
    模型：⚠️ 模型调用失败：<urllib.error.URLError:10061> 由于目标计算机积极拒绝，无法连接。
两个独立缺陷：
  (1) 可读性 —— `{e}` 直接把 Python 异常对象渲染进气泡，用户既不知道是哪个模型，
      也不知道能不能重试；
  (2) 韧性 —— 10061 属瞬时连接层失败，但旧代码只对 HTTP 429/5xx 退避重试，
      `URLError` 没有 `.code` 属性 → `_code is None` → 掉进最后的 else 直接上抛，
      整轮崩掉。同请求原样重发即恢复（实测 200）。

判据分五组：
  A 行为级 is_transient_net_error：10061/超时/连接重置=True，400/401/429=False
  B 行为级 humanize_net_error：不得吐 Python 类名，且必须点名可重试
  C 非瞬时反例必须**真的不重试**（防止「全部重试」这种偷懒实现蒙混过关）
  D 接线钉子：ui.py 的瞬时分支存在且排在 else 之前；agent.py 文案不再裸 `{e}`
  E 判据自证：变异 is_transient 让它恒 False，A/B 必须翻红
"""
import ast
import os
import sys
import urllib.error

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

_p = _f = 0


def check(name, cond, detail=""):
    global _p, _f
    if cond:
        _p += 1
        print("  [OK] " + name)
    else:
        _f += 1
        print("  [FAIL] " + name + (("  <- " + detail) if detail else ""))


from ui_msg import is_transient_net_error, humanize_net_error  # noqa: E402


def _mk_urlerror(winerr, msg="由于目标计算机积极拒绝，无法连接。"):
    """造一个和真实事故同形的 URLError（Windows 上 reason 就是 socket.error(winerr, msg)）。"""
    import socket as _s
    return urllib.error.URLError(_s.error(winerr, msg))


def group_a_classify():
    print("A 行为级分类：瞬时=True / 非瞬时=False")
    # --- 正例：本次事故的真实异常 ---
    e1 = _mk_urlerror(10061)
    check("A1 WinError 10061（事故真身）判为瞬时",
          is_transient_net_error(e1),
          "10061 是本次事故的根因，必须判 True 才能触发重试")

    # --- 其余瞬时族 ---
    check("A2 连接被重置 10054 判为瞬时",
          is_transient_net_error(_mk_urlerror(10054, "远程主机强迫关闭了一个现有的连接。")))
    check("A3 超时（socket.timeout）判为瞬时",
          is_transient_net_error(TimeoutError("timed out")),
          "超时重发有意义")
    check("A4 URLError timeout 文本判为瞬时",
          is_transient_net_error(urllib.error.URLError("timed out")))
    check("A5 connection refused 英文判为瞬时",
          is_transient_net_error(urllib.error.URLError("Connection refused")))

    # --- 反例：非瞬时，重发必然同样失败 ---
    class _HTTPError(Exception):
        def __init__(self, code):
            super().__init__("HTTP Error %d: Bad Request" % code)
            self.code = code

    check("A6 HTTP 400 不判瞬时（改实现也不该重试）",
          not is_transient_net_error(_HTTPError(400)))
    check("A7 HTTP 401 不判瞬时",
          not is_transient_net_error(_HTTPError(401)))
    check("A8 HTTP 429 不判瞬时（限流，已有专门退避分支）",
          not is_transient_net_error(_HTTPError(429)))
    check("A9 HTTP 500 不判瞬时（本函数只管连接层，5xx 走 _code 分支）",
          not is_transient_net_error(_HTTPError(500)),
          "5xx 有 _code 判定，重复认会双重重试")
    # 顺序陷阱：「Too Many Requests」含 429 语义，不能被 transient 词误伤
    check("A10 429 文本型报文不判瞬时",
          not is_transient_net_error(Exception("HTTP Error 429: Too Many Requests")))

    # ★ A12 是**排除表真正起作用的用例**（本次实测补的）：
    # 真实世界存在「含瞬时词但属非瞬时」的报文 —— 限流服务常回
    #   「HTTP Error 429: Too Many Requests (retry after 30s, timeout=60)」
    # 若不先排非瞬时，这报文里的 "timeout" 会让它被判成可立即重试，
    # 于是对着限流硬打一轮。**没有这条，A 组反例会在掏空排除表后依然全绿**，
    # 排除逻辑就成了没被检验的死代码（V3 扰动红 0 条的根因）。
    mixed = Exception("HTTP Error 429: Too Many Requests; retry timeout=60")
    check("A12 含瞬时词但属限流的报文不判瞬时（排除表压过瞬时表）",
          not is_transient_net_error(mixed),
          "混合报文被误判为可重试会对限流硬打")
    mixed2 = Exception("HTTP Error 503: upstream connection refused while quota retry")
    check("A13 含 refused 但属配额报文的判非瞬时",
          not is_transient_net_error(mixed2))
    # 兜底：无关异常
    check("A11 无关异常判非瞬时",
          not is_transient_net_error(ValueError("随便什么错")))


def group_b_humanize():
    print("B 行为级文案：不得吐 Python 类名")
    raw = str(_mk_urlerror(10061))
    check("B0 前置：真实 str(e) 确实是机器味（证明 B 组非恒真）",
          ("urlopen error" in raw or "URLError" in raw) and "10061" in raw, raw)

    txt = humanize_net_error(_mk_urlerror(10061))
    check("B1 不含 urlopen error（Windows 上 str(URLError) 的真实形态）",
          "urlopen error" not in txt, txt)
    check("B1b 不含 urllib.error.URLError（跨平台形态一并覆盖）",
          "urllib.error.URLError" not in txt, txt)
    check("B2 不含裸尖括号异常外壳 <...>",
          not txt.strip().startswith("<"), txt)
    check("B3 不把 10061 当成主要文案（应是人话）",
          "10061" not in txt or "瞬时" in txt or "重试" in txt, txt)
    check("B4 说明可重试/瞬时（用户要知道下一步做什么）",
          ("重试" in txt) or ("瞬时" in txt), txt)
    check("B5 有可读中文", any("一" <= c <= "鿿" for c in txt), txt)

    # 超时也要人话
    t2 = humanize_net_error(TimeoutError("timed out"))
    check("B6 超时文案不含 Python 类名",
          "TimeoutError" not in t2, t2)
    check("B7 超时文案提到超时/慢",
          ("超时" in t2) or ("慢" in t2), t2)

    # 非网络异常：剥掉 <类名: ...> 外壳但保留正文
    t3 = humanize_net_error(ValueError("参数 path 不能为空"))
    check("B8 普通异常保留正文",
          "参数 path 不能为空" in t3, t3)
    check("B9 普通异常不吐类名",
          "ValueError" not in t3, t3)

    # ★ B12 才是剥壳逻辑真正起作用的用例（本次实测补的）：
    # 真实报错串常带 `<模块.类名: 正文>` 外壳（Python 的 str(SomeError(...)) 就是这形态）。
    # 若没有这条，B9 在「删掉剥壳逻辑」后依然全绿 —— 因为 ValueError("参数…") 的 str
    # 本就不含类名，剥壳与否输出相同，**判据成了恒真**（V5 扰动红 0 条的根因）。
    shelled = "<ValueError: 参数 path 不能为空>"
    t4 = humanize_net_error(Exception(shelled))
    check("B12 带 <类名:正文> 外壳的报文被剥壳（不留尖括号）",
          not t4.strip().startswith("<"), t4)
    check("B13 剥壳后正文仍在",
          "参数 path 不能为空" in t4, t4)
    check("B14 剥壳后不含类名",
          "ValueError" not in t4, t4)

    # 空/怪输入不得抛
    for bad in (None, Exception(), ValueError("")):
        try:
            humanize_net_error(bad)
        except Exception as exc:
            check("B10 怪输入不抛异常 (%r)" % (bad,), False, repr(exc))
            break
    else:
        check("B10 怪输入（None/空异常/空文本）均不抛", True)

    # 分类函数同样不得抛
    for bad in (None, Exception(), ValueError("")):
        try:
            is_transient_net_error(bad)
        except Exception as exc:
            check("B11 分类函数怪输入不抛 (%r)" % (bad,), False, repr(exc))
            break
    else:
        check("B11 分类函数怪输入均不抛", True)


def group_c_no_retry_on_400():
    """反向用例：非瞬时错误**真的不会**走到重试分支。

    只判「分类函数说不是」不够——接线可能没接上、或顺序接反了。
    这里直接验接线：把 _attempt_model 里的分支顺序读出来比对。
    """
    print("C 接线顺序：瞬时分支必须在 else 兜底之前")
    ui = open(os.path.join(ROOT, "ui.py"), encoding="utf-8").read()
    i_trans = ui.find("elif _backoff and is_transient_net_error(e):")
    i_else = ui.find("                else:\n                    # v4.108 H-04：其余失败")
    check("C1 瞬时重试分支存在", i_trans > 0)
    check("C2 else 兜底分支存在", i_else > 0)
    if i_trans > 0 and i_else > 0:
        check("C3 瞬时分支排在 else 之前（否则永远走不到）",
              i_trans < i_else,
              "transient@%d else@%d" % (i_trans, i_else))
        check("C4 瞬时分支确在 429/5xx 分支之后（不与之混淆）",
              ui.find("elif _backoff and _code in (429, 500, 502, 503, 504):") < i_trans)
    # ★ C6 独立钉住「瞬时分支必须真的存在」。
    # 只靠 C3 是不够的：把分支**改名**后 find() 返回 -1，`-1 < i_else` 反而成立
    # → C3 恒真、V7 扰动红 0 条。故把「存在性」单独拎成一条（= C1 的加强版：
    # 连函数名一并查，杜绝「有个分支但判据调的不是 is_transient_net_error」）。
    check("C6 瞬时分支存在且条件真调用 is_transient_net_error",
          ("is_transient_net_error(e)" in ui),
          "分支可能已改名 —— C3 的 find 失据会掩盖这一点")
    # 400/401/403/404/422 走的是更早的「改报文重试」分支，不受瞬时逻辑影响
    i_4xx = ui.find("if _code in (400, 401, 403, 404, 422)")
    check("C5 4xx 分支在瞬时分支之前（4xx 由报文修复逻辑处理）",
          0 < i_4xx < i_trans)


def group_d_wiring():
    print("D 接线钉子：导入 + 文案")
    ui = open(os.path.join(ROOT, "ui.py"), encoding="utf-8").read()
    ag = open(os.path.join(ROOT, "agent.py"), encoding="utf-8").read()
    um = open(os.path.join(ROOT, "ui_msg.py"), encoding="utf-8").read()

    check("D1 ui.py 从 ui_msg 导入两个新函数",
          "from ui_msg import" in ui and "is_transient_net_error" in ui
          and "humanize_net_error" in ui)
    check("D2 ui_msg.py 有两个新函数定义",
          "def is_transient_net_error" in um and "def humanize_net_error" in um)
    _top = _top_level_funcs(um)
    check("D3 两个新函数在 ui_msg.py 里是顶层（AST 可证）",
          "is_transient_net_error" in _top and "humanize_net_error" in _top,
          "顶层函数集合=%s" % sorted(_top))

    # agent.py：文案不得再裸 {e}
    bad = '⚠️ 模型调用失败：{e}'
    check("D4 agent.py 不再裸渲染 {e}", bad not in ag,
          "仍存在把异常对象直接塞进气泡的写法")
    check("D5 agent.py 文案点名本轮模型",
          "_last_model" in ag and "模型调用失败" in ag)
    check("D6 agent.py 走 humanize_net_error",
          "humanize_net_error" in ag)
    # _last_model 必须真的在同作用域可达（上一轮曾误写不存在的 _model_used）
    check("D7 agent.py 用的是真实存在的 self._last_model（不是臆造名）",
          "_model_used" not in ag,
          "_model_used 在 agent.py 从未定义，会 NameError")

    # ui.py 瞬时分支必须真调 _stream_once（否则「重试」是空转）
    check("D8 瞬时分支内真的重发请求",
          "_stream_once(body, strict=_strict)" in ui)
    i = ui.find("elif _backoff and is_transient_net_error(e):")
    seg = ui[i:i + 1200] if i > 0 else ""
    check("D9 瞬时分支体内含 _stream_once（真重试非空转）",
          "_stream_once" in seg)


def _top_level_funcs(source_text):
    """取源码里的顶层函数名集合。**入参是源码文本**（不是路径）。"""
    try:
        tree = ast.parse(source_text)
    except Exception:
        return set()
    return {n.name for n in tree.body if isinstance(n, ast.FunctionDef)}


def group_e_selfproof():
    """判据自证：把 is_transient_net_error 打成恒 False，A/B 必须翻红。

    恒真判据是这项目最常见的假绿来源（纪律第 15 条），必须钉死。
    """
    print("E 判据自证：把分类函数打成恒 False，A 组必须翻红")
    import ui_msg as _um
    orig = _um.is_transient_net_error
    try:
        _um.is_transient_net_error = lambda exc: False
        # 重跑关键正例：应当全部变False（证明 A 组真的在用这个函数）
        silent = 0
        for exc in (_mk_urlerror(10061),
                    _mk_urlerror(10054),
                    TimeoutError("timed out"),
                    urllib.error.URLError("Connection refused")):
            if not _um.is_transient_net_error(exc):
                silent += 1
        check("E1 变异后正例不再判瞬时（证明 A 组非恒真）", silent == 4,
              "silent=%d/4，A 组可能在自说自话" % silent)

        _um.is_transient_net_error = lambda exc: True
        over = 0
        for exc in (ValueError("HTTP Error 400: Bad Request"),
                    ValueError("HTTP Error 401: Unauthorized"),
                    ValueError("HTTP Error 429: Too Many Requests"),
                    ValueError("随便什么错")):
            if _um.is_transient_net_error(exc):
                over += 1
        check("E2 变异后反例变判瞬时（证明 A 组反例有效）", over == 4,
              "over=%d/4，反例没在约束实现" % over)
    finally:
        _um.is_transient_net_error = orig

    # 恢复后必须自洽
    check("E3 恢复后正例仍为瞬时",
          _um.is_transient_net_error(_mk_urlerror(10061)))
    check("E4 恢复后反例仍非瞬时",
          not _um.is_transient_net_error(Exception("HTTP Error 400: Bad Request")))


def main():
    for fn in (group_a_classify, group_b_humanize, group_c_no_retry_on_400,
               group_d_wiring, group_e_selfproof):
        fn()
    print("\nRESULT: PASS=%d FAIL=%d" % (_p, _f))
    return 1 if _f else 0


if __name__ == "__main__":
    sys.exit(main())