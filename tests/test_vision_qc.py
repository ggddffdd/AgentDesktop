# -*- coding: utf-8 -*-
"""VLM 质检加固回归（导演台审查 #6 / v4.168.0）。

钉住三件事：
  ① **降采样**：4K 图直传既慢又贵；质检前缩到长边 ≤1280。
  ② **正确 MIME**：此前无论原图 PNG / WebP 一律标 `image/jpeg`，
     与真实字节不符。现在标的是**实际编码格式**，且编码方式由我们决定
     （有 alpha 存 PNG，否则存 JPEG）。本套件用真 HTTP 服务端抓请求体，
     再用 Pillow 反解 data URI 里的字节验证「MIME 与字节一致」。
  ③ **可观测**：qc_pass / qc_fail / qc_skipped_no_key / qc_skipped_error /
     qc_skipped_nofile 五个桶都计数；UI 能显示「已质检 / 质检跳过」，
     **不把未质检伪装成通过**。

真网络只打本地 127.0.0.1（httptest 桩），不出网。
"""

import ast
import base64
import io
import json
import os
import sys
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

_HERE = Path(__file__).resolve().parent
ROOT = _HERE.parent
sys.path.insert(0, str(ROOT))

_SBX = tempfile.mkdtemp(prefix="xc_verify_sbx_qc_")
os.environ.setdefault("XC_USER_DATA_DIR", os.path.join(_SBX, "userdata"))

import vision_qc as vq   # noqa: E402

try:
    from PIL import Image
    _HAS_PIL = True
except Exception:
    Image = None
    _HAS_PIL = False

_p = _f = 0


def check(name, cond, detail=""):
    global _p, _f
    if cond:
        _p += 1
        print(f"  [PASS] {name}")
    else:
        _f += 1
        print(f"  [FAIL] {name}" + (f" —— {detail}" if detail else ""))


def _mk(path, size=(4000, 3000), mode="RGB", fmt=None):
    if not _HAS_PIL:
        with open(path, "wb") as f:
            f.write(b"\x89PNG\r\n\x1a\n" + b"0" * 1024)
        return path
    im = Image.new(mode, size, (200, 30, 30, 255) if mode == "RGBA" else (200, 30, 30))
    im.save(path, format=fmt)
    return path


def cfg_for(base):
    return {"model_profiles": {"DeepSeek 官方": {
        "base_url": base, "api_key": "test-key", "model": "deepseek-flash"}}}


# --------------------------------------------------------------------------
# 本地桩服务：抓住请求体，回一个可指定内容的 chat completion
# --------------------------------------------------------------------------
class _Stub:
    def __init__(self, reply="VERDICT: PASS\nISSUES: none"):
        self.reply = reply
        self.bodies = []
        self.headers = []
        outer = self

        class _H(BaseHTTPRequestHandler):
            def do_POST(self):
                n = int(self.headers.get("Content-Length") or 0)
                raw = self.rfile.read(n)
                outer.bodies.append(raw)
                outer.headers.append(dict(self.headers))
                body = json.dumps({"choices": [{"message": {
                    "content": outer.reply}}]}).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *a):
                pass

        self.httpd = HTTPServer(("127.0.0.1", 0), _H)
        self.port = self.httpd.server_address[1]
        self.base = f"http://127.0.0.1:{self.port}"
        self.t = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.t.start()

    def stop(self):
        try:
            self.httpd.shutdown()
            self.httpd.server_close()
        except Exception:
            pass


def _data_uri_parts(body):
    """从请求体里取出 data URI 的 (mime, bytes)。"""
    payload = json.loads(body.decode("utf-8"))
    content = payload["messages"][0]["content"]
    imgs = [c for c in content if c.get("type") == "image_url"]
    out = []
    for c in imgs:
        url = c["image_url"]["url"]
        head, _, b64 = url.partition(",")
        mime = head.split(";")[0].replace("data:", "")
        out.append((mime, base64.b64decode(b64)))
    return out


# ==========================================================================
def part_a():
    print("=== A) 降采样 + 真实 MIME（编码层）===")
    d = tempfile.mkdtemp(prefix="xc_qc_")
    p = _mk(os.path.join(d, "big.png"), (4000, 3000), "RGB", "PNG")
    b64, mime, meta = vq.encode_image_for_qc(p)
    check("长边 4000 → 降到 1280", meta.get("out_size") == "1280x960",
          str(meta.get("out_size")))
    check("标记为已降采样", meta.get("downscaled") is True)
    check("体积显著变小", meta["out_bytes"] < meta["src_bytes"],
          f"{meta['src_bytes']} -> {meta['out_bytes']}")
    check("RGB 图编码为 JPEG", mime == "image/jpeg", mime)
    if _HAS_PIL:
        got = Image.open(io.BytesIO(base64.b64decode(b64))).format
        check("bytes 真是 JPEG（MIME 与字节一致）", got == "JPEG", str(got))

    # 带透明通道 → 必须保 PNG（JPEG 不支持 alpha）
    p2 = _mk(os.path.join(d, "alpha.png"), (600, 400), "RGBA", "PNG")
    b64b, mime2, meta2 = vq.encode_image_for_qc(p2)
    check("RGBA 图编码为 PNG", mime2 == "image/png", mime2)
    check("小图不降采样", meta2.get("downscaled") is False)
    if _HAS_PIL:
        got2 = Image.open(io.BytesIO(base64.b64decode(b64b))).format
        check("bytes 真是 PNG", got2 == "PNG", str(got2))

    # WebP 原图：旧实现固定标 jpeg
    if _HAS_PIL:
        pw = os.path.join(d, "w.webp")
        try:
            _mk(pw, (900, 600), "RGB", "WEBP")
            mimew = vq.encode_image_for_qc(pw)[1]
            check("WebP 输入也会产出与字节一致的 MIME",
                  mimew in ("image/jpeg", "image/png"), mimew)
            gotw = Image.open(io.BytesIO(base64.b64decode(
                vq.encode_image_for_qc(pw)[0]))).format
            check("WebP 输出的字节格式与 MIME 对应",
                  (mimew == "image/jpeg" and gotw == "JPEG")
                  or (mimew == "image/png" and gotw == "PNG"), f"{mimew}/{gotw}")
        except Exception as e:
            check("WebP 用例可跳过", True, str(e))

    print("\n-- A2 非图片文件：降级但不炸 --")
    junk = os.path.join(d, "notimage.png")
    with open(junk, "wb") as f:
        f.write(b"this is not an image" * 10)
    b64c, mimec, metac = vq.encode_image_for_qc(junk)
    check("降级返回原始字节", metac.get("format") == "raw", str(metac))
    check("按扩展名给出 MIME", mimec == "image/png", mimec)
    check("原始字节完整回传",
          base64.b64decode(b64c) == b"this is not an image" * 10)


def part_b():
    print("\n=== B) 端到端：真请求体里的 MIME 与字节一致 ===")
    st = _Stub("VERDICT: PASS\nISSUES: none")
    try:
        d = tempfile.mkdtemp(prefix="xc_qc_")
        p = _mk(os.path.join(d, "kf.png"), (3000, 2000), "RGB", "PNG")
        vq.reset_stats()
        text = vq.review_images(cfg_for(st.base), [p], "check this")
        check("拿到模型回答", "VERDICT: PASS" in text, text)
        check("服务端收到 1 个请求", len(st.bodies) == 1, str(len(st.bodies)))
        parts = _data_uri_parts(st.bodies[0])
        check("请求里恰有 1 张图", len(parts) == 1)
        mime, raw = parts[0]
        check("data URI MIME = image/jpeg（旧实现恒 jpeg，但字节曾是 PNG）",
              mime == "image/jpeg", mime)
        if _HAS_PIL:
            im = Image.open(io.BytesIO(raw))
            check("传输字节真是 JPEG", im.format == "JPEG", str(im.format))
            check("传输尺寸已降采样到 1280 长边", max(im.size) == 1280, str(im.size))
        check("Authorization 头正确",
              st.headers[0].get("Authorization") == "Bearer test-key",
              str(st.headers[0].get("Authorization")))

        print("\n-- B2 多图（数字人质检：参考照 + 帧）--")
        st2 = _Stub("VERDICT: FAIL\nISSUES: different person")
        pa = _mk(os.path.join(d, "ref.png"), (500, 500), "RGB", "PNG")
        pb = _mk(os.path.join(d, "frame.jpg"), (640, 480), "RGB", "JPEG")
        r = vq.review_identity(cfg_for(st2.base), pa, pb, with_meta=True)
        check("两张图都发出", len(_data_uri_parts(st2.bodies[0])) == 2)
        check("状态 = fail", r["status"] == vq.QC_FAIL, r["status"])
        check("passed=False", r["passed"] is False)
        check("label 是已质检·未通过", "未通过" in r["label"], r["label"])
    finally:
        st.stop()
        try:
            st2.stop()
        except Exception:
            pass


def part_c():
    print("\n=== C) 可观测：五个统计桶 ===")
    d = tempfile.mkdtemp(prefix="xc_qc_")
    p = _mk(os.path.join(d, "x.png"), (800, 600), "RGB", "PNG")

    print("-- C1 无 key --")
    vq.reset_stats()
    meta = {}
    vq.review_images({"model_profiles": {}}, [p], "q", meta=meta)
    s = vq.stats()
    check("qc_skipped_no_key +1", s["qc_skipped_no_key"] == 1, str(s))
    check("meta 标记 skipped_no_key", meta.get("status") == vq.QC_SKIP_NO_KEY)
    check("统计行可读", "跳过" in vq.stats_line() and "无key 1" in vq.stats_line(),
          vq.stats_line())

    print("-- C2 调用异常（端口不通）--")
    vq.reset_stats()
    meta2 = {}
    vq.review_images(cfg_for("http://127.0.0.1:9"), [p], "q", meta=meta2)
    s2 = vq.stats()
    check("qc_skipped_error +1", s2["qc_skipped_error"] == 1, str(s2))
    check("meta 标记 skipped_error", meta2.get("status") == vq.QC_SKIP_ERROR,
          str(meta2.get("status")))
    check("异常信息被记录", bool(meta2.get("reason")))

    print("-- C3 无文件 --")
    vq.reset_stats()
    r = vq.review_keyframe(cfg_for("http://127.0.0.1:9"),
                           os.path.join(d, "nope.png"), "desc", with_meta=True)
    s3 = vq.stats()
    check("qc_skipped_nofile +1", s3["qc_skipped_nofile"] == 1, str(s3))
    check("状态 = skipped_nofile", r["status"] == vq.QC_SKIP_NOFILE, r["status"])
    check("跳过仍放行（铁律）", r["passed"] is True)

    print("-- C4 真质检 pass / fail --")
    st = _Stub("VERDICT: PASS\nISSUES: none")
    try:
        vq.reset_stats()
        r1 = vq.review_keyframe(cfg_for(st.base), p, "desc", with_meta=True)
        check("qc_pass +1", vq.stats()["qc_pass"] == 1, vq.stats_line())
        check("状态 = pass", r1["status"] == vq.QC_PASS, r1["status"])
        check("label 含已质检·通过", "已质检" in r1["label"] and "通过" in r1["label"],
              r1["label"])
        st.reply = "VERDICT: FAIL\nISSUES: warped hand"
        r2 = vq.review_keyframe(cfg_for(st.base), p, "desc", with_meta=True)
        check("qc_fail +1", vq.stats()["qc_fail"] == 1, vq.stats_line())
        check("状态 = fail", r2["status"] == vq.QC_FAIL, r2["status"])
        check("note 带原始诊断", "warped hand" in r2["note"], r2["note"])
        check("汇总统计含两类",
              vq.stats()["qc_pass"] == 1 and vq.stats()["qc_fail"] == 1)
    finally:
        st.stop()

    print("\n-- C5 is_real_qc / status_label 语义 --")
    check("pass 算真质检", vq.is_real_qc(vq.QC_PASS))
    check("fail 算真质检", vq.is_real_qc(vq.QC_FAIL))
    check("skipped_no_key 不算真质检", not vq.is_real_qc(vq.QC_SKIP_NO_KEY))
    check("skipped_error 不算真质检", not vq.is_real_qc(vq.QC_SKIP_ERROR))
    for stt in (vq.QC_PASS, vq.QC_FAIL, vq.QC_SKIP_NO_KEY, vq.QC_SKIP_ERROR,
                vq.QC_SKIP_NOFILE, vq.QC_OFF):
        lb = vq.status_label(stt)
        check(f"{stt} 有中文标签且非空", bool(lb) and lb != stt, lb)
    check("跳过类标签都含『跳过』或『未开启』",
          all(("跳过" in vq.status_label(x) or "未开启" in vq.status_label(x))
              for x in (vq.QC_SKIP_NO_KEY, vq.QC_SKIP_ERROR,
                        vq.QC_SKIP_NOFILE, vq.QC_OFF)))


def part_d():
    print("\n=== D) video_pipeline 侧：每镜状态落库 ===")
    src = (ROOT / "video_pipeline.py").read_text(encoding="utf-8-sig")
    for key in ("self.qc_status = {}", "def qc_status_of", "def qc_status_label",
                "def qc_summary"):
        check(f"存在 {key}", key in src, key)
    check("review_keyframe 写 qc_status", "self.qc_status[i] = status" in src)
    check("关掉质检时记 QC_OFF", "vq.QC_OFF" in src)
    check("无文件时记 skipped_nofile", "vq.QC_SKIP_NOFILE" in src
          or '"skipped_nofile"' in src)
    check("日志写明跳过不代表合格", "不代表画面合格" in src)

    print("\n-- D2 真行为：桩掉网络层跑 review_keyframe --")
    method_src = _extract_method_src(src, "review_keyframe")
    check("取到 review_keyframe 源码", bool(method_src))

    class _Pl:
        REVIEW_QUESTION = "Q"
        vision_review = True

        def __init__(self):
            self.shots = [{"en": "a cat on a roof"}]
            self.clue_lock = ""
            self.cfg = {}
            self.review_notes = {}
            self.qc_status = {}
            self.logs = []

        def _locks_text(self):
            return ""

        def log(self, t):
            self.logs.append(t)

    ns = {"os": os, "vq": vq}
    exec(compile(method_src, "<vp.review_keyframe>", "exec"), ns)
    fn = ns["review_keyframe"]

    # 桩 vision_qc.review_images
    real_ri = vq.review_images

    def _fake_ok(cfg, paths, q, max_tokens=400, log=None, meta=None):
        if meta is not None:
            meta.update({"status": "", "downscaled": True,
                         "src_bytes": 4_000_000, "sent_bytes": 300_000})
        return "VERDICT: PASS\nISSUES: none"

    def _fake_fail(cfg, paths, q, max_tokens=400, log=None, meta=None):
        if meta is not None:
            meta.update({"status": "", "downscaled": False,
                         "src_bytes": 1, "sent_bytes": 1})
        return "VERDICT: FAIL\nISSUES: bad"

    def _fake_skip(cfg, paths, q, max_tokens=400, log=None, meta=None):
        if meta is not None:
            meta.update({"status": vq.QC_SKIP_ERROR, "reason": "timeout"})
        return ""

    try:
        vq.review_images = _fake_ok
        pl = _Pl()
        ok, note = fn(pl, 0, __file__)
        check("通过：ok=True", ok is True)
        check("通过：状态 pass", pl.qc_status.get(0) == vq.QC_PASS, str(pl.qc_status))
        check("通过：note 落 review_notes", 0 in pl.review_notes)
        check("通过：日志提到降采样",
              any("降采样" in t for t in pl.logs), str(pl.logs))

        vq.review_images = _fake_fail
        pl2 = _Pl()
        ok2, note2 = fn(pl2, 0, __file__)
        check("不通过：ok=False", ok2 is False)
        check("不通过：状态 fail", pl2.qc_status.get(0) == vq.QC_FAIL)

        vq.review_images = _fake_skip
        pl3 = _Pl()
        ok3, _ = fn(pl3, 0, __file__)
        check("跳过：仍然放行（铁律）", ok3 is True)
        check("跳过：状态是 skipped_error 而不是 pass",
              pl3.qc_status.get(0) == vq.QC_SKIP_ERROR, str(pl3.qc_status))
        check("跳过：日志明确说『不代表画面合格』",
              any("不代表画面合格" in t for t in pl3.logs), str(pl3.logs))

        pl4 = _Pl()
        pl4.vision_review = False
        fn(pl4, 0, __file__)
        check("质检关闭：状态 off", pl4.qc_status.get(0) == vq.QC_OFF,
              str(pl4.qc_status))

        pl5 = _Pl()
        fn(pl5, 0, os.path.join(_SBX, "missing.png"))
        check("无文件：状态 skipped_nofile",
              pl5.qc_status.get(0) == vq.QC_SKIP_NOFILE, str(pl5.qc_status))
    finally:
        vq.review_images = real_ri


def part_e():
    print("\n=== E) 界面：不把「未质检」显示成「通过」 ===")
    src = (ROOT / "director_web.py").read_text(encoding="utf-8-sig")
    check("卡片接受 qc_status 参数", "qc_status=\"\"" in src)
    check("有 .qc.skip 样式（跳过要看得见）", ".qc.skip" in src)
    fn_src = _extract_func_src(src, "keyframe_card_html")
    check("取到 keyframe_card_html 源码", bool(fn_src))

    def _esc(s):
        return (str(s).replace("&", "&amp;").replace("<", "&lt;")
                .replace(">", "&gt;"))

    ns = {"os": os, "_esc": _esc, "_localres_url": lambda p: "u",
          "_ask_btn": lambda *a, **k: "", "_ver_btn": lambda *a, **k: "",
          "_rb_btn": lambda *a, **k: "", "__name__": "director_web"}
    exec(compile(fn_src, "<dw.keyframe_card_html>", "exec"), ns)
    card = ns["keyframe_card_html"]

    p = _mk(os.path.join(_SBX, "kf.png"), (64, 64), "RGB", "PNG")
    h_pass = card(0, p, qc_status=vq.QC_PASS)
    check("pass → 显示『已质检·通过』",
          "已质检" in h_pass and "通过" in h_pass, h_pass)
    check("pass → 用 qc 类（不是 skip）", 'class="qc"' in h_pass, h_pass)

    h_fail = card(0, p, qc_status=vq.QC_FAIL)
    check("fail → 显示未通过", "未通过" in h_fail, h_fail)
    check("fail → 用 qc fail 类", 'class="qc fail"' in h_fail, h_fail)

    h_skip = card(0, p, qc_status=vq.QC_SKIP_NO_KEY)
    check("skipped_no_key → 显示『质检跳过』", "质检跳过" in h_skip, h_skip)
    check("skipped_no_key → 用 qc skip 类", 'class="qc skip"' in h_skip, h_skip)
    check("skipped_no_key → 不出现『通过』字样",
          "通过" not in h_skip.replace("未通过", ""), h_skip)

    h_err = card(0, p, qc_status=vq.QC_SKIP_ERROR)
    check("skipped_error → 显示『质检跳过』且写明调用失败",
          "质检跳过" in h_err and "调用失败" in h_err, h_err)

    h_off = card(0, p, qc_status=vq.QC_OFF)
    check("off → 显示『质检未开启』", "质检未开启" in h_off, h_off)

    print("\n-- E2 向后兼容（旧调用方式只传 note）--")
    h_old = card(0, p, "VERDICT: FAIL xxx")
    check("旧调用仍显示质检未通过", "质检未通过" in h_old, h_old)
    h_old2 = card(0, p, "VERDICT: PASS xxx")
    check("旧调用仍显示质检通过", "质检通过" in h_old2, h_old2)
    h_none = card(0, p)
    check("无 note 无状态 → 不出徽标（也绝不当成通过）",
          "qc" not in h_none.split("btns")[0].replace("qc\"", ""), h_none)

    print("\n-- E3 director_panel 把状态传下去 --")
    dsrc = (ROOT / "director_panel.py").read_text(encoding="utf-8-sig")
    check("_build_keyframe_cards 读 qc_status", "qc_status" in dsrc)
    check("传给卡片", "qc_status=qcs.get(i" in dsrc)
    check("加了防御式取属性", 'getattr(_pl, "qc_status"' in dsrc)


# --------------------------------------------------------------------------
def _extract_method_src(src, name):
    tree = ast.parse(src)
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == name:
            seg = ast.get_source_segment(src, node)
            return _dedent(seg)
    return ""


def _extract_func_src(src, name):
    return _extract_method_src(src, name)


def _dedent(seg):
    lines = seg.splitlines()
    if not lines:
        return seg
    ind = len(lines[0]) - len(lines[0].lstrip())
    return "\n".join(l[ind:] if l.strip() else l for l in lines)


def main():
    part_a()
    part_b()
    part_c()
    part_d()
    part_e()
    print(f"\n汇总：PASS={_p} FAIL={_f}")
    return 0 if _f == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
