# -*- coding: utf-8 -*-
"""task_status 状态总线回归测试

纯标准库，无 Qt 依赖：
    python tests/test_task_status.py
退出码 0=全过，1=有失败。

覆盖：
- 状态流转 received → running → done / failed
- 失败原因脱敏（sk- 密钥 / data URI / 长 base64 / URL 内 key）与截断
- 已完成环形缓冲上限、快照排序
- 多任务并发不串台、未知 id 安全返回
- 订阅通知触发、订阅者异常不影响生产者
- 重试钩子（无钩子 False / 钩子返回 True 则清除该条失败）
- track 上下文管理器（正常记 done、异常记 failed 并继续上抛）
- 多线程并发登记不丢不错
"""
import sys
import threading
from pathlib import Path

HERE = Path(__file__).resolve().parent.parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

import task_status as ts

PASS = 0
FAIL = 0


def check(name, cond, extra=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  [PASS] {name}")
    else:
        FAIL += 1
        print(f"  [FAIL] {name}  {extra}")


def test_lifecycle():
    print("== 状态流转 ==")
    r = ts.TaskRegistry()

    tid = r.begin("model", "对话回复")
    snap = r.snapshot()
    check("begin 后进入 active", snap["active_count"] == 1, str(snap["active_count"]))
    check("begin 初始状态为已接收", snap["active"][0]["state"] == ts.STATE_RECEIVED,
          snap["active"][0]["state"])
    check("状态中文标签可用", snap["active"][0]["state_label"] == "已接收",
          snap["active"][0]["state_label"])
    check("类别中文标签可用", snap["active"][0]["kind_label"] == "模型调用",
          snap["active"][0]["kind_label"])

    check("progress 生效", r.progress(tid, "首字节已到达") is True)
    snap = r.snapshot()
    check("progress 后状态为处理中", snap["active"][0]["state"] == ts.STATE_RUNNING,
          snap["active"][0]["state"])
    check("progress 附带阶段说明", snap["active"][0]["detail"] == "首字节已到达",
          snap["active"][0]["detail"])

    check("done 生效", r.done(tid) is True)
    snap = r.snapshot()
    check("done 后移出 active", snap["active_count"] == 0, str(snap["active_count"]))
    check("done 后进入 recent", len(snap["recent"]) == 1, str(len(snap["recent"])))
    check("done 状态正确", snap["recent"][0]["state"] == ts.STATE_DONE,
          snap["recent"][0]["state"])

    # 未知 id 安全返回，不抛
    check("未知 id progress 返回 False", r.progress("nope") is False)
    check("未知 id done 返回 False", r.done("nope") is False)
    check("未知 id fail 返回 False", r.fail("nope", "x") is False)


def test_failed_and_retryable():
    print("== 失败与可重试 ==")
    r = ts.TaskRegistry()
    tid = r.begin("video", "生成分镜 3")
    check("fail 生效", r.fail(tid, "Agnes 超时", retryable=True) is True)

    snap = r.snapshot()
    rec = snap["recent"][0]
    check("失败状态正确", rec["state"] == ts.STATE_FAILED, rec["state"])
    check("失败原因被保留", rec["detail"] == "Agnes 超时", rec["detail"])
    check("可重试标记为真", rec["retryable"] is True, str(rec["retryable"]))
    check("失败计入 failed_count", snap["failed_count"] == 1, str(snap["failed_count"]))

    # 无重试钩子 → 返回 False（不误报成功）
    check("无重试钩子时 retry 返回 False", r.retry(tid) is False)

    got = {}

    def hook(task_dict):
        got["id"] = task_dict["id"]
        return True

    r.set_retry_hook(hook)
    check("有钩子时 retry 返回 True", r.retry(tid) is True)
    check("钩子收到正确任务", got.get("id") == tid, str(got.get("id")))
    check("重试成功后该条已清除", len(r.snapshot()["recent"]) == 0,
          str(len(r.snapshot()["recent"])))

    # 钩子返回 False 时不应清除
    tid2 = r.begin("file", "解析大文档")
    r.fail(tid2, "编码不支持")
    r.set_retry_hook(lambda d: False)
    check("钩子返回 False 时 retry 返回 False", r.retry(tid2) is False)
    check("钩子失败后该条仍保留", len(r.snapshot()["recent"]) == 1,
          str(len(r.snapshot()["recent"])))


def test_redaction_and_truncation():
    print("== 脱敏与截断 ==")
    r = ts.TaskRegistry()

    t1 = r.begin("model", "带密钥的失败")
    r.fail(t1, "请求失败 sk-ABCDEFGHIJKLMNOPQRSTUVWXYZ")
    d = r.snapshot()["recent"][0]["detail"]
    check("明文 sk- 密钥被脱敏", "sk-ABCDEFGHIJKLMNOPQRSTUVWXYZ" not in d, d)

    t2 = r.begin("image", "带 data URI 的失败")
    r.fail(t2, "bad image data:image/png;base64," + "A" * 200)
    d2 = r.snapshot()["recent"][0]["detail"]
    check("data URI 被脱敏", "data:image/png" not in d2, d2[:60])

    t3 = r.begin("browser", "URL 带 token")
    r.fail(t3, "GET http://x.com/a?token=SECRETVALUE123&b=1 失败")
    d3 = r.snapshot()["recent"][0]["detail"]
    check("URL 内 token 被脱敏", "SECRETVALUE123" not in d3, d3)

    t4 = r.begin("file", "超长原因")
    r.fail(t4, "错" * 500)
    d4 = r.snapshot()["recent"][0]["detail"]
    check("超长原因被截断", len(d4) <= ts.DETAIL_MAX + 1, str(len(d4)))

    t5 = r.begin("model", "换行原因")
    r.fail(t5, "第一行\n第二行")
    d5 = r.snapshot()["recent"][0]["detail"]
    check("换行被压平为单行", "\n" not in d5, repr(d5))


def test_ring_buffer_and_order():
    print("== 环形缓冲与排序 ==")
    r = ts.TaskRegistry(keep_finished=2)
    for i in range(4):
        t = r.begin("model", f"任务{i}")
        r.done(t)
    snap = r.snapshot()
    check("已完成条数受上限约束", len(snap["recent"]) == 2, str(len(snap["recent"])))
    check("保留的是最新两条", snap["recent"][0]["label"] == "任务3",
          snap["recent"][0]["label"])
    check("recent 按最新在前", snap["recent"][1]["label"] == "任务2",
          snap["recent"][1]["label"])

    # active 排序：running 排在 received 之前
    a = r.begin("model", "还没开始")
    b = r.begin("video", "已经在跑")
    r.progress(b)
    active = r.snapshot()["active"]
    check("running 排在 received 之前", active[0]["state"] == ts.STATE_RUNNING,
          str([x["state"] for x in active]))

    check("clear_finished 清空历史", (r.clear_finished() or
          len(r.snapshot()["recent"]) == 0), "未清空")
    check("clear_finished 不影响 active", r.snapshot()["active_count"] == 2,
          str(r.snapshot()["active_count"]))


def test_multi_task_isolation():
    print("== 多任务不串台 ==")
    r = ts.TaskRegistry()
    t1 = r.begin("model", "任务一")
    t2 = r.begin("video", "任务二")
    r.progress(t1, "第一步")
    r.progress(t2, "另一个第一步")
    r.done(t1)

    snap = r.snapshot()
    check("完成一个后仍有 1 个活跃", snap["active_count"] == 1, str(snap["active_count"]))
    check("活跃的是任务二", snap["active"][0]["label"] == "任务二", snap["active"][0]["label"])
    check("任务二状态未被任务一影响",
          snap["active"][0]["state"] == ts.STATE_RUNNING, snap["active"][0]["state"])
    check("任务二阶段说明是自己的",
          snap["active"][0]["detail"] == "另一个第一步", snap["active"][0]["detail"])
    check("已完成的是任务一",
          snap["recent"][0]["label"] == "任务一", snap["recent"][0]["label"])


def test_subscribe():
    print("== 订阅通知 ==")
    r = ts.TaskRegistry()
    seen = []
    fn = r.subscribe(lambda snap: seen.append(snap["active_count"]))
    check("subscribe 返回回调", callable(fn))

    t = r.begin("model", "订阅测试")
    r.progress(t)
    r.done(t)
    check("变更触发了通知", len(seen) >= 3, str(seen))

    # 重复订阅同一回调不应重复注册
    before = len(seen)
    r.subscribe(fn)
    t2 = r.begin("file", "再次")
    r.done(t2)
    check("重复订阅不重复注册", len(seen) - before <= 2, str(len(seen) - before))

    r.unsubscribe(fn)
    after = len(seen)
    t3 = r.begin("file", "退订后")
    r.done(t3)
    check("退订后不再收到通知", len(seen) == after, str(len(seen) - after))

    # 订阅者抛异常不得影响生产者
    r2 = ts.TaskRegistry()
    r2.subscribe(lambda snap: 1 / 0)
    try:
        t4 = r2.begin("model", "坏订阅者")
        r2.done(t4)
        check("订阅者异常不影响生产者", True)
    except Exception as e:
        check("订阅者异常不影响生产者", False, repr(e))


def test_track_context():
    print("== track 上下文管理器 ==")
    r = ts.TaskRegistry()
    orig = ts.registry
    ts.registry = r   # 让模块级便捷函数走本测试的 registry
    try:
        with ts.track("model", "正常任务") as tid:
            ts.progress(tid, "跑起来了")
        rec = r.snapshot()["recent"][0]
        check("正常退出记为完成", rec["state"] == ts.STATE_DONE, rec["state"])

        raised = False
        try:
            with ts.track("video", "会失败的任务"):
                raise TimeoutError("生成超时")
        except TimeoutError:
            raised = True
        check("异常继续上抛（不吞异常）", raised is True)
        rec2 = r.snapshot()["recent"][0]
        check("异常退出记为失败", rec2["state"] == ts.STATE_FAILED, rec2["state"])
        check("异常任务标为可重试", rec2["retryable"] is True, str(rec2["retryable"]))
        check("失败原因含异常类型", "TimeoutError" in rec2["detail"], rec2["detail"])
    finally:
        ts.registry = orig


def test_thread_safety():
    print("== 线程安全 ==")
    r = ts.TaskRegistry(keep_finished=200)
    errors = []

    def worker(idx):
        try:
            t = r.begin("model", f"并发{idx}")
            r.progress(t, "跑")
            r.done(t)
        except Exception as e:
            errors.append(repr(e))

    ths = [threading.Thread(target=worker, args=(i,)) for i in range(24)]
    for th in ths:
        th.start()
    for th in ths:
        th.join(timeout=20)

    check("并发无异常", not errors, str(errors[:3]))
    check("并发后无残留活跃任务", r.active_count() == 0, str(r.active_count()))
    check("并发结果全部记入历史", len(r.snapshot()["recent"]) == 24,
          str(len(r.snapshot()["recent"])))


def test_module_singleton():
    print("== 模块级单例 ==")
    ts.registry.reset()
    tid = ts.begin("browser", "抓取网页")
    check("模块级 begin 可用", isinstance(tid, str) and len(tid) == 32, str(tid))
    check("模块级 snapshot 可用", ts.snapshot()["active_count"] == 1,
          str(ts.snapshot()["active_count"]))
    ts.done(tid)
    check("模块级 done 可用", ts.snapshot()["active_count"] == 0,
          str(ts.snapshot()["active_count"]))
    ts.registry.reset()


if __name__ == "__main__":
    try:
        test_lifecycle()
        test_failed_and_retryable()
        test_redaction_and_truncation()
        test_ring_buffer_and_order()
        test_multi_task_isolation()
        test_subscribe()
        test_track_context()
        test_thread_safety()
        test_module_singleton()
    except Exception:
        import traceback
        FAIL += 1
        traceback.print_exc()
    print(f"\n结果：PASS={PASS}  FAIL={FAIL}")
    sys.exit(1 if FAIL else 0)
