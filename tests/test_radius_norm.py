"""v4.181.1 回归：圆角归一（第二档）—— 矩形守三档，胶囊/圆形不得被误改。

背景：DESIGN.md §7 要求「圆角只用 6/8/10px 三档」，实测全项目 206 处圆角里
41 处非法。逐条看过上下文后发现：**大部分非法值是 radius=尺寸÷2 的胶囊/圆形**
（17px 发送按钮、24px 圆形按钮、14px 聊天气泡与步骤徽章、13px 头像…），
把它们归成三档会把按钮压成圆角方块、头像变方 —— **事故级视觉回退**。

因此确立 §7.1 豁免清单（已写进 DESIGN.md）：圆形 / 胶囊 / 滚动条 不套三档。
本套件就是这份清单的**机器守卫**，钉住两件事：

  1. **矩形元素不得出现三档外的值** —— 2/4/7/9/12px 这类"随手写"必须归零
     （v4.181.1 已把 13+1 处归完，这里防回潮）。
  2. **豁免项必须保持在豁免值** —— 防止后人按 §7 把胶囊"归正"。
     这条尤其重要：改它的人通常以为自己在"修复规范违规"。

用 AST/正则静态扫描，不 import ui（避免拉起 Qt 依赖），可跑在 CI / offscreen。
"""
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

_p = _f = 0


def check(label, got, exp=True, extra=""):
    global _p, _f
    ok = (got == exp)
    _p += ok
    _f += (not ok)
    print(f"  [{'PASS' if ok else 'FAIL'}] {label:<50} got={got!s:<6} exp={exp}  {extra}")


# 扫描范围与 _scan_radius.py 保持一致
FILES = ['ui.py', 'director_panel.py', 'legion_ui.py', 'automation_panel.py',
         'digital_twin_panel.py', 'director_chat.py', 'legion_chat.py', 'onboarding.py',
         'skill_market_ui.py', 'skill_manager_ui.py', 'tool_manager_ui.py', 'main.py',
         'chat_web.py', 'director_web.py']

RE = re.compile(r'border-radius:\s*([0-9]+)px')

# DESIGN.md §7：矩形元素只允许这三档
ALLOWED_RECT = {6, 8, 10}

# DESIGN.md §7.1 豁免清单（文件 -> 该文件中允许出现的豁免值集合）
# 这些值**必须存在**于豁免文件中；若在别的文件出现同样的值，仍按非法处理。
EXEMPT = {
    'ui.py': {
        3: '滚动条 thumb(§10) + 6x6 状态点(圆形 6/2)',
        4: '滚动条 handle(§10) + 8x8 状态点(圆形 8/2)',
        13: '26px 缩略图（圆形 26/2）',
        14: '聊天气泡（胶囊）',
        16: '32px 输入卡（胶囊 32/2）',
        17: '34px 发送/停止/录音按钮（胶囊 34/2）',
        18: '36px 搜索框（胶囊 36/2）',
        22: '44px 徽章（圆形 44/2）',
        24: '48px 圆形按钮（圆形 48/2）',
    },
    'chat_web.py': {
        14: '聊天气泡（胶囊）',
        5: 'Web 滚动条 thumb（§10）',
    },
    'director_panel.py': {
        14: '28px 步骤徽章（胶囊 28/2）',
    },
    'automation_panel.py': {
        9: '自动化 badge（胶囊）',
    },
}


def main():
    print("-- 1) 矩形元素守三档（不得有三档外的零碎值）--")

    violations = []   # (文件, 行号, 值)
    exempt_seen = {}  # 文件 -> {值: 次数}
    for f in FILES:
        path = os.path.join(ROOT, f)
        if not os.path.exists(path):
            continue
        with open(path, encoding='utf-8-sig') as fh:
            lines = fh.read().split('\n')
        allow = EXEMPT.get(f, {})
        for i, line in enumerate(lines):
            for m in RE.finditer(line):
                v = int(m.group(1))
                if v in ALLOWED_RECT:
                    continue
                if v in allow:
                    exempt_seen.setdefault(f, {}).setdefault(v, 0)
                    exempt_seen[f][v] += 1
                else:
                    violations.append((f, i + 1, v))

    check("矩形元素零碎圆角已归零", len(violations) == 0,
          extra="" if not violations else
          " | ".join(f"{f}:L{ln}={v}px" for f, ln, v in violations[:6]))

    if violations:
        for f, ln, v in violations:
            print(f"        违规：{f}:L{ln} border-radius:{v}px")

    print("-- 2) 豁免项仍在豁免值（防后人把胶囊'归正'）--")

    # 逐项核对：DESIGN.md §7.1 清单里点名的实例，都还活着
    # 注意：聊天气泡 14px 在 chat_web.py（Web 渲染），ui.py 里没有 —— 别写错文件
    expect_present = [
        ('ui.py', 17, '34px 发送/停止按钮必须保持 17px 胶囊'),
        ('ui.py', 24, '48px 圆形按钮必须保持 24px'),
        ('ui.py', 16, '32px 输入卡必须保持 16px 胶囊'),
        ('ui.py', 18, '36px 搜索框必须保持 18px 胶囊'),
        ('ui.py', 13, '26px 缩略图必须保持 13px 圆形'),
        ('ui.py', 22, '44px 徽章必须保持 22px 圆形'),
        ('chat_web.py', 14, 'Web 聊天气泡必须保持 14px 胶囊'),
        ('director_panel.py', 14, '步骤徽章必须保持 14px 胶囊'),
        ('automation_panel.py', 9, '自动化 badge 必须保持 9px 胶囊'),
    ]
    for f, v, label in expect_present:
        got = exempt_seen.get(f, {}).get(v, 0)
        check(label, got > 0, extra=f"实际 {got} 处")

    print("-- 3) DESIGN.md 必须载明豁免清单（判据同源）--")

    dpath = os.path.join(ROOT, 'DESIGN.md')
    check("DESIGN.md 存在", os.path.exists(dpath))
    if os.path.exists(dpath):
        with open(dpath, encoding='utf-8-sig') as fh:
            dsrc = fh.read()
        check("DESIGN.md 有 §7.1 圆角豁免清单",
              '7.1 圆角豁免清单' in dsrc)
        check("DESIGN.md 点名胶囊不得按三档归正",
              '把胶囊/圆形按三档' in dsrc or '胶囊' in dsrc)
        # 三档规则仍在
        check("DESIGN.md 仍写明 6/8/10 三档",
              '6px（小）' in dsrc or '圆角只用三档' in dsrc)

    print(f"\n汇总：PASS={_p} FAIL={_f}")
    return 0 if _f == 0 else 1


if __name__ == '__main__':
    sys.exit(main())
