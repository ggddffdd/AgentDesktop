# -*- coding: utf-8 -*-
"""导演台两个可靠性 P0：停止要说真话；全部重新生成不得先丢掉旧片段。

P0 停止
  点停止之后，迟到的剧本 / 分镜完成信号不得写进工程和界面，
  状态栏不得改口说「完成」或「出错」。没做完的镜显示「已停止」，
  不再挂着「排队中」。已成功的镜保留。
  整批视频在线程刚启动、begin_job 还没跑到时点的停止，不得被
  begin_job 清掉。

P0 全部重新生成
  开跑时不清 clip_paths，也不把卡片路径抹成空。
  新片段成功才替换该镜；某一镜失败或中途停止，没被替换的旧片段
  仍留在工程里，界面还能拿去合成。

无网络。视频生成用桩。
"""
import os
import sys
import tempfile
import threading
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import video_pipeline as vp  # noqa: E402

from PySide6.QtWidgets import QApplication, QLabel  # noqa: E402

_app = QApplication.instance() or QApplication([])

import director_panel as dp  # noqa: E402
import director_web as dw  # noqa: E402

_p = _f = 0


def check(name, cond, detail=""):
    global _p, _f
    if cond:
        _p += 1
        print(f"  [PASS] {name}")
    else:
        _f += 1
        extra = f" —— {detail}" if detail else ""
        print(f"  [FAIL] {name}{extra}")


def _pipe():
    p = vp.VideoPipeline({}, str(ROOT))
    p.relay = False
    p.with_dialogue = False
    p.portrait_mode = False
    p.ref_image_path = None
    p.passthrough_script = None
    p.duration = 5
    p.n = 3
    p.topic = "猫"
    p.shots = [
        {"en": "a cat walks", "zh": "猫走路", "scene": 1, "line": ""},
        {"en": "a cat sits", "zh": "猫坐下", "scene": 1, "line": ""},
        {"en": "a cat sleeps", "zh": "猫睡觉", "scene": 1, "line": ""},
    ]
    return p


class _Edit:
    def __init__(self, text=""):
        self.text = text

    def setPlainText(self, text):
        self.text = text

    def toPlainText(self):
        return self.text


class _Web:
    def __init__(self):
        self.html = ""

    def render_cards(self, html):
        self.html = html or ""

    def render_empty(self, text=""):
        self.html = text or ""


class _App:
    def __init__(self):
        self.director_busy = True
        self.director_phase = dp.DirectorPhase.RUNNING
        self.director_step = 0
        self._director_agent_cancelled = False
        self._director_agent_error = None
        self._director_agent_event = None
        self._director_agent_result = None
        self.director_status = QLabel()
        self.director_log = []
        self.director_story_edit = _Edit("旧剧本")
        self.director_pipeline = None
        self.director_thread = None
        self.director_clips_state = []
        self.director_clips_web = _Web()
        self.director_stop = None
        self.director_final_path = None


def _touch(dirpath, name):
    path = os.path.join(dirpath, name)
    with open(path, "wb") as f:
        f.write(b"clip")
    return path


def main():
    print("=== 1) 整批重生成不得在开跑时清空旧片段 ===")
    p = _pipe()
    old = ["/keep/a.mp4", "/keep/b.mp4", "/keep/c.mp4"]
    p.clip_paths = list(old)
    made = []

    def fake_clip(prompt, duration, resolution, first_frame, dialogue, idx,
                  ref_images=None):
        made.append(idx)
        if idx == 1:
            return None
        return f"/new/{idx}.mp4"

    p._gen_one_clip = fake_clip
    failed = []

    def on_clip(i, path):
        if path is None:
            failed.append(i)

    ok = p.generate_all_clips(on_clip=on_clip)
    check("第 0 镜成功后换成新片段", p.clip_paths[0] == "/new/0.mp4", p.clip_paths)
    check("第 1 镜失败仍指向旧片段", p.clip_paths[1] == "/keep/b.mp4", p.clip_paths)
    check("还没轮到的成功镜在失败后仍可被替换", p.clip_paths[2] == "/new/2.mp4",
          p.clip_paths)
    check("失败镜有失败回调", failed == [1], failed)
    check("成功数只计新片段", ok == 2, ok)

    print("\n=== 2) 停止落在 begin_job 之前时，不得把停止清掉 ===")
    p2 = _pipe()
    p2.clip_paths = ["/keep/a.mp4", "/keep/b.mp4", "/keep/c.mp4"]
    check("有 arm_job（界面在线程启动前换发令牌）", hasattr(p2, "arm_job"))
    if hasattr(p2, "arm_job"):
        p2.arm_job("generate_all_clips")
        p2.cancel_job("用户点了停止", stage="user_stop")
        seen = []

        def fake_should_not_run(*a, **k):
            seen.append(1)
            return "/new/x.mp4"

        p2._gen_one_clip = fake_should_not_run
        p2.generate_all_clips()
        check("工作线程里的 begin_job 不再把刚点的停止清掉",
              p2._is_cancelled() is True)
        check("停止后的整批生成不提交新片段", seen == [], seen)
        check("停止后旧片段路径原样还在",
              p2.clip_paths == ["/keep/a.mp4", "/keep/b.mp4", "/keep/c.mp4"],
              p2.clip_paths)
        p2.arm_job("generate_all_clips")
        check("下一轮 arm_job 仍能重新开始（停止不粘住）",
              p2._is_cancelled() is False)

    print("\n=== 3) 逐镜中途停止：已完成的留下，未跑的不记失败 ===")
    p3 = _pipe()
    p3.clip_paths = ["/keep/a.mp4", "/keep/b.mp4", "/keep/c.mp4"]
    calls = []

    def fake_stop_mid(prompt, duration, resolution, first_frame, dialogue, idx,
                      ref_images=None):
        calls.append(idx)
        if idx == 1:
            p3.cancel_job("用户点了停止", stage="user_stop")
            return None
        return f"/new/{idx}.mp4"

    p3._gen_one_clip = fake_stop_mid
    noted = []
    p3.generate_all_clips(on_clip=lambda i, path: noted.append((i, path)))
    check("停在第 2 镜时不再往下生成", calls == [0, 1], calls)
    check("已生成的第 1 镜换成新片段", p3.clip_paths[0] == "/new/0.mp4", p3.clip_paths)
    check("被停止的那一镜仍是旧片段", p3.clip_paths[1] == "/keep/b.mp4", p3.clip_paths)
    check("还没开始的镜仍是旧片段", p3.clip_paths[2] == "/keep/c.mp4", p3.clip_paths)
    check("停止不把该镜回调成失败", all(path for _i, path in noted), noted)

    print("\n=== 4) 文本步骤：停止后的剧本 / 分镜不得写入 ===")
    p4 = _pipe()
    p4.story = "旧剧本"
    p4.topic = "猫"
    p4.n = 3

    def fake_story(*a, **k):
        p4.cancel_job("用户点了停止", stage="user_stop")
        return "新剧本不该落下"

    p4._gen_story = fake_story
    p4.gen_story()
    check("停止后 gen_story 不覆盖旧剧本", p4.story == "旧剧本", p4.story)

    p4b = _pipe()
    p4b.story = "旧剧本"
    p4b.topic = "猫"
    p4b.n = 3
    p4b._gen_story = lambda *a, **k: "新剧本"
    p4b.gen_story()
    check("未停止时剧本仍正常写入", p4b.story == "新剧本", p4b.story)

    p5 = _pipe()
    p5.story = "旧剧本"
    p5.n = 3
    p5.shots = [{"en": "old"}]
    p5.clip_paths = ["/keep/a.mp4"]

    def fake_shots(*a, **k):
        p5.cancel_job("用户点了停止", stage="user_stop")
        return [{"en": "new", "zh": "", "scene": 1, "line": "", "dur": 5}]

    p5._gen_shots = fake_shots
    p5.gen_shots()
    check("停止后分镜不覆盖", (p5.shots or [{}])[0].get("en") == "old", p5.shots)
    check("停止后分镜不把已有片段清空", p5.clip_paths == ["/keep/a.mp4"], p5.clip_paths)

    print("\n=== 5) 界面：停止后的完成 / 出错信号不得改口 ===")
    app = _App()
    app.director_pipeline = p4
    app._director_agent_cancelled = True
    p4.cancelled = True
    dp._on_story_ready(app, "迟到的新剧本")
    check("停止后不把迟到剧本填进编辑框",
          app.director_story_edit.text != "迟到的新剧本",
          app.director_story_edit.text)
    check("停止后状态不是「剧本已生成」",
          "剧本已生成" not in app.director_status.text(),
          app.director_status.text())
    check("停止后状态写明已停止",
          "已停止" in app.director_status.text(),
          app.director_status.text())

    tmp = tempfile.mkdtemp(prefix="dirclips_")
    keep_a = _touch(tmp, "a.mp4")
    keep_c = _touch(tmp, "c.mp4")
    app2 = _App()
    pipe = _pipe()
    pipe.clip_paths = [keep_a, None, keep_c]
    pipe.cancelled = True
    app2.director_pipeline = pipe
    app2._director_agent_cancelled = True
    app2.director_clips_state = [
        {"status": "done", "path": keep_a, "error": "", "kf": None, "info": None},
        {"status": "queued", "path": None, "error": "", "kf": None, "info": None},
        {"status": "queued", "path": keep_c, "error": "", "kf": None, "info": None},
    ]
    dp._on_clips_done(app2, 2, 6, "逐镜生成完成")
    status = app2.director_status.text()
    check("停止后不显示逐镜生成完成", "逐镜生成完成" not in status, status)
    check("停止后状态栏不是出错", not status.startswith("出错"), status)
    check("未完成且没有旧片段的镜标成已停止",
          app2.director_clips_state[1]["status"] == "stopped",
          app2.director_clips_state[1])
    check("已有片段的镜停止后仍指向该文件",
          app2.director_clips_state[0]["path"] == keep_a
          and app2.director_clips_state[2]["path"] == keep_c,
          app2.director_clips_state)
    check("已有片段的镜不显示成排队",
          app2.director_clips_state[0]["status"] == "done"
          and app2.director_clips_state[2]["status"] != "queued",
          [c["status"] for c in app2.director_clips_state])
    html = dw.clip_card_html(1, "stopped")
    check("已停止卡片不再写排队中", "排队" not in html and "已停止" in html, html[:80])

    app3 = _App()
    app3.director_pipeline = pipe
    app3._director_agent_cancelled = True
    app3.director_phase = dp.DirectorPhase.RUNNING
    dp._on_error(app3, "clips 异常：已取消（user_stop）：用户点了停止")
    check("用户停止不进出错状态栏",
          "出错" not in app3.director_status.text(),
          app3.director_status.text())
    check("用户停止不把阶段打成 error",
          app3.director_phase != dp.DirectorPhase.ERROR,
          app3.director_phase)
    check("用户停止不留下失败回执",
          not app3._director_agent_error, app3._director_agent_error)

    print("\n=== 6) 对话回执：取消优先于错误标记 ===")
    app4 = _App()
    app4.director_busy = True
    app4._director_agent_cancelled = True
    app4._director_agent_error = "clips 异常：已取消"
    app4._director_agent_event = threading.Event()
    dp._set_busy(app4, False)
    snap = app4._director_agent_result or {}
    check("同时有错误标记和取消时，回执是 cancelled 不是 failed",
          snap.get("state") == "cancelled", snap)

    print("\n=== 7) 「全部重新生成」开跑时保留卡片路径 ===")
    app5 = _App()
    app5.director_busy = False
    app5.director_pipeline = _pipe()
    app5.director_clips_state = [
        {"status": "done", "path": keep_a, "error": "旧错误", "kf": None},
        {"status": "done", "path": keep_c, "error": "", "kf": None},
    ]
    ran = []
    real_run = dp._run_thread

    def _no_thread(*a, **k):
        ran.append(a[1] if len(a) > 1 else a)

    dp._run_thread = _no_thread
    try:
        dp._regenerate_all(app5)
    finally:
        dp._run_thread = real_run
    paths = [c.get("path") for c in app5.director_clips_state]
    check("重新生成仍会发起整批任务", ran == ["clips"], ran)
    check("开跑时卡片路径还在", paths == [keep_a, keep_c], paths)
    check("开跑时卡片进入排队以便看见进度",
          all(c.get("status") == "queued" for c in app5.director_clips_state),
          [c.get("status") for c in app5.director_clips_state])

    print("\n=== 8) 单镜重生成失败时，上一版仍留在卡片上 ===")
    app6 = _App()
    app6.director_step = 0
    app6.director_pipeline = _pipe()
    app6.director_pipeline.clip_paths = [keep_a, None, keep_c]
    app6.director_clips_state = [
        {"status": "queued", "path": keep_a, "error": "", "kf": None, "info": None},
        {"status": "queued", "path": None, "error": "", "kf": None, "info": None},
        {"status": "done", "path": keep_c, "error": "", "kf": None, "info": None},
    ]
    dp._on_clip_failed(app6, 0, "模型没返回视频")
    c0 = app6.director_clips_state[0]
    check("失败但旧文件还在时，卡片仍指向旧片段",
          c0.get("path") == keep_a and c0.get("status") == "done", c0)
    dp._on_clip_failed(app6, 1, "模型没返回视频")
    c1 = app6.director_clips_state[1]
    check("本来就没有片段的失败镜仍显示失败",
          c1.get("status") == "fail" and not c1.get("path"), c1)

    print("\n=== 9) 失败但旧片段还在时，不报成不能合成 ===")
    app7 = _App()
    app7.director_busy = True
    app7.director_step = 0
    app7._director_agent_event = threading.Event()
    pipe7 = _pipe()
    pipe7.clip_paths = [keep_a, keep_c, keep_a]
    app7.director_pipeline = pipe7
    dp._on_clips_done(app7, 2, 3, "逐镜生成完成")
    check("有上一版可留用时不写失败回执",
          not app7._director_agent_error, app7._director_agent_error)
    check("状态说明其余镜仍用上一版",
          "上一版" in app7.director_status.text(), app7.director_status.text())
    snap7 = app7._director_agent_result or {}
    check("回执不是失败", snap7.get("state") != "failed", snap7)

    app8 = _App()
    app8.director_busy = True
    app8.director_step = 0
    pipe8 = _pipe()
    pipe8.clip_paths = [keep_a, None, None]
    app8.director_pipeline = pipe8
    dp._on_clips_done(app8, 1, 3, "逐镜生成完成")
    check("真的缺片段时仍记失败",
          bool(app8._director_agent_error) and "没有可用片段" in app8._director_agent_error,
          app8._director_agent_error)

    print(f"\n汇总：PASS={_p} FAIL={_f}")
    return 0 if _f == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
