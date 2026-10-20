# -*- coding: utf-8 -*-
"""生成小臭玩AI新图标（v4.288.0）：纯靛蓝底 #4F46E5 + 白色几何 XC 字母。

设计要点（对标 Windows 11 现代图标规范）：
  · 纯色底、零渐变零浮雕——任何尺寸轮廓都清晰；
  · 白色单色几何笔画、圆头（round cap）；
  · 小尺寸光学优化：16/24px 笔画相对加粗、XC 间距收窄，防小尺寸糊成一团；
  · 圆角 22%（Win11 标准），白字留足安全边距。

用法：python _dev_history/icon_gen_20261020/gen_icon.py
产物：覆盖仓库根 icon.ico 与 images/app_icon.ico（多帧 ico）。
"""
import os
from PIL import Image, ImageDraw

BG = (79, 70, 229)        # #4F46E5 靛蓝
FG = (255, 255, 255)      # 白
SIZES = [16, 24, 32, 48, 64, 128, 256]


def _line_with_round_caps(d, p1, p2, width, fill):
    """PIL line 不支持圆头，手动画椭圆帽补。"""
    d.line([p1, p2], fill=fill, width=width)
    r = width / 2.0
    for (x, y) in (p1, p2):
        d.ellipse([x - r, y - r, x + r, y + r], fill=fill)


def draw_icon(size):
    """在 size×size 画布上画一帧：圆角方底 + XC 几何字母。"""
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    # 底：Win11 风格 22% 圆角
    d.rounded_rectangle([0, 0, size - 1, size - 1],
                        radius=max(2, int(size * 0.22)), fill=BG)
    # 光学尺寸调整：小尺寸笔画更粗、间距更紧
    if size <= 24:
        sw_ratio, gap = 0.150, -1.0
    elif size <= 48:
        sw_ratio, gap = 0.130, 0.0
    else:
        sw_ratio, gap = 0.115, 1.5
    sw = max(1, int(round(size * sw_ratio)))
    # XC 几何（100 坐标系 → 像素）：X 交叉于 (33,50)，C 圆心 (62+g,50) 半径 12
    s = size / 100.0
    cx = 62 + gap
    # X：两条对角线
    _line_with_round_caps(d, (24 * s, 41 * s), (42 * s, 59 * s), sw, FG)
    _line_with_round_caps(d, (42 * s, 41 * s), (24 * s, 59 * s), sw, FG)
    # C：左半圆弧（PIL 角度制：90°=6点，270°=12点，逆时针走左侧）
    # ⚠️ bbox 必须先算 100 制坐标再统一乘 s——r=12*s 已含缩放，严禁二次乘
    d.arc([(cx - 12) * s, (50 - 12) * s, (cx + 12) * s, (50 + 12) * s],
          start=90, end=270, fill=FG, width=sw)
    # C 两端圆帽（与 arc 端点对齐：(cx,38) 12点钟 / (cx,62) 6点钟）
    cap_r = sw / 2.0
    for (px, py) in ((cx * s, 38 * s), (cx * s, 62 * s)):
        d.ellipse([px - cap_r, py - cap_r, px + cap_r, py + cap_r], fill=FG)
    return img


def main():
    here = os.path.dirname(os.path.abspath(__file__))
    repo = os.path.abspath(os.path.join(here, "..", ".."))
    frames = [draw_icon(s) for s in SIZES]
    # PIL ico 多帧保存：按尺寸从大到小
    big = frames[-1]
    out1 = os.path.join(repo, "icon.ico")
    out2 = os.path.join(repo, "images", "app_icon.ico")
    big.save(out1, sizes=[(im.width, im.height) for im in frames])
    big.save(out2, sizes=[(im.width, im.height) for im in frames])
    for p in (out1, out2):
        print("written:", p, os.path.getsize(p), "bytes")
    # 自检：每帧都能读回
    chk = Image.open(out1)
    got = []
    for i in range(getattr(chk, "n_frames", 1)):
        chk.seek(i)
        got.append(chk.size)
    print("frames:", got)


if __name__ == "__main__":
    main()
