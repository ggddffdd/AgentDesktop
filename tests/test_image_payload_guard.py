"""v4.175.0 回归：一张坏图不得毒死整轮请求。

实测事故：会话历史里一张 **71 字节的坏 PNG**（IDAT 校验和错，libpng 报
`IDAT: incorrect data check`）被原样发给 DeepSeek →
```
HTTP 400  .messages[1].image[0]: You have uploaded an unsupported image.
Please make sure your image is valid ... (webp, png, jpeg, and gif)
```
→ **该会话此后每次带图都失败**（一张坏图毒死整个会话），
而界面只显示「HTTP Error 400: Bad Request」，完全看不出原因。

根因：`_normalize_image_dataurl` 的契约是「无法解码则**原样返回**」——
等于把坏图原封不动送给 API。本版改成「解不开就判无效，由调用方丢掉」。

本套件用**事故那张坏图的原始字节**做测试向量（写死在代码里，不读用户数据）。
"""
import base64
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtGui import QGuiApplication, QImage  # noqa: E402
_app = QGuiApplication.instance() or QGuiApplication(sys.argv)

import ui  # noqa: E402

_p = _f = 0


def check(label, got, exp=True, extra=""):
    global _p, _f
    ok = (got == exp)
    _p += ok
    _f += (not ok)
    print(f"  {'✓' if ok else '✗'} {label:<58} got={got!s:<6} exp={exp}  {extra}")


# 事故原图：1×1 RGBA PNG，IDAT 校验和是错的（libpng / QImage 都解不开）
BAD_PNG = bytes.fromhex(
    "89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c489"
    "0000000d4944415408d763606060600000000300010005fed449"
    "0000000049454e44ae426082")
BAD_URL = "data:image/png;base64," + base64.b64encode(BAD_PNG).decode()

# 合法 1×1 PNG（自己按 PNG 规范算 CRC，确保一定能解码）
def _png_chunk(tag, data):
    import struct
    import zlib
    c = tag + data
    return (struct.pack(">I", len(data)) + c
            + struct.pack(">I", zlib.crc32(c) & 0xFFFFFFFF))


def _good_png():
    import struct
    import zlib
    w = h = 8
    raw = b"".join(b"\x00" + bytes((200, 30, 30)) * w for _ in range(h))
    return (b"\x89PNG\r\n\x1a\n"
            + _png_chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0))
            + _png_chunk(b"IDAT", zlib.compress(raw))
            + _png_chunk(b"IEND", b""))


GOOD_URL = "data:image/png;base64," + base64.b64encode(_good_png()).decode()


def _img_urls(msg):
    c = (msg or {}).get("content")
    if not isinstance(c, list):
        return []
    return [p["image_url"]["url"] for p in c
            if isinstance(p, dict) and p.get("type") == "image_url"]


def _has_text(msg):
    c = (msg or {}).get("content")
    return isinstance(c, list) and any(
        isinstance(p, dict) and p.get("type") == "text" for p in c)


# ---------------------------------------------------------------------------
def part_a_normalize():
    print("\n-- A) 归一化函数：解不开就判无效（返回空串） --")
    check("A1 事故那张坏 PNG → 判为无效", ui._normalize_image_dataurl(BAD_URL), "")
    check("A2 垃圾字节 → 判为无效",
          ui._normalize_image_dataurl(
              "data:image/png;base64," + base64.b64encode(b"not an image").decode()), "")
    check("A3 非法 base64 → 判为无效（不抛异常）",
          ui._normalize_image_dataurl("data:image/png;base64,!!!!"), "")
    good_out = ui._normalize_image_dataurl(GOOD_URL)
    check("A4 合法 PNG → 归一化为 JPEG data URL",
          good_out.startswith("data:image/jpeg;base64,"), True)
    check("A5 合法图归一化后仍能被解码",
          not QImage.fromData(base64.b64decode(good_out.split(",", 1)[1])).isNull(),
          True)
    # 非 data URL / 非 base64 的形态不归我们管，应原样透传（别把合法输入改坏）
    check("A6 http(s) URL 原样透传",
          ui._normalize_image_dataurl("https://x/y.png"), "https://x/y.png")
    check("A7 非 base64 的 data URL 原样透传",
          ui._normalize_image_dataurl("data:image/png,x"), "data:image/png,x")
    check("A8 空串不炸", ui._normalize_image_dataurl(""), "")
    check("A9 None 不炸", ui._normalize_image_dataurl(None), None)


def part_b_sanitize():
    print("\n-- B) 消息清洗：坏图丢掉、消息不因此变空 --")
    m1 = {"role": "user", "content": [
        {"type": "text", "text": "看看这张图"},
        {"type": "image_url", "image_url": {"url": BAD_URL}}]}
    s1 = ui._sanitize_msg_for_api(dict(m1), vision_ok=True)
    check("B1 文本+坏图 → payload 里不再有图", _img_urls(s1), [])
    check("B2 文本被保留", _has_text(s1), True)

    m2 = {"role": "user", "content": [
        {"type": "image_url", "image_url": {"url": BAD_URL}}]}
    s2 = ui._sanitize_msg_for_api(dict(m2), vision_ok=True)
    check("B3 只有坏图 → 消息不丢（补文字占位）", s2 is not None, True)
    check("B4 占位文案写进 payload", "无法解析" in str((s2 or {}).get("content")), True)

    m3 = {"role": "user", "content": [
        {"type": "text", "text": "看看这张图"},
        {"type": "image_url", "image_url": {"url": GOOD_URL}}]}
    s3 = ui._sanitize_msg_for_api(dict(m3), vision_ok=True)
    check("B5 好图必须保留（别把闸门拆了）", len(_img_urls(s3)), 1)
    check("B6 好图保留的是归一化后的 JPEG",
          _img_urls(s3)[0].startswith("data:image/jpeg;base64,"), True)

    m4 = {"role": "user", "content": [
        {"type": "text", "text": "两张图"},
        {"type": "image_url", "image_url": {"url": BAD_URL}},
        {"type": "image_url", "image_url": {"url": GOOD_URL}}]}
    s4 = ui._sanitize_msg_for_api(dict(m4), vision_ok=True)
    check("B7 好坏混排 → 只丢坏的、留住好的", len(_img_urls(s4)), 1)


def part_c_history():
    print("\n-- C) 历史构造：坏图不能借历史混进来 --")
    msgs = [
        {"role": "user", "content": [
            {"type": "text", "text": "看看这张图"},
            {"type": "image_url", "image_url": {"url": BAD_URL}}]},
        {"role": "assistant", "content": "好的"},
        {"role": "user", "content": [
            {"type": "text", "text": "再看这张"},
            {"type": "image_url", "image_url": {"url": GOOD_URL}}]},
    ]
    hist = ui._build_api_history(msgs, vision_ok=True, max_history=20)
    all_urls = [u for m in hist for u in _img_urls(m)]
    check("C1 历史里只剩 1 张图（坏图被剔除）", len(all_urls), 1)
    check("C2 留下的那张不是坏图（坏 PNG 没混进历史）",
          bool(all_urls) and not all_urls[0].startswith("data:image/png"), True)
    check("C3 历史条数没被坏图搞崩", len(hist) >= 2, True)
    # 非视觉模型路径不应出现任何图
    hist2 = ui._build_api_history(msgs, vision_ok=False, max_history=20)
    check("C4 非视觉模型 → 一张图都不发",
          sum(len(_img_urls(m)) for m in hist2), 0)


def part_d_source_contract():
    print("\n-- D) 源码契约：两个调用点都必须丢弃无效图 --")
    # v4.216.0：归一化/整形函数族已迁 ui_msg.py
    src = open(os.path.join(ROOT, "ui_msg.py"), encoding="utf-8-sig").read()
    check("D1 归一化失败返回空串（不再原样放行）",
          "解不开 ⇒ 无效" in src, True)
    check("D2 历史路径有丢弃分支", src.count("if _nu:") >= 1, True)
    check("D3 历史路径有丢弃计数", "_dropped_img += 1" in src, True)
    check("D4 当前消息路径有丢弃计数",
          # v4.216.0：当前消息整形在 ui.py（ChatWindow），历史路径在 ui_msg.py
          "_dropped_cur += 1" in open(
              os.path.join(ROOT, "ui.py"), encoding="utf-8-sig").read(), True)
    check("D5 占位补在 `if cleaned` 之前（否则只有坏图的消息会退化）",
          src.index("[图片无法解析，已忽略]") < src.index("if cleaned:"), True)
    check("D6 400 报文会挂到异常上供界面显示", "def _attach_api_body" in src, True)
    agent_src = open(os.path.join(ROOT, "agent.py"), encoding="utf-8-sig").read()
    check("D7 agent 侧把接口原文一起显示出来",
          'getattr(e, "_api_body", "")' in agent_src
          and "接口原文：" in agent_src, True)


def part_e_negative():
    print("\n-- E) 负面验证：改回「原样返回」→ 坏图必须又混进 payload --")
    # v4.216.0：_sanitize_msg_for_api 在 ui_msg 里读 ui_msg 的模块全局，
    # 打 ui 的 re-export 碰不到真身（必须打 ui_msg）
    import ui_msg
    old = ui_msg._normalize_image_dataurl
    ui_msg._normalize_image_dataurl = lambda u: u   # 旧契约：解不开照发
    try:
        m = {"role": "user", "content": [
            {"type": "text", "text": "看看这张图"},
            {"type": "image_url", "image_url": {"url": BAD_URL}}]}
        s = ui._sanitize_msg_for_api(dict(m), vision_ok=True)
        urls = _img_urls(s)
        check("E1 旧契约下坏图确实被发出去（事故复现）", len(urls), 1)
        check("E2 发出去的正是解不开的那张",
              urls and QImage.fromData(
                  base64.b64decode(urls[0].split(",", 1)[1] + "===")).isNull(), True)
    finally:
        ui_msg._normalize_image_dataurl = old
    s2 = ui._sanitize_msg_for_api(
        {"role": "user", "content": [
            {"type": "text", "text": "看看这张图"},
            {"type": "image_url", "image_url": {"url": BAD_URL}}]}, vision_ok=True)
    check("E3 恢复修复后坏图又被丢掉", _img_urls(s2), [])


def main():
    part_a_normalize()
    part_b_sanitize()
    part_c_history()
    part_d_source_contract()
    part_e_negative()
    print(f"\n汇总：PASS={_p} FAIL={_f}")
    return 0 if _f == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
