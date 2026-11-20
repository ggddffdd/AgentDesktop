# -*- coding: utf-8 -*-
"""v4.305.0 第二批小改的确定性纯判据（无第三方依赖，可独立测试/扰动）。

覆盖第三方代码审查的三个 P2：
  F-04 技能启用判定：归一化必须真实生效（不能被吞错的 try 掩盖成 strip）。
  F-05 run_python 产物快照根：必须与子进程执行 cwd 同根，否则相对路径产物抓不到。
  F-08 eventFilter：同一类里只能有一个定义（后定义会静默覆盖前一个）。

这些函数只描述"正确口径"，不含任何运行时 I/O；业务模块只负责接线。
"""

import ast


def skill_enabled_decision(name, enabled, disabled_all, normalize):
    """F-04：技能是否启用的唯一决策口径。

    normalize 由调用方注入（skill_loader.normalize_skill_name），保证与本项目
    技能清单一处口径。顺序：全禁用 → False；空名 → False；白名单空 → 全放行；
    否则按归一化后的名字比对。任何一步都不吞掉 normalize 的异常（那是真 bug）。
    """
    if disabled_all:
        return False
    n = normalize(name or "")
    if not n:
        return False
    if not enabled:
        return True
    keep = {normalize(x) for x in enabled}
    return n in keep


def artifact_snapshot_root(run_cwd, script_dir):
    """F-05：返回产物快照应使用的根目录。

    子进程以 cwd=run_cwd 执行；模型代码里 open("out.xlsx","w") 这类相对路径
    产物落在 run_cwd，而非脚本所在的 script_dir。若快照 script_dir 则抓不到
    相对路径产物 → 交付物漏报。故快照根必须恒等于 run_cwd。

    script_dir 保留在签名里仅为显式表达"它不作为快照根"，避免将来又被误用。
    """
    return run_cwd


def count_event_filter_defs(source_text, class_name):
    """F-08：统计指定类里 eventFilter 方法定义的个数。

    Python 类体里后定义的同名方法会静默覆盖前一个；若出现 2 个，前一个的
    过滤逻辑（如输入框焦点联动）永远不执行。返回个数，正常应恰好为 1。
    """
    tree = ast.parse(source_text)
    for node in tree.body:
        if isinstance(node, ast.ClassDef) and node.name == class_name:
            return sum(
                1 for item in node.body
                if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef))
                and item.name == "eventFilter"
            )
    return -1  # 类不存在 → 判据无法成立，调用方按红处理


def normalize_skill_key(name, normalize):
    """F-04 辅助：对外暴露归一化结果，便于判据直接验证口径（不吞异常）。"""
    return normalize(name or "")
