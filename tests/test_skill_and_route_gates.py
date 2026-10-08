# -*- coding: utf-8 -*-
"""批次 B 回归测试（v4.169.0 / P1-1 ~ P1-5 + 自动记忆）。

钉住的六件事：

**P1-1 技能「必须落地」硬约束**
  `config.py` 的 HARD 规则与 `tools.py` 的加载返回语都写「加载技能后**必须立即调用**
  run_python / write_file / run_command 落地」—— 把「模型觉得某技能可能合适」
  升级成「必须产生文件或执行动作」，是"技能带偏 + 自动调工具"的放大器。

**P1-2 禁用技能仍可按名加载**
  `tool_use_skill` 只按名字扫目录，不查 `enabled_skills`；
  且 `enabled_skills = []` 同时表示「未配置」（全启用）与「全禁用」，无法真正关掉。

**P1-3 技能安装 / 创建 / 覆盖**
  `create_skill` 描述主动鼓励"做完复杂任务就自动创建"；覆盖同名 SKILL.md 无备份；
  本地「导入技能」只 copytree，没有安全审计。

**P1-4 复杂度判定扫全历史**
  `ui._is_complex` 遍历整个 messages（含 system prompt），而系统提示本身就含
  「代码/分析/报告/设计」→ 几乎每轮都判复杂 → Agent 长期升舱付费模型。
  项目里已有 `route_judge.is_complex_v2()`（只看最近 2 条 user 消息），一直没接上。

**P1-5 路由日志拼不出证据链**
  只记 model/tier/reason，看不到"这轮用户到底下没下命令 / tool_choice 是什么 /
  权限怎么判的"。

**自动记忆扫全历史**
  `any(role == "tool" for msg in self.messages)` → 历史调过工具，本轮什么都没干
  也会触发记忆提炼。
"""

import ast
import json
import os
import re
import shutil
import sys
import tempfile

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import config                                   # noqa: E402
import route_log                                # noqa: E402

_p = _f = 0


def check(name, cond, detail=""):
    global _p, _f
    if cond:
        _p += 1
        print(f"  [OK] {name}")
    else:
        _f += 1
        print(f"  [FAIL] {name}" + (f"  <- {detail}" if detail else ""))


def _src(name):
    return open(os.path.join(ROOT, name), encoding="utf-8-sig").read()


def _first_load_pos(t):
    """tools.py 里第一次真实调用 load_skill_prompt( 的位置（剔除注释后坐标系）。

    v4.226 起该调用带第三参 `strict_meta=False`，早期版本把整串
    `load_skill_prompt(skill_name, d)` 写死进位置断言 —— 签名一变就假红。
    改为正则匹配「调用括号」而非参数字面量：**保住原意（启用检查必须早于
    加载）**，且不再被参数增删打断。

    ⚠️ 返回的是**剔除注释后**的坐标系。比较方必须用 `_strip_comments(t)`
    里的位置，不能拿原文本的 `t.index(...)` 混着比 —— 剔除会缩短文本，
    两个坐标系混用会得出「顺序颠倒」的假红（本轮实测踩到）。
    """
    m = re.search(r"load_skill_prompt\s*\(", _strip_once(t))
    if not m:
        raise AssertionError("tools.py 里找不到 load_skill_prompt( 调用")
    return m.start()


def _first_pos(t, needle):
    """needle 在剔除注释后的文本里的首次出现位置。"""
    return _strip_once(t).find(needle)


# ⚠️ 剔除注释**必须只做一次并复用结果**。多次调用会让位置口径漂移：
#    实测第二次剔除后同一个 pos 切出来的内容与预期完全无关（指到了别的段落），
#    而断言会安静地给出错误结论。
#    缓存键用**字符串的哈希+长度**而不是 id() —— id() 在临时字符串被回收后
#    会被复用，两个不同文本可能撞同一个 id，缓存就会串（同样是安静地给错结论）。
_STRIP_CACHE = {}


def _strip_once(t):
    key = (hash(t), len(t))
    if key not in _STRIP_CACHE:
        _STRIP_CACHE[key] = _strip_comments(t)
    return _STRIP_CACHE[key]


def _strip_comments(src):
    """剔掉 # 注释，避免注释里的字面量被当成真调用（假红/假绿两向都坑）。"""
    try:
        import io
        import tokenize
        lines = src.splitlines(keepends=True)
        for tok in tokenize.generate_tokens(io.StringIO(src).readline):
            if tok.type == tokenize.COMMENT:
                ln = lines[tok.start[0] - 1]
                # 只挖注释那几列，保留「代码 + 行尾注释」那行的代码部分
                lines[tok.start[0] - 1] = (ln[:tok.start[1]] + ln[tok.end[1]:])
        return "".join(lines)
    except Exception:
        return src


# ---------------------------------------------------------------------------
def part_a_skill_landing_rule():
    print("\n-- A) P1-1 技能落地规则已收窄 --")
    c = _src("config.py")
    t = _src("tools.py")
    # ⚠️ 只看 HARD 列表**内容**：修复注释里刻意留了旧文案作背景说明，
    # 用整文件子串匹配会命中注释 → 假红。
    _hs = c[c.index("    HARD = ["):]
    _hs = _hs[:_hs.index("if compact:")]
    check("A1 HARD 列表内不再写「必须立即调用 … 落地」",
          "必须立即调用" not in _hs, _hs[:150])
    check("A2 HARD 改为「技能只服务用户当前明确的目标」",
          "技能只服务**用户当前明确的目标**" in c)
    check("A3 HARD 明确「讨论/咨询类不需要产出文件」",
          "不需要产出文件或执行动作" in c)
    check("A4 HARD 仍保留「要产出时别只列大纲」（治承诺式循环）",
          "禁止只输出大纲" in c)
    check("A5 tools 加载返回语不再要求必须调工具",
          "加载技能后必须立即调用相应工具" not in t)
    check("A6 tools 返回语改为「由用户请求决定要不要调工具」",
          "要不要调工具由用户的请求决定" in t)


def part_b_skill_enabled():
    print("\n-- B) P1-2 技能启用判据（清单口径 = 加载口径） --")
    check("B1 config.is_skill_enabled 已定义", hasattr(config, "is_skill_enabled"))
    check("B2 config.skills_disabled_all 已定义", hasattr(config, "skills_disabled_all"))
    t = _src("tools.py")
    check("B3 tool_use_skill 加载前校验启用状态",
          "is_skill_enabled" in t and "当前处于**禁用**状态" in t)
    check("B4 校验在扫描目录之前（否则先加载再检查＝白检）",
          _first_pos(t, "is_skill_enabled") < _first_load_pos(t), "")
    # v4.226：load_skill_prompt 加了第三参 strict_meta，位置断言改为正则匹配
    # （保住原意「启用检查必须早于加载」，且不再被签名变动打断）。
    # B4b 是升级后的加强项：strict 档的元数据拒用分支也必须排在开关之后 ——
    # 否则「禁用」与「拒用」两条拒绝路径顺序颠倒，白检又回来了。
    check("B4b strict_meta 拒用分支也在启用检查之后",
          _first_load_pos(t) > _first_pos(t, "is_skill_enabled")
          and ("reject_reason" in t or "strict_meta" in t), "")
    # B4c 补上B4/B4b 抓不到的那一半：**位置没动但条件被打穿**。
    # B4/B4b 都是位置断言 —— 把 `if not is_skill_enabled(...)` 改成 `if not True:`
    # 位置完全不变，两条都照绿，但「禁用技能照样能加载」已经成立。
    # 所以必须单独钉一条：启用判断必须真调用 is_skill_enabled，而不是恒真/恒假。
    # ⚠️ 剔除注释**只做一次**并复用：多次调用会让位置口径漂移（本轮实测踩到
    # —— 第二次剔除后 pos 指到了别的段落，切片内容与预期完全无关）。
    _t_stripped = _strip_once(t)
    _gp = _t_stripped.find("from config import is_skill_enabled")
    _gate_seg = _t_stripped[_gp:_gp + 400] if _gp >= 0 else ""
    check("B4c 启用判断真调用 is_skill_enabled（不是恒真/恒假条件）",
          bool(re.search(r"if\s+not\s+is_skill_enabled\s*\(", _gate_seg))
          and not re.search(r"if\s+not\s+(True|False|1|0)\s*:", _gate_seg),
          "启用检查被改成恒真/恒假 → 禁用技能可绕过闸门")

    # 用临时 config.json 跑三种口径
    tmp = tempfile.mkdtemp(prefix="xc_skillcfg_")
    old = config.CONFIG_PATH
    cfgp = os.path.join(tmp, "config.json")
    try:
        config.CONFIG_PATH = cfgp
        # ① 未配置 → 全启用
        with open(cfgp, "w", encoding="utf-8") as f:
            json.dump({}, f)
        check("B5 未配置 enabled_skills → 全部启用",
              config.is_skill_enabled("任意技能") is True)
        # ② 白名单 → 只放行列出的
        with open(cfgp, "w", encoding="utf-8") as f:
            json.dump({"enabled_skills": ["alpha", "beta"]}, f)
        check("B6 白名单内放行", config.is_skill_enabled("alpha") is True)
        check("B7 白名单外拒绝（旧版这里照样能加载）",
              config.is_skill_enabled("gamma") is False)
        # ③ 显式全禁用
        with open(cfgp, "w", encoding="utf-8") as f:
            json.dump({"skills_disabled_all": True}, f)
        check("B8 skills_disabled_all → 全部拒绝（旧版做不到）",
              config.is_skill_enabled("alpha") is False)
        # ④ 空名
        check("B9 空技能名 → False", config.is_skill_enabled("") is False)
    finally:
        config.CONFIG_PATH = old
        shutil.rmtree(tmp, ignore_errors=True)


def part_c_skill_install():
    print("\n-- C) P1-3 技能安装 / 创建 / 覆盖 --")
    td = _src("tool_defs.py")
    check("C1 create_skill 描述不再鼓励自动创建",
          "当你成功完成了一个需要5步以上的复杂任务后" not in td)
    check("C2 改为「只在用户明确要求时才调用」",
          "只在用户明确要求时才调用" in td)
    si = _src("skill_installer_tools.py")
    check("C3 覆盖前会备份同名 SKILL.md", "bak_" in si and "shutil.copy2(fpath" in si)
    check("C4 备份失败则拒绝覆盖（不丢用户原技能）",
          "备份失败（为安全起见未覆盖）" in si)
    check("C5 P1 警告提到结果最前面", "_p1_note" in si)
    sm = _src("skill_manager_ui.py")
    check("C6 本地导入技能加了安全审计", "audit_skill" in sm)
    check("C7 导入时 P0 拒绝 / P1 确认",
          "安全审计拒绝" in sm and "安全审计提醒" in sm)
    # 硬确认档（A3 已建）覆盖这三个工具
    from risk import ALWAYS_CONFIRM
    check("C8 skill_install / create_skill 在硬确认档内",
          {"skill_install", "create_skill"} <= ALWAYS_CONFIRM)


def part_d_route_complexity():
    print("\n-- D) P1-4 复杂度判定接入 route_judge --")
    src = _src("ui.py")
    check("D1 _is_complex 调 route_judge.is_complex_v2",
          "route_judge.is_complex_v2(messages, hints, threshold)" in src)
    check("D2 保留 v1 实现供回退", "def _is_complex_v1(" in src)
    check("D3 回退开关 route_complex_v1 存在",
          'self.cfg.get("route_complex_v1")' in src)
    check("D4 归因与判定同源（都走 route_judge）",
          src.count("route_judge.is_complex_v2(") >= 2,
          f"命中 {src.count('route_judge.is_complex_v2(')} 次")

    import ui
    W = ui.ChatWindow.__new__(ui.ChatWindow)
    W.cfg = {}
    routing = {"complex_hint": ["代码", "分析", "报告", "设计"], "length_threshold": 1500}
    sys_msg = {"role": "system",
               "content": "你可以写代码、做分析、出报告、做设计。" * 30}
    simple = [sys_msg, {"role": "user", "content": "你好"},
              {"role": "assistant", "content": "你好"}]
    complex_ = [sys_msg, {"role": "user", "content": "帮我把路由层重构一下并出个分析报告"}]
    big_tool = [sys_msg, {"role": "user", "content": "查一下"},
                {"role": "tool", "content": "x" * 30000}]
    check("D5 旧版：简单寒暄也判复杂（system 含关键词）",
          W._is_complex_v1(simple, routing) is True)
    check("D6 新版：简单寒暄不判复杂", W._is_complex(simple, routing) is False)
    check("D7 新版：真实复杂需求仍判复杂", W._is_complex(complex_, routing) is True)
    check("D8 新版：大 tool 结果不再把判定带偏",
          W._is_complex(big_tool, routing) is False)
    check("D9 回退开关生效（置 true 走旧行为）",
          W._is_complex_v1(simple, routing) is True)


def part_e_route_log():
    print("\n-- E) P1-5 路由日志证据链 --")
    check("E1 route_log 有 log_tool_decision", hasattr(route_log, "log_tool_decision"))
    src = _src("route_log.py")
    check("E2 文档写明 event=tool 字段", "event=tool" in src)
    check("E3 route 事件补了 intent / tool_choice",
          "intent=_intent" in _src("ui.py") and "tool_choice=getattr(self" in _src("ui.py"))
    check("E4 tool_choice 四个分支都记录了",
          _src("ui.py").count("self._last_tool_choice = ") >= 4,
          f"命中 {_src('ui.py').count('self._last_tool_choice = ')} 处")
    ag = _src("agent.py")
    check("E5 agent 权限决策处写日志", "log_tool_decision(" in ag)
    check("E6 日志里带 decision/rule/source", "decision=(" in ag and "rule=dec.rule" in ag)
    check("E7 参数只写摘要不写原文", "args_digest=_af(args)[:12]" in ag)

    # 行为：写进去能读出来
    tmp = tempfile.mkdtemp(prefix="xc_rl_")
    old_env = os.environ.get("XC_USER_DATA_DIR")
    old_path = route_log._PATH
    try:
        os.environ["XC_USER_DATA_DIR"] = tmp
        # ⚠️ 直接指定文件路径：_log_path() 用的是 config.USER_DATA_DIR（**导入时**确定的
        # 模块常量），改环境变量对它无效 —— 上一版这么写就把测试记录写进了用户的真实日志。
        route_log._PATH = os.path.join(tmp, "route_log.jsonl")
        ok = route_log.log_tool_decision(name="run_python", args_digest="abc123",
                                        decision="confirm", rule="implicit_intent",
                                        allowed=True, need_confirm=True, source="implicit")
        rows = route_log.read_recent(5, event="tool")
        check("E8 log_tool_decision 写入成功", ok and len(rows) == 1, f"rows={rows}")
        if rows:
            r = rows[0]
            check("E9 字段齐全",
                  r.get("tool") == "run_python" and r.get("decision") == "confirm"
                  and r.get("rule") == "implicit_intent" and r.get("need_confirm") is True,
                  str(r))
    finally:
        if old_env is None:
            os.environ.pop("XC_USER_DATA_DIR", None)
        else:
            os.environ["XC_USER_DATA_DIR"] = old_env
        route_log._PATH = old_path
        shutil.rmtree(tmp, ignore_errors=True)


def part_f_auto_memory():
    print("\n-- F) 自动记忆只看本轮 --")
    # v4.236.0：自动记忆块从 agent.py 尾部外移到 agent_memory_mixin.py。
    # 判据跟随新家；同时钉住接线，防「搬走了但没继承」的静默停摆。
    ag = _src("agent_memory_mixin.py")
    check("F0 agent.py 继承 AgentMemoryMixin（搬运后接线在位）",
          "AgentMemoryMixin" in _src("agent.py"),
          "主类没继承 → 自动记忆静默停摆")
    i = ag.index("has_tool_msg = any(")
    seg = ag[i:i + 400]
    check("F1 判定条件里含 _seq > 0（只看本轮新增）", 'msg["_seq"] > 0' in seg, seg[:200])
    check("F2 不再只按 role==tool 扫全历史",
          'any(\n            msg.get("role") == "tool" for msg in self.messages\n        )' not in ag)
    check("F3 说明「baseline 打 _seq=0」的来由", "baseline 打 _seq=0" in ag or "_seq=0" in ag)
    # 行为：直接跑那段表达式
    def _has_round_tool(msgs):
        return any(
            isinstance(msg, dict) and msg.get("role") == "tool"
            and isinstance(msg.get("_seq"), int) and msg["_seq"] > 0
            for msg in msgs)
    baseline_with_tool = [{"role": "tool", "content": "old", "_seq": 0}]
    round_tool = [{"role": "tool", "content": "new", "_seq": 7}]
    check("F4 只有历史工具消息 → 不触发", _has_round_tool(baseline_with_tool) is False)
    check("F5 本轮有工具消息 → 触发", _has_round_tool(round_tool) is True)
    check("F6 _seq 缺失的 tool 消息不算本轮（防御）",
          _has_round_tool([{"role": "tool", "content": "x"}]) is False)


def part_g_negative():
    print("\n-- G) 负面验证 --")
    # G1 关掉 is_skill_enabled → 白名单外技能又变成可加载
    tmp = tempfile.mkdtemp(prefix="xc_skillcfg_")
    old = config.CONFIG_PATH
    try:
        config.CONFIG_PATH = os.path.join(tmp, "config.json")
        with open(config.CONFIG_PATH, "w", encoding="utf-8") as f:
            json.dump({"enabled_skills": ["alpha"]}, f)
        check("G1 白名单生效（gamma 被拒）", config.is_skill_enabled("gamma") is False)
        check("G1' 白名单内的 alpha 仍放行（不是一刀切拒掉）",
              config.is_skill_enabled("alpha") is True)
    finally:
        config.CONFIG_PATH = old
        shutil.rmtree(tmp, ignore_errors=True)
    # G2 route_judge 缺失时能退回 v1（不把路由搞挂）
    import ui
    W = ui.ChatWindow.__new__(ui.ChatWindow)
    W.cfg = {}
    real = sys.modules.get("route_judge")
    sys.modules["route_judge"] = None       # 制造 import 失败
    try:
        r = W._is_complex([{"role": "user", "content": "x" * 2000}],
                          {"complex_hint": [], "length_threshold": 1500})
        check("G2 route_judge 不可用时退回 v1（长文本仍判复杂）", r is True)
    finally:
        if real is not None:
            sys.modules["route_judge"] = real
        else:
            sys.modules.pop("route_judge", None)


def main():
    part_a_skill_landing_rule()
    part_b_skill_enabled()
    part_c_skill_install()
    part_d_route_complexity()
    part_e_route_log()
    part_f_auto_memory()
    part_g_negative()
    print(f"\n汇总：PASS={_p} FAIL={_f}")
    return 0 if _f == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
