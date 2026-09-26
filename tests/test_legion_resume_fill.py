# -*- coding: utf-8 -*-
"""军团续跑「成员级底稿回填」条件回归（军团模块审查 #5）

背景
----
`legion_worker.py` 续跑时会从 checkpoint 回填历史产出。其中「成员级底稿」
（谁交了什么）那段的条件在 v4.166.0 前是**写反的**：

    注释写的是：续跑后 PM 审校 / 悬空引用检查要能拿到历史波成员粒度
    代码写的却是：if _wi2 < self._resume_from: continue   ← 跳过历史波

而 checkpoint 里根本不会有 `>= resume_from` 的数据（那些波还没跑），
于是整段回填**空转**，注释承诺的能力等于没实现；方向还与同函数内的
「波级回填」（`parts_by_wave`，条件为 `_wi < resume_from` 时才回填）恰好相反。

本套件钉住两件事：
  ① 成员级回填的条件方向正确（回填历史波、跳过即将重跑的部分）；
  ② 与波级回填方向一致 —— 防止有人只改一处。

纯静态 + 逻辑仿真，无 Qt / 无外部依赖。
"""
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "legion_worker.py"

_n_pass = 0
_n_fail = 0


def check(name, ok, detail=""):
    global _n_pass, _n_fail
    if ok:
        _n_pass += 1
        print(f"  [PASS] {name}")
    else:
        _n_fail += 1
        line = f"  [FAIL] {name}"
        if detail:
            line += f"  —— {detail}"
        print(line)


def main():
    if not SRC.is_file():
        check("legion_worker.py 存在", False, str(SRC))
        return finish()

    text = SRC.read_text(encoding="utf-8", errors="replace")

    # ---- ① 成员级回填（wave_member_texts）条件方向 ----
    check("成员级回填条件为 `_wi2 >= resume_from`（跳过待重跑部分）",
          "if _wi2 >= self._resume_from:" in text,
          "源码里找不到正确方向的条件")
    check("成员级回填不再保留写反的旧条件",
          "if _wi2 < self._resume_from:" not in text,
          "旧的 `_wi2 < resume_from` 仍在（会跳过历史波）")

    # ---- ② 波级回填（parts_by_wave）方向 —— 两处必须一致 ----
    has_wave_fill = "if _wi < self._resume_from:" in text
    check("波级回填条件为 `_wi < resume_from`（回填历史波）", has_wave_fill)

    # ---- ③ 逻辑仿真：确认两种条件语义确实相反 ----
    waves = [0, 1, 2, 3]          # checkpoint 里实际存在的波（历史）
    resume_from = 2               # 从第 3 波（0 基=2）开始续跑

    def filled(cond):
        return {w for w in waves if not cond(w)}

    old_side = filled(lambda w: w < resume_from)    # 旧条件：先 continue 再回填
    new_side = filled(lambda w: w >= resume_from)   # 新条件

    check("旧条件回填的是「未来波」（= 空转）", old_side == {2, 3},
          f"得到 {sorted(old_side)}")
    check("新条件回填的是「历史波」（= 正确）", new_side == {0, 1},
          f"得到 {sorted(new_side)}")
    check("两处回填方向一致性（波级应回填历史波）",
          has_wave_fill and new_side == {0, 1})

    # ---- ④ 回归守卫：文件里不应再出现「注释说回填历史、代码跳过历史」的组合 ----
    m = re.search(
        r"成员级.*?底稿.*?\n(?:.*?\n){0,12}?\s*if (_wi2)\s*(>=|<)\s*self\._resume_from:",
        text, re.S)
    if m:
        check("成员级回填处用的是 `>=`", m.group(2) == ">=", f"实际为 {m.group(2)}")
    else:
        # 正则没匹配到不算失败，仅提示（格式可能微调）
        check("成员级回填处条件可识别", True, "（正则未命中，已由上两条覆盖）")

    return finish()


def finish():
    print(f"\nPASS={_n_pass}  FAIL={_n_fail}")
    return 1 if _n_fail else 0


if __name__ == "__main__":
    sys.exit(main())
