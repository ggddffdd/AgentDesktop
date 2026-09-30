"""批 4（P1-8/10/11 健壮性）回归探针（v4.186.0 审查修复）。"""
import io
import os
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

_p = _f = 0


def check(name, cond, detail=""):
    global _p, _f
    if cond:
        _p += 1
        print("  PASS %s %s" % (name, ("-> " + str(detail)[:70]) if detail else ""))
    else:
        _f += 1
        print("  FAIL %s %s" % (name, ("-> " + str(detail)[:70]) if detail else ""))


def has_stmt(path, stmt):
    with io.open(os.path.join(ROOT, path), "r", encoding="utf-8-sig") as f:
        return any(l.strip() == stmt for l in f)


def main():
    print("=== [A] P1-10: build_safe 消费 _sync_to_dist 返回值 ===")
    check("同步失败退出码 2 语句",
          has_stmt("build_safe.py", "print('BUILD_EXIT=2')"))
    check("同步成功才打 BUILD_EXIT=0",
          has_stmt("build_safe.py", "if _synced:"))
    src = io.open(os.path.join(ROOT, "build_safe.py"), encoding="utf-8-sig").read()
    _real = [l for l in src.splitlines()
             if l.strip().startswith("print") and "BUILD_EXIT=0" in l]
    check("无条件 BUILD_EXIT=0 已消灭（仅剩 if _synced 内 1 处）",
          len(_real) == 1, _real)

    print("=== [B] P1-11: run_all 空输出套件判 EMPTY ===")
    # 造一个 rc=0 但无 PASS=/FAIL= 输出的假套件，直接调 run_one
    sys.path.insert(0, HERE)
    import run_all as ra
    with tempfile.TemporaryDirectory() as td:
        empty = os.path.join(td, "test_fake_empty.py")
        with io.open(empty, "w", encoding="utf-8") as f:
            f.write("print('nothing')\n")  # rc=0、无统计输出 → 旧逻辑假绿
        name, np_, nf, status, secs, out = ra.run_one(type("P", (), {"name": "test_fake_empty.py"}) and __import__("pathlib").Path(empty))
        check("空输出套件判 EMPTY（修复前 ok 假绿）", status == "EMPTY", status)
        # 对照：正常输出 PASS=1 FAIL=0 判 ok
        good = os.path.join(td, "test_fake_good.py")
        with io.open(good, "w", encoding="utf-8") as f:
            f.write("print('PASS=1 FAIL=0')\n")
        name, np_, nf, status, secs, out = ra.run_one(__import__("pathlib").Path(good))
        check("正常套件仍判 ok", status == "ok" and np_ == 1, "%s/%d" % (status, np_))
        # 对照：输出 PASS=0 FAIL=2 判 FAILED
        bad = os.path.join(td, "test_fake_bad.py")
        with io.open(bad, "w", encoding="utf-8") as f:
            f.write("print('PASS=0 FAIL=2')\nimport sys; sys.exit(1)\n")
        name, np_, nf, status, secs, out = ra.run_one(__import__("pathlib").Path(bad))
        check("失败套件仍判 FAILED", status == "FAILED" and nf == 2, "%s/%d" % (status, nf))

    print("=== [C] P1-11: 套件基线清单（MISSING 检测）===")
    ml = ra.MANIFEST
    check("清单常量定义存在", str(ml).endswith(".suite_manifest.txt"))
    # 行为验证：模拟基线里有而磁盘上没有的套件
    have = {s.name for s in ra.discover()}
    fake_baseline = sorted(list(have) + ["test_ghost_deleted.py"])
    ml.write_text("\n".join(fake_baseline) + "\n", encoding="utf-8")
    try:
        baseline = ra.load_manifest()
        missing = [n for n in baseline if n not in have]
        check("幽灵套件被识别为 MISSING", missing == ["test_ghost_deleted.py"], missing)
        # 真实套件全量在基线内（无漏报）
        check("真实存在的套件不误报", all(n in have for n in baseline if n != "test_ghost_deleted.py"))
    finally:
        # 恢复为真实基线
        ra.save_manifest(sorted(have))

    print("=== [D] P1-11: main.py 单实例锁（源码契约）===")
    check("QLockFile 锁存在", has_stmt("main.py", "if not _single_lock.tryLock(0):"))
    check("锁保活挂 app", has_stmt("main.py", "app._single_instance_lock = _single_lock"))
    check("锁故障降级不挡启动", "降级为不加锁启动" in io.open(os.path.join(ROOT, "main.py"), encoding="utf-8-sig").read())
    # QLockFile 行为验证：同进程内二次 tryLock 必失败（模拟双开）
    from PySide6.QtCore import QLockFile
    with tempfile.TemporaryDirectory() as td:
        lk = QLockFile(os.path.join(td, "probe.lock"))
        check("首次加锁成功", lk.tryLock(0))
        lk2 = QLockFile(os.path.join(td, "probe.lock"))
        check("同锁二次加锁失败（双开被拒）", not lk2.tryLock(0))
        del lk, lk2

    print("=== [E] P1-8: 数字人停止接线（源码契约）===")
    dsrc = io.open(os.path.join(ROOT, "digital_twin_panel.py"), encoding="utf-8-sig").read()
    check("TwinGenThread 持有 CancellationToken",
          "CancellationToken(name=\"twin_gen\")" in dsrc)
    check("cancel() 双通道（标志+令牌）",
          "self._ct.cancel(reason=\"user_stop\"" in dsrc)
    check("_gen_one 传 cancel_token 给 tool_video_gen",
          "cancel_token=self._ct)" in dsrc)
    check("取消异常优雅兜底（不报异常吓人）",
          "已停止（用户请求），已完成段保留" in dsrc)
    check("停止按钮存在并接 _twin_stop",
          "stop_btn.clicked.connect(lambda: _twin_stop(app))" in dsrc)
    check("生成中点亮/结束复位",
          "_sb.setEnabled(True)" in dsrc and "_sb.setEnabled(False)" in dsrc)
    check("停止按钮初始禁用", "stop_btn.setEnabled(False)  # 仅生成中可用" in dsrc)

    print()
    print("汇总：PASS=%d FAIL=%d" % (_p, _f))
    return 0 if _f == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
