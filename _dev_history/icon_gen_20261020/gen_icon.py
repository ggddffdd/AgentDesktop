# -*- coding: utf-8 -*-
"""生成小臭玩AI新图标（v4.288.0）：纯靛蓝底 #4F46E5 + 白色几何 XC 字母。

设计要点（对标 Windows 11 现代图标规范）：
  · 纯色底、零渐变零浮雕——任何尺寸轮廓都清晰；
  · 白色单色几何笔画、圆头（round cap），**笔画纤细**（≤256px 约 8.5% 宽）——
    v4.288.0 初版笔画 11.5% 把 C 弧胖成粗环（大哥反馈「C 一坨」），收细重修；
  · C 弧半径放大 + 开口 120°（而非 180°）——开口小 + 端帽重 = 胖括号既视感；
  · 小尺寸光学优化：16/24px 笔画相对加粗、XC 间距收窄，防小尺寸糊成一团；
  · 圆角 22%（Win11 标准），白字留足安全边距。

用法：python _dev_history/icon_gen_20261020/gen_icon.py
产物：覆盖仓库根 icon.ico 与 images/app_icon.ico（多帧 ico）。
"""
import math
import os
from PIL import Image, ImageDraw

BG = (79, 70, 229)        # #4F46E5 靛蓝
FG = (255, 255, 255)      # 白
SIZES = [16, 24, 32, 48, 64, 128, 256]

# ---- XC 几何（100 坐标系）----
X_CX, X_CY = 34.0, 50.0   # X 交叉点
X_HW, X_HH = 10.0, 10.0   # X 半宽/半高
C_CX, C_CY = 63.0, 50.0   # C 圆心
C_R = 13.5                # C 半径（v4.288.0 初版 12 → 弧环显胖，放大舒展曲率）
C_R_SMALL = 16.5          # ≤24px 专用：16px 下 13.5 半径只剩 1.2px 内孔，C 糊成实心团
# 弧跨度按尺寸切换（光学尺寸）：240° 开口 C 的上下两臂在 <48px 时是亚像素，
# 硬画只会得到「弧身 + 两颗甩开的圆帽」断裂感；小尺寸退回经典 180° C（12/6 点封帽）。
C_SPAN_SMALL = (90.0, 270.0)
C_SPAN_BIG = (60.0, 300.0)
C_SPAN_CUT = 48           # <48px 用 180°，≥48px 用 240°


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
    # 光学尺寸调整：小尺寸笔画相对加粗、间距收窄
    if size <= 24:
        sw_ratio = 0.140
    elif size <= 48:
        sw_ratio = 0.115
    else:
        sw_ratio = 0.085      # v4.288.0 初版 0.115 → C 显胖；收细至 8.5%
    sw = max(1, int(round(size * sw_ratio)))
    s = size / 100.0
    # X：两条对角线（交叉于 (34,50)）
    _line_with_round_caps(d, ((X_CX - X_HW) * s, (X_CY - X_HH) * s),
                          ((X_CX + X_HW) * s, (X_CY + X_HH) * s), sw, FG)
    _line_with_round_caps(d, ((X_CX + X_HW) * s, (X_CY - X_HH) * s),
                          ((X_CX - X_HW) * s, (X_CY + X_HH) * s), sw, FG)
    # C：左半圆弧（PIL 角度制顺时针）
    # ⚠️ bbox 必须先算 100 制坐标再统一乘 s——r 已含缩放，严禁二次乘
    c_r = C_R_SMALL if size <= 24 else C_R
    c_a0, c_a1 = C_SPAN_SMALL if size < C_SPAN_CUT else C_SPAN_BIG
    d.arc([(C_CX - c_r) * s, (C_CY - c_r) * s, (C_CX + c_r) * s, (C_CY + c_r) * s],
          start=c_a0, end=c_a1, fill=FG, width=sw)
    # C 两端圆帽：锚在弧的理论端点（沿半径方向），半径与弧宽对齐
    cap_r = sw / 2.0
    for ang in (c_a0, c_a1):
        rad = math.radians(ang)
        px = (C_CX + c_r * math.cos(rad)) * s
        py = (C_CY + c_r * math.sin(rad)) * s
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
    # 目检图：256 原样 + 64 中检 + 16 放大 8 倍
    frames[-1].convert("RGBA").save(os.path.join(here, "_icon_check_256.png"))
    frames[4].convert("RGBA").save(os.path.join(here, "_icon_check_64.png"))
    frames[0].resize((128, 128), Image.NEAREST).save(os.path.join(here, "_icon_check_16.png"))


if __name__ == "__main__":
    main()
