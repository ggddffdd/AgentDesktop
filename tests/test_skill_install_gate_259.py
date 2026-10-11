# -*- coding: utf-8 -*-
r"""v4.259.0 判据：军团装技能的「装前验货」判据必须与真正加载器一致（P1-8）。

judge-first：本文件先于实现编写，**预期红**。改完源码后转绿。

来源：v4.258.x 同族扫描（跨文件同名 + 体不同）扫出 `_parse_skill_md` 三份实现。
实地核对后，定位到 `legion.install_skill_from_github`（legion.py:6240）这一处：

  ① **校验器与加载器不是同一份**。
     · 校验（装前验货）走 `legion._parse_skill_md`（8530）—— 宽松版，
       注释自己写着「没有 frontmatter 也允许」，**只要文件读得到就返回非空 dict**；
     · 真正加载走 `skill_loader._parse_skill_md`（232）—— 严格版，
       `if not text.strip(): return None`（内容全空白 → 不进技能列表）。
     于是「装前先验货：解析不了 frontmatter 的技能装进去也是废的」这句注释
     **与实现对着干**：校验永远放行。

  ② 实测四种输入（无 frontmatter 无标题 / 有标题 / 折叠 description / 空文件）：
     ```python
     A 无 frontmatter 无标题 → legion 放行, loader name='slug'   可加载
     B 无 frontmatter 有标题 → legion 放行, loader name='测试技能' 可加载
     C description: >        → legion 读到 ">" , loader 读到正确描述（解析漂移）
     D 空白/空文件           → legion 放行, loader None            ★装了但列表里没有
     ```

  ③ **真正的后果是占位**。安装函数开头有
     `if os.path.isfile(dst): return False, "技能「x」已经装过了（…）。不覆盖已有版本。"`
     —— 一个被放行的空壳技能**物理占住 slug**，而用户侧看到的是「✅ 已安装」……
     然后在技能列表里找不到它，想重装又被"已经装过了"挡回来。**死循环**。

  ④ 注意：真·空字符串进不来（前面有 `if err or not txt: return "下载内容为空"`）。
     能钻过去的是 **`not txt` 判不出的空白字符内容**（`"   \n\t\n"`）：
     非空字符串 → 放行写盘 → loader `text.strip()` 为空 → None。实测可复现。

修法方向（本判据此为准绳）：
  · 「能不能装」的判据**复用真正加载器的判据**（skill_loader 那份），
    做到 **装得进 = 加载得出**；
  · **只加不减**：legion 宽松版继续供 `scan_available_skills` / `_load_skill_prompt`
    使用（它要 `requires_tools` 字段，skill_loader 版不解析，不能顶掉）；
  · **fail-open**：加载器不可用（import 失败）时放行，不能因为解析器抽风就装不了技能；
  · 不许误伤——无 frontmatter 但有 `# 标题`、或纯正文（靠 slug 兜底 name）的技能
    **本来就能加载**，必须继续放行。

纪律：行为级断言（走真实 install 返回值 + 真实 loader 加载结果，不查源码字符串）；
反向用例必配；不联网（mock `github_fetch_raw` / `audit_skill_text`）。
"""
import os
import shutil
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import legion  # noqa: E402
import skill_loader  # noqa: E402

PASS = 0
FAIL = 0


def ck(cond, name, extra=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print("  [OK] %s" % name)
    else:
        FAIL += 1
        print("  [FAIL] %s%s" % (name, ("  → " + str(extra)) if extra else ""))
    return bool(cond)


GOOD_FM = ("---\nname: 好技能\ndescription: 一个能用的技能\nemoji: ✅\n"
           "category: 工具\n---\n\n正文内容。\n")
NO_FM_TITLE = "# 标题技能\n\n这是正文。\n"
NO_FM_PLAIN = "纯正文，没有 frontmatter，也没有标题。\n"
FOLD_DESC = ("---\nname: 折叠技能\ndescription: >\n  第一行\n  第二行\n"
             "emoji: 🔥\n---\n\n正文。\n")
BLANK_ONLY = "   \n\t\n  \n"          # ★ 钻过 `not txt` 但 loader 判废
NEARLY_BLANK = "\n\n\n"


def _install(skills_dir, slug, content):
    """走**真实安装路径**（只 mock 网络与内容审计），返回 (ok, msg)。"""
    _of, _oa = legion.github_fetch_raw, legion.audit_skill_text
    legion.github_fetch_raw = lambda *a, **k: (content, None)
    legion.audit_skill_text = lambda txt: ("P2", [])
    try:
        return legion.install_skill_from_github(
            "someone/repo", "skills/x/SKILL.md", slug=slug, skills_dir=skills_dir)
    finally:
        legion.github_fetch_raw = _of
        legion.audit_skill_text = _oa


def _loadable(path, slug):
    """真正加载器（skill_loader）能不能加载出这个技能。"""
    try:
        return skill_loader._parse_skill_md(path, slug) is not None
    except Exception:
        return False


def main():
    tmp = tempfile.mkdtemp(prefix="skillgate259_")
    print("临时技能目录：%s" % tmp)

    print("\n---- G1 空白内容必须拒收并回滚（核心：不占位）----")
    ok, msg = _install(tmp, "blank_skill", BLANK_ONLY)
    dst = os.path.join(tmp, "blank_skill", "SKILL.md")
    ck(ok is False, "G1-1 空白 SKILL.md → 安装必须失败",
       "实测返回 ok=%r msg=%r" % (ok, msg))
    ck(not os.path.isfile(dst), "G1-2 空白 SKILL.md 必须已回滚（磁盘上不存在）",
       "文件仍在：%s" % dst)
    ck(not os.path.isdir(os.path.join(tmp, "blank_skill")),
       "G1-3 空目录也不残留（回滚要干净）")

    print("\n---- G2 被拒后同名可重试（★真正后果：不许被'已经装过了'挡死）----")
    ok2, msg2 = _install(tmp, "blank_skill", GOOD_FM)
    ck(ok2 is True, "G2-1 空白安装被拒后，同名可用正常内容重装成功",
       "实测 ok=%r msg=%r" % (ok2, msg2))
    ck("已经装过" not in (msg2 or ""), "G2-2 重试时不出现「已经装过了」占位提示",
       msg2)

    print("\n---- G3 换行-only 内容同样拒收 ----")
    ok3, msg3 = _install(tmp, "nl_skill", NEARLY_BLANK)
    ck(ok3 is False, "G3-1 纯换行 SKILL.md → 拒收", "ok=%r" % ok3)
    ck(not os.path.isfile(os.path.join(tmp, "nl_skill", "SKILL.md")),
       "G3-2 纯换行 SKILL.md 已回滚")

    print("\n---- G4 不许误伤：这些本来就能加载，必须继续放行 ----")
    for tag, content in (("有frontmatter", GOOD_FM),
                         ("无frontmatter有标题", NO_FM_TITLE),
                         ("无frontmatter纯正文", NO_FM_PLAIN),
                         ("折叠description", FOLD_DESC)):
        slug = "ok_" + str(abs(hash(tag)) % 10000)
        okk, mm = _install(tmp, slug, content)
        ck(okk is True, "G4 %s → 放行" % tag, "ok=%r msg=%r" % (okk, mm))
        p = os.path.join(tmp, slug, "SKILL.md")
        ck(os.path.isfile(p), "G4 %s → 文件落盘" % tag)
        ck(_loadable(p, slug), "G4 %s → 加载器确实加载得出（不许装了用不了）" % tag)

    print("\n---- G5 判据对齐不变量：装得进 ⟺ 加载得出 ----")
    rows = []
    for tag, content in (("空白", BLANK_ONLY), ("纯换行", NEARLY_BLANK),
                         ("有frontmatter", GOOD_FM),
                         ("无frontmatter有标题", NO_FM_TITLE),
                         ("无frontmatter纯正文", NO_FM_PLAIN),
                         ("折叠description", FOLD_DESC)):
        slug = "inv_" + str(abs(hash(tag)) % 10000)
        okk, _mm = _install(tmp, slug, content)
        p = os.path.join(tmp, slug, "SKILL.md")
        can_load = _loadable(p, slug) if os.path.isfile(p) else False
        rows.append((tag, okk, can_load))
        ck(okk == can_load,
           "G5 ★ 装得进 ⟺ 加载得出（%s：安装=%s 可加载=%s）" % (tag, okk, can_load))

    print("\n  对照表：")
    print("    %-22s %-8s %s" % ("输入", "安装", "加载器"))
    for tag, okk, can_load in rows:
        print("    %-22s %-8s %s" % (tag, "放行" if okk else "拒收",
                                     "可加载" if can_load else "None"))

    print("\n---- G6 反向：审计拒绝的仍然拒绝（不许被新判据顶掉）----")
    _oa = legion.audit_skill_text
    legion.audit_skill_text = lambda txt: ("P0", ["含危险指令"])
    try:
        _of = legion.github_fetch_raw
        legion.github_fetch_raw = lambda *a, **k: (GOOD_FM, None)
        try:
            okp, msgp = legion.install_skill_from_github(
                "o/r", "x/SKILL.md", slug="p0_skill", skills_dir=tmp)
        finally:
            legion.github_fetch_raw = _of
    finally:
        legion.audit_skill_text = _oa
    ck(okp is False, "G6-1 P0 审计命中 → 仍然拒收", "ok=%r" % okp)
    ck(not os.path.isfile(os.path.join(tmp, "p0_skill", "SKILL.md")),
       "G6-2 P0 拒绝时不落盘")

    print("\n---- G7 fail-open：加载器抽风时不许反过来挡住安装 ----")
    _lp = skill_loader._parse_skill_md

    def _boom(*a, **k):
        raise RuntimeError("加载器故意抽风")

    skill_loader._parse_skill_md = _boom
    try:
        ok7, msg7 = _install(tmp, "fo_skill", GOOD_FM)
    finally:
        skill_loader._parse_skill_md = _lp
    ck(ok7 is True,
       "G7 ★ 加载器抛异常 → 安装仍放行（fail-open，不能因解析器抽风就装不了）",
       "ok=%r msg=%r" % (ok7, msg7))
    ck(os.path.isfile(os.path.join(tmp, "fo_skill", "SKILL.md")),
       "G7-2 fail-open 时正常技能照常落盘")

    shutil.rmtree(tmp, ignore_errors=True)
    print("\n" + "=" * 70)
    print("v4.259.0 技能安装判据（P1-8）：PASS=%d FAIL=%d" % (PASS, FAIL))
    print("=" * 70)
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
