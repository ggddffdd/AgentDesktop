# -*- coding: utf-8 -*-
"""导演台「项目隔离」回归（审查 #4 / v4.168.0 收尾）。

要防的事故：**已经重置成新项目，旧项目的后台任务晚到，往新卡片/新状态写数据。**
典型表现是「我刚清空项目，怎么又冒出一张上一部片的关键帧」。

收口手段（五步，顺序不能换）：
  ① 广播取消   —— 让还在跑的尽早在检查点退出（协作式，不杀线程）
  ② 断开回调   —— 即便它跑完，也碰不到 UI
  ③ 标记孤儿   —— 留下"谁还在收尾"的痕迹，便于排查
  ④ 清空引用池 —— 新项目不再持有旧线程
  ⑤ 项目令牌   —— 重置即换发；漏网的迟到结果被令牌校验丢弃

本套件用 AST 抽出 `_cancel_all_director_bg` **真源码**配合桩对象跑行为，
外加源码契约钉住「换发令牌 → 再收口」的顺序与两处回调守卫。

不导入 director_panel（它依赖 PySide6）。
"""
import ast
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SRC = (ROOT / "director_panel.py").read_text(encoding="utf-8-sig")
TREE = ast.parse(SRC)
LINES = SRC.splitlines()

_p = _f = 0


def check(name, cond, detail=""):
    global _p, _f
    if cond:
        _p += 1
        print(f"  [PASS] {name}")
    else:
        _f += 1
        print(f"  [FAIL] {name}" + (f" —— {detail}" if detail else ""))


def _dedent(seg):
    ls = seg.splitlines()
    if not ls:
        return seg
    ind = len(ls[0]) - len(ls[0].lstrip())
    return "\n".join(l[ind:] if l.strip() else l for l in ls)


def _func_src(name):
    for node in ast.walk(TREE):
        if isinstance(node, ast.FunctionDef) and node.name == name:
            return _dedent(ast.get_source_segment(SRC, node))
    return ""


class _Th:
    """假后台线程：只记录被调了什么。"""

    def __init__(self, running=False, disconnect_raises=False):
        self.cancelled = 0
        self.disconnected = 0
        self.running = running
        self.disconnect_raises = disconnect_raises

    def cancel(self):
        self.cancelled += 1

    def disconnect(self):
        if self.disconnect_raises:
            raise RuntimeError("信号已断开")
        self.disconnected += 1

    def isRunning(self):
        return self.running


class _App:
    def __init__(self, kf=(), bg=()):
        self.director_kf_threads = list(kf)
        self.director_bg_threads = list(bg)
        self.logs = []


def part_a():
    print("=== A) 五步收口：真行为 ===")
    src = _func_src("_cancel_all_director_bg")
    check("取到 _cancel_all_director_bg 源码", bool(src))
    t_kf1, t_kf2 = _Th(running=False), _Th(running=True)
    t_bg1 = _Th(running=True)
    app = _App(kf=[t_kf1, t_kf2], bg=[t_bg1])
    ns = {"_log": lambda a, t: app.logs.append(t)}
    exec(compile(src, "<dp._cancel_all_director_bg>", "exec"), ns)
    killed = ns["_cancel_all_director_bg"](app, reason="项目已重置", stage="reset")

    check("① 每个旧线程都收到 cancel()",
          t_kf1.cancelled == 1 and t_kf2.cancelled == 1 and t_bg1.cancelled == 1,
          f"{t_kf1.cancelled}/{t_kf2.cancelled}/{t_bg1.cancelled}")
    check("② 每个旧线程都断开了回调",
          t_kf1.disconnected == 1 and t_kf2.disconnected == 1 and t_bg1.disconnected == 1)
    check("③ 统计了仍在收尾的孤儿（2 个）", killed["zombie"] == 2, str(killed))
    check("④ 抽帧池已清空", app.director_kf_threads == [], str(app.director_kf_threads))
    check("④ 预览池已清空", app.director_bg_threads == [], str(app.director_bg_threads))
    check("计数正确", killed["kf"] == 2 and killed["bg"] == 1, str(killed))
    check("日志告知已隔离且不会写进新项目",
          any("不会再写进新项目" in t for t in app.logs), str(app.logs))

    print("\n-- A2 异常容错：disconnect 抛错不阻断其余步骤 --")
    t_bad = _Th(running=False, disconnect_raises=True)
    app2 = _App(kf=[t_bad], bg=[])
    ns2 = {"_log": lambda a, t: app2.logs.append(t)}
    exec(compile(src, "<dp._cancel_all_director_bg>", "exec"), ns2)
    ok = True
    try:
        ns2["_cancel_all_director_bg"](app2)
    except Exception as e:
        ok = False
        print("      raised:", e)
    check("不抛穿", ok)
    check("cancel 仍执行了", t_bad.cancelled == 1)
    check("池仍被清空", app2.director_kf_threads == [])

    print("\n-- A3 空池/缺属性不炸 --")
    app3 = _App(kf=[], bg=[])
    ns3 = {"_log": lambda a, t: None}
    exec(compile(src, "<dp._cancel_all_director_bg>", "exec"), ns3)
    try:
        r = ns3["_cancel_all_director_bg"](app3)
        check("空池返回全零", r == {"kf": 0, "bg": 0, "zombie": 0}, str(r))
    except Exception as e:
        check("空池返回全零", False, str(e))

    class _Bare:
        pass

    try:
        r3 = ns3["_cancel_all_director_bg"](_Bare())
        check("连属性都没有也返回全零", r3 == {"kf": 0, "bg": 0, "zombie": 0}, str(r3))
    except Exception as e:
        check("连属性都没有也返回全零", False, str(e))


def part_b():
    print("\n=== B) 重置的顺序：先换令牌，再收口 ===")
    i = next(i for i, l in enumerate(LINES) if l.startswith("def _director_reset("))
    j = i
    while j + 1 < len(LINES) and not (LINES[j + 1].strip()
                                      and not LINES[j + 1].startswith(" ")):
        j += 1
    body = "\n".join(LINES[i:j])
    p_tok = body.find("set_project_token(")
    p_cancel = body.find("_cancel_all_director_bg(")
    check("重置里换了项目令牌", p_tok >= 0, body[:200])
    check("重置里调了统一收口", p_cancel >= 0, body[:200])
    check("换令牌早于收口（新身份已确立再清旧线程）", 0 <= p_tok < p_cancel,
          f"token@{p_tok} cancel@{p_cancel}")
    check("重置里也 cancel_job 了主任务", "cancel_job(" in body)
    check("重置里断开了主线程回调", "th.disconnect()" in body)
    check("重置清空了 pipeline 引用", "app.director_pipeline = None" in body)


def part_c():
    print("\n=== C) 迟到结果守卫（两处回调）===")
    check("_kick_kf_queue 有令牌守卫",
          "_guarded_" in _func_src("_kick_kf_queue"))
    bg = _func_src("_kick_bg")
    check("_kick_bg 有令牌守卫", "_guarded_" in bg)
    check("_kick_bg 在**启动线程前**取令牌快照", bg.find("_tok = project_token()")
          < bg.find("th.start()"), bg)
    check("守卫比对失败即丢弃",
          "if _t != project_token():" in bg and "return" in bg)
    kf = _func_src("_kick_kf_queue")
    check("抽帧队列同样快照 + 比对",
          "project_token()" in kf and "!=" in kf)

    print("\n-- C2 行为仿真：令牌变了就不写 UI --")
    token = {"v": "p0"}

    def _make_guard(on_done):
        _t = token["v"]

        def _guarded(r, e):
            if _t != token["v"]:
                return
            on_done(r, e)
        return _guarded

    hits = []
    g = _make_guard(lambda r, e: hits.append(r))
    g(1, None)
    check("同项目：回调生效", hits == [1], str(hits))
    token["v"] = "p1"          # 换项目
    g(2, None)
    check("换项目后：迟到结果被丢弃", hits == [1], str(hits))

    print("\n-- C3 远端接回横幅也走受守卫的通道 --")
    seg = _func_src("_maybe_offer_remote_resume")
    check("接回用 _kick_bg（带令牌守卫）", "_kick_bg(app, _work, _done" in seg)
    check("接回不在后台线程直接碰 UI", "on_clip=lambda" not in seg)
    check("接回前先看有没有待接回任务", "pending_remote_shots()" in seg)
    check("放弃按钮走 abandon_remote_clips", "abandon_remote_clips()" in seg)


def part_d():
    print("\n=== D) 会话与工程隔离 ===")
    check("_clear_session 存在于重置路径", "_clear_session(app)" in SRC)
    check("重置会清 director_final_path", "app.director_final_path = None" in SRC)
    check("重置会清 stale 标脏表", "_director_stale = {" in SRC)
    # 工程 manifest 不随重置删除（产物要留在磁盘上）
    i = next(i for i, l in enumerate(LINES) if l.startswith("def _director_reset("))
    j = i
    while j + 1 < len(LINES) and not (LINES[j + 1].strip()
                                      and not LINES[j + 1].startswith(" ")):
        j += 1
    body = "\n".join(LINES[i:j])
    check("重置**不**删工程目录（素材必须留在磁盘）",
          "rmtree" not in body and "shutil.rmtree" not in body)


def main():
    part_a()
    part_b()
    part_c()
    part_d()
    print(f"\n汇总：PASS={_p} FAIL={_f}")
    return 0 if _f == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
