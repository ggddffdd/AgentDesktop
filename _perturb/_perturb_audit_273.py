# -*- coding: utf-8 -*-
"""扰动：证明 tests/test_audit_273.py 会红在该红的地方。

变异副本落在临时目录，用环境变量交给判据，不改仓库里的源码。
用法：python _perturb/_perturb_audit_273.py
"""
import os
import re
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
JUDGE = os.path.join(ROOT, "tests", "test_audit_273.py")

sys.path.insert(0, ROOT)
import _perturb_guard as _guard  # noqa: E402
_guard.arm()

PASS_N = 0
FAIL_N = 0
FAILED_CASES = []


def read(name):
    with open(os.path.join(ROOT, name), encoding="utf-8") as f:
        return f.read()


def sub(src, old, new, label, count=1):
    found = src.count(old)
    if found != count:
        raise AssertionError("锚点失配(%s): 命中 %d 次，期望 %d" % (label, found, count))
    return src.replace(old, new, count)


def run_judge(env_src):
    paths = []
    env = dict(os.environ, QT_QPA_PLATFORM="offscreen")
    for key in ("AUTOMATION_PATH", "EVIDENCE_PATH", "CHAT_WEB_PATH",
                "UI_FOCUS_PATH", "MCP_PATH", "VOICE_PATH", "UI_PATH"):
        env.pop(key, None)
    try:
        for var, (prefix, src) in env_src.items():
            fd, p = tempfile.mkstemp(prefix=prefix, suffix=".py",
                                     dir=tempfile.gettempdir())
            os.close(fd)
            with open(p, "w", encoding="utf-8", newline="") as f:
                f.write("# 扰动：_perturb_audit_273 临时副本，跑完即删\n" + src)
            paths.append(p)
            env[var] = p
        r = subprocess.run([sys.executable, JUDGE], capture_output=True, text=True,
                           encoding="utf-8", errors="replace", timeout=180,
                           env=env, cwd=ROOT)
        out = (r.stdout or "") + (r.stderr or "")
    except Exception as e:  # noqa: BLE001
        out = "%s: %s" % (type(e).__name__, e)
    finally:
        for p in paths:
            try:
                os.remove(p)
            except Exception:  # noqa: BLE001
                pass
    red = re.findall(r"\[FAIL\]\s+(.+?)\s*$", out, re.M)
    return red, out


def case(name, env_src, expects):
    global PASS_N, FAIL_N
    red, out = run_judge(env_src)
    print("-" * 66)
    print("case %s" % name)
    if not red:
        print("  [FAIL] 变异后居然零红 —— 判据根本没抓到这个改动")
        print("\n".join(out.strip().splitlines()[-16:]))
        FAIL_N += 1
        FAILED_CASES.append(name + "（零红）")
        return
    missed = [e for e in expects if not any(e in r for r in red)]
    if missed:
        print("  [FAIL] 期望转红但没红：%s" % missed)
        print("  实际红项：")
        for r in red:
            print("    -", r)
        FAIL_N += 1
        FAILED_CASES.append(name + "（漏红）")
        return
    print("  [OK  ] 命中 %d 项：%s" % (
        len(red), "；".join(red[:3]) + ("…" if len(red) > 3 else "")))
    PASS_N += 1


AUTO = read("automation.py")
EV = read("evidence.py")
CHAT = read("chat_web.py")
FOCUS = read("ui_focus.py")
MCP = read("mcp_client.py")
VOICE = read("voice.py")
UI = read("ui.py")

_mut = sub(
    AUTO,
    "    if stopped:\n        return False\n"
    "    text = reply.strip() if isinstance(reply, str) else \"\"\n",
    "    return True  # 扰动：推脱也算成功\n"
    "    if stopped:\n        return False\n"
    "    text = reply.strip() if isinstance(reply, str) else \"\"\n",
    "P1 always success")
case("P1 推脱回复也算成功",
     {"AUTOMATION_PATH": ("auto_mut_", _mut)},
     ["A1a 推脱回复不算成功"])

_mut = sub(
    AUTO,
    "    task[\"last_attempt\"] = now.timestamp()\n"
    "    task[\"last_result\"] = \"failed\"\n",
    "    task[\"last_run\"] = now.timestamp()  # 扰动：失败也占掉今天\n"
    "    task[\"last_attempt\"] = now.timestamp()\n"
    "    task[\"last_result\"] = \"failed\"\n",
    "P2 fail consumes today")
case("P2 失败也写 last_run",
     {"AUTOMATION_PATH": ("auto_mut_", _mut)},
     ["A1c 失败不写 last_run", "A1f 冷却结束后可补跑"])

_mut = sub(
    AUTO,
    "    if sid:\n        sessions = getattr(store, \"sessions\", None)\n",
    "    if False and sid:  # 扰动：不按编号取会话\n"
    "        sessions = getattr(store, \"sessions\", None)\n",
    "P3 ignore sid")
case("P3 回写跟着当前会话走",
     {"AUTOMATION_PATH": ("auto_mut_", _mut)},
     ["A2a 钉住的会话不是当前会话"])

_mut = sub(
    UI,
    "msgs = self._agent_write_session().messages",
    "msgs = self.store.active().messages  # 扰动：流式写进当前会话",
    "P4 stream active")
_mut = sub(
    _mut,
    "self._agent_write_session().messages.append(rec)",
    "self.store.active().messages.append(rec)  # 扰动：工具记录写进当前会话",
    "P4 tool active")
case("P4 界面回写改回 active()",
     {"UI_PATH": ("ui_mut_", _mut)},
     ["A2b 流式回写走钉住的会话", "A2c 工具记录走钉住的会话"])

_mut = sub(
    UI,
    "        w = getattr(self, \"_agent_worker\", None)\n"
    "        bound = getattr(w, \"_bound_sid\", None) if w is not None else None\n"
    "        if bound:\n"
    "            sess = automation.bound_session(getattr(self, \"store\", None), bound)\n"
    "            if sess is not None:\n"
    "                return sess\n"
    "        return self.store.active()\n",
    "        return self.store.active()  # 扰动：忽略钉住的会话\n",
    "P4b ignore bound write")
case("P4b 回写函数忽略钉住的会话",
     {"UI_PATH": ("ui_mut_", _mut)},
     ["A2d 工具记录运行时进钉住的会话"])

_mut = sub(
    EV,
    "                    cand = int(row[0] or 0) + 1\n",
    "                    cand = 1  # 扰动：编号从 1 重来\n",
    "P5 id restart")
case("P5 证据编号每次从 1 开始",
     {"EVIDENCE_PATH": ("ev_mut_", _mut)},
     ["A3a 重启后编号接着最大号", "A3b 新编号取回的是新页面"])

_mut = sub(
    CHAT,
    "                    self._load_gave_up = True\n"
    "                    self.loadGaveUp.emit()\n",
    "                    self._load_gave_up = True\n"
    "                    pass  # 扰动：失败也不通知界面\n",
    "P6 no give up")
case("P6 加载失败不发放弃信号",
     {"CHAT_WEB_PATH": ("chat_mut_", _mut)},
     ["E1 连续六次失败只发一次放弃"])

_mut = sub(
    FOCUS,
    "        \"聊天页没有加载出来\",\n"
    "        \"页面是空白的。点重试再加载一次。\",\n"
    "        \"重试\",\n",
    "        \"聊天页没有加载出来\",\n"
    "        \"页面是空白的。点重试再加载一次。\",\n"
    "        \"关闭\",  # 扰动：空状态没有重试\n",
    "P7 no retry")
case("P7 空状态按钮不是重试",
     {"UI_FOCUS_PATH": ("focus_mut_", _mut)},
     ["E4 按钮文案是重试"])

_mut = sub(
    MCP,
    "    command = (command or \"\").strip()\n",
    "    return True, \"\"  # 扰动：缺目录也启动\n"
    "    command = (command or \"\").strip()\n",
    "P8 preflight always ok")
case("P8 缺目录也去启动",
     {"MCP_PATH": ("mcp_mut_", _mut)},
     ["B1 目录不在就跳过", "B2 没有 Node 就跳过"])

_mut = sub(
    VOICE,
    "    if tts_failure_is_quota(exc):\n"
    "        return \"朗读暂时不可用：语音服务拒绝了这次请求。已先关掉朗读，稍后再开即可。\"\n",
    "    return str(exc)  # 扰动：把接口原文给用户\n"
    "    if tts_failure_is_quota(exc):\n"
    "        return \"朗读暂时不可用：语音服务拒绝了这次请求。已先关掉朗读，稍后再开即可。\"\n",
    "P9 raw tts")
case("P9 朗读失败把域名给用户",
     {"VOICE_PATH": ("voice_mut_", _mut)},
     ["C1 朗读失败不说域名"])

_mut = sub(
    UI,
    "高级选项：五档执行 / 总是启用工具",
    "高级五档",
    "P10 hide advanced label",
    count=1)
case("P10 高级选项按钮改回内部说法",
     {"UI_PATH": ("ui_mut_", _mut)},
     ["C3 高级选项写在按钮上"])

print("-" * 66)
print("case 基线（原样，期望零红）")
red, out = run_judge({})
if red:
    print("  [FAIL] 基线竟有红项：")
    for r in red:
        print("    -", r)
    print("\n".join(out.strip().splitlines()[-20:]))
    FAIL_N += 1
    FAILED_CASES.append("基线")
else:
    print("  [OK  ] 基线零红")
    PASS_N += 1

print("=" * 66)
print("PERTURB PASS=%d FAIL=%d" % (PASS_N, FAIL_N))
if FAILED_CASES:
    print("哑弹：" + "，".join(FAILED_CASES))
sys.exit(0 if FAIL_N == 0 else 1)
