# -*- coding: utf-8 -*-
"""第 5/6 步增强判据套件：Agnes 真接入（inpaint / 视频）+ 促销动效（promo_fx）。

设计：判据**不**真打 Agnes 网络（用 mock 回调验证「注入生效」），因此无头沙箱可全绿。
真实网络行为集中在 agnes_bridge.py，由大哥手动点「运行」触发。

判据三段：
  A 静态  —— 模块 / 函数 / 参数透传 / UI 符号存在性
  B 行为  —— mock inpaint_fn / video_fn / motion_fn 注入后真正生效并落盘登记
  C 结构  —— 切片含 Agnes 端点调用 / video_fn 调用 / motion_fn 透传
支持 CX_PATH（限定本文件）/ CX_CHECK（前缀过滤某几条判据，加速扰动脚本）。
"""

import os
import re
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import canvas_graph as cg                     # noqa: E402
import executors as ex                         # noqa: E402
import agnes_bridge as ab                      # noqa: E402
import image_local_edit as il                 # noqa: E402

# --------------------------------------------------------------------------
# 判据框架
# --------------------------------------------------------------------------
_passed = 0
_failed = 0
_log = []


def check(name, cond, detail=""):
    global _passed, _failed
    if cond:
        _passed += 1
        _log.append(("PASS", name, detail))
    else:
        _failed += 1
        _log.append(("FAIL", name, detail))


CX_PATH = os.environ.get("CX_PATH")
CX_CHECK = os.environ.get("CX_CHECK")


def active(name):
    if CX_PATH and CX_PATH not in __file__:
        return False
    if CX_CHECK and not name.startswith(CX_CHECK):
        return False
    return True


def src_of(name):
    with open(os.path.join(ROOT, name), encoding="utf-8") as f:
        return f.read()


AGNES = src_of("agnes_bridge.py")
EXEC = src_of("executors.py")
GRAPH = src_of("canvas_graph.py")
PANEL = src_of("canvas_panel.py")


# --------------------------------------------------------------------------
# A 静态
# --------------------------------------------------------------------------
if active("A1"):
    check("A1 agnes_bridge 模块可导入", True)
if active("A2"):
    for fn in ("agnes_image_inpaint", "agnes_video_generate", "pil_promo_motion",
               "get_agnes_inpaint_fn", "get_agnes_video_fn", "make_promo_motion_preview"):
        check("A2 agnes_bridge 含函数 %s" % fn, ("def %s(" % fn) in AGNES)
if active("A3"):
    for fn in ("gen_video_executor", "promo_fx_executor", "_upstream_asset_paths"):
        check("A3 executors 含函数 %s" % fn, ("def %s(" % fn) in EXEC)
if active("A4"):
    check("A4 use_real_executors 透传 video_fn", "video_fn" in GRAPH)
    check("A4b use_real_executors 透传 motion_fn", "motion_fn" in GRAPH)
if active("A5"):
    check("A5 canvas_panel 含 _make_promo_motion 方法",
          "def _make_promo_motion(self)" in PANEL)
    check("A5b 工具栏有『促销动效预览』按钮文本", "促销动效预览" in PANEL)
if active("A6"):
    check("A6 agnes_image_inpaint 签名兼容 inpaint_fn(out,region,instr,params)",
          "def agnes_image_inpaint(arr, region, instruction, params" in AGNES)
if active("A7"):
    check("A7 DEFAULT_EXECUTORS 注册 gen_video / promo_fx",
          '"gen_video"' in EXEC and '"promo_fx"' in EXEC)
if active("A8"):
    # 2026-10-03 泄露事故防回潮守卫：源码里**绝不允许**出现明文 API key。
    # 背景：agnes_bridge.py 曾硬编码国内站会员 key，随公开仓库 ggddffdd/AgentDesktop
    # 的提交 3c9d285 泄露（打包产物 PYZ 内亦带明文）。此处按「sk- + 长随机串」形态判定，
    # 注释/文档里的示意写法（如 sk-explicit、sk-xxxx）不匹配长随机串，不会误报。
    _leak = re.findall(r"sk-[A-Za-z0-9_-]{24,}", AGNES)
    check("A8 源码不得出现明文 API key（防泄露回潮）", not _leak,
          "命中 %d 处: %s" % (len(_leak), _leak[:2]))
if active("A9"):
    # 凭据必须来自「运行时可换」的通道，而不是烧死在代码里
    check("A9 凭据解析链：env AGNES_API_KEY 优先",
          "AGNES_API_KEY" in AGNES, "否则无法换号/换站")
    check("A9b 凭据解析链：回落用户配置 model_profiles.Agnes.api_key",
          'prof.get("api_key")' in AGNES and "_user_config_agnes_key" in AGNES
          and "def _default_cred(" in AGNES,
          "否则换 key 必须改源码重打包（断言的是真实读取表达式，不是注释里的字样）")
    check("A9c 缺 key 明确报错，不静默用假 key",
          "Agnes 凭据缺失" in AGNES)


# --------------------------------------------------------------------------
# B 行为（mock 注入，不真打网络）
# --------------------------------------------------------------------------
def _touch(path, payload=b"x"):
    """写一个非空占位文件（含父目录），供 mock 回调模拟「真产出」。
    内容不参与校验，只满足 存在 + 非空；扩展名由调用方给对。"""
    d = os.path.dirname(path)
    if d:
        os.makedirs(d, exist_ok=True)
    with open(path, "wb") as f:
        f.write(payload)
    return path


class _Calls:
    def __init__(self):
        self.inpaint = []
        self.video = []
        self.motion = []

    def do_inpaint(self, out, region, instruction, params):
        self.inpaint.append((region, instruction))
        return out

    def do_video(self, node, asset_root, src_image_path, prompt):
        nid = getattr(node, "id", "x")
        self.video.append((nid, src_image_path, prompt))
        p = os.path.join(asset_root, "video", "%s_clip.mp4" % nid)
        # 必须真落盘：执行器侧 validate_output（Wave A #6）校验 存在+非空+扩展名+在资产目录内，
        # 返回不存在的路径会诚实地判 failed —— mock 若不写文件，测的就不是「注入生效」而是假绿。
        _touch(p, b"\x00\x00\x00\x18ftypmp42")
        return p

    def do_motion(self, src_paths, out_path, params):
        self.motion.append((src_paths, out_path))
        p = out_path + ".gif"
        _touch(p, b"GIF89a")
        return p


if active("B1"):
    g = cg.build_sample_graph()
    g.nodes["img"].config = {
        "prompt": "测试",
        "local_edits": [{"mode": "inpaint",
                         "region": {"type": "rect", "x": 0.1, "y": 0.1, "w": 0.3, "h": 0.3},
                         "instruction": "去掉右下角水印"}],
    }
    root = tempfile.mkdtemp()
    c = _Calls()
    g.use_real_executors(root, inpaint_fn=c.do_inpaint)
    g.run({})
    edited = g.nodes["img"].out_assets.get("image")
    check("B1 注入的 inpaint_fn 被调用", len(c.inpaint) >= 1, "calls=%d" % len(c.inpaint))
    check("B1b 编辑结果文件已落盘", edited is not None and os.path.exists(edited.path),
          str(edited.path if edited else None))

if active("B2"):
    g2 = cg.build_sample_graph()
    root2 = tempfile.mkdtemp()
    cv = _Calls()
    g2.use_real_executors(root2, video_fn=cv.do_video)
    g2.run({})
    vid = g2.nodes["vid"].out_assets.get("clip")
    img_path = g2.nodes["img"].out_assets.get("image")
    expected = os.path.join(root2, "video", "vid_clip.mp4")
    check("B2 gen_video 调用 video_fn", len(cv.video) >= 1, "calls=%d" % len(cv.video))
    check("B2b clip 资产已登记且路径=video_fn返回",
          vid is not None and vid.path == expected, str(vid.path if vid else None))
    check("B2c video_fn 收到上游 image 路径",
          vid is not None and img_path is not None and cv.video[0][1] == img_path.path,
          "%s vs %s" % (cv.video[0][1], img_path.path if img_path else None))

if active("B3"):
    g3 = cg.build_sample_graph()
    root3 = tempfile.mkdtemp()
    cm = _Calls()
    # promo 的上游 vid(gen_video) 需要 video_fn 才能成功，否则 vid failed 会阻塞 promo
    g3.use_real_executors(root3, video_fn=cm.do_video, motion_fn=cm.do_motion)
    g3.run({})
    promo = g3.nodes["promo"].out_assets.get("video")
    check("B3 promo_fx 调用 motion_fn", len(cm.motion) >= 1, "calls=%d" % len(cm.motion))
    check("B3b video 资产路径 = motion 返回",
          promo is not None and promo.path == cm.motion[0][1] + ".gif",
          str(promo.path if promo else None))

if active("B4"):
    # 独立建图并 run（gen_image 本地 PIL 生真实 image 文件），再生成促销动效预览
    g4 = cg.build_sample_graph()
    root4 = tempfile.mkdtemp()
    cv4 = _Calls()
    g4.use_real_executors(root4, video_fn=cv4.do_video)
    g4.run({})
    out_gif = os.path.join(root4, "promo_preview.gif")
    p = ab.make_promo_motion_preview(g4, root4, out_gif)
    check("B4 促销动效预览 GIF 已生成", os.path.exists(p), p)
    if os.path.exists(p):
        from PIL import Image
        im = Image.open(p)
        n = getattr(im, "n_frames", 1)
        check("B4b GIF 可打开且帧数>=1", n >= 1, "frames=%d" % n)

if active("B5"):
    g5 = cg.build_sample_graph()
    g5.nodes["img"].out_assets["image"] = cg.AssetRef(
        kind="image", name="img", path="Z:/fake_img.png")
    paths = ex._upstream_asset_paths(g5, g5.nodes["promo"], "image")
    check("B5 递归收集上游 image 资产", "Z:/fake_img.png" in paths, str(paths))

if active("B6"):
    # 无 image 资产时 pil_promo_motion 退化帧仍可生成合法 GIF
    import io
    out = os.path.join(tempfile.mkdtemp(), "degenerate.gif")
    r = ab.pil_promo_motion([], out, {"caption": "测试促销"})
    check("B6 无帧时退化生成 GIF", os.path.exists(r), r)


if active("B7"):
    # 行为：凭据解析（不真打网络）。要覆盖三种来源与失败路径 —— 只看静态字符串
    # 挡不住「解析链写反了」（例如 env 覆盖不到、config 路径拼错）。
    _env_bak = os.environ.pop("AGNES_API_KEY", None)
    _base_bak = os.environ.pop("AGNES_BASE_URL", None)
    try:
        k1, b1 = ab._resolve_cred(None, None)
        check("B7 无 env 时从用户配置解析出 key", bool(k1) and k1.startswith("sk-"),
              "%s...%s" % (k1[:7], k1[-4:]) if k1 else "空")
        check("B7b base 与 key 同站（国内站）", b1.startswith("https://api.agnes-ai.cn"),
              b1)
        os.environ["AGNES_API_KEY"] = "sk-envtest0123456789abcdefghijklmn"
        os.environ["AGNES_BASE_URL"] = "https://example.invalid/v1"
        k2, b2 = ab._resolve_cred(None, None)
        check("B7c env 覆盖用户配置", k2 == "sk-envtest0123456789abcdefghijklmn"
              and b2 == "https://example.invalid/v1", "%s | %s" % (k2, b2))
        k3, b3 = ab._resolve_cred("sk-explicit", "https://x/v1")
        check("B7d 显式传参优先级最高", k3 == "sk-explicit" and b3 == "https://x/v1",
              "%s | %s" % (k3, b3))
        os.environ.pop("AGNES_API_KEY", None)
        os.environ.pop("AGNES_BASE_URL", None)
        _orig = ab._user_config_agnes_key
        ab._user_config_agnes_key = lambda: ""
        try:
            ab._resolve_cred(None, None)
            check("B7e 缺 key 必须明确报错（不得静默打网络）", False, "未抛异常")
        except RuntimeError as e:
            check("B7e 缺 key 必须明确报错（不得静默打网络）",
                  "Agnes 凭据缺失" in str(e), str(e)[:60])
        finally:
            ab._user_config_agnes_key = _orig
    except Exception as e:  # noqa: BLE001
        check("B7 凭据解析行为", False, "%s: %s" % (type(e).__name__, e))
    finally:
        os.environ.pop("AGNES_API_KEY", None)
        os.environ.pop("AGNES_BASE_URL", None)
        if _env_bak is not None:
            os.environ["AGNES_API_KEY"] = _env_bak
        if _base_bak is not None:
            os.environ["AGNES_BASE_URL"] = _base_bak


# --------------------------------------------------------------------------
# C 结构
# --------------------------------------------------------------------------
if active("C1"):
    check("C1 gen_video_executor 调用 video_fn", "video_fn(node, asset_root, src_path, prompt)" in EXEC
          or "video_fn(" in EXEC)
if active("C2"):
    check("C2 agnes_image_inpaint 调用 Agnes 图生图端点", "/images/generations" in AGNES)
if active("C3"):
    check("C3 agnes_video_generate 调用 /videos 端点", "/videos" in AGNES)
if active("C4"):
    check("C4 promo_fx_executor 调用 motion_fn", "motion_fn(img_paths, out_path, params)" in EXEC
          or "motion_fn(" in EXEC)


# --------------------------------------------------------------------------
# 横幅
# --------------------------------------------------------------------------
print("=" * 64)
print("CANVAS AGNES / PROMO 判据套件")
print("=" * 64)
for st, name, detail in _log:
    flag = "✅" if st == "PASS" else "❌"
    extra = ("  <%s>" % detail) if detail else ""
    print("  %s %s%s" % (flag, name, extra))
print("-" * 64)
print("通过 %d / 失败 %d / 共 %d" % (_passed, _failed, _passed + _failed))
print("=" * 64)
sys.exit(1 if _failed else 0)
