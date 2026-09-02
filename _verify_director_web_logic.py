# -*- coding: utf-8 -*-
"""director_web 网页渲染模块的逻辑验证（无需 GUI）。
覆盖：四个卡片 HTML 模板、localres:// 编码、SKELETON 变量替换。
运行：python _verify_director_web_logic.py
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import director_web as d

_fail = []


def chk(name, cond):
    print(("PASS " if cond else "FAIL ") + name)
    if not cond:
        _fail.append(name)


# 真实素材路径
clip = os.path.join(HERE, "_dw_test", "clip1.mp4")
kf = os.path.join(HERE, "_dw_test", "kf1.png")
miss = os.path.join(HERE, "_dw_test", "nope.png")

# 1. 分镜卡：done（视频内嵌 + 关键帧 poster + 三个按钮）
h = d.clip_card_html(0, "done", path=clip, kf=kf)
chk("clip done 含 <video>", "<video" in h)
chk("clip done 含 localres 视频源", "localres:///" in h)
chk("clip done 含关键帧 poster", "poster=" in h)
chk("clip done 含 ✎改/↻/🔍 三个动作", "act('mod',0)" in h and "act('regen',0)" in h and "act('view',0)" in h)

# 2. 分镜卡：fail（🔍看原因 + ↻重生成）
h2 = d.clip_card_html(1, "fail")
chk("clip fail 含 生成失败", "生成失败" in h2)
chk("clip fail 含 重生成动作", "act('regen',1)" in h2)

# 3. 分镜卡：queued（占位，无 video）
h3 = d.clip_card_html(2, "queued")
chk("clip queued 无 <video>", "<video" not in h3)
chk("clip queued 含 排队中", "排队中" in h3)

# 4. 关键帧卡：有图 + 质检 FAIL
h4 = d.keyframe_card_html(0, kf, note="VLM VERDICT: FAIL 人物手部崩坏")
chk("kf 含 <img>", "<img" in h4)
chk("kf 含 zoom 灯箱", "zoom(this.src)" in h4)
chk("kf 质检失败标红", "质检未通过" in h4 and "qc fail" in h4)

# 5. 关键帧卡：有图 + 质检 PASS
h5 = d.keyframe_card_html(0, kf, note="VLM VERDICT: PASS 画面稳定")
chk("kf 质检通过", "质检通过" in h5)

# 6. 关键帧卡：缺图
h5b = d.keyframe_card_html(0, miss)
chk("kf 缺图走占位", "无关键帧" in h5b)

# 7. 角色卡：三视图 + 灯箱 + 名称
h6 = d.character_card_html({"name": "小臭", "desc": "主角", "views": [kf, kf, kf]})
chk("char 含角色名", "小臭" in h6)
chk("char 含三张三视图", h6.count("<img") == 3)
chk("char 三视图可灯箱", h6.count("zoom(this.src)") == 3)

# 8. 合成卡：成片视频
h7 = d.merge_card_html(clip)
chk("merge 含 <video>", "<video" in h7)
chk("merge 尚未合成占位", "尚未合成" in d.merge_card_html(None))

# 9. localres_url 编码（中文 + 空格）
u = d._localres_url("C:/Users/xyb/测试 dir/clip.png")
chk("localres scheme 前缀", u.startswith("localres:///"))
chk("localres 编码空格", "%20" in u)
chk("localres 不破坏盘符", "C:" in u)

# 10. SKELETON 变量替换
sk = d.DirectorWebView(None)._skeleton if False else d._SKELETON
filled = sk.replace("__BG__", "#fff").replace("__CARD__", "#eee").replace(
    "__BORDER__", "#ddd").replace("__TEXT__", "#111").replace(
    "__DIM__", "#888").replace("__ACCENT__", "#07f").replace("__CARDS__", "<x>")
chk("skeleton 变量替换无残留占位", "__BG__" not in filled and "__CARDS__" not in filled)
chk("skeleton 含 lightbox 结构", 'id="lb"' in filled and "zoom(" in filled)

print("\n=== %s ===" % ("ALL_OK" if not _fail else "HAS_FAIL:" + ",".join(_fail)))
sys.exit(1 if _fail else 0)
