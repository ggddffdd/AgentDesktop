# -*- coding: utf-8 -*-
"""v4.222 P1 不可信内容边界：wrap_untrusted + 工具产出包边界 + 技能元数据/包边界。

审查报告 P1 指出：外部/工具/技能产出的内容可能含注入指令，必须显式标记，
模型须当作数据而非指令。修复：
- agent.wrap_untrusted / _wrap_tool_content：不可信工具产出（web_fetch/web_search/
  read_file/download 等）包进 <untrusted_tool_output>；可信工具原样。
- skill_loader.Skill 增 source/version/hash/audited_at/allow_* 元数据；
  load_skill_prompt 返回内容包进 <untrusted skill>。
- config.system_prompt 加不可信内容边界规则。

判据：
  A wrap_untrusted 格式正确
  B1 不可信工具产出被包边界
  B2 可信工具产出不包边界（原样）
  C 技能 frontmatter 元数据解析成功
  D load_skill_prompt 返回内容被 <untrusted skill> 包边界
  E 系统提示含不可信内容边界规则
"""
import os
import sys
import tempfile
import shutil

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from agent import wrap_untrusted, _wrap_tool_content  # noqa: E402
from skill_loader import scan_skills, load_skill_prompt, wrap_skill_prompt  # noqa: E402

_p = _f = 0


def check(name, cond, detail=""):
    global _p, _f
    if cond:
        _p += 1
        print("  [OK] " + name)
    else:
        _f += 1
        print("  [FAIL] " + name + (("  <- " + detail) if detail else ""))


def main():
    print("-- P1 不可信内容边界 --")
    # A：wrap_untrusted 格式
    w = wrap_untrusted("hello world", "web_fetch", "EV#1")
    ok_a = ('<untrusted_tool_output source="web_fetch" evidence_id="EV#1">' in w
            and w.strip().endswith("</untrusted_tool_output>")
            and "hello world" in w)
    check("A wrap_untrusted 格式正确（含 source/evidence_id 与闭合标签）", ok_a)

    # B：工具产出包边界
    wrapped = _wrap_tool_content("web_fetch", "page body")
    ok_b1 = ('<untrusted_tool_output source="web_fetch">' in wrapped
             and "page body" in wrapped)
    check("B1 不可信工具(web_fetch)产出被包边界", ok_b1)
    plain = _wrap_tool_content("write_file", "done")
    ok_b2 = plain == "done"
    check("B2 可信工具(write_file)产出不包边界（原样）", ok_b2)

    # C/D：技能元数据解析 + 包边界
    tmp = tempfile.mkdtemp(prefix="skilltest_")
    try:
        sd = os.path.join(tmp, "demo-skill")
        os.makedirs(sd)
        md = os.path.join(sd, "SKILL.md")
        content = (
            "---\n"
            "name: demo-skill\n"
            "description: 演示技能\n"
            "emoji: x\n"
            "source: official\n"
            "version: 1.2.3\n"
            "hash: abc123def\n"
            "audited_at: 2026-10-06\n"
            "allow_tools: web_search,read_file\n"
            "allow_dir: /tmp\n"
            "allow_network: true\n"
            "allow_system: false\n"
            "---\n"
            "# demo-skill\n"
            "这是一个演示技能正文。\n"
        )
        with open(md, "w", encoding="utf-8") as f:
            f.write(content)
        skills = scan_skills(tmp)
        ok_c = any(
            s.name == "demo-skill" and s.source == "official"
            and s.version == "1.2.3" and s.hash == "abc123def"
            and s.audited_at == "2026-10-06"
            and s.allow_tools == "web_search,read_file"
            and s.allow_network == "true"
            and s.allow_system == "false"
            for s in skills
        )
        check("C 技能 frontmatter 元数据（source/version/hash/audited_at/allow_*）解析成功", ok_c)

        p = load_skill_prompt("demo-skill", tmp)
        ok_d = (p is not None
                and p.startswith('<untrusted skill="demo-skill">')
                and p.strip().endswith("</untrusted skill>")
                and "演示技能正文" in p)
        check("D load_skill_prompt 返回内容被 <untrusted skill> 包边界", ok_d)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    # 辅助：wrap_skill_prompt 直接调用
    ok_aux = wrap_skill_prompt("xyz", "s1").startswith('<untrusted skill="s1">')
    check("辅助 wrap_skill_prompt 格式正确", ok_aux)

    # E：系统提示含边界规则（静态核查 config.py，避免重导入重依赖）
    cfg_path = os.path.join(ROOT, "config.py")
    with open(cfg_path, "rb") as f:
        cfg_text = f.read().decode("utf-8-sig")
    ok_e = ("不可信内容边界" in cfg_text) and ("<untrusted_tool_output" in cfg_text)
    check("E 系统提示含不可信内容边界规则", ok_e)

    print("\n汇总：PASS=%d FAIL=%d" % (_p, _f))
    return 0 if _f == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
