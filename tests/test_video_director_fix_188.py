"""批 D（P2-6/7/8 + P3×3，video/director 线）回归（v4.188 批 D 修复）。

覆盖：
  [A] P2-7  RES_PRESETS 归一（ui.py 唯一来源，twin/director 同对象引用）
  [B] P2-8  _check_audio_level 返回值升级 + _merge 三处成功路径写 last_audio_warn
  [C] P2-6  DirectorThread merge 成功消息明示缺镜/无声警告
  [D] P3    口播字数按语速动态化（旧「20~35 字」写死与 4~12s 时长矛盾）
  [E] P3    非合并 ffmpeg 可取消（转场/volumedetect 传 cancel_check + 行为级 kill）
  [F] P3    twin task_id 登记（_record_task / on_submit 透传 / 并发锁）
  [G]       四文件语法编译
"""
import os
import sys
import json
import time
import types
import threading
import tempfile

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
    os.chdir(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    tmp = tempfile.mkdtemp(prefix="fix188_d_")

    print("=== [A] P2-7 RES_PRESETS 归一 ===")
    import ui
    import digital_twin_panel as dtp
    import director_panel as dp
    check("A1 ui.RES_PRESETS 8项", len(ui.RES_PRESETS) == 8, len(ui.RES_PRESETS))
    check("A2 twin 同对象引用", dtp.RES_PRESETS is ui.RES_PRESETS)
    check("A3 director 同对象引用", dp.RES_PRESETS is ui.RES_PRESETS)
    check("A4 内容首尾抽查",
          ui.RES_PRESETS[0] == ("竖屏 1080×1920 (9:16)", "1080x1920")
          and ui.RES_PRESETS[-1] == ("方形 1024×1024 (1:1)", "1024x1024"))
    defs = []
    for f in ("ui.py", "ui_widgets.py", "digital_twin_panel.py",
              "director_panel.py", "video_pipeline.py", "tools.py"):
        with open(f, encoding="utf-8") as fh:
            if "RES_PRESETS = [" in fh.read():
                defs.append(f)
    # v4.216.0：RES_PRESETS 定义随视频页构件迁到 ui_widgets.py（ui.py re-export）
    check("A5 定义仅存 ui_widgets.py", defs == ["ui_widgets.py"], defs)

    print("=== [B] P2-8 _check_audio_level 返回值 + _merge 写入 ===")
    from video_pipeline import VideoPipeline
    logs = []
    vp = VideoPipeline({}, tmp, callbacks={"log": logs.append})
    calls = {"n": 0}

    def fake_ff(args, timeout=60, cancel_check=None):
        calls["n"] += 1
        return 0, "", "max_volume: -75.0 dB"

    vp._run_ff = fake_ff
    r = vp._check_audio_level(os.path.join(tmp, "x.mp4"),
                              expect_silent=True, n_silent=3, n_clip=3)
    check("B1 expect_silent 跳过", r is None and calls["n"] == 0)

    r = vp._check_audio_level(os.path.join(tmp, "x.mp4"))
    check("B2 静音峰值→警告文本",
          isinstance(r, str) and "疑似无声" in r and "-75.0" in r, r)

    def fake_ff2(args, timeout=60, cancel_check=None):
        calls["n"] += 1
        return 0, "", "max_volume: -12.3 dB"

    vp._run_ff = fake_ff2
    r = vp._check_audio_level(os.path.join(tmp, "x.mp4"))
    check("B3 正常峰值→None", r is None)

    vp.cancelled = True
    calls["n"] = 0
    r = vp._check_audio_level(os.path.join(tmp, "x.mp4"))
    check("B4 前置取消跳过（不碰ffmpeg）", r is None and calls["n"] == 0)
    vp.cancelled = False

    vp._run_ff = lambda a, timeout=60, cancel_check=None: (0, "", "no volume line")
    vp._probe_has_audio = lambda p: False
    r = vp._check_audio_level(os.path.join(tmp, "x.mp4"))
    check("B5 无音轨→警告文本", isinstance(r, str) and "没有音轨" in r, r)

    # _merge 成功路径写入 + 入口重置 + 静音轨兜底
    vp2 = VideoPipeline({}, tmp, callbacks={"log": logs.append})
    vp2._build_segs = lambda cp, sh: [{"kind": "clip"}]
    vp2._merge_exec = lambda *a, **k: (True, "")
    vp2._expect_silent = lambda segs: (False, 0, 3)
    vp2._check_audio_level = lambda p, **k: "⚠️ 成片疑似无声（音轨峰值 -80.0 dB）"
    vp2.last_audio_warn = "旧警告"
    ok, d = vp2._merge(["a.mp4"], [{}], os.path.join(tmp, "o.mp4"))
    check("B6 成功路径写 warn", ok is True
          and vp2.last_audio_warn == "⚠️ 成片疑似无声（音轨峰值 -80.0 dB）")

    vp2._build_segs = lambda cp, sh: []
    vp2.last_audio_warn = "旧"
    ok, d = vp2._merge([], [], "x")
    check("B7 入口重置旧值", ok is False and vp2.last_audio_warn is None)

    vp2._build_segs = lambda cp, sh: [{"kind": "clip"}]
    _seq = {"i": 0}

    def exec_seq(*a, **k):
        _seq["i"] += 1
        if _seq["i"] == 1:
            return False, "Stream map '0:a' matches no streams"
        return True, ""

    vp2._merge_exec = exec_seq
    ok, d = vp2._merge(["a.mp4"], [{}], os.path.join(tmp, "o2.mp4"))
    check("B8 静音轨兜底置 warn", ok is True
          and "静音轨" in (vp2.last_audio_warn or ""), vp2.last_audio_warn)

    print("=== [C] P2-6 DirectorThread merge 消息明示 ===")

    def run_merge(p):
        got = []
        th = dp.DirectorThread(p, "merge")
        th.merge_ready.connect(lambda ok, msg, path: got.append((ok, msg, path)))
        th.run()
        return got[0] if got else (None, None, None)

    stub = types.SimpleNamespace(
        merge=lambda: ("D:/x/out.mp4", ""),
        clip_paths=["a.mp4", None, None], shots=[{}, {}, {}],
        last_audio_warn=None)
    ok, msg, path = run_merge(stub)
    check("C1 缺镜明示", ok is True and "1/3 镜" in msg and "缺 2 镜" in msg, msg)

    stub2 = types.SimpleNamespace(
        merge=lambda: ("D:/x/out.mp4", ""),
        clip_paths=["a", "b", "c"], shots=[{}, {}, {}], last_audio_warn=None)
    ok, msg, path = run_merge(stub2)
    check("C2 全齐不含缺字样", ok is True and "缺" not in msg, msg)

    stub3 = types.SimpleNamespace(
        merge=lambda: ("D:/x/out.mp4", ""),
        clip_paths=["a", None, None], shots=[{}, {}, {}],
        last_audio_warn="⚠️ 成片按静音轨合成（素材无可用音轨，成片无声）")
    ok, msg, path = run_merge(stub3)
    check("C3 无声警告拼入", ok is True and "静音轨" in msg and "缺 2 镜" in msg, msg)

    print("=== [D] P3 口播字数动态化 ===")
    import video_pipeline as vpl
    cap = {}

    def fake_chat(cfg, messages, temperature=None, **kw):
        cap["msgs"] = messages
        return "文案正文"

    orig_chat = vpl._agnes_chat
    vpl._agnes_chat = fake_chat
    try:
        vp3 = VideoPipeline({}, tmp)
        vp3.portrait_mode = True
        vp3.smart = True
        vp3._gen_story("测试主题", 4)
        u = cap["msgs"][1]["content"]
        check("D1 smart 动态语速提示", "每秒 4~5 字" in u and "20~35" not in u)

        vp3.smart = False
        vp3.duration = 6
        vp3._gen_story("测试主题", 4)
        u = cap["msgs"][1]["content"]
        check("D2 dur6→24~30字", "24~30 字" in u, u[:90])

        vp3.duration = 12
        vp3._gen_story("测试主题", 4)
        u = cap["msgs"][1]["content"]
        check("D3 dur12→48~60字", "48~60 字" in u, u[:90])
    finally:
        vpl._agnes_chat = orig_chat

    print("=== [E] P3 非合并 ffmpeg 可取消 ===")
    with open("video_pipeline.py", encoding="utf-8") as fh:
        src = fh.read()
    check("E1 转场传 cancel_check（源码）",
          "转场渲染也可被「停止」中断" in src)
    i = src.find("volumedetect")
    check("E2 volumedetect 传 cancel_check（源码）",
          i >= 0 and "cancel_check" in src[i:i + 400])

    # 行为级：cancel_check 立即为真 → 子进程被 kill、快速返回「已取消」
    args = ["ping", "-n", "30", "127.0.0.1"]
    t0 = time.time()
    rc, o, e = VideoPipeline._run_ff(vp, args, timeout=60,
                                     cancel_check=lambda: True)
    dt = time.time() - t0
    check("E3 取消快速kill", rc == -1 and "取消" in (e or "") and dt < 5,
          f"rc={rc} {dt:.1f}s")

    print("=== [F] P3 twin task_id 登记 ===")
    from digital_twin_panel import TwinGenThread
    tlogs = []
    tw = TwinGenThread(cfg={}, app_dir=tmp, portrait="x.png", segs=[],
                       scene="s", keep_bg=True, resolution="720x1280",
                       qc=False, job_dir=os.path.join(tmp, "job"))
    tw.log.connect(tlogs.append)
    os.makedirs(tw.job_dir, exist_ok=True)
    sp = os.path.join(tw.job_dir, "state.json")
    state = {}
    tw._record_task(state, sp, 1, "task-abc-123")
    check("F1 内存登记", state.get("tasks", {}).get("1", {})
          .get("task_id") == "task-abc-123")
    with open(sp, encoding="utf-8") as fh:
        on_disk = json.load(fh)
    check("F2 落盘 JSON", on_disk.get("tasks", {}).get("1", {})
          .get("task_id") == "task-abc-123")
    check("F3 日志 emit", any("task-abc-123" in s for s in tlogs))

    tw2 = TwinGenThread(cfg={}, app_dir=tmp, portrait="x.png", segs=[],
                        scene="s", keep_bg=True, resolution="720x1280",
                        qc=False)
    st2 = {}
    tw2._record_task(st2, os.path.join(tmp, "no.json"), 2, "t-x")
    check("F4 无 job_dir 早退", "tasks" not in st2
          and not os.path.exists(os.path.join(tmp, "no.json")))

    st3 = {}
    sp3 = os.path.join(tmp, "state3.json")
    ths = [threading.Thread(target=tw._record_task,
                            args=(st3, sp3, i, "t%d" % i))
           for i in range(1, 9)]
    for t in ths:
        t.start()
    for t in ths:
        t.join()
    with open(sp3, encoding="utf-8") as fh:
        disk3 = json.load(fh)
    check("F5 并发 8 线程全落账",
          len(st3.get("tasks", {})) == 8
          and len(disk3.get("tasks", {})) == 8,
          f"mem={len(st3.get('tasks', {}))} disk={len(disk3.get('tasks', {}))}")

    import tools as _tools
    orig_gen = _tools.tool_video_gen
    capk = {}

    def fake_gen(cfg, app_dir, prompt, dur, aspect=None, **kw):
        capk.update(kw)
        cb = kw.get("on_submit")
        if cb:
            cb("task-xyz-999")  # 模拟「提交成功」即刻回调
        return ("rel_seg.mp4", "video", "rel_seg.mp4")

    _tools.tool_video_gen = fake_gen
    try:
        tw.qc = False
        seg, note = tw._gen_one(0, "p", 6, None, None, tmp, None,
                                images=["x.png"],
                                on_submit=lambda tid: tw._record_task(
                                    state, sp, 1, tid))
        check("F6 _gen_one 透传 on_submit",
              capk.get("on_submit") is not None
              and str(seg).endswith("rel_seg.mp4"), seg)
        check("F7 提交回调落账", state["tasks"]["1"]["task_id"]
              == "task-xyz-999", state.get("tasks"))
    finally:
        _tools.tool_video_gen = orig_gen

    with open("digital_twin_panel.py", encoding="utf-8") as fh:
        src2 = fh.read()
    check("F8 serial/parallel 两处回调构造（源码）",
          src2.count("_on_sub = (lambda tid, _i=i: self._record_task(") == 2,
          src2.count("_on_sub = (lambda tid, _i=i: self._record_task("))

    print("=== [G] 四文件语法编译 ===")
    import py_compile
    ok_all = True
    for f in ("ui.py", "director_panel.py", "digital_twin_panel.py",
              "video_pipeline.py"):
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
