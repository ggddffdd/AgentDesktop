# -*- coding: utf-8 -*-
"""视频生成熔断（circuit breaker）— 回归判据 v4.260.2

复盘（2026-10-11）：生视频服务队列满(503)/参数错(400)时，Agent 会在对话循环里
反复调用 video_gen，每次再打一次网络、再失败一次，表现「状态一直闪、无结论」。

本套件验的是**行为**：
① 连续 2 次失败 → 第 3 次调用**不再打网络**、直接返回 fail-closed 回退
   （含「熔断」「HTML」字样，指向代码/HTML 动画方案）；
② 任一次成功 → 熔断复位（之后仅 1 次失败不会触发开路）；
③ 复位后能正常再次成功。

纯行为级（用 monkeypatch 替换 core_agnes.AgnesClient.generate_video 制造成败），
不盯源码字符串，改坏网络分支必红。
"""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import tools

REPO = r"D:\小臭玩AI\deepseek-desktop"
tools.WORKSPACE_DIR = REPO

PASS = FAIL = 0

def check(name, cond, extra=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print("  [PASS]", name)
    else:
        FAIL += 1
        print("  [FAIL]", name, ("| " + str(extra)) if extra else "")

def reset_breaker():
    b = getattr(tools, "_VIDEO_GEN_BREAKER", None)
    if isinstance(b, dict):
        b["consecutive_fail"] = 0
        b["open"] = False
        b["open_at"] = 0.0

def make_patch(raises=True):
    """把 core_agnes.AgnesClient.generate_video 换成可控桩，返回调用计数。"""
    import core_agnes
    calls = {"n": 0}
    def _fake(self, **kw):
        calls["n"] += 1
        if raises:
            raise RuntimeError("simulated 503 video_queue_full")
        # 成功路径：在 dest_path 写一个 ≥10KB 的合法文件，让落盘校验通过
        dest = kw.get("dest_path")
        if dest:
            with open(dest, "wb") as f:
                f.write(b"\x00" * (20 * 1024))
        self.last_source = "agnes"
        return dest
    core_agnes.AgnesClient.generate_video = _fake
    return calls

def run_gen():
    return tools.tool_video_gen({}, REPO, prompt="测试视频提示词", progress=lambda m: None)

# ── ① 连续 2 次失败 → 第 3 次短路 ──
reset_breaker()
calls = make_patch(raises=True)
r1 = run_gen()
r2 = run_gen()
r3 = run_gen()
check("①a 前两次均为服务失败字符串",
      ("视频生成失败" in (r1 or "")) and ("视频生成失败" in (r2 or "")),
      (r1, r2))
check("①b 第 3 次不再调用网络（generate_video 仅 2 次）",
      calls["n"] == 2, calls["n"])
check("①c 第 3 次返回 fail-closed 熔断回退",
      isinstance(r3, str) and "熔断" in r3 and "HTML" in r3, r3)
check("①d 第 3 次明确「不再继续请求」",
      isinstance(r3, str) and ("停止" in r3 or "不再" in r3 or "已停止" in r3), r3)

# ── ② 成功复位：1 次失败不触发开路 ──
reset_breaker()
calls = make_patch(raises=True)
r_ok = run_gen()        # 1 次失败（calls=1）
# 此时不应开路：再来一次失败仍会真正打到网络
r_ok2 = run_gen()
check("② 仅 1 次失败后未开路（第 2 次仍真正调用网络）",
      calls["n"] == 2, calls["n"])

# ── ③ 成功复位后能再次成功 ──
reset_breaker()
calls = make_patch(raises=False)
s1 = run_gen()          # 成功
check("③a 成功调用返回交付物元组",
      isinstance(s1, tuple) and len(s1) == 3 and s1[1] == "video", s1)
# 成功后下一次失败不应开路（连续失败计数已清零）
calls = make_patch(raises=True)
sf = run_gen()          # 成功后的第 1 次失败
check("③b 成功复位后第 1 次失败不触发开路（仍调用网络）",
      calls["n"] == 1, calls["n"])
check("③c 成功复位后第 1 次失败是服务失败字符串（非熔断）",
      isinstance(sf, str) and "视频生成失败" in sf and "熔断" not in sf, sf)

print("\n结果：PASS=%d  FAIL=%d" % (PASS, FAIL))
sys.exit(1 if FAIL else 0)
