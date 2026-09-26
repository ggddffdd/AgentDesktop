# -*- coding: utf-8 -*-
"""导演台「停止后可重试」与后台任务隔离的回归测试（审查 §1 / §4）。

钉住两件事：

**§1 停止后必须还能重试。**
  原实现用一个**永不复位**的全局布尔 `cancelled`：停止置 True 之后
  `regenerate_clip()` 第一行就 `return None`，用户只能「重置项目」，
  前面写好的剧本/分镜/关键帧全丢。
  现在：停止只取消**当前这一轮 job**；重试/续生成会 `begin_job()` 换发新令牌。

**§4 重置项目时后台任务要统一收口 + 隔离。**
  旧项目的抽帧/预览晚到，不得写进新项目。

同时做**源码契约检查**（静态）：防止将来有人把 `if self.cancelled:`
这种"永久布尔"读点又写回来。

无 Qt 依赖（video_pipeline 可轻量导入）。
"""

import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

_p = _f = 0


def check(name, cond, detail=""):
    global _p, _f
    if cond:
        _p += 1
        print(f"  [PASS] {name}")
    else:
        _f += 1
        print(f"  [FAIL] {name}" + (f" —— {detail}" if detail else ""))


def read(name):
    with open(os.path.join(ROOT, name), encoding="utf-8") as f:
        return f.read()


def main():
    import video_pipeline as vp

    print("=== 1) 构造与初始状态 ===")
    p = vp.VideoPipeline({}, ROOT)
    check("初始未取消", p._is_cancelled() is False)
    check("初始任务名空", p.job_state()["job"] == "")
    check("初始序号 0", p.job_state()["seq"] == 0)

    print("\n=== 2) cancel_job：停止只终结当前一轮 ===")
    p.begin_job("generate_all_clips")
    check("begin_job 后有任务名", p.job_state()["job"] == "generate_all_clips")
    check("begin_job 后未取消", p._is_cancelled() is False)

    first = p.cancel_job("用户点了停止", stage="user_stop")
    check("cancel_job 返回 True（首次生效）", first is True)
    check("★ 停止后 _is_cancelled() 为真", p._is_cancelled() is True)
    st = p.job_state()
    check("任务状态记录原因", st["reason"] == "用户点了停止", st)
    check("任务状态记录取消点", st["stage"] == "user_stop", st)

    again = p.cancel_job("再点一次")
    check("重复取消返回 False（幂等）", again is False)
    check("原因仍保留首次", p.job_state()["reason"] == "用户点了停止")

    print("\n=== 3) ★ 核心：停止之后还能重试（换发令牌即复位）===")
    p.begin_job("regenerate_clip#0")
    check("★ begin_job 之后不再视为已取消（这就是「可重试」）",
          p._is_cancelled() is False)
    check("任务序号递增（新旧任务可区分）", p.job_state()["seq"] == 2,
          p.job_state()["seq"])
    check("历史全局标志也被复位（兼容旧读点）",
          getattr(p, "cancelled", False) is False)

    print("\n=== 4) 兼容：外部直接置 cancelled=True 也能被复位 ===")
    p.cancelled = True                      # 模拟旧代码路径 / 旧 pipeline 桩
    check("外部置位后 _is_cancelled() 为真", p._is_cancelled() is True)
    p.begin_job("manual_retry")
    check("★ 换发令牌后复位（不再被永久粘住）", p._is_cancelled() is False)

    print("\n=== 5) regenerate_clip 不再被永久布尔卡死 ===")
    p.cancel_job("先停一次")
    check("停止后状态为已取消", p._is_cancelled() is True)
    p.shots = []                            # 空分镜 → 会走到 index 检查分支返回 None
    r = p.regenerate_clip(0)
    check("越界/空分镜仍返回 None", r is None, r)
    check("★ 但内部已换发令牌 → 状态恢复可用（不再永久卡死）",
          p._is_cancelled() is False)
    check("★ 任务名已更新为本次重试", "regenerate" in p.job_state()["job"],
          p.job_state()["job"])

    print("\n=== 6) 再停一次依然生效（令牌逐轮独立）===")
    p.cancel_job("第二次停", stage="user_stop")
    check("第二次停止同样生效", p._is_cancelled() is True)
    p.begin_job("third")
    check("第三次换发后仍可继续", p._is_cancelled() is False)

    print("\n=== 7) job_state 供 UI 显示「可从第 X 镜继续」===")
    p.shots = [1, 2, 3, 4]
    p.clip_paths = ["a.mp4", None, "c.mp4", None]
    st = p.job_state()
    check("报告已完成镜数", st["done_clips"] == 2, st)
    check("报告总镜数", st["total_shots"] == 4, st)

    print("\n=== 8) 源码契约：不得回退成「永久布尔」读点 ===")
    src = read("video_pipeline.py")
    # 只允许在 _is_cancelled / begin_job / cancel_job 内部出现 self.cancelled
    bad = []
    for m in re.finditer(r"if\s+self\.cancelled\b[^_]", src):
        line_no = src[:m.start()].count("\n") + 1
        line = src.split("\n")[line_no - 1].strip()
        if "_is_cancelled" not in line:
            bad.append(f"{line_no}: {line}")
    check("★ 没有裸 `if self.cancelled:` 读点（必须走 _is_cancelled()）",
          not bad, bad[:4])

    check("定义了 _is_cancelled", "def _is_cancelled(" in src)
    check("定义了 begin_job", "def begin_job(" in src)
    check("定义了 cancel_job", "def cancel_job(" in src)
    check("定义了 job_state", "def job_state(" in src)
    check("generate_all_clips 会换发令牌",
          "self.begin_job(\"generate_all_clips\")" in src)
    check("regenerate_clip 会换发令牌",
          "self.begin_job(f\"regenerate_clip#" in src)

    panel = read("director_panel.py")
    check("★ _director_stop 用 cancel_job（不再置永久布尔）",
          "cancel_job(\"用户点了停止\"" in panel)
    check("★ _director_stop 提示可继续（不再让人以为只能重置）",
          "不必重置项目" in panel)
    check("定义了后台任务统一收口 _cancel_all_director_bg",
          "def _cancel_all_director_bg(" in panel)
    check("★ _director_reset 调用了统一收口",
          "_cancel_all_director_bg(app, reason=\"项目已重置\"" in panel)
    check("★ 抽帧回调带项目令牌校验（旧结果不写新项目）",
          "_guarded_item" in panel and "!= project_token()" in panel)
    check("★ 预览回调也带令牌校验", "_guarded_done" in panel)

    print("\n=== 9) 源码契约：收口五步都在（取消/断连/标孤儿/清池）===")
    body = panel.split("def _cancel_all_director_bg(")[1].split("\ndef ")[0]
    check("① 广播取消（调 cancel()）", "th.cancel()" in body)
    check("② 断开回调（disconnect）", "th.disconnect()" in body)
    check("③ 标记孤儿（isRunning 统计）",
          "isRunning()" in body and "zombie" in body)
    check("④ 清空引用池（setattr 置空）", "setattr(app, attr, [])" in body)
    check("覆盖两个线程池",
          "director_kf_threads" in body and "director_bg_threads" in body)

    print(f"\n汇总：PASS={_p} FAIL={_f}")
    return 0 if _f == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
