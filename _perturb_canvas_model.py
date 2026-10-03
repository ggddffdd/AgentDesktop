# -*- coding: utf-8 -*-
"""扰动验证：证明 tests/test_canvas_model.py 的静态判据「会红在该红的地方」。

判据绿不等于判据有效。一份只会点头的判据比没有更糟——它让人以为防线还在。
本脚本的做法：**逐个删掉数据模型里的契约写法**，看对应 A 组判据是否真的转红。

与 _perturb_probe_graphics.py 同一套规矩：
  1. 期望采用**包含式**：expect ⊆ actual_red。只要求「该红的红了」，不禁止
     连带红其它项（改一处往往顺带塌一片，那正是我们想知道的）；
  2. 每条 case 都真的生成一份**变异后源码**落临时文件，再用环境变量指向它跑
     判据（CANVAS_PATH / CANVAS_STATIC），不在原文件上动刀；
  3. 期望为空 means 这条 case 应该保持全绿 —— 用来防「判据过宽、什么都判红」。

用法：python _perturb_canvas_model.py
"""

import os
import re
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = HERE
CANVAS = os.path.join(ROOT, "canvas_graph.py")
# Wave B（审查 #2）：依赖原语（depend / undepend）住在 task_graph.py，
# 针对它们的变异要落到另一个变异源上（判据用 TG_PATH 指向它）。
TG = os.path.join(ROOT, "task_graph.py")
JUDGE = os.path.join(ROOT, "tests", "test_canvas_model.py")

# 护栏：快照被测源码 + 装 SIGTERM/SIGINT/atexit 还原 + 残留变异预检
sys.path.insert(0, ROOT)
import _perturb_guard as _guard  # noqa: E402
_guard.arm()

SRC = open(CANVAS, encoding="utf-8").read()
TG_SRC = open(TG, encoding="utf-8").read()

PASS_N = 0
FAIL_N = 0
FAILED_CASES = []


def run_judge(mutated_src, tag, tg_src=None, static=True):
    """把变异后的源码落临时文件跑判据，返回 (红名集合, 原始输出)。

    tg_src 不为 None 时，一并把 task_graph.py 也换成变异版（判据读 TG_PATH）。
    两个临时副本都以 `*_mut_*.py` 命名 —— 护栏的 scratch 清理认得这个前缀，
    万一本脚本被强杀也能兜底删掉。

    static=False 时不加 CANVAS_STATIC → 连 B/C 行为组一起跑，用来验证
    「只有行为判据能抓到的」变异（例如成环判定恒假、can_connect 偷偷建边）。
    """
    fd, path = tempfile.mkstemp(prefix="canvas_mut_", suffix=".py", dir=ROOT)
    os.close(fd)
    with open(path, "w", encoding="utf-8") as f:
        f.write(mutated_src)
    env = dict(os.environ, CANVAS_PATH=path)
    if static:
        env["CANVAS_STATIC"] = "1"
    tg_path = None
    if tg_src is not None:
        fd2, tg_path = tempfile.mkstemp(prefix="taskgraph_mut_", suffix=".py",
                                        dir=ROOT)
        os.close(fd2)
        with open(tg_path, "w", encoding="utf-8") as f:
            f.write(tg_src)
        env["TG_PATH"] = tg_path
    try:
        r = subprocess.run([sys.executable, JUDGE], capture_output=True, text=True,
                           encoding="utf-8", errors="replace", timeout=300, env=env)
        out = (r.stdout or "") + (r.stderr or "")
    except Exception as e:  # noqa: BLE001
        out = "%s: %s" % (type(e).__name__, e)
    finally:
        for p in (path, tg_path):
            if p is None:
                continue
            try:
                os.remove(p)
            except Exception:
                pass
    red = set(re.findall(r"\[FAIL\]\s+(.+?)\s*$", out, re.M))
    return red, out


def case(name, mutate, expect_ids, on="canvas", static=True):
    """mutate(src) -> 变异后源码。expect_ids：期望转红的判据名（子串匹配即可）。

    on="tg"     → 变异的是 task_graph.py（经 TG_PATH 传给判据）。
    static=False→ 连 B/C 行为组一起跑（给「只有行为判据抓得住」的变异用）。
    """
    global PASS_N, FAIL_N
    base = TG_SRC if on == "tg" else SRC
    try:
        new_src = mutate(base)
    except Exception as e:  # noqa: BLE001
        print("  [FAIL] %s  — 变异函数本身出错：%s: %s" % (name, type(e).__name__, e))
        FAIL_N += 1
        FAILED_CASES.append(name)
        return
    if new_src == base:
        print("  [FAIL] %s  — 变异没有生效（源码没变），这条 case 是假的" % name)
        FAIL_N += 1
        FAILED_CASES.append(name)
        return
    if on == "tg":
        red, _out = run_judge(SRC, name, tg_src=new_src, static=static)
    else:
        red, _out = run_judge(new_src, name, static=static)
    missing = [e for e in expect_ids if not any(e in r for r in red)]
    ok = not missing
    print("  [%s] %s   期望红 %s | 实际红 %d 项%s"
          % ("OK  " if ok else "FAIL", name, "、".join(expect_ids) or "(无)",
             len(red), ("  缺：" + "、".join(missing)) if missing else ""))
    if ok:
        PASS_N += 1
    else:
        FAIL_N += 1
        FAILED_CASES.append(name)


def sub(old, new):
    """生成一个「把 old 替换成 new」的变异函数（自带生效校验）。"""
    def f(src):
        if old not in src:
            raise AssertionError("锚点不在源码里：%r" % old[:60])
        return src.replace(old, new, 1)
    return f


def drop_line(anchor):
    """删掉包含 anchor 的整行。"""
    def f(src):
        if anchor not in src:
            raise AssertionError("锚点不在源码里：%r" % anchor[:60])
        lines = src.splitlines(True)
        for i, ln in enumerate(lines):
            if anchor in ln:
                del lines[i]
                return "".join(lines)
        raise AssertionError("没找到可删的行")
    return f


print("\n=== 扰动：数据模型契约被删 → 对应静态判据必须转红 ===")

# ---- 端口 / 节点类型校验 ----
case("P01 撤掉 Port 端口类型校验（if False）",
     sub("        if self.port_type not in VALID_PORT_TYPES:",
         "        if False:"),
     ["A1"])
case("P02 撤掉 CanvasNode 节点类型校验（if False）",
     sub("        if node_type not in NODE_TYPES:",
         "        if False:"),
     ["A2"])

# ---- connect_data 三处校验 ----
case("P03 撤掉「上游输出端口存在」校验的报错文案",
     sub("不存在输出端口", "port_ok"),
     ["A3"])
case("P04 撤掉「下游输入端口存在」校验的报错文案",
     sub("不存在输入端口", "port_ok"),
     ["A4"])
case("P05 撤掉类型兼容校验（if False）",
     sub("        if fp.port_type not in _PORT_ACCEPTS[tp.port_type]:",
         "        if False:"),
     ["A5"])

# ---- 数据边隐含顺序边 + 去重（Wave B 后：统一走 _sync_order_deps 全量重算）----
case("P06 撤掉 _sync_order_deps 里的 self._tg.depend",
     sub("            self._tg.depend(to_node, from_node)",
         "            pass"),
     ["A6"])
case("P07 撤掉「撤多余依赖」分支（_order_seen - desired 不再回退）",
     sub("        for from_node, to_node in sorted(self._order_seen - desired):\n"
         "            self._tg.undepend(to_node, from_node)",
         "        pass  # 扰动：不撤多余依赖"),
     ["A7"])

# ---- 节点状态初值 ----
case("P08 删掉 self.status = \"pending\"",
     drop_line('        self.status = "pending"'),
     ["A8"])

# ---- Wave A #3：单入端口禁止静默覆盖 ----
case("P09 撤掉单入端口拒第二条（if not tp.multi → if False）",
     sub("            if not tp.multi:", "            if False:"),
     ["A14"])
case("P10 撤掉同四元组幂等判断（if (from_node, from_port) in existing → if False）",
     sub("            if (from_node, from_port) in existing:",
         "            if False:"),
     ["A15"])
case("P11 撤掉 _incoming_assets 的 multi 收列表分支（if False）",
     sub('                if port is not None and getattr(port, "multi", False):',
         "                if False:"),
     ["A16"])

# ---- Wave B（审查 #2）：依赖可撤销 + 与边集一致 ----
case("P12 藏起 task_graph.undepend（依赖只剩加、没有撤）",
     sub("    def undepend(self, task_id: str, blocked_by_id: str):",
         "    def _undepend_disabled(self, task_id: str, blocked_by_id: str):"
         "  # 扰动：藏起 undepend"),
     ["A17"], on="tg")
case("P13 撤掉 undepend 的入口恢复（依赖清空后不回 _entry_ids）",
     sub("        if not t.blocked_by and task_id not in self._entry_ids:\n"
         "            self._entry_ids.append(task_id)",
         "        if not t.blocked_by and task_id not in self._entry_ids:"
         "  # 扰动：不恢复入口\n            pass"),
     ["A18"], on="tg")
case("P14 撤掉 depend 的幂等判断（同一对节点重复登记）",
     sub("        if blocked_by_id in t.blocked_by:\n            return self",
         "        if blocked_by_id in t.blocked_by:  # 扰动：不判重复\n"
         "            pass"),
     ["A19"], on="tg")
case("P15 撤掉连线校验里的自依赖拦截（if False）",
     # 锚点必须带上「raise」那两行：`if from_node == to_node:` 现在在 would_cycle 里
     # 也出现一次（返回 [from, to] 而不是抛错），只匹配条件行会打到 would_cycle 上，
     # 于是 A21 照样绿、这条 case 变成假绿。
     sub("        if from_node == to_node:\n"
         "            raise ValueError(\n"
         '                f"{from_node} 不能连到自己：自依赖会永远等不到就绪（run 时死锁）")',
         "        if False:  # 扰动：不拦自依赖\n            pass"),
     ["A21"])
case("P16 撤掉 remove_data_edge 删边后的依赖重算",
     sub("        self.data_edges = kept\n        self._sync_order_deps()",
         "        self.data_edges = kept\n        pass  # 扰动：不重算依赖"),
     ["A22"])
case("P17 撤掉 _order_seen 回写（重算结果不用作下次差集）",
     sub("        self._order_seen = desired",
         "        self._order_seen = set()  # 扰动：不回写期望集合"),
     ["A20"])

# ---- Wave D 发现①②：纯校验 API + 连线即成环拒绝 ----
case("P18 撤掉数据边校验体里的成环拒绝",
     sub('        self._reject_cycle(from_node, to_node)\n        return "ok"',
         '        return "ok"  # 扰动：不拒成环'),
     ["A25"])
case("P19 撤掉顺序边连线前的成环拒绝",
     sub("        self._reject_cycle(from_node, to_node)"
         "   # 顺序边一样能把图连成环\n", ""),
     ["A25"])
case("P20 _edge_pairs 漏掉顺序边（成环判定与依赖重算一起漏）",
     sub("        pairs |= {(e.from_node, e.to_node) for e in self.order_edges}\n", ""),
     ["A26"])
case("P21 校验函数不再返回 'noop'（幂等判断消失）",
     sub('                return "noop"', '                return "ok"  # 扰动：不判幂等'),
     ["A24"])
case("P22 藏起纯校验 API can_connect",
     sub("    def can_connect(self, from_node, from_port, to_node, to_port) -> bool:",
         "    def _no_can_connect(self, from_node, from_port, to_node, to_port)"
         " -> bool:  # 扰动：藏起 can_connect"),
     ["A23"])

# ---- 行为组专属：这些变异只有 B 组抓得住（静态判据照样绿）----
case("P23 would_cycle 永远返回空（成环不再被拒）",
     sub("        back = self.edge_path(to_node, from_node)\n"
         "        return ([from_node] + back) if back else []",
         "        return []  # 扰动：永不判成环"),
     ["B20"], static=False)
case("P24 can_connect 偷偷真建边（预校验变成有副作用）",
     sub("        self._check_connect(from_node, from_port, to_node, to_port)\n"
         "        return True",
         "        # 扰动：预校验改成真建边\n"
         "        self.connect_data(from_node, from_port, to_node, to_port)\n"
         "        return True"),
     ["B21"], static=False)

# ---- Wave D #7：占位语义显式化 ----
case("P25 stub 产出不标占位（placeholder=True 删掉）",
     sub("                placeholder=True,\n", ""),
     ["A27"])
case("P26 _wrap 进入执行前不重置占位标记",
     sub("            node.placeholder = False          # 每次重跑都重算，不沿用上一轮结论\n",
         ""),
     ["A28"])
case("P27 跑完不再按产出重算节点占位标记",
     sub('            node.placeholder = any(\n'
         '                getattr(a, "placeholder", False)\n'
         '                for a in node.out_assets.values() if isinstance(a, AssetRef))',
         "            node.placeholder = False  # 扰动：不按产出重算"),
     ["A28"], static=False)
case("P28 AssetRef 不再序列化 placeholder（导出/导入丢占位标记）",
     sub('            "placeholder": self.placeholder,\n', ""),
     ["A29"])
case("P29 asset_registrations 不再报 placeholder",
     sub('                        "placeholder": bool(a.placeholder),\n', ""),
     ["A30"])
case("P30 端口序列化不写 multi（退回 v1 行为）",
     sub('return {"type": self.port_type, "multi": bool(self.multi)}',
         'return {"type": self.port_type}  # 扰动：不写 multi'),
     ["A31"])
case("P31 port_from_spec 忽略 multi（往返丢标记）",
     sub('        multi = bool(spec.get("multi", False))',
         "        multi = False  # 扰动：忽略 multi"),
     ["B24"], static=False)

# ---- 反向：没坏就不许红（防判据过宽）----
print("\n=== 反向：原样通过时不许有任何红项 ===")
_red0, _out0 = run_judge(SRC, "baseline")
if _red0:
    print("  [FAIL] 未变异的源码居然红了：%s" % sorted(_red0))
    FAIL_N += 1
    FAILED_CASES.append("baseline")
else:
    print("  [OK  ] 未变异源码全绿（%d 项静态判据）"
          % len(re.findall(r"\[OK  \] A", _out0)))
    PASS_N += 1

print("\nPASS=%d FAIL=%d" % (PASS_N, FAIL_N))
if FAILED_CASES:
    print("失效 case：")
    for c in FAILED_CASES:
        print("  -", c)
    sys.exit(1)
print("=== PERTURB_CANVAS_MODEL_OK ===")
