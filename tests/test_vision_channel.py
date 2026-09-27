"""v4.174.0 回归：图像链路「路由目标」与「视觉能力判定」必须一致。

实测事故（大哥验收）：
  普通对话附一张图问「这张图里有什么」→ 小臭回「我这边没有收到任何图片」。
  查证：图**确实附上了**（route_log 记了 reason="image"、payload 79 万字符），
  但发给模型的 payload 里图已被抹成纯文本 —— 因为
    · 路由：带图 force_complex → model_routing.complex_model =「DeepSeek 官方」= deepseek-flash
    · 判定：`_model_supports_vision("deepseek-flash")` = **False**（内置词表里没有它）
    · 结果：`_flatten_text_content()` 把图归一化成 `…\n[图片]`，模型只能看到文字占位符
  **实测定案：deepseek-flash 认图**（128×128 纯红 PNG → 答「红色」，reasoning 写明看图过程）。

本套件的核心是**漂移守卫**：不写死模型名，而是拿配置里图像链路**实际指向**的模型，
核对 `_model_supports_vision` 认不认 —— 以后换视觉模型忘了同步词表，这里就红。
（顺带说明：本该守住这条的 `test_v4102_vision.py` 一直躺在仓库根目录、
`tests/run_all.py` 收不到，所以它红了很久没人知道。）
"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import config  # noqa: E402
import ui      # noqa: E402

_p = _f = 0

import base64
import struct
import zlib


def _good_png():
    """合法 8×8 纯色 PNG（自己按 PNG 规范算 CRC）。

    ⚠️ 别再用 `data:image/png;base64,AAAA` 这种假图当测试向量 ——
    v4.175.0 起「解不开的图会在发送前被丢掉」，假图会被判无效，
    断言"图被保留"就会假红（真发生过一次）。测试向量必须真能解码。
    """
    def chunk(tag, data):
        c = tag + data
        return (struct.pack(">I", len(data)) + c
                + struct.pack(">I", zlib.crc32(c) & 0xFFFFFFFF))

    w = h = 8
    raw = b"".join(b"\x00" + bytes((200, 30, 30)) * w for _ in range(h))
    return (b"\x89PNG\r\n\x1a\n"
            + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw))
            + chunk(b"IEND", b""))


IMG_URL = "data:image/png;base64," + base64.b64encode(_good_png()).decode()
IMG_MSG = {"role": "user", "content": [
    {"type": "text", "text": "这张图里有什么"},
    {"type": "image_url", "image_url": {"url": IMG_URL}},
]}


def check(label, got, exp=True, extra=""):
    global _p, _f
    ok = (got == exp)
    _p += ok
    _f += (not ok)
    print(f"  {'✓' if ok else '✗'} {label:<56} got={got!s:<6} exp={exp}  {extra}")


def _keeps_image(msg):
    c = (msg or {}).get("content")
    return isinstance(c, list) and any(
        isinstance(p, dict) and p.get("type") == "image_url" for p in c)


def part_a_channel_model_recognized():
    """漂移守卫：配置里图像链路指向谁，就必须认谁。"""
    print("\n-- A) 漂移守卫：图像链路实际指向的模型，必须被判为「认图」 --")
    mr = (config.DEFAULT_CONFIG.get("model_routing") or {})
    prof_name = mr.get("complex_model")
    prof = (config.DEFAULT_CONFIG.get("model_profiles") or {}).get(prof_name)
    model = (prof or {}).get("model")
    print(f"  图像链路：model_routing.complex_model={prof_name!r} → 模型 {model!r}")
    check("A1 能从默认配置解析出图像链路模型（解析不到=守卫变瞎，必须红）",
          bool(model), True)
    if model:
        check(f"A2 _model_supports_vision({model!r}) 必须为 True",
              ui._model_supports_vision(model), True)
    # 内置词表里显式列出（防止只靠追加清单、词表又漂走）
    check("A3 词表里显式含 deepseek-flash",
          "deepseek-flash" in ui.VISION_MODEL_KW)


def part_b_known_models():
    print("\n-- B) 词表基本判定（认图 / 不认图两侧都要对） --")
    for m in ("deepseek-flash", "deepseek-v4-flash-vision-exp",
              "gpt-4o", "qwen-vl-max", "glm-4v", "OpenGVLab/InternVL2-8B"):
        check(f"B1 认图：{m}", ui._model_supports_vision(m), True)
    for m in ("agnes-3.0-flash", "agnes-2.5-flash", "glm-4-flash",
              "deepseek-ai/DeepSeek-V3", "hy3-preview", ""):
        check(f"B2 不认图：{m or '(空)'}", ui._model_supports_vision(m), False)


def part_c_extra_hints():
    print("\n-- C) 追加清单（换视觉模型不必改代码） --")
    probe = "acme-ocr-9"           # 刻意不含 vision/vl/gpt-4o 等内置关键词
    check("C1 未加白名单前不认", ui._model_supports_vision(probe), False)
    old = config.VISION_MODEL_EXTRA_HINTS
    config.VISION_MODEL_EXTRA_HINTS = (probe,)
    try:
        check("C2 加进 VISION_MODEL_EXTRA_HINTS 后即认",
              ui._model_supports_vision(probe), True)
    finally:
        config.VISION_MODEL_EXTRA_HINTS = old
    check("C3 恢复后不再认（不残留副作用）",
          ui._model_supports_vision(probe), False)


def part_d_payload_level():
    print("\n-- D) payload 层：认图 → 保留 image_url；不认图 → 归一化成纯文本 --")
    s_on = ui._sanitize_msg_for_api(dict(IMG_MSG), vision_ok=True)
    check("D1 vision_ok=True → 保留 image_url", _keeps_image(s_on), True)
    s_off = ui._sanitize_msg_for_api(dict(IMG_MSG), vision_ok=False)
    check("D2 vision_ok=False → 内容变字符串（图被抹掉）",
          isinstance((s_off or {}).get("content"), str), True)
    check("D3 抹掉后只剩文字占位符（模型看到的就是这个）",
          "[图片]" in str((s_off or {}).get("content")), True)

    h_on = ui._build_api_history([dict(IMG_MSG)], vision_ok=True, max_history=10)
    check("D4 history(vision_ok=True) 保留 image_url",
          any(_keeps_image(x) for x in h_on), True)
    h_off = ui._build_api_history([dict(IMG_MSG)], vision_ok=False, max_history=10)
    check("D5 history(vision_ok=False) 不保留 image_url",
          any(_keeps_image(x) for x in h_off), False)


def part_e_source_contract():
    print("\n-- E) 源码契约：链路上「判定 → 保留图」必须是同一处判据 --")
    src = open(os.path.join(ROOT, "ui.py"), encoding="utf-8-sig").read()
    check("E1 带图升舱用 reason=\"image\"", 'reason="image")' in src)
    check("E2 保留了 `if _has_img and _vision_ok:` 这道门",
          "if _has_img and _vision_ok:" in src)
    check("E3 非视觉分支确实走归一化",
          "_flatten_text_content(lc) or text" in src)
    check("E4 判定入口是 _model_supports_vision(_m)",
          "_vision_ok = _model_supports_vision(_m)" in src)
    check("E5 视觉词表已提为模块常量（可被测试核对）",
          "VISION_MODEL_KW = (" in src)
    check("E6 config 暴露可编辑的追加清单",
          hasattr(config, "VISION_MODEL_EXTRA_HINTS"))


def part_f_negative():
    print("\n-- F) 负面验证：把 deepseek-flash 从词表拆掉 → 事故必须复现 --")
    old_kw = ui.VISION_MODEL_KW
    ui.VISION_MODEL_KW = tuple(k for k in old_kw if k != "deepseek-flash")
    try:
        check("F1 拆掉后 _model_supports_vision('deepseek-flash') 变 False",
              ui._model_supports_vision("deepseek-flash"), False)
        # 走**链路本身的判定**（而不是我手写 vision_ok=False），复现事故：
        _vok = ui._model_supports_vision("deepseek-flash")     # 带图升舱后的目标模型
        sent = ui._sanitize_msg_for_api(dict(IMG_MSG), vision_ok=_vok)
        check("F2 链路判定为不认图 → 发给模型的 payload 里图已消失（事故复现）",
              _keeps_image(sent), False)
        check("F2b 模型收到的只剩文字占位符",
              "[图片]" in str((sent or {}).get("content")), True)
    finally:
        ui.VISION_MODEL_KW = old_kw
    check("F3 恢复后 deepseek-flash 又认图", ui._model_supports_vision("deepseek-flash"), True)
    _vok2 = ui._model_supports_vision("deepseek-flash")
    check("F4 恢复后同一链路把图保留下来（修复生效）",
          _keeps_image(ui._sanitize_msg_for_api(dict(IMG_MSG), vision_ok=_vok2)), True)


def main():
    part_a_channel_model_recognized()
    part_b_known_models()
    part_c_extra_hints()
    part_d_payload_level()
    part_e_source_contract()
    part_f_negative()
    print(f"\n汇总：PASS={_p} FAIL={_f}")
    return 0 if _f == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
