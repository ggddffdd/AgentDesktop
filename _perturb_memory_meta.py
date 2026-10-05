# -*- coding: utf-8 -*-
"""扰动验证：证明 `tests/test_memory_metadata.py` 的判据「会红在该红的地方」。

规矩（与 _perturb_critical_proc_deny.py 同一套）：
  1. 期望采用**包含式**：expect ⊆ actual_red；
  2. 变异副本落 %TEMP% 临时文件，用 MS_PATH / AGENT_PATH 指向它跑判据；
  3. 收尾「原样基线」零红。

五个变异：
  MM1 agent.py 调用点退回旧三参数（元数据在源头就丢）  → D1
  MM2 删 append_memory 普通追加的元数据行拼接         → A1a/A1b
  MM3 删 recall_memory 的过期过滤                     → B1
  MM4 删 _replace_by_topic 的元数据拼接               → A3
  MM5 删 _meta_line 的有效期字段                      → A1a

用法：python _perturb_memory_meta.py
"""
import os
import re
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = HERE
JUDGE = os.path.join(ROOT, "tests", "test_memory_metadata.py")

sys.path.insert(0, ROOT)
import _perturb_guard as _guard  # noqa: E402
_guard.arm()

MS_SRC = open(os.path.join(ROOT, "memory_store.py"), encoding="utf-8").read()
AGENT_SRC = open(os.path.join(ROOT, "agent.py"), encoding="utf-8").read()

PASS_N = 0
FAIL_N = 0
FAILED_CASES = []


def run_judge(env_src):
    paths = []
    env = dict(os.environ, QT_QPA_PLATFORM="offscreen")
    for k in ("MS_PATH", "AGENT_PATH"):
        env.pop(k, None)  # 清掉可能泄漏的上一次变异
    try:
        for var, (prefix, src) in env_src.items():
            fd, p = tempfile.mkstemp(prefix=prefix, suffix=".py",
                                     dir=tempfile.gettempdir())
            os.close(fd)
            with open(p, "w", encoding="utf-8", newline="") as f:
                f.write("# 扰动：_perturb_memory_meta 临时副本，跑完即删\n" + src)
            paths.append(p)
            env[var] = p
        r = subprocess.run([sys.executable, JUDGE], capture_output=True, text=True,
                           encoding="utf-8", errors="replace", timeout=420,
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
        for r in red:
            print("    -", r)
        FAIL_N += 1
        FAILED_CASES.append(name + "（漏红）")
        return
    print("  [OK  ] 命中 %d 项：%s" % (len(red), "；".join(red[:3]) + ("…" if len(red) > 3 else "")))
    PASS_N += 1


# ---------------------------------------------------------------------------
# MM1：agent.py 调用点退回旧三参数
# ---------------------------------------------------------------------------
_mut = sub(AGENT_SRC,
           "                    result = memory_store.append_memory(\n"
           "                        v.get(\"fact\", \"\"), type=v.get(\"category\"), topic=v.get(\"topic\"),\n"
           "                        source=v.get(\"source\"), confidence=v.get(\"confidence\"),\n"
           "                        evidence_id=v.get(\"evidence_id\"),\n"
           "                        expires_at=v.get(\"expires_at\"),\n"
           "                        verified=bool(v.get(\"verified\")))\n",
           "                    result = memory_store.append_memory(\n"
           "                        v.get(\"fact\", \"\"), type=v.get(\"category\"),\n"
           "                        topic=v.get(\"topic\"))  # 扰动：旧三参数\n",
           "MM1 drop agent kwargs")
case("MM1 agent.py 调用点退回旧三参数（元数据在源头就丢）",
     {"AGENT_PATH": ("agent_meta_mut_", _mut)},
     ["D1  五个治理字段全部传入"])

# ---------------------------------------------------------------------------
# MM2：删 append_memory 普通追加的元数据行拼接
# ---------------------------------------------------------------------------
_mut = sub(MS_SRC,
           "    block = f\"\\n{header}\\n\"\n"
           "    if meta:\n"
           "        block += f\"{meta}\\n\"\n"
           "    block += f\"{fact}\\n\"\n",
           "    block = f\"\\n{header}\\n{fact}\\n\"  # 扰动：不写元数据行\n",
           "MM2 drop meta block")
case("MM2 删普通追加路径的元数据行（落库丢元数据回归）",
     {"MS_PATH": ("ms_meta_mut_", _mut)},
     ["A1a 元数据行存在且五字段齐全", "A1b 元数据行紧跟结构化头"])

# ---------------------------------------------------------------------------
# MM3：删 recall_memory 的过期过滤
# ---------------------------------------------------------------------------
_mut = sub(MS_SRC,
           "            # v4.213.0：过有效期的事实不再当确定事实注入（外部审核 P1-1）。\n"
           "            # 注意只在召回路径过滤——管理/搜索（search_memory）仍可见全部条目。\n"
           "            if entry_is_expired(content):\n"
           "                continue\n",
           "",
           "MM3 drop expiry filter")
assert "过有效期的事实不再当确定事实注入" not in _mut, "MM3 变异未生效"
case("MM3 删召回的过期过滤（过期事实继续注入回归）",
     {"MS_PATH": ("ms_meta_mut_", _mut)},
     ["B1  过期条目（股价）不被注入"])

# ---------------------------------------------------------------------------
# MM4：删 _replace_by_topic 的元数据拼接
# ---------------------------------------------------------------------------
_mut = sub(MS_SRC,
           "            entry = f\"{header}\\n\"\n"
           "            if meta:\n"
           "                entry += f\"{meta}\\n\"\n"
           "            entry += fact\n",
           "            entry = f\"{header}\\n{fact}\"  # 扰动：替换路径丢元数据\n",
           "MM4 drop replace meta")
case("MM4 删 topic 替换路径的元数据拼接（覆盖旧条目丢元数据回归）",
     {"MS_PATH": ("ms_meta_mut_", _mut)},
     ["A3  topic 替换路径保留元数据行"])

# ---------------------------------------------------------------------------
# MM5：删 _meta_line 的有效期字段
# ---------------------------------------------------------------------------
_mut = sub(MS_SRC,
           "    if expires_at:\n"
           "        segs.append(f\"有效期至:{expires_at}\")\n",
           "    # 扰动：有效期字段不落库\n",
           "MM5 drop expires field")
case("MM5 删元数据行里的有效期字段（有效期只存在 gate 内存里）",
     {"MS_PATH": ("ms_meta_mut_", _mut)},
     ["A1a 元数据行存在且五字段齐全"])

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
if FAILED_CASES:
    print("失效 case：")
    for c in FAILED_CASES:
        print("  -", c)
    sys.exit(1)
print("=== PERTURB_MEMORY_META_OK ===")
