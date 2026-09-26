# -*- coding: utf-8 -*-
"""军团成员权限适配器（legion_permissions.py）回归测试。

钉住审查 §1 提的五条规则：
  1. 成员默认只读（EXEC / EXTERNAL 未授权一律拒）
  2. write_file 只能在军团工作区内
  3. 先查路径作用域 / 外发白名单，再谈信任（军团不复用主链信任态）
  4. 授权可授予、可一键收回
  5. 每笔非只读决策留审计痕（含参数摘要哈希、脱敏）

外加：危险命令底线、多线程审计不串行损坏。

审计目录一律指向临时目录，绝不碰真实 LEGION_DIR。
"""

import json
import os
import shutil
import sys
import tempfile
import threading

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import legion_permissions as lp   # noqa: E402
from permissions import PermissionEngine   # noqa: E402

_p = _f = 0
WS = ""      # 临时工作区
OUTSIDE = ""  # 工作区外的路径


def check(name, cond, detail=""):
    global _p, _f
    if cond:
        _p += 1
        print(f"  [PASS] {name}")
    else:
        _f += 1
        print(f"  [FAIL] {name}" + (f" —— {detail}" if detail else ""))


def new_adapter(tmp, **kw):
    scope = kw.pop("scope_paths", [WS])
    return lp.LegionPermissionAdapter(
        run_id="run-test", project_id="p1", project_name="测试项目",
        scope_paths=scope, audit_dir=tmp, **kw)


def read_audit(tmp):
    p = os.path.join(tmp, "legion_tool_audit.jsonl")
    if not os.path.isfile(p):
        return []
    lines = []
    with open(p, encoding="utf-8") as f:
        for ln in f:
            ln = ln.strip()
            if ln:
                lines.append(json.loads(ln))
    return lines


def main():
    global WS, OUTSIDE
    tmp = tempfile.mkdtemp(prefix="legion_perm_")
    WS = os.path.join(tmp, "workspace")
    OUTSIDE = os.path.join(tmp, "outside")
    os.makedirs(WS, exist_ok=True)
    os.makedirs(OUTSIDE, exist_ok=True)

    try:
        print("=== 1) 成员默认只读 ===")
        a = new_adapter(tmp)
        d = a.check("read_file", {"path": os.path.join(WS, "x.txt")})
        check("读文件放行", d.allowed and d.rule == "read", d.rule)

        d = a.check("web_search", {"query": "x"})
        check("搜索放行（只读）", d.allowed, d.rule)

        d = a.check("run_command", {"command": "echo hi"})
        check("★ 执行类默认拒绝", not d.allowed and d.rule == "needs_auth", d.rule)
        check("  且标记为 needs_auth（可区分文案）", d.needs_auth is True)

        d = a.check("run_python", {"code": "print(1)"})
        check("★ run_python 默认拒绝", not d.allowed and d.rule == "needs_auth", d.rule)

        d = a.check("send_email", {"to": "a@b.c"})
        check("★ 外发默认拒绝", not d.allowed, d.rule)
        check("  外发命中白名单围栏（先于授权）", d.rule == "external_block", d.rule)

        print("\n=== 2) write_file 作用域围栏 ===")
        a = new_adapter(tmp)
        d = a.check("write_file", {"path": os.path.join(WS, "a", "b.txt")})
        check("工作区内写入放行", d.allowed and d.rule == "write_local", d.rule)

        d = a.check("write_file", {"path": os.path.join(OUTSIDE, "evil.txt")})
        check("★ 工作区外写入拒绝", not d.allowed and d.rule == "scope", d.rule)

        d = a.check("write_file", {"path": "C:/Windows/System32/evil.dll"})
        check("★ 系统目录写入拒绝", not d.allowed and d.rule == "scope", d.rule)

        d = a.check("write_file", {})
        check("无路径拒绝", not d.allowed and d.rule == "scope", d.rule)

        # 前缀越界：workspace 的兄弟目录（历史 C2 修过的坑）
        d = a.check("write_file", {"path": WS + "_backup/x.txt"})
        check("兄弟目录前缀不算命中（前缀越界防护）", not d.allowed, d.rule)

        d = a.check("read_file", {"path": os.path.join(OUTSIDE, "x.txt")})
        check("读工作区外也拦（同一条边界）", not d.allowed and d.rule == "scope", d.rule)

        print("\n=== 3) 先围栏，再信任（军团不吃主链信任） ===")
        # 模拟主对话链：用户点了"本次会话全部信任"
        main_engine = PermissionEngine(
            mode="interactive", scope_paths=[WS], external_allow=[])
        main_engine.set_session_trusted()
        check("主链引擎已置信任", main_engine.session_trusted is True)
        # 主链此时会放行越界写入吗？—— v4.167.0 起不会（围栏先于信任）
        dm = main_engine.decide("write_file", {"path": os.path.join(OUTSIDE, "x")})
        check("主链：信任也不绕过越界写入", not dm.allowed and dm.rule == "scope", dm.rule)

        a2 = new_adapter(tmp)
        d = a2.check("run_command", {"command": "echo hi"})
        check("★ 军团不受主链信任影响（仍拒）", not d.allowed, d.rule)
        d = a2.check("send_email", {"to": "a@b.c"})
        check("★ 军团外发仍被白名单拦（不受主链信任影响）",
              not d.allowed and d.rule == "external_block", d.rule)

        print("\n=== 4) 授权与一键收回 ===")
        a = new_adapter(tmp)
        d = a.check("run_command", {"command": "echo hi"}, wave=1)
        check("授权前拒", not d.allowed)
        a.grant_wave(1, by="user")
        d = a.check("run_command", {"command": "echo hi"}, wave=1)
        check("★ 本波放行后执行类放行", d.allowed and d.rule == "grant", d.rule)
        d = a.check("run_command", {"command": "echo hi"}, wave=2)
        check("★ 授权按波生效（第 2 波不受益）", not d.allowed, d.rule)

        a.grant_all(classes=("exec",), by="gate_off")
        d = a.check("run_command", {"command": "echo hi"}, wave=99)
        check("grant_all 对任意波生效", d.allowed, d.rule)

        a.revoke()
        d = a.check("run_command", {"command": "echo hi"}, wave=99)
        check("★ 一键收回后立即恢复拒绝", not d.allowed and d.rule == "needs_auth", d.rule)
        check("收回后授权账为空", a.granted_waves() == [], a.granted_waves())

        print("\n=== 5) 危险命令底线（即便已授权也不放） ===")
        a = new_adapter(tmp)
        a.grant_wave(1, by="user")
        for cmd in ("rm -rf /", "format c:", "diskpart", "vssadmin delete shadows"):
            d = a.check("run_command", {"command": cmd}, wave=1)
            check(f"★ 已授权仍拒: {cmd[:24]}",
                  not d.allowed and d.rule == "dangerous_cmd", d.rule)
        d = a.check("run_command", {"command": "echo ok"}, wave=1)
        check("正常命令在授权后仍放行", d.allowed, d.rule)

        print("\n=== 6) 外发需 白名单 + 授权 双条件 ===")
        a = new_adapter(tmp, external_allow=["send_email"])
        d = a.check("send_email", {"to": "a@b.c"}, wave=1)
        check("白名单内但未授权 → 拒", not d.allowed and d.rule == "needs_auth", d.rule)
        a.grant_wave(1, classes=("exec", "external"), by="user")
        d = a.check("send_email", {"to": "a@b.c"}, wave=1)
        check("★ 白名单 + 授权 → 放行", d.allowed and d.rule == "external", d.rule)
        d = a.check("webhook_start", {}, wave=1)
        check("白名单外仍拒（授权也救不了）",
              not d.allowed and d.rule == "external_block", d.rule)

        print("\n=== 7) 审计留痕 ===")
        tmp2 = tempfile.mkdtemp(prefix="legion_audit_")
        try:
            a = new_adapter(tmp2)
            a.check("read_file", {"path": os.path.join(WS, "x")}, role="研究员", wave=1)
            a.check("run_command", {"command": "echo hi"}, role="研究员", wave=1)
            a.check("write_file", {"path": os.path.join(OUTSIDE, "e")}, role="写手", wave=1)
            a.check("send_email", {"to": "a@b.c", "body": "x"}, role="外联", wave=2)

            recs = read_audit(tmp2)
            check("审计有记录", len(recs) >= 3, f"实际 {len(recs)}")
            check("★ 只读不记审计（避免账本被淹）",
                  all(r["tool"] != "read_file" for r in recs),
                  [r["tool"] for r in recs])
            tools = {r["tool"] for r in recs}
            check("执行/外发/越界写都记了",
                  {"run_command", "write_file", "send_email"} <= tools, tools)

            rc = [r for r in recs if r["tool"] == "run_command"][0]
            check("审计含 run_id", rc["run_id"] == "run-test")
            check("审计含 wave", rc["wave"] == 1)
            check("审计含 role", rc["role"] == "研究员")
            check("审计含决策与规则", rc["decision"] == "deny" and rc["rule"] == "needs_auth")
            check("审计含参数摘要哈希", len(rc["args_digest"]) == 12, rc["args_digest"])
            check("审计含批准来源字段", "by" in rc)

            # 脱敏：敏感键不得出现明文
            a2 = new_adapter(tmp2)
            a2.check("send_email", {"to": "a@b.c", "api_key": "sk-SECRETVALUE123456"},
                     role="外联", wave=1)
            recs2 = read_audit(tmp2)
            blob = json.dumps(recs2, ensure_ascii=False)
            check("★ 敏感键已脱敏（不留明文）", "sk-SECRETVALUE123456" not in blob)

            # 摘要稳定性：同参数 → 同摘要；改一个字符 → 变
            a3 = new_adapter(tmp2)
            d1 = lp._digest({"command": "echo A"})
            d2 = lp._digest({"command": "echo A"})
            d3 = lp._digest({"command": "echo B"})
            check("同参数摘要一致", d1 == d2)
            check("★ 参数变化 → 摘要变化（可查「参数变了」）", d1 != d3)

            # 授权/收回也留痕
            a3.grant_wave(3, by="user")
            a3.revoke(3)
            recs3 = [r for r in read_audit(tmp2) if r["tool"].startswith("__")]
            check("★ 授权与收回本身也有审计痕",
                  any(r["tool"] == "__grant__" for r in recs3) and
                  any(r["tool"] == "__revoke__" for r in recs3),
                  [r["tool"] for r in recs3])
        finally:
            shutil.rmtree(tmp2, ignore_errors=True)

        print("\n=== 8) 并发：多线程 check + 审计不串行损坏 ===")
        tmp3 = tempfile.mkdtemp(prefix="legion_mt_")
        try:
            a = new_adapter(tmp3)
            a.grant_wave(1, by="user")
            errs = []

            def _worker(i):
                try:
                    for _ in range(30):
                        a.check("run_command", {"command": f"echo {i}"},
                                role=f"r{i % 4}", wave=1)
                        a.check("write_file", {"path": os.path.join(WS, f"f{i}.txt")},
                                role=f"r{i % 4}", wave=1)
                        a.check("send_email", {"to": "x"}, role=f"r{i % 4}", wave=1)
                except Exception as e:
                    errs.append(repr(e))

            ths = [threading.Thread(target=_worker, args=(i,), daemon=True)
                   for i in range(8)]
            for x in ths:
                x.start()
            for x in ths:
                x.join(timeout=30)
            check("并发无异常", not errs, errs[:3])
            check("线程全部结束（无死锁）", all(not x.is_alive() for x in ths))

            recs = None
            parse_err = ""
            try:
                recs = read_audit(tmp3)
            except Exception as e:      # 任一行坏掉都会在这里炸出来
                parse_err = repr(e)
            check("★ 每行都是合法 JSON（无串行损坏）", recs is not None and not parse_err,
                  parse_err or "解析失败")
            # 每轮 3 条非只读（run_command / write_file / send_email）× 30 轮 × 8 线程
            # + 1 条 grant_wave 本身的授权痕
            expect = 8 * 30 * 3 + 1
            check(f"审计行数 = 8线程 × 30轮 × 3条 + 1条授权 = {expect}",
                  len(recs) == expect, f"实际 {len(recs)}")
            # 角色只看真正的工具决策行（授权/收回那几条 role 为空是正常的）
            roles = {r["role"] for r in recs if not str(r["tool"]).startswith("__")}
            check("★ 各线程角色都正确落盘（无串台）",
                  roles == {"r0", "r1", "r2", "r3"}, sorted(roles))
        finally:
            shutil.rmtree(tmp3, ignore_errors=True)

        print("\n=== 9) state() 快照 ===")
        a = new_adapter(tmp)
        s = a.state()
        check("快照含 run_id", s["run_id"] == "run-test")
        check("快照含 scope", len(s["scope_paths"]) >= 1)
        a.grant_wave(2, classes=("exec", "external"), by="user")
        s = a.state()
        check("快照反映已授权波次", "2" in s["granted"], s["granted"])
        check("快照列出能力类", set(s["granted"]["2"]) == {"exec", "external"},
              s["granted"])

        print("\n=== 10) 未知工具保守拒绝 ===")
        a = new_adapter(tmp)

        class _FakeRisk:
            pass

        # 用未分类工具名（risk.classify 对未知返回 EXTERNAL）→ 应被外发围栏拦
        d = a.check("__totally_unknown_tool__", {})
        check("★ 未分类工具被保守拦截", not d.allowed, d.rule)

        print(f"\n汇总：PASS={_p} FAIL={_f}")
        return 0 if _f == 0 else 1

    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
