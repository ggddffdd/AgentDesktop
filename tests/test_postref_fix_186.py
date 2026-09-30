"""批 5（P1-2 intent_guard 后置质疑句）回归（v4.186.0 审查修复）。"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

_p = _f = 0


def check(name, cond, detail=""):
    global _p, _f
    if cond:
        _p += 1
        print("  PASS %s %s" % (name, ("-> " + str(detail)[:60]) if detail else ""))
    else:
        _f += 1
        print("  FAIL %s %s" % (name, ("-> " + str(detail)[:60]) if detail else ""))


def main():
    import intent_guard as ig

    print("=== [A] 后置质疑句应判引用（修复前全部漏放）===")
    for t in ("生成视频这件事你怎么看", "生成视频你怎么看",
              "画张图片这件事如何评价", "做个数字人口播你怎么理解"):
        r = ig.is_ref_context(t)
        check("ref=True | %s" % t, r is True, r)

    print("=== [B] 既有行为不回归 ===")
    for t in ("分析下生成视频这件事", "你刚才说的生成个视频，是BUG",
              "自检一下生成图片的功能"):
        r = ig.is_ref_context(t)
        check("ref=True | %s" % t, r is True, r)

    print("=== [C] 真指令零误伤 ===")
    for t in ("帮我生成一张图片", "剪个视频聊聊昆明",
              "生成个视频，顺便分析下", "生成视频然后聊聊下一步",
              "生成视频，你怎么看"):
        r = ig.is_ref_context(t)
        check("ref=False | %s" % t, r is False, r)

    print()
    print("汇总：PASS=%d FAIL=%d" % (_p, _f))
    return 0 if _f == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
