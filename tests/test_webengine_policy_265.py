# -*- coding: utf-8 -*-
"""v4.265.0：WebEngine 导航白名单、仅冻结版关沙箱、critical 扰动抽样入口。

不导入 PySide6。页面类只做源码接线断言；策略本身是纯函数。
"""
import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import webengine_policy as wp  # noqa: E402

_p = _f = 0


def check(name, cond, detail=""):
    global _p, _f
    if cond:
        _p += 1
        print(f"  [PASS] {name}")
    else:
        _f += 1
        print(f"  [FAIL] {name}" + (f" —— {detail}" if detail else ""))


def _read(name):
    with open(os.path.join(ROOT, name), encoding="utf-8") as f:
        return f.read()


def main():
    print("=== 1) 沙箱只在冻结版关 ===")
    env = {}
    wrote = wp.apply_sandbox_env(env, frozen=False)
    check("开发版不写 QTWEBENGINE_DISABLE_SANDBOX",
          wrote is False and wp.SANDBOX_ENV not in env, str(env))
    env = {}
    wrote = wp.apply_sandbox_env(env, frozen=True)
    check("冻结版写入 1", wrote is True and env.get(wp.SANDBOX_ENV) == "1", str(env))
    env = {wp.SANDBOX_ENV: "0"}
    wrote = wp.apply_sandbox_env(env, frozen=True)
    check("用户已设置则不覆盖", wrote is False and env[wp.SANDBOX_ENV] == "0")

    main_src = _read("main.py")
    check("main 不再无条件 setdefault 关沙箱",
          'setdefault("QTWEBENGINE_DISABLE_SANDBOX"' not in main_src
          and "setdefault('QTWEBENGINE_DISABLE_SANDBOX'" not in main_src)
    check("main 走 apply_sandbox_env", "apply_sandbox_env" in main_src)

    print("\n=== 2) 导航白名单 ===")
    check("about:blank 允许", wp.navigation_allowed("about:blank"))
    check("空 URL 允许", wp.navigation_allowed(""))
    check("localres 允许", wp.navigation_allowed("localres:///C:/a.png"))
    check("app 业务链接允许（页面层会拦截）",
          wp.navigation_allowed("app://regen/1"))
    check("javascript 拒绝", not wp.navigation_allowed("javascript:alert(1)"))
    check("data 拒绝", not wp.navigation_allowed("data:text/html,hi"))
    check("未列入的 https 拒绝",
          not wp.navigation_allowed("https://evil.example/a", allow_hosts=[]))
    check("显式域允许（大小写不敏感）",
          wp.navigation_allowed("https://CDN.Example/a", allow_hosts=["cdn.example"]))
    check("子域不算显式域",
          not wp.navigation_allowed(
              "https://a.cdn.example/x", allow_hosts=["cdn.example"]))

    d = tempfile.mkdtemp(prefix="xc_we_")
    inside = os.path.join(d, "a.txt")
    with open(inside, "w", encoding="utf-8") as f:
        f.write("x")
    from urllib.request import pathname2url
    url_in = "file://" + pathname2url(os.path.abspath(inside))
    url_out = "file://" + pathname2url(os.path.abspath(d + "_other") + "/b.txt")
    check("工作区内 file 允许",
          wp.navigation_allowed(url_in, workspace_roots=[d]), url_in)
    check("工作区外 file 拒绝",
          not wp.navigation_allowed(url_out, workspace_roots=[d]), url_out)
    check("兄弟目录前缀不算工作区",
          not wp.navigation_allowed(
              "file://" + pathname2url(os.path.abspath(d + "_backup/x.txt")),
              workspace_roots=[d]))
    check("没有工作区根时 file 拒绝",
          not wp.navigation_allowed(url_in, workspace_roots=[]))

    chat = _read("chat_web.py")
    check("聊天页调用 navigation_allowed", "navigation_allowed" in chat)
    check("白名单异常按拒绝", "按拒绝处理" in chat)

    print("\n=== 3) critical 扰动抽样入口 ===")
    run_src = _read(os.path.join("tests", "run_all.py"))
    check("run_all 登记 --perturb-critical",
          'add_argument("--perturb-critical"' in run_src)
    import tests.run_all as R
    names = list(R.PERTURB_CRITICAL)
    check("critical 抽样至少 8 个", len(names) >= 8, str(len(names)))
    check("含军团权限扰动", "_perturb_legion_perm_265.py" in names)
    check("含权限硬确认扰动", "_perturb_hard_confirm_bypass_221.py" in names)
    check("含路由扰动", "_perturb_system_prompt_routing_221.py" in names)
    missing = [n for n in names
               if not os.path.isfile(os.path.join(ROOT, "_perturb", n))]
    check("critical 名单上的脚本都在磁盘上", not missing, str(missing))
    wf = _read(os.path.join(".github", "workflows", "perturb-nightly.yml"))
    check("nightly 有 schedule", "schedule:" in wf and "cron:" in wf)
    check("nightly 有 workflow_dispatch", "workflow_dispatch:" in wf)
    check("nightly 会跑 --perturb-critical", "--perturb-critical" in wf)
    check("nightly 也能全量 --with-perturb", "--with-perturb" in wf)

    print(f"\n汇总：PASS={_p} FAIL={_f}")
    return 0 if _f == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
