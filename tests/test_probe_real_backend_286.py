# -*- coding: utf-8 -*-
"""真后端探针判据（probe_real_backend.py，v4.286.0）——「第四层测试」的自守。

这份判据守的不是探针「跑没跑」，而是探针的**状态语义**不许退化：
  · SKIP 不冒充通过（没 key 时必须 SKIP + 横幅，不许变 PASS）；
  · 前提齐了必须真调（不许永远 SKIP 装绿）；
  · 真调失败必须 fail-closed（403/401 类事故唯一自动拦截层，不许降级成 WARNING）；
  · env 兜底只进探针 cfg，不回写用户配置。

行为组全部 monkeypatch 网络层（voice.synthesize / tools.tool_web_search /
tools.tool_image_gen），零真实 API 调用——真调是 nightly workflow 的事，
主回归里判据只验分支逻辑。

独立运行：python tests/test_probe_real_backend_286.py
"""
import importlib
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

import probe_real_backend as probe

FAIL = []
CHECKED = 0


def check(name, ok, extra=""):
    global CHECKED
    CHECKED += 1
    tag = "OK  " if ok else "FAIL"
    print(f"  [{tag}] {name}" + (f"  — {extra}" if extra and not ok else ""))
    if not ok:
        FAIL.append(name)


def _hook(t, v, tb):
    print(f"\n[!] 未捕获异常：{t.__name__}: {v}")
    print(f"PASS={CHECKED - len(FAIL)} FAIL={len(FAIL)}")
    sys.exit(1)


sys.excepthook = _hook

import voice
import tools


# ===== A. 静态组：探针结构与语义在场（按函数体切片，防关键词误报）=====
SRC = open(os.path.join(ROOT, "probe_real_backend.py"), encoding="utf-8").read()

check("A1 三个探针函数齐在场",
      all(("def probe_%s(" % n) in SRC for n in ("edge_tts", "web_search", "image_gen")))
check("A2 fail-closed：FAIL 明细分支在位（rc=1 出口）",
      "return 1" in SRC and "FAIL 明细" in SRC)
check("A3 SKIP 不冒充通过：横幅文案在位",
      "SKIP 不冒充通过" in SRC and "PASS=%d FAIL=0 SKIP=%d" in SRC)
check("A4 env 兜底不回写用户配置（注释声明 + 只写局部 dict）",
      "只在探针内合并，不回写用户 config" in SRC
      and "cfg = dict(config.load_config() or {})" in SRC)
check("A5 edge-tts 异常不做额度豁免（v4.283.1 教训注释在场）",
      "不做额度豁免" in SRC)


# ===== B. 行为组：web_search 的状态分支 =====
_CFG = {"brave_api_key": "k"}

def _ws(text):
    def fake(cfg, query, app_dir=None):
        return text
    return fake

_orig_ws = tools.tool_web_search
try:
    # B1 无 key → SKIP（不冒充通过）
    tools.tool_web_search = _ws("x" * 200)
    st, detail = probe.probe_web_search({})
    check("B1 web_search 无 key → SKIP", st == probe.SKIP, "st=%s %s" % (st, detail))

    # B2 有 key + 正常返回 → PASS
    tools.tool_web_search = _ws("昆明天气：晴，气温 12~22℃。数据来源 open-meteo。" + "详情 " * 20)
    st, detail = probe.probe_web_search(dict(_CFG))
    check("B2 web_search 有 key + 正常返回 → PASS", st == probe.PASS, "st=%s %s" % (st, detail))

    # B3 有 key + 真调异常 → FAIL（fail-closed，不降级）
    def _boom(cfg, query, app_dir=None):
        raise RuntimeError("HTTPError 403: Sec-MS-GEC")
    tools.tool_web_search = _boom
    st, detail = probe.probe_web_search(dict(_CFG))
    check("B3 web_search 真调异常 → FAIL（fail-closed）", st == probe.FAIL,
          "st=%s %s" % (st, detail))

    # B4 有 key + 返回错误前缀 → FAIL（静默失败形状）
    tools.tool_web_search = _ws("搜索失败：所有渠道均不可用，请检查 key。")
    st, detail = probe.probe_web_search(dict(_CFG))
    check("B4 web_search 返回错误前缀 → FAIL", st == probe.FAIL, "st=%s %s" % (st, detail))

    # B5 有 key + 返回过短 → FAIL（疑似没真跑）
    tools.tool_web_search = _ws("晴")
    st, detail = probe.probe_web_search(dict(_CFG))
    check("B5 web_search 返回过短 → FAIL", st == probe.FAIL, "st=%s %s" % (st, detail))
finally:
    tools.tool_web_search = _orig_ws


# ===== C. 行为组：image_gen 的状态分支 =====
_orig_ig = tools.tool_image_gen
_orig_creds = tools._agnes_creds
_tmp_png = os.path.join(ROOT, "probe_ig_ok_286.png")  # 真实 rel 相对 app_dir(=ROOT)，夹具同律
with open(_tmp_png, "wb") as f:
    f.write(b"\x89PNG\r\n\x1a\n" + b"0" * 4096)
try:
    # C1 provider=gateway 无 api_key → SKIP（C3 复用本 lambda，返回真实形状）
    tools.tool_image_gen = lambda cfg, app_dir, prompt, size=None, progress=None, image=None: \
        ("probe_ig_ok_286.png", "image", "probe_ig_ok_286.png")
    st, detail = probe.probe_image_gen({"image_gen_provider": "gateway"})
    check("C1 image_gen gateway 无 key → SKIP", st == probe.SKIP, "st=%s %s" % (st, detail))

    # C2 agnes provider 无 key → SKIP（走 _agnes_creds 判定）
    tools._agnes_creds = lambda cfg: ("https://api.agnes-ai.cn/v1", "")
    st, detail = probe.probe_image_gen({"image_gen_provider": "agnes"})
    check("C2 image_gen agnes 无 key → SKIP", st == probe.SKIP, "st=%s %s" % (st, detail))

    # C3 有 key + 返回 3 元组且文件存在 → PASS
    tools._agnes_creds = lambda cfg: ("https://api.agnes-ai.cn/v1", "cpk-x")
    st, detail = probe.probe_image_gen({"image_gen_provider": "agnes"})
    check("C3 image_gen 有 key + 正常落盘 → PASS", st == probe.PASS, "st=%s %s" % (st, detail))

    # C4 有 key + 真调异常 → FAIL
    def _boom2(cfg, app_dir, prompt, size=None, progress=None, image=None):
        raise RuntimeError("401 Unauthorized")
    tools.tool_image_gen = _boom2
    st, detail = probe.probe_image_gen({"image_gen_provider": "agnes"})
    check("C4 image_gen 真调异常 → FAIL", st == probe.FAIL, "st=%s %s" % (st, detail))

    # C5 有 key + 返回非 3 元组（错误串形状）→ FAIL
    tools.tool_image_gen = lambda cfg, app_dir, prompt, size=None, progress=None, image=None: \
        "生成失败：模型不存在"
    st, detail = probe.probe_image_gen({"image_gen_provider": "agnes"})
    check("C5 image_gen 返回错误串 → FAIL", st == probe.FAIL, "st=%s %s" % (st, detail))

    # C6 有 key + 返回 3 元组但文件不存在 → FAIL
    tools.tool_image_gen = lambda cfg, app_dir, prompt, size=None, progress=None, image=None: \
        ("nope_missing.png", "image", "nope_missing.png")
    st, detail = probe.probe_image_gen({"image_gen_provider": "agnes"})
    check("C6 image_gen 路径不存在 → FAIL", st == probe.FAIL, "st=%s %s" % (st, detail))
finally:
    tools.tool_image_gen = _orig_ig
    tools._agnes_creds = _orig_creds
    if os.path.exists(_tmp_png):
        os.remove(_tmp_png)


# ===== D. 行为组：edge-tts 的状态分支 =====
_orig_syn = voice.synthesize
_ok_mp3 = os.path.join(tempfile.gettempdir(), "probe_tts_ok.mp3")
with open(_ok_mp3, "wb") as f:
    f.write(b"ID3" + b"0" * 4096)
_empty_mp3 = os.path.join(tempfile.gettempdir(), "probe_tts_empty.mp3")
open(_empty_mp3, "wb").close()
try:
    # D1 依赖不可用 → SKIP（import 失败形状）
    _saved = sys.modules.get("edge_tts")
    sys.modules["edge_tts"] = None
    try:
        st, detail = probe.probe_edge_tts({})
        check("D1 edge_tts 库不可用 → SKIP", st == probe.SKIP, "st=%s %s" % (st, detail))
    finally:
        if _saved is not None:
            sys.modules["edge_tts"] = _saved
        else:
            sys.modules.pop("edge_tts", None)

    # D2 正常合成 + 产物足尺寸 → PASS
    voice.synthesize = lambda text, tts, out_path=None: _ok_mp3
    st, detail = probe.probe_edge_tts({})
    check("D2 edge_tts 正常合成 → PASS", st == probe.PASS, "st=%s %s" % (st, detail))

    # D3 合成异常 → FAIL（403 形状：不降级成 WARNING）
    def _boom3(text, tts, out_path=None):
        raise RuntimeError("403 Sec-MS-GEC token expired")
    voice.synthesize = _boom3
    st, detail = probe.probe_edge_tts({})
    check("D3 edge_tts 合成异常 → FAIL（不降级）", st == probe.FAIL, "st=%s %s" % (st, detail))

    # D4 产物 0 字节 → FAIL（空文件形状）
    voice.synthesize = lambda text, tts, out_path=None: _empty_mp3
    st, detail = probe.probe_edge_tts({})
    check("D4 edge_tts 空产物 → FAIL", st == probe.FAIL, "st=%s %s" % (st, detail))
finally:
    voice.synthesize = _orig_syn
    for p in (_ok_mp3, _empty_mp3):
        if os.path.exists(p):
            os.remove(p)


# ===== E. 行为组：main 汇总语义（fail-closed 总闸）=====
_orig_probes = probe.PROBES
try:
    # E1 全 SKIP → rc=0 且打 SKIP 横幅（环境缺前提不是代码的错）
    probe.PROBES = (("p1", lambda cfg: (probe.SKIP, "no key")),)
    import io
    import contextlib
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        rc = probe.main()
    out = buf.getvalue()
    check("E1 全 SKIP → rc=0", rc == 0, "rc=%s" % rc)
    check("E2 全 SKIP → 打 SKIP 横幅（不冒充通过）", "SKIP 不冒充通过" in out)

    # E3 任一 FAIL → rc=1（fail-closed 总闸）
    probe.PROBES = (("p1", lambda cfg: (probe.PASS, "ok")),
                    ("p2", lambda cfg: (probe.FAIL, "403")))
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        rc = probe.main()
    check("E3 任一 FAIL → rc=1（fail-closed）", rc == 1, "rc=%s" % rc)

    # E4 探针自身崩了 → FAIL（裸异常不许穿透成「看似没跑」）
    def _boom_probe(cfg):
        raise ValueError("probe crashed")
    probe.PROBES = (("p1", _boom_probe),)
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        rc = probe.main()
    out = buf.getvalue()
    check("E4 探针自身异常 → rc=1 且记 FAIL", rc == 1 and "探针自身异常" in out,
          "rc=%s" % rc)
finally:
    probe.PROBES = _orig_probes


print(f"\nPASS={CHECKED - len(FAIL)} FAIL={len(FAIL)}")
sys.exit(1 if FAIL else 0)
