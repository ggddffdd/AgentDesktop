# -*- coding: utf-8 -*-
"""真后端探针（probe_real_backend.py）—— v4.286.0「真后端 E2E 层」第一弹。

v4.287.0 修复记录：nightly 首跑（run 39092681499）image_gen 探针 401 撞红——
CI 空壳 config 下 provider=agnes（默认），`_agnes_creds` 的 key 兜底链
（model_profiles["Agnes"].api_key → 顶层 api_key）在 CI 里落到 PROBE_API_KEY
注入的 DeepSeek key，拿 DeepSeek key 打 Agnes 必 401；而 PROBE_AGNES_API_KEY
secret 注入的顶层 agnes_api_key 字段原本无消费者，配了也白配。修复 =
`_agnes_creds` key 链补中间档 `cfg.get("agnes_api_key")`（tools.py），
本探针 env_map 的 PROBE_AGNES_API_KEY → agnes_api_key 映射由此真正生效。

背景（彻查「判断不准/工具调用不准」时挖出的测试盲区）
--------------------------------------------------
小臭的测试层长期只有三层：unit（monkeypatch 假 handler）/ 判据（行为断言）/
扰动（改坏必红）。**没有一层真的去调外部后端**。后果实证两次：
  · edge-tts 7.2.7 被微软 Sec-MS-GEC 403 拒——mock 全绿、日志干净、用户没声音；
  · Agnes 端点迁移（apihub → 国内站）——旧 key 打新域名 401，同样是发布后才发现。
共同形状：**昨天还好好的，今天悄悄坏了，而测试层一片绿**。

本探针就是补这第四层：对三个外部依赖 tool 各做一次低频真调，只验
「HTTP 通 + 返回结构对」，不验内容质量（内容质量是另一层的事）。

三个目标（为什么是这三个）
  · edge-tts  —— 免 key、微软公共端点，说挂就挂（403 实锤过），且是语音链唯一出口；
  · web_search —— brave/serper key 限额度，key 失效/欠费是常态，失效后 agent
                  「搜索」行为静默降级成猜；
  · image_gen —— Agnes 订阅 key + 端点双变量，模型清单/域名一变就整族红。

三种状态语义（与 probe_graphics 的 SKIP 纪律一致，SKIP 不冒充通过）
  · PASS —— 真调成功且结构校验过；
  · FAIL —— 前提齐（key/依赖都在）但真调失败 → **fail-closed 撞红**；
  · SKIP —— 环境前提缺（没配 key / 库没装）→ 输出醒目横幅，绝不算通过。

配置来源（双轨，本地优先）
  · cfg = config.load_config()（本地 config.json）；
  · env 兜底：PROBE_BRAVE_API_KEY / PROBE_SERPER_API_KEY / PROBE_API_KEY /
    PROBE_AGNES_API_KEY —— 供 CI nightly 用 repository secrets 注入，
    **只在探针内合并，不回写用户 config**。
"""
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

# 真调超时（秒）：web_search 快、image_gen 慢（订阅档实测 8~20s），给足不误杀。
SEARCH_TIMEOUT = 30
IMAGE_TIMEOUT = 90

PASS, FAIL, SKIP = "PASS", "FAIL", "SKIP"


def _probe_cfg():
    """探针专用 cfg：本地 config + env 兜底（只进探针，不回写用户配置）。"""
    import config
    cfg = dict(config.load_config() or {})
    env_map = {
        "PROBE_BRAVE_API_KEY": "brave_api_key",
        "PROBE_SERPER_API_KEY": "serper_api_key",
        "PROBE_API_KEY": "api_key",
        "PROBE_AGNES_API_KEY": "agnes_api_key",
    }
    for env, field in env_map.items():
        v = os.environ.get(env, "")
        if v and not cfg.get(field):
            cfg[field] = v
    return cfg


def _has_key(cfg, *fields):
    return any(str(cfg.get(f) or "").strip() for f in fields)


# ===================== 三个探针 =====================

def probe_edge_tts(cfg):
    """真调一次 edge-tts：文本 -> mp3，验文件存在且非空。

    SKIP：edge_tts 库不可用（依赖没装全）。
    FAIL：库在但合成失败 / 产物为空文件（403 类事故的形状：异常或 0 字节）。
    """
    try:
        import edge_tts  # noqa: F401  （探测依赖是否可用）
    except Exception as e:
        return (SKIP, "edge_tts 不可用：%s" % e)
    import voice
    tts = {"voice": cfg.get("tts_voice") or "zh-CN-XiaoxiaoNeural",
           "rate": "+0%", "volume": "+0%"}
    out = os.path.join(tempfile.gettempdir(), "probe_tts_286.mp3")
    try:
        path = voice.synthesize("探针测试。", tts, out)
    except Exception as e:
        # v4.283.1 教训：edge-tts 403 曾因 tts_failure_is_quota 降级成 WARNING，
        # 「日志干净但没声」。探针层任何异常都是 FAIL，不做额度豁免。
        return (FAIL, "synthesize 异常：%s: %s" % (type(e).__name__, e))
    if not path or not os.path.exists(path):
        return (FAIL, "产物不存在：%r" % (path,))
    size = os.path.getsize(path)
    if size < 2048:
        return (FAIL, "产物过小（%d 字节）——疑似空/坏文件（403 形状）" % size)
    return (PASS, "mp3 %d 字节" % size)


def probe_web_search(cfg):
    """真调一次 web_search：固定 query，验返回文本非空且不含明显错误串。

    SKIP：brave/serper key 都没配（本地 & env 兜底后）。
    FAIL：key 在但搜索失败/返回空（key 失效、欠费、provider 全挂的形状）。
    """
    if not _has_key(cfg, "brave_api_key", "serper_api_key"):
        return (SKIP, "未配置 brave_api_key / serper_api_key")
    import tools
    try:
        text = tools.tool_web_search(cfg, "昆明天气", app_dir=HERE)
    except Exception as e:
        return (FAIL, "tool_web_search 异常：%s: %s" % (type(e).__name__, e))
    text = str(text or "")
    if len(text.strip()) < 50:
        return (FAIL, "返回过短（%d 字符）——疑似搜索未真正执行" % len(text.strip()))
    bad_heads = ("未提供搜索词", "搜索失败", "所有搜索渠道")
    for h in bad_heads:
        if text.lstrip().startswith(h):
            return (FAIL, "返回错误前缀「%s」：%s" % (h, text[:120]))
    return (PASS, "返回 %d 字符" % len(text))


def probe_image_gen(cfg):
    """真调一次 image_gen：固定 prompt，验落盘文件存在且非空。

    SKIP：当前 provider 的 key 缺失（gateway/deepseek→api_key；agnes→agnes key）。
    FAIL：key 在但生成失败/返回错误串/文件缺失（端点迁移、模型下线的形状）。
    """
    provider = cfg.get("image_gen_provider", "gateway")
    if provider == "local_stability":
        # 本地 SD 服务：key 无意义，连通性即前提。
        import urllib.request
        try:
            urllib.request.urlopen("http://127.0.0.1:7860", timeout=5)
        except Exception:
            return (SKIP, "local_stability 服务不可达（127.0.0.1:7860）")
    elif provider == "agnes":
        import tools as _t
        _base, _key = _t._agnes_creds(cfg)
        if not _key:
            return (SKIP, "agnes provider 未配置 api_key")
    else:  # gateway / deepseek 走统一 api_key
        if not _has_key(cfg, "api_key"):
            return (SKIP, "provider=%s 未配置 api_key" % provider)
    import tools
    try:
        res = tools.tool_image_gen(cfg, HERE, "一只橘猫坐在窗台上")
    except Exception as e:
        return (FAIL, "tool_image_gen 异常：%s: %s" % (type(e).__name__, e))
    if isinstance(res, tuple) and len(res) == 3:
        rel, _kind, _name = res
        path = rel if os.path.isabs(str(rel)) else os.path.join(HERE, str(rel))
        if not os.path.exists(path):
            return (FAIL, "返回路径不存在：%r" % (rel,))
        size = os.path.getsize(path)
        if size < 1024:
            return (FAIL, "产物过小（%d 字节）" % size)
        return (PASS, "%s %d 字节" % (os.path.basename(path), size))
    return (FAIL, "返回非预期结构（期望 3 元组）：%s" % str(res)[:120])


PROBES = (
    ("edge_tts", probe_edge_tts),
    ("web_search", probe_web_search),
    ("image_gen", probe_image_gen),
)


def main():
    print("=" * 64)
    print("真后端探针 v4.288.5 —— 只验「通 + 结构」，不验内容质量")
    print("=" * 64)
    results = []
    for name, fn in PROBES:
        try:
            # v4.288.1：_probe_cfg() 纳入 try——load_config 崩过（CI runner
            # 无 ~/Documents/小臭玩AI/ 目录，FileNotFoundError），当时它在
            # try 外，一个配置崩溃直接裸 traceback 杀死整轮，三个探针一个
            # 都没跑。配置级问题 = 带明细的 FAIL，不是整轮消失。
            cfg = _probe_cfg()
            status, detail = fn(cfg)
        except Exception as e:
            # 探针自身崩了也算 FAIL（不许裸异常穿透成「看似没跑」）。
            status, detail = (FAIL, "探针自身异常：%s: %s" % (type(e).__name__, e))
        results.append((name, status, detail))
        print("  [%s] %-12s %s" % (status, name, detail))
    fails = [r for r in results if r[1] == FAIL]
    skips = [r for r in results if r[1] == SKIP]
    print("-" * 64)
    if fails:
        # fail-closed：真调失败必须红。这是 403/401 类事故唯一的自动拦截层。
        print("PASS=%d FAIL=%d SKIP=%d" % (
            len([r for r in results if r[1] == PASS]), len(fails), len(skips)))
        for name, _s, detail in fails:
            print("  FAIL 明细 %s: %s" % (name, detail))
        return 1
    if skips:
        # SKIP 不冒充通过：醒目横幅 + rc=0（环境缺前提不是代码的错）。
        print("  [SKIP 横幅] %d 个探针因环境前提缺失跳过（无 key / 库未装）——" % len(skips))
        print("              SKIP 不冒充通过；配好 key 的环境才会真调用。")
    print("PASS=%d FAIL=0 SKIP=%d" % (
        len([r for r in results if r[1] == PASS]), len(skips)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
