# v4.178.0：数字人「缺段」抗失败回归 —— 单段失败不再吞掉后续段。
# 从仓库根目录搬入 tests/ 时须显式补 sys.path（统一入口以 cwd=仓库根运行，
# 但 sys.path[0] 是 tests/），否则 import digital_twin_panel 会失败。
import os as _os
import sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))

"""离线单测：数字人口播「分段生成」的抗失败行为。

背景（2026-09-28，大哥反馈）：
    「给小臭数字人一段口播稿，有时候差一大段它就合成了」。
根因（已取证）：
    ① `TwinGenThread._work` 里单段失败走 `break` —— 从失败那一段起，**后面所有段
       全部丢弃**，而成片照常拼接产出、照常 `done.emit(成片)`（静默成功）。
       切分函数 `split_dialogue` 经四种边界实测**不丢字**，故问题纯在 break。
    ② 「生成失败」（限流 / 免费队列拥堵 / 超时）原本**没有重试**，
       只有「质检不过」才重试。
本套件全程打桩：不联网、不跑真 ffmpeg、**不写真实用户目录**（有哨兵兜底）。
"""
import ast
import os
import sys
import tempfile

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from PySide6.QtWidgets import QApplication  # noqa: E402

_app = QApplication.instance() or QApplication([])

import digital_twin_panel as dt  # noqa: E402

_PASS = []
_FAIL = []


def check(name, cond, extra=""):
    if cond:
        _PASS.append(name)
        print(f"✅ {name} {extra}")
    else:
        _FAIL.append(name)
        print(f"❌ {name} {extra}")


# ---- 安全哨兵：真实产物目录（测试绝不该碰它）--------------------------------
_REAL_PRODUCTS = os.path.join(os.path.expanduser("~"), "Documents", "小臭玩AI", "产物")


def _real_snapshot():
    try:
        return sorted(os.listdir(_REAL_PRODUCTS))
    except OSError:
        return []


_REAL_BEFORE = _real_snapshot()


# ---- 全打桩跑一遍 _work ------------------------------------------------------
def _run(script, n=4, qc_plan=None, job_dir=None, resume=True, texts=None,
         app_dir=None, **kw):
    """同步跑一次 TwinGenThread._work（全打桩）。

    script: {段序号(0基): [动作, ...]} —— 该段第 c 次被调用时取 script[idx][c]，
            越界则重复最后一个。动作 "ok" = 成功；"ERR:xxx" = 生成失败。
    job_dir: 固定任务目录（断点续跑用）；app_dir 可复用，以便跑"第二次"。
    返回 (done 载荷, logs, calls, thread, app_dir)
    """
    app_dir = app_dir or tempfile.mkdtemp(prefix="xc_twin_seg_sbx_")
    seg_texts = list(texts) if texts else [f"P{k + 1}台词内容测试" for k in range(n)]
    n = len(seg_texts)
    calls = {k: 0 for k in range(n)}
    seen = {"done": None}
    logs = []
    qc_left = list(qc_plan or [])
    segs = [(t, 5) for t in seg_texts]

    def fake_gen(cfg, ad, prompt, sec, *a, **k):
        idx = next((j for j, t in enumerate(seg_texts) if t in prompt), None)
        if idx is None:
            return "ERR:未识别段号"
        seq = list(script.get(idx) or ["ok"])
        c = calls[idx]
        calls[idx] += 1
        act = seq[c] if c < len(seq) else seq[-1]
        if isinstance(act, str) and act.startswith("ERR"):
            return act
        p = os.path.join(app_dir, "products", f"seg_{idx}.mp4")
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "wb") as f:
            f.write(b"\x00" * 32)
        return (os.path.relpath(p, app_dir).replace("\\", "/"),
                "video", os.path.basename(p))

    def fake_concat(paths, out, ffmpeg=None, log=None):
        os.makedirs(os.path.dirname(out), exist_ok=True)
        with open(out, "wb") as f:
            f.write(b"\x00" * 64)
        return True

    def fake_review(cfg, portrait, probe, log=None):
        return qc_left.pop(0) if qc_left else (True, "ok")

    names = ("_ffmpeg", "fit_image_to_aspect", "extract_last_frame",
             "concat_videos", "burn_ai_mark", "extract_probe_frame")
    saved = {nm: getattr(dt, nm) for nm in names}
    saved["gen"] = dt.tools_mod.tool_video_gen
    saved["products"] = getattr(dt.tools_mod, "PRODUCTS_DIR", None)
    saved["review"] = dt.vq.review_identity

    dt._ffmpeg = lambda: "ffmpeg"
    dt.fit_image_to_aspect = lambda *a, **k: False
    dt.extract_last_frame = lambda *a, **k: True
    dt.concat_videos = fake_concat
    dt.burn_ai_mark = lambda *a, **k: False
    # 质检用例必须让"抽质检帧"成功，否则会走「抽帧失败 → 放行」分支，
    # 根本到不了 vq.review_identity（第一版就踩了这个，G1/G3 假红）。
    dt.extract_probe_frame = (lambda *a, **k: True) if kw.get("probe_ok") \
        else (lambda *a, **k: False)
    dt.tools_mod.tool_video_gen = fake_gen
    dt.tools_mod.PRODUCTS_DIR = "products"      # ⚠️ 改道沙箱，绝不写真实工作区
    dt.vq.review_identity = fake_review

    th = None
    try:
        th = dt.TwinGenThread(
            {}, app_dir, os.path.join(app_dir, "portrait.png"), segs, "场景", True,
            "768x1152", qc=kw.get("qc", False), ai_mark=kw.get("ai_mark", False),
            max_gen_retry=kw.get("max_gen_retry", 2), gen_retry_delay=0.0,
            max_qc_retry=kw.get("max_qc_retry", 2),
            resume=resume, job_dir=job_dir)
        th.log.connect(lambda m: logs.append(str(m)))
        th.done.connect(lambda r: seen.__setitem__("done", r))
        th._work()
    finally:
        for nm in names:
            setattr(dt, nm, saved[nm])
        dt.tools_mod.tool_video_gen = saved["gen"]
        if saved["products"] is None:
            try:
                del dt.tools_mod.PRODUCTS_DIR
            except AttributeError:
                pass
        else:
            dt.tools_mod.PRODUCTS_DIR = saved["products"]
        dt.vq.review_identity = saved["review"]
    return seen["done"], logs, calls, th, app_dir


# ---- 源码契约（AST 巡检，防实现被改回去）------------------------------------
def _cls_ast():
    tree = ast.parse(open(dt.__file__, encoding="utf-8-sig").read())
    cls = next(n for n in tree.body
               if isinstance(n, ast.ClassDef) and n.name == "TwinGenThread")
    return tree, cls


def _func(cls, name):
    return next(n for n in cls.body
                if isinstance(n, ast.FunctionDef) and n.name == name)


def _fail_branch(cls):
    """定位 _work 里 `if not seg_path:` 这个「单段失败」分支节点。"""
    for node in ast.walk(_func(cls, "_work")):
        if isinstance(node, ast.If):
            t = node.test
            if (isinstance(t, ast.UnaryOp) and isinstance(t.op, ast.Not)
                    and isinstance(t.operand, ast.Name) and t.operand.id == "seg_path"):
                return node
    return None


def main():
    print("=" * 60)
    print("A) 全部成功 —— 基线不变")
    print("=" * 60)
    done, logs, calls, th, ad = _run({}, n=4)
    check("A1 四段各调用一次", all(calls[k] == 1 for k in range(4)), str(calls))
    check("A2 failed_segs 为空", th.failed_segs == [], str(th.failed_segs))
    check("A3 产出成片且文件存在", isinstance(done, str) and os.path.isfile(done))

    print()
    print("=" * 60)
    print("B) ★核心：中间段永久失败 —— 后续段必须继续生成")
    print("=" * 60)
    done, logs, calls, th, ad = _run({1: ["ERR:免费队列拥堵"]}, n=4)
    check("B1 失败段确实重试到了上限（1+max_gen_retry 次）", calls[1] == 3, str(calls))
    check("B2 后续两段仍被生成（不再 break）", calls[2] >= 1 and calls[3] >= 1, str(calls))
    check("B3 failed_segs 精确记录了第 2 段",
          [n for n, _ in th.failed_segs] == [2], str(th.failed_segs))
    check("B4 仍产出成片（缺 1 段，而不是缺 3 段）",
          isinstance(done, str) and os.path.isfile(done))
    check("B5 日志明确提示缺段（不静默）",
          any(("缺少" in m) or ("缺 " in m) for m in logs))

    print()
    print("=" * 60)
    print("C) 源码契约：失败分支不许再用 break")
    print("=" * 60)
    tree, cls = _cls_ast()
    br = _fail_branch(cls)
    check("C1 找到「单段失败」分支", br is not None)
    has_break = br is not None and any(isinstance(x, ast.Break) for x in ast.walk(br))
    has_cont = br is not None and any(isinstance(x, ast.Continue) for x in ast.walk(br))
    check("C2 该分支里没有 break（AST 证明不会中断后续段）", br is not None and not has_break)
    check("C3 该分支里有 continue", has_cont)
    gen_src = ast.unparse(_func(cls, "_gen_one"))
    check("C4 生成失败分支会重试（引用 max_gen_retry）", "max_gen_retry" in gen_src)
    check("C5 重试前有可取消的退避等待", "gen_retry_delay" in gen_src and "_sleep" in gen_src)

    print()
    print("=" * 60)
    print("D) 生成失败会自动重试（原来只有质检不过才重试）")
    print("=" * 60)
    done, logs, calls, th, ad = _run({0: ["ERR:超时", "ok"]}, n=2)
    check("D1 第 1 段被调用 2 次（失败 1 次后重试成功）", calls[0] == 2, str(calls))
    check("D2 没有缺段", th.failed_segs == [], str(th.failed_segs))
    check("D3 日志有重试字样", any("重试" in m for m in logs))

    print()
    print("=" * 60)
    print("E) 重试额度可配 + 用尽即跳过该段")
    print("=" * 60)
    done, logs, calls, th, ad = _run({0: ["ERR:x"]}, n=3, max_gen_retry=1)
    check("E1 max_gen_retry=1 → 恰好尝试 2 次", calls[0] == 2, str(calls))
    check("E2 第 1 段记进 failed_segs", [n for n, _ in th.failed_segs] == [1])
    check("E3 后两段照常成功", calls[1] >= 1 and calls[2] >= 1)
    check("E4 成片仍产出", isinstance(done, str) and os.path.isfile(done))

    print()
    print("=" * 60)
    print("F) 全部失败 → 明确报错，不产出假成片")
    print("=" * 60)
    done, logs, calls, th, ad = _run({k: ["ERR:x"] for k in range(3)}, n=3,
                                     max_gen_retry=0)
    check("F1 无成片路径（done 是错误文本）",
          isinstance(done, str) and not os.path.isfile(done))
    check("F2 错误里带失败明细", isinstance(done, str) and "失败明细" in done)
    check("F3 max_gen_retry=0 → 每段只调 1 次", all(calls[k] == 1 for k in range(3)))
    check("F4 三段全部记入 failed_segs", len(th.failed_segs) == 3)

    print()
    print("=" * 60)
    print("G) 质检不过 → 重生成；若随后生成失败，宁可留有瑕疵段也不丢段")
    print("=" * 60)
    done, logs, calls, th, ad = _run({0: ["ok"]}, n=2, qc=True, probe_ok=True,
                                     qc_plan=[(False, "不像本人"), (True, "ok")])
    check("G1 质检不过后重生成（该段被调 2 次）", calls[0] == 2, str(calls))
    check("G2 最终质检通过 → 无缺段", th.failed_segs == [], str(th.failed_segs))

    done, logs, calls, th, ad = _run({0: ["ok", "ERR:挂了"]}, n=2, qc=True,
                                     probe_ok=True, max_qc_retry=1, max_gen_retry=0,
                                     qc_plan=[(False, "不像本人")])
    check("G3 质检不过→重生成→生成失败：该段**不丢**（保留上一次结果）",
          th.failed_segs == [] and calls[0] == 2, str(th.failed_segs))
    check("G4 成片仍产出", isinstance(done, str) and os.path.isfile(done))

    print()
    print("=" * 60)
    print("H) UI 契约：结果回调必须把缺段说出来 + 有「全部重新生成」开关")
    print("=" * 60)
    onres = ast.unparse(next(n for n in tree.body
                             if isinstance(n, ast.FunctionDef) and n.name == "_twin_on_result"))
    check("H1 结果回调读取 failed_segs", "failed_segs" in onres)
    check("H2 结果回调提示含「缺」字", "缺" in onres)
    check("H3 结果回调会汇报复用的段数", "reused" in onres)
    gen_src2 = ast.unparse(next(n for n in tree.body
                                if isinstance(n, ast.FunctionDef) and n.name == "_twin_generate"))
    check("H4 生成入口接了断点续跑（指纹 + resume + job_dir）",
          "_twin_fingerprint" in gen_src2 and "resume" in gen_src2
          and "job_dir" in gen_src2)
    check("H5 生成入口有「全部重新生成」开关（twin_force_redo）",
          "twin_force_redo" in gen_src2)

    print()
    print("=" * 60)
    print("I) ★断点续跑：差一段，第二次只补那一段（不重复烧额度）")
    print("=" * 60)
    _ad = tempfile.mkdtemp(prefix="xc_twin_resume_")
    _jd = os.path.join(_ad, "products", "_twin_jobs", "fp_test")
    _d1, _l1, _c1, _th1, _ = _run({1: ["ERR:挂了"]}, n=4, app_dir=_ad,
                                  job_dir=_jd, max_gen_retry=0)
    check("I1 第一次：缺第 2 段", [n for n, _ in _th1.failed_segs] == [2],
          str(_th1.failed_segs))
    check("I2 第一次：1/3/4 段都已生成",
          _c1[0] == 1 and _c1[2] == 1 and _c1[3] == 1, str(_c1))
    check("I3 有缺段 → 任务目录与 state.json 保留",
          os.path.isdir(_jd) and os.path.isfile(os.path.join(_jd, "state.json")))
    _d2, _l2, _c2, _th2, _ = _run({}, n=4, app_dir=_ad, job_dir=_jd,
                                  max_gen_retry=0)
    check("I4 第二次：复用了 3 段", _th2.reused == 3, f"reused={_th2.reused}")
    check("I5 第二次：只调用了第 2 段（额度没被重复烧）",
          _c2[0] == 0 and _c2[1] == 1 and _c2[2] == 0 and _c2[3] == 0, str(_c2))
    check("I6 第二次：无缺段", _th2.failed_segs == [], str(_th2.failed_segs))
    check("I7 第二次全部成功 → 任务目录被清理（不残留）", not os.path.isdir(_jd))
    check("I8 第二次日志提示了复用/跳过",
          any(("复用" in m) or ("跳过" in m) for m in _l2))

    print()
    print("=" * 60)
    print("J) 勾「全部重新生成」→ 无视上次进度")
    print("=" * 60)
    _ad2 = tempfile.mkdtemp(prefix="xc_twin_redo_")
    _jd2 = os.path.join(_ad2, "products", "_twin_jobs", "fp_test")
    _run({1: ["ERR:挂了"]}, n=4, app_dir=_ad2, job_dir=_jd2, max_gen_retry=0)
    _d3, _l3, _c3, _th3, _ = _run({}, n=4, app_dir=_ad2, job_dir=_jd2,
                                  resume=False, max_gen_retry=0)
    check("J1 resume=False → 四段全部重新生成", all(_c3[k] == 1 for k in range(4)),
          str(_c3))
    check("J2 resume=False → 复用数为 0", _th3.reused == 0)

    print()
    print("=" * 60)
    print("K) 台词改了 → 不许复用旧段（sha1 校验）")
    print("=" * 60)
    _ad3 = tempfile.mkdtemp(prefix="xc_twin_chg_")
    _jd3 = os.path.join(_ad3, "products", "_twin_jobs", "fp_test")
    _run({1: ["ERR:挂了"]}, n=4, app_dir=_ad3, job_dir=_jd3, max_gen_retry=0)
    _d4, _l4, _c4, _th4, _ = _run({}, n=4, app_dir=_ad3, job_dir=_jd3,
                                  max_gen_retry=0,
                                  texts=[f"改过的台词{k}" for k in range(4)])
    check("K1 台词变了 → 不复用任何段", _th4.reused == 0, f"reused={_th4.reused}")
    check("K2 台词变了 → 四段全部重新生成",
          all(_c4[k] == 1 for k in range(4)), str(_c4))

    print()
    print("=" * 60)
    print("L) 任务指纹：只随「决定段内容」的输入变化")
    print("=" * 60)
    _base = dict(dialogue="稿", portrait=os.path.join(_ad3, "nope.jpg"),
                 scene="场景", resolution="768x1152", dur=8,
                 keep_bg=True, dual_frame=True)
    _f1 = dt._twin_fingerprint(**_base)
    check("L1 同输入 → 同指纹", dt._twin_fingerprint(**_base) == _f1)
    check("L2 改台词 → 指纹变",
          dt._twin_fingerprint(**{**_base, "dialogue": "稿2"}) != _f1)
    check("L3 改场景 → 指纹变",
          dt._twin_fingerprint(**{**_base, "scene": "别的"}) != _f1)
    check("L4 改画幅 → 指纹变",
          dt._twin_fingerprint(**{**_base, "resolution": "1920x1080"}) != _f1)
    check("L5 改单段时长 → 指纹变",
          dt._twin_fingerprint(**{**_base, "dur": 10}) != _f1)
    check("L6 改「保持原图背景」→ 指纹变",
          dt._twin_fingerprint(**{**_base, "keep_bg": False}) != _f1)
    _pf = os.path.join(tempfile.mkdtemp(prefix="xc_twin_fp_"), "p.jpg")
    with open(_pf, "wb") as _f:
        _f.write(b"x" * 10)
    _fa = dt._twin_fingerprint("稿", _pf, "场景", "768x1152", 8, True, True)
    os.utime(_pf, (0, 0))
    check("L7 参考图变了（mtime）→ 指纹变",
          dt._twin_fingerprint("稿", _pf, "场景", "768x1152", 8, True, True) != _fa)

    print()
    print("=" * 60)
    print("M) 顺带修掉的既有 bug：单段 + 关 AI 标识 → 成片曾被自己删掉")
    print("=" * 60)
    _d5, _l5, _c5, _th5, _ad5 = _run({}, n=1, ai_mark=False)
    check("M1 单段成片文件存在（没被收尾清理删掉）",
          isinstance(_d5, str) and os.path.isfile(_d5), str(_d5))
    check("M2 成片落在输出目录（不在任务临时目录内）",
          os.path.dirname(_d5) == os.path.join(_ad5, "products"), _d5)

    print()
    print("=" * 60)
    print("Z) 安全哨兵：测试没有污染真实用户目录")
    print("=" * 60)
    check("Z1 真实产物目录内容未变化（沙箱改道生效）",
          _real_snapshot() == _REAL_BEFORE)

    print()
    print("=" * 60)
    print(f"汇总：PASS={len(_PASS)} FAIL={len(_FAIL)}")
    if _FAIL:
        print("失败项：", _FAIL)
    else:
        print("ALL_TWIN_SEG_RESILIENCE_OK")
    return 0 if not _FAIL else 1


if __name__ == "__main__":
    sys.exit(main())
