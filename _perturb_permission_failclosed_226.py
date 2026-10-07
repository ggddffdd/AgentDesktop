# -*- coding: utf-8 -*-
"""扰动 v4.226 P1-1：权限 fail-closed 是否真的生效（非恒真）。

背景
----
`agent._AllowAllDecision` 是个 `allowed = True` 的兜底决策，在拿不到权限决策时
替并发批次「按了放行」。后果不是少弹一次窗，而是**整个最终闸门被短路**：
`tools._permission_gate` 认鸭子类型 `hasattr(perm_ctx, "allowed")`，
拿到它就无条件放行 —— WRITE_LOCAL / EXEC / EXTERNAL 全部不拦。

真实可触发路径（**不是**「引擎为 None」——那条在当前唯一入口会先 AttributeError，
到不了并发兜底）：**引擎存在但 decide() 抛异常** → `_dec = None` → 兜底放行。

第二处：`_run_workflow_guarded` 原本 `if engine is not None:` 把整段判定包住，
引擎为 None 时直接落到末尾 `return self._run_workflow(...)`，一个字没判就启动
子代理任务图（内含搜索 / 写文件）。

本扰动验证的三件事
------------------
1. **A 组真能抓住「放行被改回来」**：把 `_permission_gate` 的 fail-closed 分支
   改成恒放行，A2/A3/A5 必须红 —— 否则 A 组就是恒真装饰。
2. **B 组真能抓住「兜底对象复活」**：把 allowed=True 的兜底决策重新塞回
   并发批次，B4/B5 必须红。
3. **D 组真能挡住「好心的一刀切」**：把 agent_node 的军团侧兜底翻成拒绝，
   D1 必须红 —— 拦住下一个人把它当「同类漏项」顺手改掉。

判据：tests/test_permission_failclosed_226.py 必须翻对应红条。
验收标准 = **哑弹清零**（HIT == CASES 数）。
"""
import os
import re
import subprocess
import sys

ROOT = os.path.dirname(os.path.abspath(__file__))
PY = sys.executable
TEST = os.path.join(ROOT, "tests", "test_permission_failclosed_226.py")

FILES = ["agent.py", "agent_node.py", "tools.py"]

_backup = {}
_crlf = {}


def read_raw(fp):
    with open(os.path.join(ROOT, fp), "rb") as f:
        return f.read()


def write_raw(fp, blob):
    with open(os.path.join(ROOT, fp), "wb") as f:
        f.write(blob)


def is_crlf(fp):
    return b"\r\n" in read_raw(fp)


def run_test():
    env = dict(os.environ, QT_QPA_PLATFORM="offscreen")
    try:
        r = subprocess.run([PY, TEST], capture_output=True, text=True, cwd=ROOT,
                           timeout=300, env=env, encoding="utf-8",
                           errors="replace")
    except subprocess.TimeoutExpired:
        return ["<TIMEOUT>"]
    out = (r.stdout or "") + (r.stderr or "")
    return re.findall(r"\[FAIL\] ([^\n]+)", out)


sys.path.insert(0, ROOT)
import _perturb_guard as _guard  # noqa: E402

_guard.arm()

# ------------------------------------------------------------
# 原串一律从真实源码抄（缩进差一级就 SKIP；SKIP 不能当「跑过了」）
# ------------------------------------------------------------

# ---- 并发批次：拿不到决策时的兜底（v4.226 已删，此处用于「复活」变异） ----
_DEC_OLD = '''                _engine = getattr(self.mw, "permission_engine", None)
                _dec = None
                if _engine is not None:
                    try:
                        _dec = _engine.decide(
                            name, args,
                            explicit_intent=getattr(self, "explicit_intent", True))
                    except Exception as _pe:
                        # 决策异常必须留痕：否则「权限闸失效」这件事只有代码知道。
                        log.warning("权限决策异常（并发批次 %s），按无授权处理: %s", name, _pe)
                        _dec = None
'''

# 变异1：把允许兜底重新塞回来（allowed=True 的假决策）
_DEC_REVIVED = '''                _engine = getattr(self.mw, "permission_engine", None)
                _dec = None
                if _engine is not None:
                    try:
                        _dec = _engine.decide(
                            name, args,
                            explicit_intent=getattr(self, "explicit_intent", True))
                    except Exception as _pe:
                        log.warning("权限决策异常（并发批次 %s），按无授权处理: %s", name, _pe)
                        _dec = None
                if _dec is None:
                    _dec = _AllowAllDecision()
'''

# 变异2：只删动作留条件 —— 异常分支里把「按无授权处理」悄悄改成放行
#       （log.warning 还在、_dec = None 还在，最阴的一种）
_DEC_SHELL = '''                _engine = getattr(self.mw, "permission_engine", None)
                _dec = None
                if _engine is not None:
                    try:
                        _dec = _engine.decide(
                            name, args,
                            explicit_intent=getattr(self, "explicit_intent", True))
                    except Exception as _pe:
                        # 决策异常必须留痕：否则「权限闸失效」这件事只有代码知道。
                        log.warning("权限决策异常（并发批次 %s），按无授权处理: %s", name, _pe)
                        _dec = _AllowAllDecision()
'''

# ---- workflow：缺引擎拒绝分支 ----
_WF_OLD = '''        if engine is None:
            log.warning("run_workflow 缺权限引擎，按无授权拒绝")
            return ("（子代理工作流未执行：权限引擎未启用，"
                    "无法完成授权判定，按保守策略拒绝）")
'''

# 变异3：缺引擎分支保留（结构还在）但不 return —— 落回末尾照跑
_WF_NO_RETURN = '''        if engine is None:
            log.warning("run_workflow 缺权限引擎，按无授权拒绝")
'''

# ---- tools：最终闸门的 fail-closed 分支 ----
_GATE_OLD = '''    # 无上下文：按风险分类 fail-closed
    try:
        _risk = classify(name)
    except Exception:
        _risk = RiskClass.EXTERNAL
    if _risk == RiskClass.READ:
        return (True, None)
'''

# 变异4：把闸门改成恒放行 —— 这是「A 组是不是恒真」的照妖镜
_GATE_OPEN = '''    # 无上下文：按风险分类 fail-closed
    try:
        _risk = classify(name)
    except Exception:
        _risk = RiskClass.EXTERNAL
    if True:
        return (True, None)
'''

# 变异5：闸门改成恒拒 —— 证明 A 组不是「怎么改都红」的假红
_GATE_CLOSED = '''    # 无上下文：按风险分类 fail-closed
    try:
        _risk = classify(name)
    except Exception:
        _risk = RiskClass.EXTERNAL
    if True:
        return (False, "恒拒")
'''

# ---- 军团侧兜底（D 组照妖镜） ----
_AD_OLD = '''    allowed = True
    needs_user = False
    reason = "legion-no-perm fallback"
    rule = "fallback"
'''
_AD_DENY = '''    allowed = False
    needs_user = False
    reason = "legion-no-perm fallback"
    rule = "fallback"
'''

CASES = [
    # ---- 防线1：最终闸门本身（A 组自证） ----
    ("最终闸门改成恒放行（A组自证：不是恒真装饰）",
     "tools.py", _GATE_OLD, _GATE_OPEN,
     ["A2 WRITE_LOCAL(write_file) 无授权被最终闸门拒绝",
      "A5 端到端：write_file 无授权调用后哨兵文件**未被创建**"]),

    ("最终闸门改成恒拒（反向自证：A组不是怎么改都红）",
     "tools.py", _GATE_OLD, _GATE_CLOSED,
     ["A1 READ(web_search) 无授权仍放行",
      "A7 只读工具在无授权下仍放行"]),

    # ---- 防线2：并发批次的兜底 ----
    ("并发批次把 allowed=True 兜底决策复活",
     "agent.py", _DEC_OLD, _DEC_REVIVED,
     ["B4 并发批次 perm_ctx 链", "B5 并发批次不再构造任何 *_Decision"]),

    ("并发批次只删动作留条件（异常分支改放行，log 还在）",
     "agent.py", _DEC_OLD, _DEC_SHELL,
     ["B4 并发批次 perm_ctx 链", "B5 并发批次不再构造任何 *_Decision"]),

    # ---- 防线3：workflow 缺引擎分支 ----
    ("workflow 缺引擎分支保留但不 return（落回末尾照跑）",
     "agent.py", _WF_OLD, _WF_NO_RETURN,
     ["B9 workflow 闸门对「缺引擎」显式 return",
      "C5 workflow 缺引擎拒绝文案含「未执行」"]),

    # ---- 防线4：军团侧设计决定（D 组照妖镜） ----
    ("军团侧兜底被好心翻成拒绝（D组照妖镜：拦住一刀切）",
     "agent_node.py", _AD_OLD, _AD_DENY,
     ["D1 agent_node._AllowDecision 现状为 allowed=True"]),
]

HIT, MISS = [], []
try:
    for fp in FILES:
        _backup[fp] = read_raw(fp)
        _crlf[fp] = is_crlf(fp)

    for name, fp, old, new, expect in CASES:
        src = _backup[fp].decode("utf-8").replace("\r\n", "\n")
        if old not in src:
            MISS.append(name + "（原串未命中）")
            print("  [SKIP] " + name)
            continue
        mutated = src.replace(old, new, 1)
        write_raw(fp, (mutated.replace("\n", "\r\n") if _crlf[fp] else mutated)
                  .encode("utf-8"))
        red = run_test()
        ok = all(any(e in r for r in red) for e in expect)
        tag = "HIT " if ok else "MISS"
        print("  [%s] %s → 红 %d 条" % (tag, name, len(red))
              + ("" if ok else ("；期望 %s，实际 %s" % (expect, red[:4]))))
        (HIT if ok else MISS).append(name)
        write_raw(fp, _backup[fp])
finally:
    for fp, blob in _backup.items():
        write_raw(fp, blob)
    print("  （已恢复原文件，原始字节无损）")

print("\n=== 扰动汇总：命中 %d/%d ===" % (len(HIT), len(CASES)))
print("PERTURB PASS=%d FAIL=%d" % (len(HIT), len(MISS)))
sys.exit(1 if MISS else 0)
