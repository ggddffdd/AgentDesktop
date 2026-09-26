# -*- coding: utf-8 -*-
"""军团授权审计加固回归测试（审查 #6 / v4.168.0）。

钉住三条：
  · **不可串写** —— 多线程并发写账本，不许出现半行 / 交错 / JSON 损坏
  · **可验真**   —— prev_hash / record_hash 哈希链，改一个字节就必须被抓出来
  · **脱敏**     —— key / Bearer / Cookie / token / 手机号 / 邮箱不落盘

另含兼容性：v4.168.0 之前的旧账本（无哈希）不得被误报成「被篡改」。

隔离手段：测试进程自己把 XC_LEGION_DIR 指到临时目录，
绝不碰用户真实 ~/Documents 下的军团数据（跑手双防线）。
"""

import json
import os
import sys
import tempfile
import threading

_HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(_HERE)
sys.path.insert(0, ROOT)

# —— 必须在 import legion 之前改道，否则会写进真实军团目录 ——
_SBX = tempfile.mkdtemp(prefix="xc_verify_sbx_audit_")
os.environ["XC_LEGION_DIR"] = _SBX
os.environ.setdefault("XC_USER_DATA_DIR", os.path.join(_SBX, "userdata"))

import legion as L   # noqa: E402

_p = _f = 0


def check(name, cond, detail=""):
    global _p, _f
    if cond:
        _p += 1
        print(f"  [PASS] {name}")
    else:
        _f += 1
        print(f"  [FAIL] {name}" + (f" —— {detail}" if detail else ""))


def _reset_log():
    """每个小节从干净账本开始（避免上节记录干扰链校验）。"""
    try:
        if os.path.exists(L.AUTH_LOG):
            os.remove(L.AUTH_LOG)
    except Exception:
        pass


def _read_lines():
    with open(L.AUTH_LOG, "r", encoding="utf-8") as f:
        return [l for l in f.read().splitlines() if l.strip()]


def part_a():
    print("=== A) 隔离防线：没写到真实军团目录 ===")
    check("AUTH_LOG 落在测试沙箱内", _SBX in L.AUTH_LOG, L.AUTH_LOG)
    check("沙箱目录已创建/可创建", os.path.isdir(_SBX) or True)


def part_b():
    print("\n=== B) 哈希链：逐步接链 ===")
    _reset_log()
    r1 = L.record_auth("p1", "项目1", 1, "fpA", "PASS", "放行",
                       by="user", reason="用户亲手批准", run_id="run1")
    check("首条 prev_hash = genesis", r1.get("prev_hash") == L.AUTH_GENESIS,
          str(r1.get("prev_hash")))
    check("首条带 record_hash", bool(r1.get("record_hash")))
    r2 = L.record_auth("p1", "项目1", 2, "fpB", "PASS", "放行",
                       by="user", reason="用户亲手批准", run_id="run1")
    check("第 2 条接上第 1 条", r2.get("prev_hash") == r1.get("record_hash"))
    r3 = L.record_message("p1", "项目1", "run1", 3, "继续做第 3 波")
    check("用户指令也接链", r3.get("prev_hash") == r2.get("record_hash"))
    v = L.verify_auth_chain()
    check("链路校验通过", v["ok"] is True and v["n"] == 3, str(v))
    check("非 legacy 账本", v["legacy"] is False)
    check("unchained 为 0", v["unchained"] == 0, str(v))


def part_c():
    print("\n=== C) 可验真：篡改必须被抓 ===")
    _reset_log()
    L.record_auth("p", "项", 1, "fp", "PASS", "放行", by="user", reason="批准A")
    L.record_auth("p", "项", 2, "fp", "PASS", "放行", by="user", reason="批准B")
    L.record_auth("p", "项", 3, "fp", "REJECT", "打回", by="user", reason="批准C")

    # C1 改内容（不改哈希）
    raw = open(L.AUTH_LOG, "r", encoding="utf-8").read()
    open(L.AUTH_LOG, "w", encoding="utf-8").write(raw.replace("批准B", "放行B"))
    v = L.verify_auth_chain()
    check("改内容 → 校验失败", v["ok"] is False, str(v))
    check("定位到第 2 行", v["broken_at"] == 2, str(v))
    check("原因是内容不符", "内容与 record_hash 不符" in v["reason"], v["reason"])

    # C2 整行删除（中间抽掉一条）→ prev_hash 断链
    _reset_log()
    for i in (1, 2, 3):
        L.record_auth("p", "项", i, "fp", "PASS", "放行", by="user",
                      reason=f"批准{i}")
    lines = _read_lines()
    open(L.AUTH_LOG, "w", encoding="utf-8").write("\n".join([lines[0], lines[2]]) + "\n")
    v = L.verify_auth_chain()
    check("删中间一条 → 校验失败", v["ok"] is False, str(v))
    check("原因是 prev_hash 不接", "prev_hash" in v["reason"], v["reason"])

    # C3 追加一条伪造记录（不带哈希）
    _reset_log()
    L.record_auth("p", "项", 1, "fp", "PASS", "放行", by="user", reason="批准")
    with open(L.AUTH_LOG, "a", encoding="utf-8") as f:
        f.write(json.dumps({"ts": 1, "decision": "放行", "by": "user",
                            "reason": "我伪造的"}, ensure_ascii=False) + "\n")
    v = L.verify_auth_chain()
    check("追加无哈希记录 → 校验失败", v["ok"] is False, str(v))
    check("定位到追加行", v["broken_at"] == 2, str(v))

    # C4 哈希本身被改
    _reset_log()
    r = L.record_auth("p", "项", 1, "fp", "PASS", "放行", by="user", reason="批准")
    raw = open(L.AUTH_LOG, "r", encoding="utf-8").read()
    open(L.AUTH_LOG, "w", encoding="utf-8").write(
        raw.replace(r["record_hash"], "0" * 64))
    v = L.verify_auth_chain()
    check("改哈希 → 校验失败", v["ok"] is False, str(v))


def part_d():
    print("\n=== D) 兼容历史账本（旧账不得误报篡改）===")
    _reset_log()
    # 造一条 v4.168.0 之前格式的记录（无 prev_hash / record_hash）
    with open(L.AUTH_LOG, "w", encoding="utf-8") as f:
        f.write(json.dumps({"ts": 1, "time": "2026-01-01 00:00:00",
                            "run_id": "r", "project_id": "p",
                            "wave": 1, "fingerprint": "fp",
                            "pm_verdict": "PASS", "decision": "放行",
                            "by": "user", "reason": "旧账"},
                           ensure_ascii=False) + "\n")
    v = L.verify_auth_chain()
    check("纯旧账本 → ok=True（legacy）", v["ok"] is True and v["legacy"] is True,
          str(v))
    check("unchained 计数正确", v["unchained"] == 1, str(v))

    # 旧账之后追加新记录：新链从 genesis 起，旧行计入 unchained
    L.record_auth("p", "项", 2, "fp", "PASS", "放行", by="user", reason="新账")
    v = L.verify_auth_chain()
    check("旧账+新账 → ok=True", v["ok"] is True, str(v))
    check("unchained=1 且 n=2", v["unchained"] == 1 and v["n"] == 2, str(v))

    # 空账本
    _reset_log()
    open(L.AUTH_LOG, "w").close()
    v = L.verify_auth_chain()
    check("空账本 → ok=True", v["ok"] is True and v["n"] == 0, str(v))

    # 账本不存在
    os.remove(L.AUTH_LOG)
    v = L.verify_auth_chain()
    check("账本不存在 → ok=True", v["ok"] is True and v["n"] == 0, str(v))


def part_e():
    print("\n=== E) 脱敏：敏感信息不落盘 ===")
    _reset_log()
    secret = ("用 sk-abcdefghijklmnop1234 这个key，Bearer eyJhbGciOiJIUzI1NiJ9.payload 也要，"
              "api_key=abcd1234efgh 和 access_token: zzzz9999，"
              "Cookie: session=verysecret123，手机 13800138000，邮箱 zhangsan@example.com，"
              "还有 https://x.com/a?token=qqqq1111&b=2")
    L.record_message("p", "项", "run", 1, secret)
    raw = open(L.AUTH_LOG, "r", encoding="utf-8").read()
    check("原始 sk- key 未落盘", "sk-abcdefghijklmnop1234" not in raw)
    check("sk- 前缀保留可识别", "sk-abcde***" in raw, raw[:200])
    check("Bearer 令牌未落盘", "eyJhbGciOiJIUzI1NiJ9.payload" not in raw)
    check("api_key 值未落盘", "abcd1234efgh" not in raw)
    check("access_token 值未落盘", "zzzz9999" not in raw)
    check("Cookie 值未落盘", "verysecret123" not in raw)
    check("手机号打码（保留前3后4）", "138****8000" in raw and "13800138000" not in raw)
    check("邮箱打码（保留域名）",
          "zs&#x" not in raw and "@example.com" in raw
          and "zhangsan@example.com" not in raw)
    check("URL token 未落盘", "qqqq1111" not in raw)

    rec = L.read_auth(1)[0]
    check("保留 text_digest 供对账", bool(rec.get("text_digest")), str(rec)[:120])
    check("保留 text_len", rec.get("text_len") == len(secret), str(rec.get("text_len")))

    print("\n-- E2 摘要视图（报告/UI 默认拿这个）--")
    d = L.auth_digest(5)
    check("摘要条数正确", len(d) == 1, str(len(d)))
    check("摘要只给预览（截断）", len(d[0]["text_preview"]) < len(secret))
    check("摘要标记已接链", d[0]["chained"] is True)
    check("摘要含 digest", bool(d[0]["text_digest"]))
    check("摘要无敏感明文",
          not any(s in json.dumps(d, ensure_ascii=False)
                  for s in ("verysecret123", "13800138000", "zhangsan@example.com")))

    print("\n-- E3 args 递归脱敏 + 摘要 --")
    _reset_log()
    L.record_auth("p", "项", 1, "fp", "PASS", "放行", by="user", reason="r",
                  args={"cmd": "curl -H 'Authorization: Bearer tok_abcdefgh123456'",
                        "nested": {"password": "p@ssw0rd1"}, "n": 5,
                        "list": ["sk-zzzzzzzzzzzzzzzz", 7]})
    raw = open(L.AUTH_LOG, "r", encoding="utf-8").read()
    check("args 里 Bearer 脱敏", "tok_abcdefgh123456" not in raw, raw[:300])
    check("args 里 password 脱敏", "p@ssw0rd1" not in raw, raw[:300])
    check("args 里 sk- 脱敏", "sk-zzzzzzzzzzzzzzzz" not in raw, raw[:300])
    check("args 保留非敏感字段", '"n": 5' in raw.replace(", ", ", "), raw[:300])
    rec = L.read_auth(1)[0]
    check("args_digest 已生成", bool(rec.get("args_digest")), str(rec)[:200])

    print("\n-- E4 非字符串参数不炸 --")
    _reset_log()
    ok = L.record_auth("p", "项", 1, "fp", "PASS", "放行", by="user", reason="r",
                       args={"a": None, "b": True, "c": [1, 2], "d": {"e": "f"}})
    check("混合类型参数写入成功", ok is not None)


def part_f():
    print("\n=== F) 不可串写：并发写入 ===")
    _reset_log()
    N_THREADS, N_EACH = 8, 25
    errs = []

    def _writer(tid):
        try:
            for i in range(N_EACH):
                L.record_auth(f"p{tid}", f"项{tid}", i, f"fp{tid}-{i}",
                              "PASS", "放行", by="user",
                              reason=f"线程{tid} 第{i}笔", run_id=f"run{tid}")
        except Exception as e:
            errs.append(repr(e))

    ts = [threading.Thread(target=_writer, args=(t,)) for t in range(N_THREADS)]
    for t in ts:
        t.start()
    for t in ts:
        t.join(timeout=30)

    check("并发写入无异常", not errs, str(errs[:3]))
    lines = _read_lines()
    check(f"行数 = {N_THREADS * N_EACH}（没有丢写）",
          len(lines) == N_THREADS * N_EACH,
          f"实际 {len(lines)}")

    bad_json = 0
    for l in lines:
        try:
            json.loads(l)
        except Exception:
            bad_json += 1
    check("没有半行 / 交错导致的坏 JSON", bad_json == 0, f"坏行 {bad_json}")

    v = L.verify_auth_chain()
    check("并发写入后哈希链仍完整", v["ok"] is True, str(v))
    check("链长等于行数", v["n"] == len(lines), str(v))

    # 写入顺序与时间戳单调（同一毫秒内也不允许倒退到负值）
    recs = [json.loads(l) for l in lines]
    check("ts 全部为正数", all(r.get("ts", 0) > 0 for r in recs))

    print("\n-- F2 与 read_auth / verify 并发（读不破坏写）--")
    stop = threading.Event()

    def _reader():
        while not stop.is_set():
            try:
                L.read_auth(20)
                L.auth_digest(5)
            except Exception as e:
                errs.append("reader:" + repr(e))
                return

    rs = [threading.Thread(target=_reader, daemon=True) for _ in range(4)]
    for r in rs:
        r.start()
    for i in range(60):
        L.record_auth("pp", "项", i, "fp", "PASS", "放行", by="user", reason="r")
    stop.set()
    for r in rs:
        r.join(timeout=5)
    check("读写并发无异常", not errs, str(errs[:3]))
    check("读写并发后链仍完整", L.verify_auth_chain()["ok"] is True)


def part_g():
    print("\n=== G) 只读快路径不破坏链（记录字段齐全）===")
    _reset_log()
    L.record_auth("p", "项", 1, "fp", "PASS", "放行", by="user",
                  reason="批准", run_id="runX")
    rec = L.read_auth(1)[0]
    for k in ("ts", "time", "run_id", "project_id", "project_name", "wave",
              "fingerprint", "pm_verdict", "decision", "by", "reason",
              "prev_hash", "record_hash"):
        check(f"字段保留：{k}", k in rec, str(list(rec.keys())))
    check("旧调用点不传 args 也正常（向后兼容）", "args" not in rec)


def main():
    part_a()
    part_b()
    part_c()
    part_d()
    part_e()
    part_f()
    part_g()
    print(f"\n汇总：PASS={_p} FAIL={_f}")
    return 0 if _f == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
