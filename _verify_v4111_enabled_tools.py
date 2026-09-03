# -*- coding: utf-8 -*-
"""v4.111 enabled_tools 工具白名单 —— 回归验证（真实调用 + 源码结构双保险）

为什么必须有「真实调用」段：只查源码里有没有某个函数名，会漏掉
「配置改了但实际没生效」这类问题（本项目真踩过：过滤函数读磁盘文件、
调用方改的是内存里的 cfg，结果白名单配了却不生效，源码断言全绿照样通过）。

六段：
  A 源码结构（config.py）
  B 真实调用（空/未配置/指定/未知名/MCP 五种情况，落盘验证）
  C 行为零变化回归（默认配置下工具数与 v4.110 一致 = 68）
  D tool_manager_ui 源码结构（含防自锁那条）
  E tool_manager_ui 真实调用（无头起窗口，预设档位 + 重开不丢工具）
  F ui.py 托盘入口

防假通过：取不到源码的方法一律进 _MISSING 并判 FAIL，
          绝不让"空串让 `x not in ""` 恒真"偷跑。
"""
import ast
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
_FAIL = []
_MISSING = []
_OK = 0


def chk(name, cond, detail=""):
    global _OK
    if cond:
        _OK += 1
        print("  PASS  %s" % name)
    else:
        _FAIL.append(name)
        print("  FAIL  %s   %s" % (name, detail))


def read_src(path):
    """读源码。⚠ 必须用 utf-8-sig：项目里 config.py / ui.py 带 BOM，
    用 utf-8 读会把 U+FEFF 一起带进来，ast.parse 直接抛 SyntaxError。"""
    with open(path, "r", encoding="utf-8-sig") as f:
        return f.read()


def src_of(path, funcname):
    """取某个函数的源码文本；取不到返回空串并登记 _MISSING。"""
    try:
        text = read_src(path)
        tree = ast.parse(text)
    except Exception:
        _MISSING.append(funcname)
        return ""
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) \
                and node.name == funcname:
            seg = ast.get_source_segment(text, node)
            if seg:
                return seg
    _MISSING.append(funcname)
    return ""


CONFIG_PY = os.path.join(HERE, "config.py")
TM_PY = os.path.join(HERE, "tool_manager_ui.py")
UI_PY = os.path.join(HERE, "ui.py")


def section(t):
    print()
    print("-" * 68)
    print(t)
    print("-" * 68)


def _run(pristine_bytes):
    # ============================================================ A 源码结构
    section("A  config.py 源码结构")
    cfg_src = read_src(CONFIG_PY)
    tree = ast.parse(cfg_src)
    func_names = [n.name for n in ast.walk(tree)
                  if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]

    chk("A1 _load_enabled_tools 函数存在", "_load_enabled_tools" in func_names)
    chk("A2 _filter_enabled_tools 函数存在", "_filter_enabled_tools" in func_names)

    f_src = src_of(CONFIG_PY, "_filter_enabled_tools")
    chk("A3 _filter_enabled_tools 源码非空", bool(f_src))
    chk("A4 优先用调用方传进来的 cfg（不是只读磁盘）",
        'cfg.get("enabled_tools")' in f_src,
        "读磁盘会和调用方手上的 cfg 脱节，表现为配了白名单却不生效")
    chk("A5 空清单 = 全开（向后兼容）", "if not enabled:" in f_src and "return tools" in f_src)
    chk("A6 匹配不到的名字会告警而非静默吞掉",
        "enabled_tools 里有" in f_src and "log.warning" in f_src)

    g_src = src_of(CONFIG_PY, "get_all_tools")
    chk("A7 get_all_tools 源码非空", bool(g_src))
    chk("A8 get_all_tools 末尾调用了过滤", "_filter_enabled_tools(tools, cfg)" in g_src)
    chk("A9 过滤在 MCP 合并之后（MCP 工具也受控）",
        g_src.index("tools.extend(client.tools)") < g_src.index("_filter_enabled_tools"),
        "顺序反了的话 MCP 工具不受白名单控制")
    chk("A10 一致性自检仍在过滤之前（不因关工具而漏检）",
        g_src.index("工具一致性") < g_src.index("_filter_enabled_tools"))

    # DEFAULT_CONFIG 含 enabled_tools
    found = False
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            for t in node.targets:
                if isinstance(t, ast.Name) and t.id == "DEFAULT_CONFIG":
                    for k in node.value.keys:
                        if getattr(k, "value", None) == "enabled_tools":
                            found = True
    chk("A11 DEFAULT_CONFIG 含 enabled_tools 键", found)

    # ============================================================ B 真实调用
    section("B  真实调用（五种情况）")
    sys.path.insert(0, HERE)
    import config
    cfg_path = os.path.join(os.path.expanduser("~"), "Documents",
                            "小臭玩AI", "config.json")
    # B 段有一半走「读磁盘文件」的兜底路径，磁盘状态必须已知，否则结果随机。
    # 先归一化成"全开"再跑（外层 main() 已做整文件备份，跑完必还原）。
    with open(cfg_path, "r", encoding="utf-8-sig") as f:
        real_cfg = json.load(f)
    real_cfg["enabled_tools"] = []
    with open(cfg_path, "w", encoding="utf-8") as f:
        json.dump(real_cfg, f, ensure_ascii=False, indent=2)
    backup = json.dumps(real_cfg.get("enabled_tools"), ensure_ascii=False)

    def names(c):
        return sorted((t.get("function") or {}).get("name") or t.get("name") or ""
                      for t in config.get_all_tools(c))

    try:
        probe = dict(real_cfg)
        probe.pop("enabled_tools", None)
        n_unset = len(names(probe))
        probe["enabled_tools"] = []
        n_empty = len(names(probe))
        probe["enabled_tools"] = ["web_search", "read_file", "run_command"]
        n_three = names(probe)
        probe["enabled_tools"] = ["web_search", "no_such_tool_xyz_123"]
        n_unknown = names(probe)
        probe["enabled_tools"] = ["web_search"]
        n_one = names(probe)

        chk("B1 未配置键 → 全量（向后兼容）", n_unset == 68, "实际 %d" % n_unset)
        chk("B2 空数组 → 全量（行为零变化）", n_empty == 68, "实际 %d" % n_empty)
        chk("B3 指定 3 个 → 只剩这 3 个",
            n_three == ["read_file", "run_command", "web_search"], str(n_three))
        chk("B4 含不存在的名字 → 忽略且不抛异常", n_unknown == ["web_search"], str(n_unknown))
        chk("B5 指定 1 个 → 只剩 1 个", n_one == ["web_search"], str(n_one))

        class _FakeMCP:
            tools = [{"type": "function",
                      "function": {"name": "mcp_fake_tool", "description": "x"}}]
        old_clients = config.mcp_clients
        try:
            config.mcp_clients = [_FakeMCP()]
            probe["enabled_tools"] = ["run_command"]
            chk("B6 MCP 工具也被白名单过滤", names(probe) == ["run_command"], str(names(probe)))
            probe["enabled_tools"] = ["mcp_fake_tool"]
            chk("B7 白名单可只放行 MCP 工具", names(probe) == ["mcp_fake_tool"], str(names(probe)))
        finally:
            config.mcp_clients = old_clients
    finally:
        if backup is not None:
            real_cfg["enabled_tools"] = json.loads(backup)
        else:
            real_cfg.pop("enabled_tools", None)

    # ============================================================ C 零变化回归
    section("C  行为零变化回归（默认配置 = v4.110 原样）")
    with open(cfg_path, "r", encoding="utf-8-sig") as f:
        live = json.load(f)
    live_enabled = live.get("enabled_tools", []) or []
    chk("C1 当前真实 config.json 的 enabled_tools 为空（= 全开）",
        live_enabled == [], "当前值：%s" % (live_enabled[:5],))
    chk("C2 真实配置下工具数仍为 68", len(names(live)) == 68, "实际 %d" % len(names(live)))

    # ============================================================ D 管理器源码
    section("D  tool_manager_ui.py 源码结构")
    if not os.path.exists(TM_PY):
        chk("D0 tool_manager_ui.py 存在", False)
    else:
        t_src = read_src(TM_PY)
        chk("D1 存在 ToolManagerWindow 类", "class ToolManagerWindow" in t_src)
        chk("D2 存在 open_tool_manager 入口函数", "def open_tool_manager" in t_src)
        lt = src_of(TM_PY, "load_tools")
        chk("D3 load_tools 源码非空", bool(lt))
        chk("D4 ⚠ 取工具时清空白名单拿全量（防自锁）",
            'probe["enabled_tools"] = []' in lt,
            "否则被关掉的工具不会出现在列表里，永远开不回来")
        chk("D5 空清单 = 全开（与 config 同规则）",
            "if not self.enabled:" in t_src and "return True" in t_src)
        chk("D6 预设档位三档齐全",
            all(k in t_src for k in ("全开", "均衡", "精简")))
        chk("D7 统计栏会显示省了多少", "相对全量省" in t_src)
        chk("D8 关窗时全开会归一化成空数组",
            'self.cfg["enabled_tools"] = []' in src_of(TM_PY, "closeEvent"))
        chk("D9 口径差异有注释说明（调用次数 vs 轮次数）",
            "调用次数" in t_src and "轮次数" in t_src)

    # ============================================================ E 管理器真实调用
    section("E  tool_manager_ui 真实调用（无头）")
    try:
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        from PySide6.QtWidgets import QApplication
        app = QApplication.instance() or QApplication([])
        from tool_manager_ui import ToolManagerWindow

        cfg2 = dict(live)
        cfg2["enabled_tools"] = []
        w = ToolManagerWindow(cfg2)
        n_all = len(w.all_tools)
        chk("E1 无头起窗口成功且读到全量工具", n_all == 68, "实际 %d" % n_all)

        # 先关掉一批，再重开窗口，确认还能读回全量（防自锁的真实验证）
        cfg2["enabled_tools"] = ["web_search"]
        w2 = ToolManagerWindow(cfg2)
        chk("E2 白名单只剩 1 个时，管理器仍能列出全部 68 个",
            len(w2.all_tools) == 68, "实际 %d" % len(w2.all_tools))

        w3 = ToolManagerWindow(dict(live, enabled_tools=[]))
        w3._apply_preset("精简")
        n_lean = w3.tool_list.count()
        chk("E3 「精简」档位后列表仍显示全部行（不隐藏未启用项）",
            n_lean == 68, "实际 %d 行" % n_lean)
        chk("E4 「精简」确实缩小了注入体积",
            len(w3.enabled) < 68 and len(w3.enabled) > 0,
            "启用 %d 个" % len(w3.enabled))
        w3._apply_preset("全开")
        chk("E5 「全开」后 enabled 为空数组（= 全开语义）", w3.enabled == [], str(w3.enabled[:5]))
        stats = w3.stats_label.text()
        chk("E6 统计栏渲染出内容", "启用" in stats and "省" in stats, stats[:80])
        cfg2["enabled_tools"] = []
    except Exception as e:
        chk("E0 无头调用未抛异常", False, repr(e))

    # ============================================================ F 托盘入口
    section("F  ui.py 托盘入口")
    ui_src = read_src(UI_PY)
    chk("F1 托盘菜单加了「工具管理器」动作", "工具管理器" in ui_src)
    chk("F2 动作已 addAction 进菜单", "menu.addAction(tool_action)" in ui_src)
    tr = src_of(UI_PY, "_open_tool_manager")
    chk("F3 _open_tool_manager 源码非空", bool(tr))
    chk("F4 入口做了 try/except（开窗口失败不能拖垮托盘）",
        "try:" in tr and "except Exception" in tr)
    chk("F5 入口调用 open_tool_manager", "open_tool_manager(self.cfg)" in tr)

    # ============================================================ 汇总
    section("汇总")
    if _MISSING:
        print("  ⚠ 以下函数源码未能取得（判 FAIL，防止空串偷跑）：%s"
              % ", ".join(sorted(set(_MISSING))))
        for m in sorted(set(_MISSING)):
            chk("源码可读性[%s]" % m, False)
    print("  通过 %d 项，失败 %d 项" % (_OK, len(_FAIL)))
    if _FAIL:
        print()
        for f in _FAIL:
            print("    FAIL: %s" % f)
        return 1
    print()
    print("ALL_PASS")
    return 0


def main():
    """外层包装：整文件备份 → 跑 → 无条件还原。

    ⚠ 为什么必须包一层（v4.111 实踩）：E 段会真的起 ToolManagerWindow 并点
    「精简」预设档位，而预设会调 `config.save_config()` 直接**写真实 config.json**。
    不还原的话，大哥一开程序就只剩 26 个工具 —— 等于测试脚本把生产配置改了。
    这类"验证脚本副作用改了用户数据"的坑，本项目 v4.110 已在路由日志上踩过一次。
    """
    import shutil
    cfg_path = os.path.join(os.path.expanduser("~"), "Documents",
                            "小臭玩AI", "config.json")
    bak = cfg_path + ".v4111_verify_bak"
    have = os.path.exists(cfg_path)
    if have:
        shutil.copy2(cfg_path, bak)
        with open(cfg_path, "rb") as f:
            pristine = f.read()
    else:
        pristine = b""
    try:
        return _run(pristine)
    finally:
        if have and os.path.exists(bak):
            shutil.copy2(bak, cfg_path)
            os.remove(bak)
            print()
            print("（已还原真实 config.json，enabled_tools 保持测试前的值）")


if __name__ == "__main__":
    raise SystemExit(main())
