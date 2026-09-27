# v4.175.0：从仓库根目录搬入 tests/ —— 统一入口以 `python tests/xxx.py` 运行，
# 此时 sys.path[0] 是 tests/，必须显式把仓库根加回来，否则 import ui 会失败。
import os as _os
import sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))

# -*- coding: utf-8 -*-
"""数字人分身：背景锁定验证（2026-08-30 用户反馈「模型擅自改我的背景」）。

根因：场景描述为空时，旧代码回退成 "A real person, ..., warm indoor lighting, ..."，
等于明确指示模型换一个温暖室内背景 → 参考图的真实背景被丢掉。
"""
import digital_twin_panel as dtp

fails = []


def check(name, ok, extra=""):
    print(f"[{'OK' if ok else 'FAIL'}] {name}{(' -> ' + extra) if extra else ''}")
    if not ok:
        fails.append(name)


def main():
    dlg = "大家好，今天聊点 AI"

    # 1. 默认（保持原图背景 + 不填场景）
    p = dtp._build_twin_prompt("", dlg, keep_bg=True)
    check("默认不编造温暖室内背景", "warm indoor lighting" not in p)
    check("默认带 BACKGROUND LOCK", "[BACKGROUND LOCK]" in p)
    check("默认沿用参考图背景描述", "SAME background" in p)
    check("台词进入 prompt", dlg in p)
    check("无泄漏字样", "用中文说" not in p)

    # 2. 关掉保持原图背景 -> 允许另造背景（旧行为）
    p2 = dtp._build_twin_prompt("", dlg, keep_bg=False)
    check("关闭时允许编造背景", "warm indoor lighting" in p2)
    check("关闭时无 BACKGROUND LOCK", "[BACKGROUND LOCK]" not in p2)

    # 3. 填了场景 + 保持原图背景 -> 场景文字保留，但背景锁仍生效
    p3 = dtp._build_twin_prompt("穿白色衬衫，面对镜头微笑", dlg, keep_bg=True)
    check("自定义场景保留", "穿白色衬衫" in p3)
    check("自定义场景下仍有背景锁", "[BACKGROUND LOCK]" in p3)

    # 4. 三种组合都必须有人脸锁 + 镜头锁
    for i, pp in enumerate((p, p2, p3), 1):
        check(f"组合{i}含 FACE LOCK", "[CRITICAL FACE LOCK]" in pp)
        check(f"组合{i}含 CAMERA LOCK", "[CAMERA LOCK]" in pp)

    # 5. 背景锁必须排在台词之后（尾部指令遵循度最高）
    check("BACKGROUND LOCK 在台词之后",
          p.index(dlg) < p.index("[BACKGROUND LOCK]"))

    print()
    if fails:
        print(f"FAILED {len(fails)} 项：{fails}")
        raise SystemExit(1)
    print("ALL_TWIN_BG_LOCK_OK")


if __name__ == "__main__":
    main()
