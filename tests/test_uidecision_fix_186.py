"""批 3（P1-3/4/5 ui 决策链）回归探针（v4.186.0 审查修复）。"""
import io
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

_p = _f = 0


def check(name, cond, detail=""):
    global _p, _f
    if cond:
        _p += 1
        print("  PASS %s %s" % (name, ("-> " + str(detail)[:60]) if detail else ""))
    else:
        _f += 1
        print("  FAIL %s %s" % (name, ("-> " + str(detail)[:60]) if detail else ""))


def main():
    print("=== [A] P1-3: force_tool 优先于 guard（源码契约）===")
    def has_stmt(path, stmt):
        with io.open(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), path),
                     "r", encoding="utf-8-sig") as f:
            return any(l.strip() == stmt for l in f)
    check("guard 分支带 not force_tool 条件",
          has_stmt("ui.py", "if _guard_block and not force_tool:"))
    check("guard 反盖说明注释存在",
          has_stmt("ui.py", "# v4.186.0（P1-3 修）：已验证在工具表内的 force_tool 优先级高于 guard 拦截。"))
    check("elif force_tool 分支仍在（优先级链完整）",
          has_stmt("ui.py", "elif force_tool:"))

    print("=== [B] P1-4: _needs_tool_intent 跳过 _internal 消息 ===")
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication(sys.argv)
    import ui as u

    w = u.ChatWindow.__new__(u.ChatWindow)
    # 真实用户句（含搜索词，会判 True）+ 末尾注入一条 _internal nudge
    real = [{"role": "user", "content": "帮我搜索一下今天的新闻"},
            {"role": "assistant", "content": "好的我来查"},
            {"role": "user", "content": "【系统提醒】请立即真实调用工具：搜索/写文件/读取文件，不要只说不做。", "_internal": True}]
    r1 = w._needs_tool_intent(real)
    check("真实句仍判工具意图（不误伤）", r1 is True, r1)
    # 只有 _internal 消息（极端：整轮只剩内部注入）→ 不该拿 nudge 文本当用户句
    only_internal = [{"role": "user", "content": "【系统提醒】请立即真实调用工具：搜索/写文件", "_internal": True}]
    r2 = w._needs_tool_intent(only_internal)
    check("纯 _internal 消息不触发工具意图（修复前 nudge 文本必命中『搜索/写文件』）", r2 is False, r2)
    # 对照：无 _internal 标记的同文本仍判 True（判据本身没坏）
    plain = [{"role": "user", "content": "【系统提醒】请立即真实调用工具：搜索/写文件"}]
    r3 = w._needs_tool_intent(plain)
    check("对照组：同文本无 _internal 标记仍判 True", r3 is True, r3)

    print("=== [C] P1-5: 发送前最终剥离 _internal（源码契约）===")
    check("最终剥离语句存在",
          has_stmt("ui.py", "if any(isinstance(m, dict) and \"_internal\" in m"))
    check("剥离重建语句存在",
          has_stmt("ui.py", "body[\"messages\"] = ["))
    # 运行时验证剥离逻辑本身（复刻同款表达式）
    body_msgs = [
        {"role": "user", "content": "hi"},
        {"role": "user", "content": "【系统强制指令】…", "_internal": True},
        {"role": "assistant", "content": "ok"},
    ]
    stripped = [{k: v for k, v in m.items() if k != "_internal"}
                if isinstance(m, dict) else m for m in body_msgs]
    check("剥离后无 _internal 键", all("_internal" not in m for m in stripped))
    check("剥离后内容保留", stripped[1]["content"].startswith("【系统强制指令】") and len(stripped) == 3)

    print()
    print("汇总：PASS=%d FAIL=%d" % (_p, _f))
    return 0 if _f == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
