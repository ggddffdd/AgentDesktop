"""v4.173.0 回归：① 导演台判据不被附件名点着 ② 附件路径基准（工作区优先）。

来源是两个实测事故（大哥附了 3 个 .md 后发消息）：
  A) 消息被整条吞掉（渲染「这条像给导演台的指令」、不处理）——
     对象词「导演台」出自「导演台模块改进建议…md」、动作词「修改」出自
     「…BUG修改方案…md」，**两个词分别来自两个附件名**，旧判据跨附件凑对命中。
  B) 同一批附件被判「文件不存在」—— v4.165.0 把附件落点迁到 WORKSPACE_DIR，
     解析调用点仍传 APP_DIR（漏改）。期间**选择文件附上的图片对模型完全不可见**。

自带临时工作区（monkeypatch ui.WORKSPACE_DIR），不碰用户数据。
"""
import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import ui  # noqa: E402

_p = _f = 0


def check(label, got, exp=True, extra=""):
    global _p, _f
    ok = (got == exp)
    _p += ok
    _f += (not ok)
    print(f"  {'✓' if ok else '✗'} {label:<58} got={got!s:<6} exp={exp}  {extra}")


BASE = "这几天都在修BUG，我花了大价钱调GPT高级模型弄的，还好现在都修完了"
ATTS = ["对话误调用工具BUG修改方案_v4.164.0_20260927.md",
        "Agent军团模块改进建议_v4.164.0_20260927.md",
        "导演台模块改进建议_v4.164.0_20260927.md"]
PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 40      # 只测解析路径，不校验图像内容


def _W():
    w = ui.ChatWindow.__new__(ui.ChatWindow)
    w.cfg = {}
    return w


# ---------------------------------------------------------------------------
def part_a_director_not_fooled():
    print("\n-- A) 附件名不能把导演台判据点着（实测事故原样回放） --")
    w = _W()
    marker = BASE + "".join(f"\n[文件: incoming/{f}]\n" for f in ATTS)
    missing = BASE + "".join(f"\n[文件不存在: incoming/{f}]\n" for f in ATTS)
    check("A1 原始形态（[文件:] 标记）→ 不判导演台",
          w._is_director_command(marker), False)
    check("A2 实际收到形态（[文件不存在:]）→ 不判导演台",
          w._is_director_command(missing), False)
    check("A3 纯正文（无附件）→ 不判导演台", w._is_director_command(BASE), False)
    stripped = ui._strip_attachment_refs(missing).lower()
    check("A4 剥离后不含对象词",
          [k for k in w._DIRECTOR_OBJ_KW if k in stripped], [])
    check("A5 剥离后不含动作词",
          [k for k in w._DIRECTOR_VERB_KW if k in stripped], [])
    check("A6 正文被完整保留（只剥标记、不动一个字）",
          all(s in stripped for s in ("这几天都在修bug", "还好现在都修完了")), True)
    # 三种标记形态都要能剥
    for label, s in (("[文件不存在: x]", ui._strip_attachment_refs("[文件不存在: a.md]")),
                     ("[非图片文件: x]", ui._strip_attachment_refs("[非图片文件: a.md]")),
                     ("[file: x]", ui._strip_attachment_refs("[file: a.png]")),
                     ("[图片已粘贴 2]", ui._strip_attachment_refs("[图片已粘贴 2]"))):
        check(f"A7 能剥掉 {label}", s.strip(), "")


def part_b_true_director_blocked():
    print("\n-- B) 真导演台指令必须仍然拦住（放宽判据最怕放跑真拦截） --")
    w = _W()
    for s in ["导演台进度怎么样了", "分镜跑到哪了", "把第3镜的关键帧改成夜晚",
              "主角换成短发", "重新生成三视图", "合成成片", "把这一镜重做一遍",
              "帮我看看成片到哪一步了"]:
        check(f"B {s}", w._is_director_command(s), True)


def part_c_same_sentence():
    print("\n-- C) 同句要求：挡跨句凑对，但不能因逗号漏判 --")
    w = _W()
    check("C1 对象词在前句、动作词在后句 → 不命中",
          w._is_director_command("导演台那边先不管。这份文案帮我修改一下。"), False)
    check("C2 动作词在前句、对象词在后句 → 不命中",
          w._is_director_command("帮我修改一下。导演台那边的事。"), False)
    check("C3 同一句（逗号隔开）→ 仍命中（逗号是有意义的停顿，不切断）",
          w._is_director_command("帮我把第三镜的关键帧，改成夜晚"), True)
    check("C4 同一句（无标点）→ 仍命中", w._is_director_command("把这一镜重做成白天"), True)


def part_d_no_false_positive():
    print("\n-- D) 正常消息不能被误伤 --")
    w = _W()
    for s in ["为什么会自动搜索？", "你好", "解释一下闭包", "帮我删掉昨天的临时文件",
              "做个视频", "Agent军团模块改进建议", "对话误调用工具BUG修改方案"]:
        check(f"D {s}", w._is_director_command(s), False)


def part_e_attachment_path_basis():
    print("\n-- E) 附件路径基准：工作区优先、程序目录回退（自带临时工作区） --")
    tmp = tempfile.mkdtemp(prefix="xc_attach_")
    inc = os.path.join(tmp, "incoming")
    os.makedirs(inc, exist_ok=True)
    with open(os.path.join(inc, "shot.png"), "wb") as f:
        f.write(PNG)
    with open(os.path.join(inc, "导演台模块改进建议_v4.1.md"), "w", encoding="utf-8") as f:
        f.write("# x")

    alt = tempfile.mkdtemp(prefix="xc_appdir_")
    os.makedirs(os.path.join(alt, "incoming"), exist_ok=True)
    with open(os.path.join(alt, "incoming", "shot.png"), "wb") as f:
        f.write(PNG)

    _old = ui.WORKSPACE_DIR
    ui.WORKSPACE_DIR = tmp      # _resolve 运行期读模块全局 → 替换有效
    # v4.216.0：_extract_file_image_parts 已迁 ui_msg.py，其模块全局必须同步替换
    # （ui 的 re-export 是另一份绑定，只打 ui 打不到真身）
    import ui_msg
    _old_msg = ui_msg.WORKSPACE_DIR
    ui_msg.WORKSPACE_DIR = tmp
    try:
        clean_img, parts = ui._extract_file_image_parts(
            "[文件: incoming/shot.png]", ui.APP_DIR)
        check("E1 工作区图片能被解析成 image part（v4.165.0 起这里一直是坏的）",
              len(parts), 1)
        check("E2 解析成功后不再出现「文件不存在」", "文件不存在" in clean_img, False)
        clean_doc, parts2 = ui._extract_file_image_parts(
            "[文件: incoming/导演台模块改进建议_v4.1.md]", ui.APP_DIR)
        check("E3 .md 判为非图片文件并保留可读路径", clean_doc.count("非图片文件"), 1)
        check("E4 .md 不产生 image part", len(parts2), 0)
        clean_no, _ = ui._extract_file_image_parts(
            "[文件: incoming/nope.png]", ui.APP_DIR)
        check("E5 真不存在的文件仍提示「文件不存在」（别把闸门拆了）",
              "文件不存在" in clean_no, True)

        ui.WORKSPACE_DIR = tempfile.mkdtemp(prefix="xc_empty_")   # 工作区空
        ui_msg.WORKSPACE_DIR = ui.WORKSPACE_DIR
        clean_alt, parts_alt = ui._extract_file_image_parts(
            "[文件: incoming/shot.png]", alt)
        check("E6 工作区没有时回退程序目录（兼容历史相对路径）",
              "文件不存在" in clean_alt, False)
        check("E6b 回退时同样能提取出 image part", len(parts_alt), 1)
    finally:
        ui.WORKSPACE_DIR = _old
        ui_msg.WORKSPACE_DIR = _old_msg


def part_f_source_contract():
    print("\n-- F) 源码契约：两个基准必须同源（防同类漏改复发） --")
    src = open(os.path.join(ROOT, "ui.py"), encoding="utf-8-sig").read()
    # v4.216.0：_extract_file_image_parts 已迁 ui_msg.py，其源码锚点跟过去
    src_msg = open(os.path.join(ROOT, "ui_msg.py"), encoding="utf-8-sig").read()
    check("F1 附件落点用 WORKSPACE_DIR/incoming",
          'os.path.join(WORKSPACE_DIR, "incoming")' in src)
    check("F2 解析侧不再有「只按 app_dir 拼相对路径」的老写法",
          "path = os.path.join(app_dir, rel) if not os.path.isabs(rel) else rel"
          not in src and "path = os.path.join(app_dir, rel) if not os.path.isabs(rel) else rel"
          not in src_msg)
    check("F3 解析侧显式两基准回退（工作区优先）",
          "for _base in (WORKSPACE_DIR, app_dir):" in src_msg)
    check("F4 判据前统一剥附件标记",
          "t = _strip_attachment_refs(text).lower()" in src)
    check("F5 同句判据已抽出（不散在分支里）",
          "def _director_kw_same_sentence" in src)


def part_g_negative():
    print("\n-- G) 负面验证：拆掉任一道修，事故必须复现 --")
    w = _W()

    # G1 证明「剥附件标记」必需 —— 单行（附件标记与正文同句）
    one_line = "看下这个 [文件不存在: incoming/导演台模块改进建议.md] 帮我修改一下"
    check("G1-前 剥离生效时不误判", w._is_director_command(one_line), False)
    _old_strip = ui._strip_attachment_refs
    ui._strip_attachment_refs = lambda t: t          # 模拟「剥离那步失效」
    try:
        check("G1 拆掉剥离 → 单行附件名立刻点着判据（证明剥离必需）",
              w._is_director_command(one_line), True)
    finally:
        ui._strip_attachment_refs = _old_strip

    # G2 证明「同句判据」必需 —— 对象词与动作词分处两行
    multi = "导演台模块改进建议_v4.1.md 收到。\n这份文案帮我修改一下。"
    check("G2-前 同句判据生效时不误判", w._is_director_command(multi), False)
    _old_ss = ui.ChatWindow._director_kw_same_sentence
    ui.ChatWindow._director_kw_same_sentence = staticmethod(
        lambda t, objs, verbs: any(o in t for o in objs) and any(v in t for v in verbs))
    try:
        check("G2 拆掉同句判据 → 跨行凑对又命中（证明同句判据必需）",
              w._is_director_command(multi), True)
    finally:
        ui.ChatWindow._director_kw_same_sentence = _old_ss


def part_h_intent_guard_not_fooled():
    print("\n-- H) 附件名也不能点着 intent_guard（同类隐患的第三处） --")
    import intent_guard as ig
    # 只附一个附件、正文为空 → 旧版会被文件名判成「喊停/讨论」，整轮禁止工具
    for s in ["[文件不存在: incoming/不要跟陌生人说话.md]",
              "[文件不存在: incoming/别急着辞职.md]",
              "[文件不存在: incoming/为什么会自动搜索.md]",
              "[文件不存在: incoming/视频怎么做出来的.md]",
              "[非图片文件: incoming/不要乱动配置.md]",
              "[文件: incoming/导演台模块改进建议.md]"]:
        check(f"H1 只附附件不误判：{s[18:40]}", ig.blocks_tool_call(s), False)
    # 反方向：真喊停 / 真讨论仍必须拦住
    for s in ["别生成视频了", "不要做了", "算了", "取消",
              "为什么会自动搜索？", "这个视频生成得真不错", "视频怎么生成的？"]:
        check(f"H2 真拦的仍拦：{s}", ig.blocks_tool_call(s), True)
    # 单一来源：ui 那份必须是转发，不是第二份实现
    check("H3 ui._strip_attachment_refs 与 intent_guard 同源",
          ui._strip_attachment_refs("[文件: a.md]")
          == ig.strip_attachment_refs("[文件: a.md]"), True)
    src_ig = open(os.path.join(ROOT, "intent_guard.py"), encoding="utf-8-sig").read()
    check("H4 归一化入口 _norm 里剥附件引用（单点，不靠各判据自觉）",
          "return strip_attachment_refs(text).strip()" in src_ig)
    src_ui = open(os.path.join(ROOT, "ui.py"), encoding="utf-8-sig").read()
    check("H5 ui 侧只转发、不重复实现剥法",
          "def strip_attachment_refs(text)" not in src_ui)
    # 负面：把入口剥离拆掉 → 误判必须复现
    _old = ig.strip_attachment_refs
    ig.strip_attachment_refs = lambda t: t          # 模拟「入口不剥了」
    try:
        check("H6 拆掉入口剥离 → 附件名立刻点着判据（证明单点必需）",
              ig.blocks_tool_call("[文件不存在: incoming/不要跟陌生人说话.md]"), True)
    finally:
        ig.strip_attachment_refs = _old


def main():
    part_a_director_not_fooled()
    part_b_true_director_blocked()
    part_c_same_sentence()
    part_d_no_false_positive()
    part_e_attachment_path_basis()
    part_f_source_contract()
    part_g_negative()
    part_h_intent_guard_not_fooled()
    print(f"\n汇总：PASS={_p} FAIL={_f}")
    return 0 if _f == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
