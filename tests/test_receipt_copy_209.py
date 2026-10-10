# -*- coding: utf-8 -*-
"""回执文案统一验收（v4.209.0 波A，DESIGN.md §13）

范围：12 处「用户点了查看→ 进去发现没东西可看」型回执。
不是全部 153 处回执 —— 那些是操作前校验 / 操作结果，该用模态弹窗，不动。

本波口径（DESIGN §13.2）：
  句式统一「还没有X」／两段用句号断开不用破折号／按钮名用「」不带 emoji／
  **err=True 只留给真失败**（"还没开始做"不是错误）

判据分六类（独立运行：python tests/test_receipt_copy_209.py）：

A 导演台 6 处   —— 文案写死 + err 语义（4 处去 / 2 处留）
B 军团 4 处     —— 文案写死 + emoji/破折号清零
C 技能市场 1 处  —— 句式对齐
D 全局口径守门   —— 破折号/emoji 在这11 处里零残留；err 用法逐处写死
E 不接组件       —— 回执类**不许**长成空态卡（v4.208 已定，跨轮守护）
F 文档与版本

沿用 L190（先剥注释再数字符串）/ L192（分类判据用白名单，不用句式推断）。
"""
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
sys.path.insert(0, HERE)

FAIL = []
CHECKED = 0


def check(name, ok, extra=""):
    global CHECKED
    CHECKED += 1
    tag = "OK  " if ok else "FAIL"
    print(f"  [{tag}] {name}" + (f"  — {extra}" if extra and not ok else ""))
    if not ok:
        FAIL.append(name)


def _hook(t, v, tb):
    print(f"\n[!] 未捕获异常：{t.__name__}: {v}")
    print(f"PASS={CHECKED - len(FAIL)} FAIL={len(FAIL)}")
    sys.exit(1)


sys.excepthook = _hook


def read(name):
    with open(os.path.join(ROOT, name), encoding="utf-8") as f:
        return f.read()


def strip_comments(src):
    """剥注释（L190：注释里会故意引用旧文案解释改动）。"""
    out = []
    for ln in src.replace("\r\n", "\n").split("\n"):
        if ln.strip().startswith("#"):
            continue
        out.append(re.sub(r"#.*$", "", ln))
    return "\n".join(out)


def strip_comments_keep_lines(src):
    """剥注释但**保留行号**（v4.210.0）。

    与 `strip_comments` 的区别：那个会 `continue` 掉整行 → 行号漂移
    （本波踩过：扫出的候选行号偏了 616 行，差点写成幽灵条目）。
    这里把注释行**清空而不是丢弃**，行号与原文一一对应。
    """
    out = []
    for ln in src.replace("\r\n", "\n").split("\n"):
        out.append("" if ln.strip().startswith("#") else re.sub(r"#.*$", "", ln))
    return "\n".join(out)


_dp = strip_comments(read("director_panel.py"))
_lg = strip_comments(read("legion_ui.py"))
_sm = strip_comments(read("skill_market_ui.py"))

print("=== v4.209.0 回执文案统一 波A 验收（DESIGN §13）===")

# ------------------------------------------------------------- A 导演台 6 处

print("\n--- A 导演台 6 处：文案 + err 语义 ---")
# A1-A5：文案写死
NEW_DP = {
    "还没有可重新生成的片段。先跑一遍生成或重生成。": "A1",
    "还没有素材。先点「开始导演」，跑起来后这里就有东西。": "A2",
    "还没有工程可导出。先点「开始导演」建一个。": "A3",
    "还没有可用片段。先去「生成」出片，再来编排时间线。": "A4",
    "还没有历史版本。改一次（✎改 / ↻重生成）就有了。": "A5",
}
for txt, tag in NEW_DP.items():
    check(f"{tag} 导演台文案写死：{txt[:16]}…", f'"{txt}"' in _dp)

# A6：4 处"还没开始做"不许再带 err=True
_no_err = [
    "还没有可重新生成的片段。先跑一遍生成或重生成。",
    "还没有工程可导出。先点「开始导演」建一个。",
    "还没有可用片段。先去「生成」出片，再来编排时间线。",
    "还没有历史版本。改一次（✎改 / ↻重生成）就有了。",
]
for txt in _no_err:
    line = [l for l in _dp.split("\n") if f'"{txt}"' in l]
    ok = line and "err=True" not in line[0]
    check(f"A6 这处不该再是红色：{txt[:14]}…", ok,
          line[0].strip()[:80] if line else "未找到")

# A7：回滚失败那处**必须保留** err=True（它真的是失败）
_rb = [l for l in _dp.split("\n") if "回滚失败" in l]
check("A7 回滚失败保留 err=True（真的是失败）",
      _rb and "err=True" in _rb[0], _rb[0].strip()[:90] if _rb else "未找到")
check("A7b 回滚失败文案写死（括号改冒号 + 句式统一）",
      '"回滚失败：还没有历史版本可回滚。"' in _dp)

# A8：导演台这批一共只应剩 1 处 err=True
_dp_receipt = [l for l in _dp.split("\n")
               if "_set_status(" in l and "还没有" in l]
check("A8 导演台「还没有」类回执行数 == 6", len(_dp_receipt) == 6, len(_dp_receipt))
check("A8b 其中带 err=True 的恰好 1 处（回滚失败）",
      sum(1 for l in _dp_receipt if "err=True" in l) == 1,
      sum(1 for l in _dp_receipt if "err=True" in l))

# -------------------------------------------------------------- B 军团 4 处

print("\n--- B 军团面板 4 处 ---")
check("B1 弹窗标题改成主文案口径「还没有成员」",
      'QMessageBox.information(self, "还没有成员",' in _lg)
check("B2 正文补下一步、逗号改句号",
      '"这个项目还没有成员，先给波次里加人。"' in _lg)
check("B3 选中团队：破折号改句号 + 去 emoji",
      '"还没有选中团队。先去「团队库」选一个。"' in _lg)
# B4：v4.253.0 死代码清理——原「在编成员」文案宿主 _install_skill 已删（v4.247 删
# 按钮后无调用方）。改为负向断言：这句破折号文案不该再出现（口径守门仍有效，防未来又加回）。
check("B4 在编成员文案已随死代码移除（破折号文案不再出现）",
      '"「%s」已装好。\\n\\n项目「%s」还没有在编成员。先组队，' not in _lg)
check("B5 报告：补下一步",
      '"还没有生成过报告。跑完一波就会出来。"' in _lg)
# B6：授权记录那条**按设计不动**（v4.208 判过：回执不是空态）
check("B6 授权记录回执未被改动（v4.208 定的口径：回执不接组件）",
      "还没有任何授权记录。跑一次带验收的军团后这里就有。" in _lg)

# ------------------------------------------------------------ C 技能市场 1 处

print("\n--- C 技能市场 1 处（范本，只对齐句式）---")
check("C1 句式统一为「还没有X」",
      '"该技能还没有可用的安装链接。' in _sm)
check("C2 句号断开（不用句号连排）",
      "请用其仓库链接安装，或从「从链接安装」粘贴。" in _sm)
check("C3 两条替代路径仍在（这是它作为范本的原因）",
      "仓库链接" in _sm and "从「从链接安装」粘贴" in _sm)

# ------------------------------------------------------------ D 全局口径守门

print("\n--- D 口径守门（反向计数）---")
# D1：这 11 处里破折号清零
import_lines = [l for l in (_dp + _lg + _sm).split("\n")
                if "还没有" in l and ("_set_status" in l or "MessageBox" in l
                                     or "chat_panel.say" in l
                                     or "已装好" in l)]
check("D1 「还没有」类文案里破折号 —— 清零",
      not any("——" in l for l in import_lines),
      [l.strip()[:60] for l in import_lines if "——" in l])
# D2：emoji 清零（🧩 那处）
# D2：emoji 清零。
# 两轮踩坑记在这里：
# ① 第一版 `[\U0001F300-\U0001FAFF☀-➿]` 把 `↻`(U+21BB) 误判成 emoji；
# ② 第二版 `[...\U00002600-\U000027BF...]` —— `↻` 修好了，但 `✎`(U+270E)
#    正好落在 U+2600-U+27BF 区间里，于是「改一次（✎改 / ↻重生成）」又假红。
#    实测：`✎`=0x270E、`↻`=0x21BB。
# 结论：`✎改` / `↻重生成` 是**功能标记**（指代界面上的按钮/动作），
# 不属于"装饰性 emoji"，与项目去 emoji 化的方向不冲突 —— 明确列进白名单。
_EMOJI_RE = re.compile(r"[\U0001F300-\U0001FAFF\U0001F000-\U0001F0FF"
                       r"\U00002600-\U000027BF\U0000FE0F]")
_ALLOWED_SYMBOLS = {"✎", "↻"}   # 功能标记，白名单


def _emoji_hits(s):
    return {c for c in _EMOJI_RE.findall(s)} - _ALLOWED_SYMBOLS


check("D2 这批文案里 emoji 清零（✎/↻ 属功能标记，已豁免）",
      not any(_emoji_hits(l) for l in import_lines),
      [l.strip()[:60] for l in import_lines if _emoji_hits(l)])
check("D2b 确认「✎改 / ↻重生成」不算 emoji（假阳性回归，两轮都栽在这）",
      not _emoji_hits("改一次（✎改 / ↻重生成）就有了。"),
      _emoji_hits("改一次（✎改 / ↻重生成）就有了。"))
check("D2c 但真 emoji 仍会被抓住（豁免没有放水）",
      bool(_emoji_hits("去「\U0001F9E9 团队库」选一个")),
      _emoji_hits("去「\U0001F9E9 团队库」选一个"))
# ------------------------------------------- D3 旧句式分类登记表（v4.210.0 重写）
# 为什么重写：**白名单证明不了"该改的都改了"**。
# 旧 D3 是 `re.search(r'没有(可用|素材|工程|历史)')` —— 只能守住列进正则的那
# 4 个词。本波按"含『没有』且不含『还没有』"重扫，**新冒出 6 处审核没点名的**，
# 旧判据对它们一无所知，照样绿。
#
# 登记表按**关键串**索引，不用行号（行号随编辑漂移，见 L196）。
# 决定只有两种，各有对应的判据：
#   改 → 关键串是**旧文案**，D3c 要求它在源码里已不存在（证明真改了）
#   留 → 关键串是**当前文案**，D3b 要求它仍在源码里（证明登记表没漂移）
_REGISTRY = [
    # (文件, 关键串, 决定, 理由)
    ("legion_ui.py", "没有 checkpoint", "改",
     "存档是用户能补的 → 待办语义；原标题还是名词短语，一并改成完整句"),
    ("legion_ui.py", "角色库里没有「项目经理」角色", "改",
     "角色是用户能补的（正文已指引去角色库新建）→ 待办语义"),
    ("ui.py", "产物根目录没有需要归档的内容（已按日期分层）", "改",
     "待办语义 + 原句把范围说死在「产物根目录」，破折号式括号也一并去掉"),
    ("ui.py", "没有勾选任何项，未做改动", "改",
     "勾选是用户接下来就能做的事 → 待办语义"),
    ("workflow_manager_ui.py", "这一步没有提示词", "改",
     "提示词是用户能补上的 → 待办语义"),
    ("legion_ui.py", "没有可清的内容", "留",
     "状态说明：可清的内容不是用户要补的东西，说「还没有」等于暗示去制造垃圾"),
    ("director_panel.py", "口播模式没有跨镜道具", "留",
     "状态说明：口播模式本就不抽跨镜道具；err=True 是「拒绝执行」，不是误红"),
    ("director_panel.py", "本剧本没有跨镜复用的关键道具", "留",
     "状态说明：抽了但没结果，正文已声明不影响后续流程"),
    ("ui.py", "没有可用麦克风", "留",
     "状态说明：设备不存在不是用户「还没做」的事，后半句已给出动作指引"),
    ("legion_ui.py", "没有项目经理可联系", "留",
     "状态说明：因军团没在跑而联系不上，前提在前半句已说明，不是待办"),
]

_CARRIERS = ("_set_status(", "QMessageBox.information(",
             "status_label.setText(", "chat_panel.say(")


def _iter_calls(fname, src):
    """产出 (行号, 调用文本)。

    调用可能**跨行** —— 从含载体的那行一直吃到括号配平（最多再看 5 行），
    否则像 `QMessageBox.information(self, "已清空" if did else "无需清空",`
    换行后再写 `did or "没有可清的内容")` 这种写法会**整条漏扫**。
    """
    lines = strip_comments_keep_lines(src).split("\n")
    for i, ln in enumerate(lines, 1):
        if not any(c in ln for c in _CARRIERS):
            continue
        buf, j = ln, i
        while buf.count("(") > buf.count(")") and (j - i) < 5 and j < len(lines):
            j += 1
            buf += " " + lines[j - 1]
        yield i, buf


_PY_SRC = {f: strip_comments_keep_lines(read(f))
           for f in os.listdir(ROOT)
           if f.endswith(".py") and not f.startswith("_")}
_CANDS = []
for _f, _s in sorted(_PY_SRC.items()):
    for _i, _t in _iter_calls(_f, _s):
        if "没有" in _t and "还没有" not in _t:
            # 存**全文**用于匹配，显示时才截断 —— 截断过的文本会让关键串匹配失效
            # （本波踩过：80 字截断把行尾的「没有可清的内容」切掉了，判成未登记）
            _CANDS.append((_f, _i, _t.strip()))

# D3a：扫到的每一处都必须**已登记为「留」**（新冒出来的不许无声无息）
_unreg = [f"{f}:{i} {t[:60]}" for f, i, t in _CANDS
          if not any(k in t for ff, k, d, _ in _REGISTRY
                     if ff == f and d == "留")]
check("D3a 每个「没有X」回执都已登记（新冒出来的必须处置，不许漏）",
      not _unreg, _unreg)

# D3b：标「留」的仍在源码里 —— 登记表记了个已被删掉的串就是漂移
_missing = [f"{f}::{k}" for f, k, d, _ in _REGISTRY
            if d == "留" and k not in _PY_SRC.get(f, "")]
check("D3b 标「留」的关键串在源码里仍找得到（登记表没漂移）",
      not _missing, _missing)

# D3c：标「改」的旧文案真的没了 —— 这是登记表比白名单强的地方。
# 必须用 `(?<!还)` 否定后视：**「还没有X」里天然含「没有X」这个子串**，
# 本波 4 条"改"里有 2 条就是这样被误判成"没改"（改成"还没有"后旧串仍在）。
_left = [f"{f}::{k}" for f, k, d, _ in _REGISTRY
         if d == "改"
         and re.search(r"(?<!还)" + re.escape(k), _PY_SRC.get(f, ""))]
check("D3c 标「改」的旧文案确实已不存在（不是嘴上说改）", not _left, _left)

# D3d~D3f：登记表自身的完整性（不许靠删条目 / 写空理由让上面三条空转）
_keys = [k for _, k, _, _ in _REGISTRY]
check("D3d 关键串无重复", not [k for k in set(_keys) if _keys.count(k) > 1],
      [k for k in set(_keys) if _keys.count(k) > 1])
_thin = [k for _, k, _, r in _REGISTRY if len(r.strip()) < 12]
check("D3e 每条登记都写了可查的理由（≥12 字，不许一句话蒙混）", not _thin, _thin)
_bad = [k for _, k, d, _ in _REGISTRY if d not in ("改", "留")]
check("D3f 决定值只有「改 / 留」两种", not _bad, _bad)
_ANCHOR = {"没有 checkpoint", "角色库里没有「项目经理」角色",
           "产物根目录没有需要归档的内容（已按日期分层）",
           "没有勾选任何项，未做改动", "这一步没有提示词"}
check("D3g 本波「改」记录仍在登记表里（删条目会让 D3c 空转）",
      _ANCHOR <= {k for _, k, d, _ in _REGISTRY if d == "改"},
      sorted(_ANCHOR - {k for _, k, d, _ in _REGISTRY if d == "改"}))
check("D3h 扫描器确实扫到了东西（登记表不是空转：候选 ≥3）", len(_CANDS) >= 3,
      f"候选数={len(_CANDS)}")
# D4：状态栏没被顺手改布局（波A 只改文案）
check("D4 director_status 未被加 wordWrap（波 A 不动布局）",
      "director_status.setWordWrap" not in _dp)
check("D5 _set_status 函数本身未被改（err 语义保持原样）",
      'color = THEME["accent"] if not err else THEME["danger_text"]' in _dp)

# ------------------------------------------------------- E 回执不接组件（跨轮守护）

print("\n--- E 回执类不许长成空态卡（v4.208 跨轮守护）---")
# v4.208 E4b 的白名单是"5 处合法空态的主文案"；这里守的是反面：
# 本轮这11 处回执里任何一句都**不许**出现在 empty_state() 的参数里。
_ok_titles = {
    "还没有对话",         # ui.py 会话空态
    "还没有会话",         # ui.py 搜索空态 · 无关键词分支
    "没有匹配「{q}」的会话",  # ui.py 搜索空态 · 有关键词分支（**f-string**）
    "还没有任务",         # automation_panel
    "本波还没有成员",     # legion_ui
    "暂无待审核技能",     # ui.py 技能审核
    # v4.210.0（P2-2 / P2-3）新增两处，同屏指引按钮均已 grep 确认真实存在：
    "这里是最近对话",     # ui.py 欢迎页 · "有会话但都被过滤掉"分支（非"还没有"）
    "还没有波次",         # legion_ui 波次区 · 删光最后一波后
}
_seen = set()
for f in os.listdir(ROOT):
    if not f.endswith(".py") or f.startswith("_"):
        continue
    # 抓 f 前缀：v4.207 搜索空态那条是 f"没有匹配「{q}」的会话"，
    #   第一版只抓 `empty_state(\s*"` 漏掉它 → E1 假红。
    # 排除 empty_state.py 自身：它的 **docstring 用法示例**里写着
    #   `empty_state("没有匹配的会话", compact=True)`，
    #   那是文档不是真实调用点（第二版漏了它 → 又一次假红）。
    if f == "empty_state.py":
        continue
    for m in re.finditer(r'empty_state\(\s*f?"([^"]*)"', read(f)):
        _seen.add(m.group(1))
check("E1 全项目 empty_state 主文案仍只有那 6 处合法空态",
      _seen == _ok_titles, sorted(_seen ^ _ok_titles))
for _t in ("还没有素材", "还没有工程可导出", "还没有可用片段", "还没有任何授权记录",
           "还没有生成过报告", "还没有选中团队"):
    check(f"E2 回执文案没被接成空态：{_t[:12]}…", _t not in _seen)

# --------------------------------------------------------------- F 文档与版本

print("\n--- F 文档与版本 ---")
_dz = read("DESIGN.md")
check("F1 DESIGN 记了本波的三条口径（§13）", "13." in _dz and "还没有" in _dz)
# F2 第一版只查 `err=True` 一个短语 —— 把 §13.2 第 3 条整段删掉也不红
#（因为波次表里还有别处提到 err=True）。改成查那一段的**多个要素**。
_i = _dz.find("### 13.2")
_seg = _dz[_i:_i + 900] if _i >= 0 else ""
check("F2 DESIGN §13.2 记了 err 语义这条（'还没开始做不是错误'）",
      all(k in _seg for k in ("err=True", "danger_text", "回滚失败", "4 处")),
      [k for k in ("err=True", "danger_text", "回滚失败", "4 处") if k not in _seg])
_cl = read("CHANGELOG.md")
check("F3 CHANGELOG 有 v4.209.0 条目", "v4.209.0" in _cl)
# F4 第一版把版本号写死成 v4.209.0 —— v4.209.1 一升就红。
# 这跟 test_empty_state_208 的 F5 是同一个坑（L196），当时只修了 208 那个。
# 现在统一改成跟随式：版本号一致性由 release_check.py 把关，判据不重复守。
_cf = read("config.py")
_m = re.search(r'APP_VERSION = "v(\d+)\.(\d+)\.(\d+)"', _cf)
_rd = read("README.md")
_i = _rd.find("当前版本")
_r = re.search(r'v\d+\.\d+\.\d+', _rd[_i:_i + 60]) if _i >= 0 else None
check("F4 config.APP_VERSION 存在", _m is not None,
      _cf[_cf.find("APP_VERSION"):_cf.find("APP_VERSION") + 40])
check("F4b README 与 config 版本一致（跟随式）",
      _m and _r and _m.group(0).split('"')[1] == _r.group(0),
      f"config={_m.group(0) if _m else '?'} README={_r.group(0) if _r else '?'}")

print(f"\nPASS={CHECKED - len(FAIL)} FAIL={len(FAIL)}")
if FAIL:
    print("失败项：")
    for f in FAIL:
        print("  -", f)
sys.exit(1 if FAIL else 0)
