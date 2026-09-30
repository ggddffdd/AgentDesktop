"""批 E（P2-14/15/16/17 + P3×4，main/release_check/agent/ui 线）回归（v4.188 批 E 修复）。

覆盖：
  [A] P2-14 早期 crash hook（PySide6 import 前装上，import 阶段崩溃可落盘）
  [B] P2-15 密钥扫描三缺口（无引号风格 / 16 位短 key / .log .db .sqlite 后缀）
  [C] P2-16 退出路径（gateway terminate+wait 收尸 / obsidian worker 有界等待）
      + P3 gateway py launcher 兜底
  [D] P2-17 版本门禁（两段号正则 / 数值元组取 max / README 无版本改 fail）
      + P3 APP_BUILD_DATE 新鲜度校验
  [E] P3 _user_refuses_tools 过滤 _internal 注入消息
  [F] P3 agent 词表归一（_REF_KW 并集化 / _META_VERBS 权威引用 / _DISCUSS_KW 改名）
  [G] 四文件语法编译
"""
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
    os.chdir(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    tmp = tempfile.mkdtemp(prefix="fix188_e_")

    print("=== [A] P2-14 早期 crash hook ===")
    with open("main.py", encoding="utf-8") as fh:
        msrc = fh.read()
    _i_hook = msrc.find("sys.excepthook = _early_dump")
    _i_qt = msrc.find("from PySide6.QtWidgets import QApplication")
    check("A1 hook 先于 import PySide6（源码序）",
          0 < _i_hook < _i_qt, f"hook@{_i_hook} qt@{_i_qt}")

    import main as main_mod
    check("A2 import main 后早期 hook 生效",
          sys.excepthook is main_mod._early_dump, sys.excepthook)

    _orig_dir = main_mod._EARLY_LOG_DIR
    try:
        main_mod._EARLY_LOG_DIR = os.path.join(tmp, "logs")
        try:
            raise ValueError("early_hook_probe")
        except ValueError:
            et, ev, tb = sys.exc_info()
        sys.excepthook(et, ev, tb)
        logp = os.path.join(main_mod._EARLY_LOG_DIR, "app.log")
        with open(logp, encoding="utf-8") as fh:
            body = fh.read()
        check("A3 import 阶段崩溃可落盘",
              "early_hook_probe" in body and "ValueError" in body)
    finally:
        main_mod._EARLY_LOG_DIR = _orig_dir

    print("=== [B] P2-15 密钥扫描三缺口 ===")
    import release_check as rc

    def hit(data):
        return [lbl for rx, lbl in rc.SECRET_PATTERNS if rx.search(data)]

    check("B1 无引号 shell/env 风格命中",
          bool(hit(b"api_key=abcd1234efgh5678")), hit(b"api_key=abcd1234efgh5678"))
    check("B2 带引号 16 位短 key 命中",
          bool(hit(b'api_key = "abcd1234efgh5678"')))
    check("B3 纯字母无数字不误伤",
          not hit(b"access_token = ABCDEFGHIJKLMNOP"))
    check("B4 变量引用不误伤",
          not hit(b"api_key = get_api_key()"))
    check("B5 后缀含 .log/.db/.sqlite",
          all(s in rc.SCAN_SUFFIX for s in (".log", ".db", ".sqlite")))

    bad = os.path.join(tmp, "creds.log")
    with open(bad, "wb") as fh:
        fh.write(b"config dump: api_key=zzz999yyy888xxx7\n")
    hits = rc._scan_secrets(tmp)
    check("B6 .log 集成命中", any("creds.log" in h for h in hits), hits[:3])

    print("=== [C] P2-16 退出路径 + gateway 兜底（源码断言） ===")
    check("C1 gateway terminate 后 wait 收尸", "gp.wait(timeout=3)" in msrc)
    check("C2 obsidian worker 有界等待", "_ow.wait(2000)" in msrc)
    check("C3 gateway py launcher 兜底", 'shutil.which("py")' in msrc)

    print("=== [D] P2-17 版本门禁 + APP_BUILD_DATE ===")
    check("D1 _vkey 解析", rc._vkey("v4.186") == (4, 186)
          and rc._vkey("v4.187.0") == (4, 187, 0))
    check("D2 数值取 max（非字典序）",
          max(["v4.99.0", "v4.187.0", "v4.100.3"], key=rc._vkey) == "v4.187.0")
    check("D3 README 版本可读（真实）", rc._read_readme_version() is not None,
          rc._read_readme_version())
    check("D4 两段号正则",
          re.search(r"v4\.\d+(?:\.\d+)?", "我们升级到 v4.188").group(0) == "v4.188")

    n0 = len(rc._RESULTS)
    rc.check_version(scan_dist=False)
    new = {r[0]: r[1] for r in rc._RESULTS[n0:]}
    check("D5 README 与 config 一致", new.get("README 版本与 config 一致") is True,
          {k: v for k, v in new.items()})
    check("D6 APP_BUILD_DATE 新鲜且可读",
          new.get("APP_BUILD_DATE 新鲜（≤2 天）") is True,
          rc._read_build_date())

    print("=== [E] P3 _user_refuses_tools 过滤 _internal ===")
    from ui import ChatWindow
    msgs_refused_then_internal = [
        {"role": "user", "content": "今天不要用工具，纯聊天"},
        {"role": "user", "_internal": True,
         "content": "【系统】当前任务必须通过调用工具完成，帮我生成视频"},
    ]
    msgs_refused_then_real = [
        {"role": "user", "content": "今天不要用工具，纯聊天"},
        {"role": "user", "content": "帮我生成视频"},
    ]
    check("E1 internal 注入不顶掉用户拒绝",
          ChatWindow._user_refuses_tools(None, msgs_refused_then_internal) is True)
    check("E2 真用户新指令正常顶掉拒绝",
          ChatWindow._user_refuses_tools(None, msgs_refused_then_real) is False)
    check("E3 从未拒绝 → False",
          ChatWindow._user_refuses_tools(
              None, [{"role": "user", "content": "帮我生成视频"}]) is False)
    # v4.188 P3+（探针暴露的既有 bug）：拒绝句自我抵消——
    # 「不要调用工具」本身含 ACTION_KW 的「调用工具」，旧逻辑同句双命中
    # → last_refuse == last_action → 拒绝永不生效。修复后拒绝短语被剥离再查动作。
    check("E4 拒绝句自我抵消已修（「不要调用工具」→ True）",
          ChatWindow._user_refuses_tools(
              None, [{"role": "user", "content": "不要调用工具"}]) is True)
    check("E5 同句拒绝+新动作（动作胜出 → False）",
          ChatWindow._user_refuses_tools(
              None, [{"role": "user",
                      "content": "不要用工具，帮我生成个视频"}]) is False)

    print("=== [F] P3 agent 词表归一 ===")
    import agent
    import intent_guard as ig
    AW = agent.AgentWorker
    check("F1 _REF_KW ⊇ intent_guard 权威强引用表",
          all(k in AW._REF_KW for k in ig._STRONG_REF_KW),
          f"{len(ig._STRONG_REF_KW)} 词全含")
    check("F2 _REF_KW 保留 agent 独有宽词",
          all(k in AW._REF_KW for k in ("这件事", "你说的", "讨论", "评价")))
    check("F3 _META_VERBS 与权威表一致",
          tuple(AW._META_VERBS) == tuple(ig._META_VERBS))
    check("F4 _CLAUSE_SEP 与权威表一致",
          tuple(AW._CLAUSE_SEP) == tuple(ig._CLAUSE_SEP))
    check("F5 _DISCUSS_KW 已改名消歧",
          hasattr(AW, "_WEAK_DISCUSS_KW") and "聊聊" in AW._WEAK_DISCUSS_KW
          and not hasattr(AW, "_DISCUSS_KW"))

    aw = agent.AgentWorker(None, [], [])  # 轻量实例（判定只读类属性词表）
    r1 = aw._route_force_tool("生成个视频")
    check("F6 真指令不回归（→video_gen）", r1 == "video_gen", r1)
    r2 = aw._route_force_tool("分析下生成视频")
    check("F7 引用语境仍拦截（→None）", r2 is None, r2)
    r3 = aw._route_force_tool("我什么时候让你生成视频了")
    check("F8 强引用句仍拦截（→None）", r3 is None, r3)

    print("=== [G] 语法编译 ===")
    import py_compile
    ok_all = True
    for f in ("main.py", "release_check.py", "agent.py", "ui.py"):
        try:
            py_compile.compile(f, doraise=True)
        except Exception as ex:
            ok_all = False
            print("    compile fail: %s %r" % (f, ex))
    check("G1 py_compile 全过", ok_all)

    print()
    print("汇总：PASS=%d FAIL=%d" % (_p, _f))
    return 0 if _f == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
