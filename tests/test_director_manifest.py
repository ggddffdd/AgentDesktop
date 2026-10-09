# -*- coding: utf-8 -*-
"""导演台「工程归口 + 远端任务可恢复」回归（审查 #2 / #5 / v4.168.0）。

钉住两条链：

**#5 工程目录收口**
  改前：`project_dir` 只承载 versions/；关键帧、片段落到**公共产物目录**
        且文件名是秒级时间戳 → 多项目素材混在一起、同秒理论上撞名、
        拷走一个导演工程拿不齐素材、会话恢复依赖一堆散落绝对路径。
  改后：工程目录一次建全（keyframes/ clips/ characters/ final/ logs/），
        片段命名 `shot_001_<uuid>.mp4`，`manifest.json` 为唯一事实源，
        路径一律存**相对工程目录**的短路径。

**#2 远端任务断点续跑**
  改前：提交后拿到的远端 task_id 只存在内存里，一关软件/断网/点停止就丢，
        远端还在跑、还在扣费，本地只能重新提交 → 重复生成、重复扣费。
  改后：task_id 立刻落 manifest；重启时给「继续查询」/「放弃重生成」两个按钮；
        `resume_video()` 只轮询下载不重新提交；取消**不**被写成 failed。

用 httptest 桩与打桩函数，不出网。
"""

import json
import os
import sys
import tempfile
import threading
import time
from pathlib import Path

_HERE = Path(__file__).resolve().parent
ROOT = _HERE.parent
sys.path.insert(0, str(ROOT))

_SBX = tempfile.mkdtemp(prefix="xc_verify_sbx_dirmf_")
os.environ["XC_USER_DATA_DIR"] = os.path.join(_SBX, "userdata")
os.environ.setdefault("XC_LOG_DIR", os.path.join(_SBX, "logs"))

import video_pipeline as vp   # noqa: E402
try:
    import core_agnes as ca   # noqa: E402
except ImportError as _e:
    # v4.239.1（CI 红 → 修）：core_agnes 依赖仓库外的 video-agent/core 包，
    # CI 干净 clone 上不存在 → 环境缺失 SKIP，不假红（与 frozen_smoke 同口径）。
    print(f"  [SKIP] video-agent/core 不在（{_e}）——外部依赖缺失，跳过不冒充通过")
    sys.exit(0)
import cancel_token as ct     # noqa: E402

_p = _f = 0


def check(name, cond, detail=""):
    global _p, _f
    if cond:
        _p += 1
        print(f"  [PASS] {name}")
    else:
        _f += 1
        print(f"  [FAIL] {name}" + (f" —— {detail}" if detail else ""))


def _mkpipe(name="proj"):
    d = tempfile.mkdtemp(prefix="xc_dir_")
    p = vp.VideoPipeline(cfg={}, app_dir=d)
    pd = os.path.join(d, name)
    p.prepare("测试主题", 3, 8, "768x1152", "realistic", None, False, False,
              True, "black", 0.4, True, None, clip_dir=pd)
    p.shots = [{"en": "shot a"}, {"en": "shot b"}, {"en": "shot c"}]
    return p, pd


# ==========================================================================
def part_a():
    print("=== A) 工程目录收口 ===")
    p, pd = _mkpipe()
    names = set(os.listdir(pd))
    for d in ("keyframes", "clips", "characters", "final", "logs"):
        check(f"工程内建了 {d}/", d in names, str(sorted(names)))
    check("versions/ 仍保留（回滚历史）", "versions" in names)
    check("manifest.json 已生成", "manifest.json" in names)

    m = json.load(open(os.path.join(pd, "manifest.json"), encoding="utf-8"))
    check("manifest magic 正确", m.get("magic") == vp.VideoPipeline.MANIFEST_MAGIC)
    check("manifest 记录 project_id", bool(m.get("project_id")), str(m)[:120])
    check("manifest 记录主题", m.get("topic") == "测试主题", str(m.get("topic")))

    print("\n-- A2 片段命名与稳定性 --")
    c0 = p.clip_dest_path(0)
    rel = os.path.relpath(c0, pd)
    check("落进 clips/ 子目录", rel.replace("\\", "/").startswith("clips/"), rel)
    base = os.path.basename(c0)
    check("文件名是 shot_001_<uuid>.mp4 形态",
          base.startswith("shot_001_") and base.endswith(".mp4")
          and len(base) == len("shot_001_") + 8 + 4, base)
    check("无秒级时间戳（旧坑）",
          not any(x in base for x in (time.strftime("%Y%m%d"), "_final_")), base)
    check("同一镜多次取路径一致（uuid 只生成一次）",
          p.clip_dest_path(0) == c0)
    check("不同镜文件名不同", p.clip_dest_path(1) != c0)
    check("镜号补零到三位",
          os.path.basename(p.clip_dest_path(11)).startswith("shot_012_"))
    m2 = json.load(open(os.path.join(pd, "manifest.json"), encoding="utf-8"))
    check("manifest 记了相对路径 target",
          m2["shots"]["0"].get("target", "").replace("\\", "/").startswith("clips/"),
          str(m2["shots"].get("0")))

    print("\n-- A3 成片归口 final/ --")
    src = (ROOT / "video_pipeline.py").read_text(encoding="utf-8-sig")
    check("merge 优先写工程 final/",
          'get("final")' in src and 'f"director_final_{ts}.mp4"' in src)
    check("成片路径进 manifest", '"final"' in src and '"updated": time.time()' in src)


def part_b():
    print("\n=== B) manifest 状态机 ===")
    p, pd = _mkpipe("proj_b")
    p.manifest_update_shot(0, task_id="tid_aaa", status="pending",
                           prompt="p0", prompt_hash=p.prompt_fingerprint("p0"),
                           submitted_at=time.time())
    p.manifest_update_shot(1, task_id="tid_bbb", status="downloading")
    p.manifest_update_shot(2, status="done")
    pend = p.pending_remote_shots()
    check("pending 只含未接回的镜", [n for n, _ in pend] == [1, 2], str(pend))
    check("hash 指纹稳定", p.prompt_fingerprint("p0") == p.prompt_fingerprint("p0"))
    check("不同提示词指纹不同", p.prompt_fingerprint("a") != p.prompt_fingerprint("b"))
    check("指纹长度 16", len(p.prompt_fingerprint("x")) == 16)

    s = p.manifest_summary()
    check("摘要含镜数与远端待接回", "3 镜已登记" in s and "远端待接回 2 镜" in s, s)

    print("\n-- B2 放弃远端 --")
    marked = p.abandon_remote_clips()
    check("放弃返回镜号（1 基）", marked == [1, 2], str(marked))
    check("放弃后不再提示接回", p.pending_remote_shots() == [])
    check("放弃不写成 failed（是 cancelled）",
          p.manifest_shot(0).get("status") == "cancelled", str(p.manifest_shot(0)))

    print("\n-- B3 落盘是原子的 & 幂等 --")
    p.manifest_update_shot(0, status="done")
    p.manifest_update_shot(0, error="x")
    rec = p.manifest_shot(0)
    check("同镜多次更新不丢字段", rec.get("task_id") == "tid_aaa"
          and rec.get("status") == "done" and rec.get("error") == "x", str(rec))
    check("无 .tmp 残留", not os.path.isfile(p.manifest_path() + ".tmp"))

    print("\n-- B4 损坏文件不炸（返回空骨架）--")
    p2, pd2 = _mkpipe("proj_b2")
    with open(p2.manifest_path(), "w", encoding="utf-8") as f:
        f.write("{ this is not json")
    p2._manifest_cache = None
    m = p2.manifest_load()
    check("返回 dict 骨架", isinstance(m, dict) and m.get("magic"))
    check("shots 为空", (m.get("shots") or {}) == {})


def part_c():
    print("\n=== C) 恢复态脚手架补齐 ===")
    # 模拟 _load_session 的拼装方式：只 setattr project_dir，不调 prepare
    d = tempfile.mkdtemp(prefix="xc_dir_")
    p = vp.VideoPipeline(cfg={}, app_dir=d)
    p.project_dir = os.path.join(d, "restored")
    os.makedirs(p.project_dir)
    check("恢复前没有 asset_dirs", not getattr(p, "asset_dirs", None))
    ok = p.ensure_project_scaffold()
    check("ensure_project_scaffold 返回 True", ok is True)
    names = set(os.listdir(p.project_dir))
    for sub in ("clips", "keyframes", "final", "logs", "characters"):
        check(f"补齐 {sub}/", sub in names, str(sorted(names)))
    check("补齐 manifest.json", "manifest.json" in names)
    check("clip 路径落在工程内",
          p.clip_dest_path(0).startswith(p.project_dir))
    check("幂等：再调一次仍 True 且路径不变",
          p.ensure_project_scaffold() is True
          and p.clip_dest_path(0).startswith(p.project_dir))

    print("\n-- C2 无工程目录时不炸 --")
    p3 = vp.VideoPipeline(cfg={}, app_dir=d)
    p3.project_dir = None
    check("project_dir=None → 返回 False 不抛", p3.ensure_project_scaffold() is False)

    print("\n-- C3 接线（director_panel）--")
    dp = (ROOT / "director_panel.py").read_text(encoding="utf-8-sig")
    check("_load_session 调 ensure_project_scaffold",
          "p.ensure_project_scaffold()" in dp)
    check("恢复后调用远端接回横幅", "_maybe_offer_remote_resume(app)" in dp)
    check("横幅存在两个按钮",
          "🔌 继续查询已提交任务" in dp and "放弃并重新生成" in dp)
    check("接回跑在后台线程（不卡 UI）", "_kick_bg(app, _work, _done" in dp)
    check("后台线程不回调 UI（避免跨线程改控件）",
          "_work" in dp and "on_clip=lambda" not in dp.split("def _do_resume")[1][:900])
    check("接回后统一重渲染", "_render_clips(app)" in dp)


def part_d():
    print("\n=== D) _gen_one_clip：target / on_submit / cancel 接线 ===")
    p, pd = _mkpipe("proj_d")
    src = (ROOT / "video_pipeline.py").read_text(encoding="utf-8-sig")
    check("_gen_one_clip 用 clip_dest_path", "dest = self.clip_dest_path(idx)" in src)
    check("给 tool_video_gen 传 dest_path", "dest_path=dest" in src)
    check("传 on_submit", "on_submit=_on_submit" in src)
    check("传 cancel_token", "cancel_token=getattr(self, \"_job_token\", None)" in src)
    check("on_submit 里落 task_id", "task_id=str(tid)" in src)
    check("取消原样抛（不伪装失败）", "_is_cancel_error(e)" in src
          and 'status="cancelled"' in src)
    check("成功后 manifest 记 done", 'status="done", error="", done_at' in src)

    print("\n-- D2 真行为：打桩 tool_video_gen 跑一镜 --")
    real = vp.tool_video_gen
    calls = {}

    def _fake(cfg, app_dir, prompt, duration=None, aspect=None, resolution=None,
              first_frame=None, dialogue=None, images=None, ref_images=None,
              dest_path=None, on_submit=None, cancel_token=None, **kw):
        calls.update({"dest": dest_path, "has_submit": callable(on_submit),
                      "token": cancel_token})
        if callable(on_submit):
            on_submit("tid_real_123")
        os.makedirs(os.path.dirname(dest_path), exist_ok=True)
        with open(dest_path, "wb") as f:
            # v4.186.0：假片段必须 ≥ 体积守卫阈值（真实 mp4 至少几十 KB），
            # 否则被 video_pipeline 的 _MIN_CLIP_BYTES 完整性校验判成残片。
            f.write(b"fake-mp4" + b"\x00" * (vp._MIN_CLIP_BYTES + 1))
        return (os.path.relpath(dest_path, app_dir), "video", os.path.basename(dest_path))

    try:
        vp.tool_video_gen = _fake
        p.begin_job("test_wave")
        out = p._gen_one_clip("a prompt", 8, "720P", None, None, 0, ref_images=None)
        check("返回工程内的片段路径", out and out.startswith(p.project_dir), str(out))
        check("dest_path 指向 clips/",
              calls.get("dest", "").replace("\\", "/").find("/clips/") > 0,
              str(calls.get("dest")))
        check("把本轮令牌传下去了", calls.get("token") is not None)
        rec = p.manifest_shot(0)
        check("manifest 拿到远端 task_id", rec.get("task_id") == "tid_real_123", str(rec))
        check("manifest 状态 done", rec.get("status") == "done", str(rec))
        check("manifest 记了模型", rec.get("model") == vp.AGNES_VIDEO_MODEL, str(rec))
        check("manifest 记了 prompt 指纹", bool(rec.get("prompt_hash")), str(rec))
        check("完成后不在 pending 列表", p.pending_remote_shots() == [], "")

        print("\n-- D3 取消必须原样抛（不能被吞成失败）--")
        def _fake_cancel(*a, **kw):
            raise ct.CancelledError("用户停止", stage="video_poll")

        class _CancelExc(Exception):
            pass

        # 用真 CancelledError（cancel_token 的类型）验证
        vp.tool_video_gen = _fake_cancel
        p.begin_job("test_cancel2")
        raised = False
        try:
            p._gen_one_clip("p", 8, "720P", None, None, 1, ref_images=None)
        except BaseException as e:
            raised = type(e).__name__ == "CancelledError"
        check("CancelledError 被原样抛出", raised)
        check("该镜被标记 cancelled 而不是 failed",
              p.manifest_shot(1).get("status") == "cancelled",
              str(p.manifest_shot(1)))
    finally:
        vp.tool_video_gen = real


def part_e():
    print("\n=== E) Agnes 内核：取消令牌 + 接回 ===")
    c = ca.AgnesClient(cfg={}, api_key="k", base_url="http://127.0.0.1:1")

    print("-- E1 wait_video 响应取消（1 秒内）--")
    polls = {"n": 0}

    def _q(tid, model=None, timeout=30):
        polls["n"] += 1
        return {"status": "pending", "url": None, "raw": "", "data": {}}

    c.query_video = _q
    tok = ct.CancellationToken(name="t")
    threading.Timer(0.5, lambda: tok.cancel("用户点了停止", stage="video_poll")).start()
    t0 = time.time()
    err = None
    try:
        c.wait_video("tid", timeout=60, interval=8, cancel_token=tok)
    except BaseException as e:
        err = e
    dt = time.time() - t0
    check("取消后抛 CancelledError", type(err).__name__ == "CancelledError",
          f"{type(err).__name__}: {err}")
    check("1.5 秒内就响应了（不是等满 8 秒轮询间隔）", dt < 1.5, f"{dt:.2f}s")
    check("取消原因带到上层", getattr(err, "reason", "") == "用户点了停止",
          str(getattr(err, "reason", "")))

    print("\n-- E2 无令牌时行为不变（向后兼容）--")
    seq = {"n": 0}

    def _q2(tid, model=None, timeout=30):
        seq["n"] += 1
        if seq["n"] >= 2:
            return {"status": "done", "url": "http://x/v.mp4", "raw": "", "data": {}}
        return {"status": "pending", "url": None, "raw": "", "data": {}}

    c.query_video = _q2
    url = c.wait_video("tid", timeout=30, interval=1)
    check("旧调用签名仍可用并拿到 URL", url == "http://x/v.mp4", str(url))

    print("\n-- E3 resume_video 只轮询不提交 --")
    submitted = {"n": 0}
    c.submit_video = lambda *a, **k: submitted.__setitem__("n", submitted["n"] + 1)
    c.query_video = lambda tid, model=None, timeout=30: {
        "status": "done", "url": "http://x/v.mp4", "raw": "", "data": {}}
    dest = os.path.join(_SBX, "resumed.mp4")

    def _dl(url, dest_path, timeout=600, on_progress=None, cancel_token=None):
        with open(dest_path, "wb") as f:
            f.write(b"v")
        return dest_path

    c.download = _dl
    got = c.resume_video("tid_x", dest)
    check("接回返回本地路径", got == dest, str(got))
    check("**没有**重新提交（不重复扣费）", submitted["n"] == 0, str(submitted))

    print("\n-- E4 cancel 类判定 --")
    check("CancelledError 算取消", ca.is_cancel_error(ct.CancelledError("x")))
    check("AgnesCancelled 算取消", ca.is_cancel_error(ca.AgnesCancelled("x")))
    check("AgnesError 不算取消",
          not ca.is_cancel_error(ca.AgnesError("x")))
    check("普通异常不算取消", not ca.is_cancel_error(ValueError("x")))

    print("\n-- E5 generate_video：提交即回调 task_id --")
    got_tid = {}
    c.submit_video = lambda *a, **k: "tid_submit_1"
    c.wait_video = lambda tid, **k: "http://x/v.mp4"
    c.download = _dl
    c.generate_video("p", seconds=4, dest_path=os.path.join(_SBX, "g.mp4"),
                     on_submit=lambda tid: got_tid.setdefault("tid", tid))
    check("on_submit 拿到 task_id（落盘用）", got_tid.get("tid") == "tid_submit_1",
          str(got_tid))

    print("\n-- E6 取消时不得触发智谱兜底（否则越停越花钱）--")
    def _wv_cancel(tid, **k):
        raise ct.CancelledError("停", stage="video_poll")
    c.wait_video = _wv_cancel
    c.zhipu_key = "zk"          # 有兜底能力
    err2 = None
    try:
        c.generate_video("p", seconds=4, dest_path=os.path.join(_SBX, "g2.mp4"))
    except BaseException as e:
        err2 = e
    check("取消原样抛出", type(err2).__name__ == "CancelledError",
          str(type(err2).__name__))
    check("last_source 没被改成 zhipu", c.last_source == "agnes", c.last_source)

    print("\n-- E7 download 取消清理 .part --")
    import http.server
    payload = b"x" * (1 << 20)

    class _H(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def log_message(self, *a):
            pass

    srv = http.server.HTTPServer(("127.0.0.1", 0), _H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    dest2 = os.path.join(_SBX, "dl.mp4")
    tok2 = ct.CancellationToken(name="t2")
    tok2.cancel("停", stage="video_download")
    err3 = None
    try:
        # 注意：前面几节把 c.download 打桩成"直接写文件"，这里必须走**真实实现**
        ca.AgnesClient.download(c, f"http://127.0.0.1:{srv.server_address[1]}/v.mp4",
                                dest2, cancel_token=tok2)
    except BaseException as e:
        err3 = e
    srv.shutdown()
    check("已取消的下载直接抛错", type(err3).__name__ == "CancelledError",
          str(type(err3).__name__))
    check("不留 .part 垃圾文件", not os.path.exists(dest2 + ".part"))
    check("不留未完成的成品", not os.path.exists(dest2))


def part_f():
    print("\n=== F) resume_remote_clips 真行为 ===")
    p, pd = _mkpipe("proj_f")
    for i in range(3):
        p.manifest_update_shot(i, task_id=f"tid_{i}", status="pending",
                               target=os.path.join("clips",
                                                   f"shot_{i+1:03d}_aaaa.mp4"))
    got = {}

    class _Client:
        def resume_video(self, tid, dest, model=None, cancel_token=None,
                         timeout=0, **kw):
            got[tid] = got.get(tid, 0) + 1
            if tid == "tid_1":
                raise ca.AgnesError("远端任务已失效")
            os.makedirs(os.path.dirname(dest), exist_ok=True)
            with open(dest, "wb") as f:
                f.write(b"v")
            return dest

    p._agnes_client = lambda: _Client()
    p.clip_paths = [None, None, None]
    ok_n, fails = p.resume_remote_clips()
    check("成功 2 镜", ok_n == 2, str(ok_n))
    check("失败 1 镜", len(fails) == 1 and fails[0][0] == 2, str(fails))
    check("每镜只接回一次（没重复提交）",
          sorted(got.values()) == [1, 1, 1], str(got))
    check("成功镜标 done", p.manifest_shot(0).get("status") == "done",
          str(p.manifest_shot(0)))
    check("失败镜标 failed 且带原因",
          p.manifest_shot(1).get("status") == "failed"
          and "失效" in (p.manifest_shot(1).get("error") or ""),
          str(p.manifest_shot(1)))
    check("失败镜可重新生成（不再挂 pending）",
          all(n != 2 for n, _ in p.pending_remote_shots()),
          str(p.pending_remote_shots()))
    check("成功后 clip_paths 回填", p.clip_paths[0] and p.clip_paths[2],
          str(p.clip_paths))

    print("\n-- F2 取消接回：标 cancelled，不标 failed --")
    p2, _ = _mkpipe("proj_f2")
    p2.manifest_update_shot(0, task_id="tid_c", status="pending",
                            target=os.path.join("clips", "shot_001_c.mp4"))

    class _Cancel:
        def resume_video(self, tid, dest, **kw):
            raise ct.CancelledError("用户停止", stage="video_poll")

    p2._agnes_client = lambda: _Cancel()
    p2.clip_paths = [None]
    ok2, fails2 = p2.resume_remote_clips()
    check("取消时成功 0", ok2 == 0, str(ok2))
    check("取消不算失败", fails2 == [], str(fails2))
    check("状态 = cancelled（远端可能还在跑，不判死）",
          p2.manifest_shot(0).get("status") == "cancelled",
          str(p2.manifest_shot(0)))

    print("\n-- F3 无凭据时给出明确提示（不是静默失败）--")
    p3, _ = _mkpipe("proj_f3")
    p3.manifest_update_shot(0, task_id="tid_x", status="pending")
    p3.clip_paths = [None]
    p3._agnes_client = lambda: None
    ok3, fails3 = p3.resume_remote_clips()
    check("成功 0 且失败有原因", ok3 == 0 and fails3 and "凭据" in fails3[0][1],
          str(fails3))


def part_g():
    print("\n=== G) tools.tool_video_gen 接线 ===")
    tsrc = (ROOT / "tools.py").read_text(encoding="utf-8-sig")
    check("签名新增 dest_path/on_submit/cancel_token",
          "dest_path=None, on_submit=None, cancel_token=None" in tsrc)
    check("dest_path 优先于公共产物目录",
          "if dest_path:" in tsrc and "v_dir = _products_dir(cfg, \"video\")" in tsrc)
    check("把 on_submit/cancel_token 透传给内核",
          "on_submit=on_submit" in tsrc and "cancel_token=cancel_token" in tsrc)
    check("取消原样抛出去（不写成生成失败）",
          "if is_cancel_error(e):\n            raise" in tsrc)
    check("仍保留 AgnesError 的友好文案", "视频生成失败（统一内核）：{e.msg}" in tsrc)

    print("\n-- G2 旧调用方式不破（不传新参数）--")
    import inspect
    sig = inspect.signature(vp.tool_video_gen)
    for k in ("dest_path", "on_submit", "cancel_token"):
        check(f"{k} 有默认值", sig.parameters[k].default is None)
    for k in ("prompt", "duration", "aspect", "first_frame", "dialogue", "images"):
        check(f"{k} 仍在（向后兼容）", k in sig.parameters)


def main():
    part_a()
    part_b()
    part_c()
    part_d()
    part_e()
    part_f()
    part_g()
    print(f"\n汇总：PASS={_p} FAIL={_f}")
    return 0 if _f == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
