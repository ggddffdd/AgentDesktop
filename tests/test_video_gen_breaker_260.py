# -*- coding: utf-8 -*-
"""视频生成熔断（circuit breaker）— 回归判据 v4.260.3

复盘（2026-10-11）：生视频服务队列满(503)/参数错(400)时，Agent 会在对话循环里
反复调用 video_gen，每次再打一次网络、再失败一次，表现「状态一直闪、无结论」。

本套件断言两件事：
① 状态机：连续 2 次失败 → 熔断开路；任一次成功 → 复位（连续失败计数清零）。
② 短路回退：开路时 tool_video_gen **不再走网络层**（早在 `from core_agnes import`
之前就 return fail-closed 字符串，含「熔断」「HTML」字样，指向代码/HTML 动画方案）。

关键工程约束：本文件**绝不依赖 core_agnes / core 包**——它们是大哥本地 video-agent
的包，不在仓库依赖、CI 干净 Linux 环境装不了。v4.260.2 版的判据在模块级
`import core_agnes` 无 try/except，CI 上 ModuleNotFoundError 直接崩套件（PASS=0 FAIL=1）。
现改为：状态机+短路纯测 tools 内部函数（CI 必过）；仅当 core_agnes 可导入时，
再跑「真实网络失败→开路→第3次短路」的集成段（本地全验，CI 跳过不红）。
"""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import tools

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # 仓库根，与上方 sys.path 同源（CI Linux 无 D:\ 盘）
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
    # _video_gen_record_success 把连续失败计数清零 + 关闭开路，正好作复位
    tools._video_gen_record_success()

# ─────────────────────────────────────────────────────────
# ① 状态机：连续 2 次失败 → 开路；1 次失败不开路
# ─────────────────────────────────────────────────────────
reset_breaker()
tools._video_gen_record_failure()                       # 第 1 次失败
check("①a 仅 1 次失败尚未开路",
      tools._video_gen_breaker_open() is False)
tools._video_gen_record_failure()                       # 第 2 次失败 → 达阈值(2)
check("①b 连续 2 次失败 → 熔断开路",
      tools._video_gen_breaker_open() is True)

# ─────────────────────────────────────────────────────────
# ② 短路回退：开路时 tool_video_gen 不再走网络层
#    用哨兵 patch tools._agnes_creds（网络层入口，在短路 return 之后才被调用）；
#    若被触发说明没有短路。
# ─────────────────────────────────────────────────────────
reset_breaker()
tools._video_gen_record_failure()
tools._video_gen_record_failure()                       # 开路
network_hit = {"called": False}
_orig_creds = tools._agnes_creds
def _watch_creds(cfg):
    network_hit["called"] = True
    return "https://apihub.agnes-ai.cn/v1", ""
tools._agnes_creds = _watch_creds
try:
    r = tools.tool_video_gen({}, REPO, prompt="测试视频提示词", progress=lambda m: None)
finally:
    tools._agnes_creds = _orig_creds
check("②a 开路时返回 fail-closed 熔断回退（含「熔断」）",
      isinstance(r, str) and "熔断" in r, r[:80] if r else r)
check("②b 回退指引「代码/HTML 动画」方案（含「HTML」）",
      isinstance(r, str) and "HTML" in r, r[:80] if r else r)
check("②c 回退明确「不再继续请求」",
      isinstance(r, str) and ("停止" in r or "不再" in r or "已停止" in r), r[:80] if r else r)
check("②d 短路生效：网络层入口（_agnes_creds）根本没被调用",
      network_hit["called"] is False, network_hit)

# ─────────────────────────────────────────────────────────
# ③ 成功复位：开路后调用 _video_gen_record_success → 复位
# ─────────────────────────────────────────────────────────
reset_breaker()                                         # 已复位
tools._video_gen_record_failure()
tools._video_gen_record_failure()                       # 开路
tools._video_gen_record_success()                       # 模拟一次成功 → 复位
check("③a 成功后熔断复位（不再开路）",
      tools._video_gen_breaker_open() is False)
tools._video_gen_record_failure()                       # 复位后第 1 次失败
check("③b 复位后连续失败计数清零（再 1 次失败不触发开路）",
      tools._video_gen_breaker_open() is False)
reset_breaker()                                         # 收尾复位，保持干净

# ─────────────────────────────────────────────────────────
# ④ 集成段（仅本地可导入 core_agnes 时跑；CI 跳过，不红不哑）
#    制造成功/失败，验证「真实网络失败计入熔断 + 第3次短路」。
# ─────────────────────────────────────────────────────────
try:
    import core_agnes  # 大哥本地 video-agent 包，CI 无 → 走 except 跳过
    INTEG_OK = True
except Exception as e:
    INTEG_OK = False
    print("  [SKIP] core_agnes 不可导入（CI 干净环境无 video-agent core 包）→ "
          "集成段跳过；状态机+短路段已覆盖熔断行为，不冒充通过")

if INTEG_OK:
    def make_patch(raises=True):
        calls = {"n": 0}
        def _fake(self, **kw):
            calls["n"] += 1
            if raises:
                raise RuntimeError("simulated 503 video_queue_full")
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

    # ④a 连续 2 次失败 → 第 3 次短路（不再调网络）
    reset_breaker()
    calls = make_patch(raises=True)
    r1 = run_gen(); r2 = run_gen(); r3 = run_gen()
    check("④a-1 前两次均为服务失败字符串",
          ("视频生成失败" in (r1 or "")) and ("视频生成失败" in (r2 or "")), (r1, r2))
    check("④a-2 第 3 次不再调用网络（generate_video 仅 2 次）",
          calls["n"] == 2, calls["n"])
    check("④a-3 第 3 次返回 fail-closed 熔断回退（含「熔断」「HTML」）",
          isinstance(r3, str) and "熔断" in r3 and "HTML" in r3, r3)

    # ④b 成功复位后能再次成功
    reset_breaker()
    calls = make_patch(raises=False)
    s1 = run_gen()
    check("④b-1 成功调用返回交付物元组",
          isinstance(s1, tuple) and len(s1) == 3 and s1[1] == "video", s1)
    calls = make_patch(raises=True)
    sf = run_gen()   # 成功后的第 1 次失败
    check("④b-2 成功复位后第 1 次失败不触发开路（仍调用网络）",
          calls["n"] == 1, calls["n"])
    check("④b-3 成功复位后第 1 次失败是服务失败字符串（非熔断）",
          isinstance(sf, str) and "视频生成失败" in sf and "熔断" not in sf, sf)
    reset_breaker()

print("\n结果：PASS=%d  FAIL=%d" % (PASS, FAIL))
sys.exit(1 if FAIL else 0)
