# -*- coding: utf-8 -*-
"""扰动验证：证明 `tests/test_tool_audit_c.py` 的判据「会红在该红的地方」。

判据绿不等于判据有效。本脚本逐个**拆掉 C 批判据所守的写法**，看对应判据是否真转红。

规矩（与 _perturb_system_control_*.py / _perturb_canvas_*.py 同一套）：
  1. 期望采用**包含式**：expect ⊆ actual_red；只要求「该红的红了」，不禁止连带红；
  2. 每个 case 真的生成变异源码落临时文件，用环境变量（TOOL_AUDIT_PATH /
     PERM_PATH / LEGION_PERM_PATH / UI_PATH）指向它跑判据，**不在原文件上动刀**；
  3. 收尾跑一次「原样基线」，期望零红 —— 防「判据过宽、什么都判红」。

两个方向都验（「整段删掉」与「只删动作留条件」）：
  落盘本身：PC1 删落盘调用（留装饰器与记录构造） / PC2 留记录但写盘改成恒 False
  只读口径：PC9 只删只读过滤（留 classify 与其余逻辑）

**多点变异（LEARNINGS：同一缺陷有两道独立防线时，单点变异永远翻不红）**：
「审计失败不打断决策」有**两道**独立兜底 —— ① `tool_audit.write()` 内部 try/except；
② `_audited` 包装器的外层 try/except。故拆成两 case：
  PC7a 只拆第一道 → 期望「直接调 write 的场景」翻红（证明这道防线守的是什么）
  PC7b 两道一起拆 → 期望「decide() 不抛异常」翻红（证明 decide 的不抛是双防线）

⚠️ PC4（默认也落盘）会让判据套件往**真实账本**写十几行垃圾 —— 那正是它要证明的事。
脚本收尾会把这十几行**按字节截回**原始长度（账本是 append-only，截回安全），
并把截断字节数打出来；截断带 64KB 上限护栏，避免误切真实数据。

用法：python _perturb_tool_audit_c.py
"""
import os
import re
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = HERE
JUDGE = os.path.join(ROOT, "tests", "test_tool_audit_c.py")

sys.path.insert(0, ROOT)
import _perturb_guard as _guard  # noqa: E402
_guard.arm()

import tool_audit  # noqa: E402
# PC4 会让变异源码往真实账本写垃圾，先记下原始长度，收尾按字节截回
_LEDGER = os.path.join(tool_audit.default_dir(), tool_audit.MAIN_AUDIT_NAME)
_LEDGER0 = os.path.getsize(_LEDGER) if os.path.exists(_LEDGER) else 0

PERM_SRC = open(os.path.join(ROOT, "permissions.py"), encoding="utf-8").read()
TA_SRC = open(os.path.join(ROOT, "tool_audit.py"), encoding="utf-8").read()
LG_SRC = open(os.path.join(ROOT, "legion_permissions.py"), encoding="utf-8").read()
UI_SRC = open(os.path.join(ROOT, "ui.py"), encoding="utf-8").read()

PASS_N = 0
FAIL_N = 0
FAILED_CASES = []


def run_judge(env_src):
    paths = []
    env = dict(os.environ, QT_QPA_PLATFORM="offscreen")
    try:
        for var, (prefix, src) in env_src.items():
            fd, p = tempfile.mkstemp(prefix=prefix, suffix=".py", dir=ROOT)
            os.close(fd)
            with open(p, "w", encoding="utf-8", newline="") as f:
                f.write(src)
            paths.append(p)
            env[var] = p
        r = subprocess.run([sys.executable, JUDGE], capture_output=True, text=True,
                           encoding="utf-8", errors="replace", timeout=600,
                           env=env, cwd=ROOT)
        out = (r.stdout or "") + (r.stderr or "")
    except Exception as e:                                     # noqa: BLE001
        out = "%s: %s" % (type(e).__name__, e)
    finally:
        for p in paths:
            try:
                os.remove(p)
            except Exception:                                  # noqa: BLE001
                pass
    red = re.findall(r"\[FAIL\]\s+(.+?)\s*$", out, re.M)
    return red, out


def sub(src, old, new, label, count=1):
    if src.count(old) != count:
        raise AssertionError("锚点失配(%s): 命中 %d 次，期望 %d" % (label, src.count(old), count))
    return src.replace(old, new)


def edits(src, pairs, label):
    for i, (old, new) in enumerate(pairs):
        src = sub(src, old, new, "%s#%d" % (label, i))
    return src


def case(name, env_src, expects):
    global PASS_N, FAIL_N
    red, out = run_judge(env_src)
    print("-" * 66)
    print("case %s" % name)
    if not red:
        print("  [FAIL] 变异后居然零红 —— 判据根本没抓到这个改动")
        print("  ---- 判据输出尾部 ----")
        print("\n".join(out.strip().splitlines()[-12:]))
        FAIL_N += 1
        FAILED_CASES.append(name + "（零红）")
        return
    missed = [e for e in expects if not any(e in r for r in red)]
    if missed:
        print("  [FAIL] 期望转红但没红：%s" % missed)
        print("  实际红项：")
        for r in red[:12]:
            print("    -", r)
        FAIL_N += 1
        FAILED_CASES.append(name + "（漏红）")
        return
    print("  [OK  ] 命中 %d 项：%s" % (len(red), "；".join(red[:3]) + ("…" if len(red) > 3 else "")))
    PASS_N += 1


# ---------------------------------------------------------------------------
# 预期的红项名字（**从判据源码逐字复制**，不手打 —— 见 LEARNINGS L249）
# ---------------------------------------------------------------------------
A_RUNCMD = "A 交互·EXEC→tier:manual（run_command）：+1 行"
A_ONE = "A 交互·EXEC→tier:manual（run_command）：账本记的 rule 就是决策的 rule"
C_DIGEST = "★ args_digest 为 12 位十六进制（与军团同长）"
C_PREVIEW = "★ args_preview 非空（光有哈希等于查不出「干了啥」）"
C_TOOLNAME = "tool 名如实记录（写成空串就等于不知道干了什么）"
D_LEAK = "★ 敏感键的值一律不落盘"
E_REAL = "★ 未接线时**一个字节都不写真实账本**（判据套件零副作用）"
E_NORAISE = "★ 审计写盘失败时 decide() 不抛异常"
E_BADPATH = "★ 目录不可建（父路径是文件）→ False 且不抛"
F_WIRE = "★ 每一处构造都显式接线 audit_dir（漏接线 = 主对话决策无痕）"
F_DEFAULT = '★ PermissionEngine 的 audit_dir 默认值必须是 ""（关闭）'
G_REDACT = "★ legion._redact 就是 tool_audit.redact"
G_ORIGIN = '军团 origin == "legion"'
G_DIGEST12 = "军团的 args_digest 长度与主对话一致（12）"
H_DECORATOR = "★ decide 由 @_audited 装饰（这就是「每条 return 路径都落一笔」的实现方式）"
H_SHELL = "★ _audited 内部真的调用了 _audit_decision（不是空壳）"
H_WRITE = "_audit_decision 真的调了 tool_audit.write（不是只组记录不落盘）"
B_READONLY = "★ 8 个只读工具全部不记（账本 0 行）"
B_CONTRAST = "对照：同引擎下一笔非只读立刻落盘（证明确实接上了）"
B_UNKNOWN = "★ 未登记工具（fail-closed 落 EXTERNAL）也记账 —— 可多记不可漏记"

# ---------------------------------------------------------------------------
# PC1 整段删掉落盘调用（装饰器与记录构造都留着 → 记录组了但不写、也不落盘）
# ---------------------------------------------------------------------------
_mut = sub(PERM_SRC,
           "            _audit_decision(self, _name, _args, dec, _intent)\n",
           "            pass\n",
           "PC1 drop audit call")
case("PC1 删掉落盘调用（装饰器与 rec 构造都留着）",
     {"PERM_PATH": ("perm_mut_", _mut)},
     [A_RUNCMD, B_CONTRAST, B_UNKNOWN])

# ---------------------------------------------------------------------------
# PC2 只删「写盘动作」：记录照组，但 write 恒不执行
# ---------------------------------------------------------------------------
_mut = sub(PERM_SRC,
           '    return tool_audit.write(audit_dir, tool_audit.MAIN_AUDIT_NAME, rec, tag="main")\n',
           "    return False\n",
           "PC2 stub write")
case("PC2 只把 tool_audit.write 换成恒 False（rec 照组）",
     {"PERM_PATH": ("perm_mut_", _mut)},
     [A_RUNCMD, H_WRITE])

# ---------------------------------------------------------------------------
# PC3 去掉装饰器（审计整条链断掉）
# ---------------------------------------------------------------------------
_mut = sub(PERM_SRC,
           "    @_audited\n"
           "    def decide(self, name, args=None, explicit_intent=True, task_risk=None):\n",
           "    def decide(self, name, args=None, explicit_intent=True, task_risk=None):\n",
           "PC3 remove decorator")
case("PC3 去掉 @_audited 装饰器",
     {"PERM_PATH": ("perm_mut_", _mut)},
     [A_RUNCMD, H_DECORATOR])

# ---------------------------------------------------------------------------
# PC4 默认也落盘（破坏「未接线 = 关闭」契约）
# ---------------------------------------------------------------------------
_mut = edits(PERM_SRC,
             [('                 external_allow=None, audit_dir=""):\n',
               "                 external_allow=None, audit_dir=None):\n"),
              ('        self.audit_dir = audit_dir or ""\n',
               '        self.audit_dir = (audit_dir if audit_dir is not None\n'
               '                          else tool_audit.default_dir())\n')],
             "PC4 default on")
case("PC4 把 audit_dir 默认值改成自动推导（判据套件开始污染真实账本）",
     {"PERM_PATH": ("perm_mut_", _mut)},
     [E_REAL, F_DEFAULT])

# ---------------------------------------------------------------------------
# PC5 redact 不脱敏（敏感值原样落盘）
# ---------------------------------------------------------------------------
_mut = sub(TA_SRC, '                safe[k] = "***"\n', "                safe[k] = v\n",
           "PC5 no redact")
case("PC5 redact 不再抹敏感键（凭据原文落盘）",
     {"TOOL_AUDIT_PATH": ("ta_mut_", _mut)},
     [D_LEAK])

# ---------------------------------------------------------------------------
# PC6 摘要改成完整 64 位（与军团长度不再一致）
# ---------------------------------------------------------------------------
_mut = sub(TA_SRC,
           '    return hashlib.sha256(blob.encode("utf-8", "replace")).hexdigest()[:ARG_DIGEST_LEN]\n',
           '    return hashlib.sha256(blob.encode("utf-8", "replace")).hexdigest()\n',
           "PC6 full digest")
case("PC6 args_digest 改成完整 64 位",
     {"TOOL_AUDIT_PATH": ("ta_mut_", _mut)},
     [C_DIGEST, G_DIGEST12])

# ---------------------------------------------------------------------------
# PC7a 只拆第一道防线：tool_audit.write 失败不再吞异常
# ---------------------------------------------------------------------------
_mut = sub(TA_SRC,
           '    except Exception:\n'
           '        _warned.warn(tag, "工具审计写入失败（只少一条痕，不影响主流程）")\n'
           "        return False\n",
           "    except Exception:\n"
           "        raise\n",
           "PC7a raise on failure")
case("PC7a 只拆 write() 内部兜底（第二道防线仍在，decide 仍不抛）",
     {"TOOL_AUDIT_PATH": ("ta_mut_", _mut)},
     [E_BADPATH])

# ---------------------------------------------------------------------------
# PC7b 两道防线一起拆：write 抛 + 包装器不再兜
# ---------------------------------------------------------------------------
_mut_ta = sub(TA_SRC,
              '    except Exception:\n'
              '        _warned.warn(tag, "工具审计写入失败（只少一条痕，不影响主流程）")\n'
              "        return False\n",
              "    except Exception:\n"
              "        raise\n",
              "PC7b ta raise")
_mut_perm = sub(PERM_SRC,
                "        dec = fn(self, *a, **kw)\n"
                "        try:\n"
                "            try:\n"
                "                ba = inspect.signature(fn).bind(self, *a, **kw)\n"
                '                _name = ba.arguments.get("name", "")\n'
                '                _args = ba.arguments.get("args")\n'
                '                _intent = ba.arguments.get("explicit_intent", True)\n'
                "            except Exception:\n"
                '                _name, _args, _intent = "", None, True\n'
                "            _audit_decision(self, _name, _args, dec, _intent)\n"
                "        except Exception:\n"
                "            # 审计是旁路：任何意外都不许影响决策结果\n"
                "            pass\n"
                "        return dec\n",
                "        dec = fn(self, *a, **kw)\n"
                "        ba = inspect.signature(fn).bind(self, *a, **kw)\n"
                '        _name = ba.arguments.get("name", "")\n'
                '        _args = ba.arguments.get("args")\n'
                '        _intent = ba.arguments.get("explicit_intent", True)\n'
                "        _audit_decision(self, _name, _args, dec, _intent)\n"
                "        return dec\n",
                "PC7b remove wrapper guard")
case("PC7b 两道兜底一起拆（审计失败真的会打断决策）",
     {"TOOL_AUDIT_PATH": ("ta_mut_", _mut_ta),
      "PERM_PATH": ("perm_mut_", _mut_perm)},
     [E_NORAISE, E_BADPATH])

# ---------------------------------------------------------------------------
# PC8 ui.py 构造点删掉接线
# ---------------------------------------------------------------------------
_mut = sub(UI_SRC,
           "                                                  audit_dir=default_audit_dir())\n",
           "                                                  )\n",
           "PC8 unwire ui")
case("PC8 ui.py 引擎构造不再传 audit_dir（主对话彻底无痕）",
     {"UI_PATH": ("ui_mut_", _mut)},
     [F_WIRE])

# ---------------------------------------------------------------------------
# PC9 只读也落盘（口径边界被抹掉）
# ---------------------------------------------------------------------------
_mut = sub(PERM_SRC,
           "        if classify(name) == RiskClass.READ:\n            return False\n",
           "        if False:\n            return False\n",
           "PC9 record read too")
case("PC9 去掉只读过滤（账本被只读操作淹掉）",
     {"PERM_PATH": ("perm_mut_", _mut)},
     [B_READONLY])

# ---------------------------------------------------------------------------
# PC10 legion 脱钩：自己一份 redact（不再共用公共件）
# ---------------------------------------------------------------------------
_mut = sub(LG_SRC,
           "_redact = tool_audit.redact\n",
           "_redact = lambda a: repr(a)      # 脱钩：自己一份\n",
           "PC10 fork redact")
case("PC10 军团自己另起一份 _redact（两账本范式开始漂移）",
     {"LEGION_PERM_PATH": ("lg_mut_", _mut)},
     [G_REDACT])

# ---------------------------------------------------------------------------
# PC11 军团记录丢掉 origin（两个账本混在一起无法区分通道）
# ---------------------------------------------------------------------------
_mut = sub(LG_SRC,
           '            by=by, origin="legion", allowed=dec.allowed,\n',
           "            by=by, allowed=dec.allowed,\n",
           "PC11 drop legion origin")
case("PC11 军团记录不再标 origin（默认落成 main，抽查时认错通道）",
     {"LEGION_PERM_PATH": ("lg_mut_", _mut)},
     [G_ORIGIN])

# ---------------------------------------------------------------------------
# PC12 参数不现取、写死空名（账本里看不出干了什么工具）
# ---------------------------------------------------------------------------
_mut = sub(PERM_SRC,
           '                _name = ba.arguments.get("name", "")\n',
           '                _name = ""\n',
           "PC12 hardcode name")
case("PC12 装饰器不再现取参数名（账本 tool 全空）",
     {"PERM_PATH": ("perm_mut_", _mut)},
     [C_TOOLNAME])

# ---------------------------------------------------------------------------
# PC13 build_record 丢掉 args_preview（只剩不可逆哈希）
# ---------------------------------------------------------------------------
_mut = sub(TA_SRC, '        "args_preview": redact(args),\n', "", "PC13 drop preview")
case("PC13 build_record 不再写 args_preview（账本只剩查不出的哈希）",
     {"TOOL_AUDIT_PATH": ("ta_mut_", _mut)},
     [C_PREVIEW])

# ---------------------------------------------------------------------------
# 反向基线
# ---------------------------------------------------------------------------
print("-" * 66)
print("反向基线：原样源码跑判据，不许有任何红项")
_red0, _out0 = run_judge({})
if _red0:
    print("  [FAIL] 未变异的源码居然红了：%s" % _red0)
    FAIL_N += 1
    FAILED_CASES.append("baseline")
else:
    n_pass = len(re.findall(r"\[PASS\]", _out0))
    print("  [OK  ] 未变异源码全绿（%d 项判据）" % n_pass)
    PASS_N += 1

print("\nPERTURB PASS=%d FAIL=%d" % (PASS_N, FAIL_N))


def _restore_ledger():
    """PC4（默认也落盘）会让被测源码往**真实账本**写十几行垃圾 —— 按字节截回原长。

    账本是 append-only，截回原长度是安全的；但若增长超过 64KB 就不敢动了
    （可能混进了真实记录），只报警让人工处理。
    """
    if not os.path.exists(_LEDGER):
        if _LEDGER0:
            print("  [WARN] 真实账本原本存在，现在没了：%s" % _LEDGER)
        return
    now = os.path.getsize(_LEDGER)
    if now <= _LEDGER0:
        print("  真实账本未被污染（%d 字节）" % now)
        return
    grown = now - _LEDGER0
    if grown > 65536:
        print("  [WARN] 账本增长 %d 字节，超过护栏，**不截断**，请人工检查 %s"
              % (grown, _LEDGER))
        return
    with open(_LEDGER, "r+b") as f:
        f.truncate(_LEDGER0)
    print("  已把变异写进真实账本的 %d 字节垃圾截回原长（%d 字节）" % (grown, _LEDGER0))


_restore_ledger()

if FAILED_CASES:
    print("失效 case：")
    for c in FAILED_CASES:
        print("  -", c)
    sys.exit(1)
print("=== PERTURB_TOOL_AUDIT_C_OK ===")
