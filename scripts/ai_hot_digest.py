#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
AI 热点速递 · 聚合扫描 + LLM 摘要 + 微信推送文本
来源: HN / 量子位 / TechCrunch / The Verge / Ars Technica / OpenAI /
      Simon Willison / Import AI / GitHub 新项目 / MCP 官方注册表 /
      ClawHub 新技能 / OpenRouter 新模型 / wmpeng codingplan
用法:
  python3 ai_hot_digest.py                # 扫描 26h 窗口，生成 state/latest_message.txt
  python3 ai_hot_digest.py --print        # 同时打印消息
  python3 ai_hot_digest.py --hours 48 --no-llm
"""
import os, sys, json, re, time, calendar, argparse, subprocess
from datetime import datetime, timedelta
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import quote
import requests

try:
    import feedparser
except ImportError:
    feedparser = None

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE) if os.path.basename(HERE) == "scripts" else HERE
STATE_DIR = os.path.join(ROOT, "state")
OUT_DIR = os.path.join(ROOT, "output")
os.makedirs(STATE_DIR, exist_ok=True)
os.makedirs(OUT_DIR, exist_ok=True)

STATE_FILE = os.path.join(STATE_DIR, "seen.json")
MSG_FILE = os.path.join(STATE_DIR, "latest_message.txt")

UA = "Mozilla/5.0 (compatible; ai-hot-digest/0.1)"
LLM_BASE = os.environ.get("LLM_BASE_URL", "https://token-plan.cn-beijing.maas.aliyuncs.com/apps/anthropic")
LLM_KEY = os.environ.get("LLM_API_KEY", "")  # 本地配置：请在环境变量中设置 LLM_API_KEY
LLM_MODEL = os.environ.get("LLM_MODEL", "deepseek-v4.1-flash")

AI_KW = re.compile(r"(?i)\b(ai|llm|gpt|claude|openai|anthropic|gemini|agent|agents|agentic|mcp|model|models|inference|deepseek|qwen|kimi|minimax|llama|mistral|chatbot|neural|copilot|codex|sora|opus|sonnet|grok)\b")

FEEDS = [
    ("量子位", "https://www.qbitai.com/feed"),
    ("TechCrunch AI", "https://techcrunch.com/category/artificial-intelligence/feed/"),
    ("The Verge AI", "https://www.theverge.com/rss/ai-artificial-intelligence/index.xml"),
    ("Ars Technica AI", "https://arstechnica.com/ai/feed/"),
    ("OpenAI 官方", "https://openai.com/news/rss.xml"),
    ("Simon Willison", "https://simonwillison.net/atom/everything/"),
    ("Import AI", "https://jack-clark.net/feed/"),
]


def http_get(url, timeout=25, headers=None):
    h = {"User-Agent": UA}
    if headers:
        h.update(headers)
    for verify in (True, False):
        try:
            r = requests.get(url, headers=h, timeout=(10, timeout), verify=verify)
            if r.status_code < 400:
                return r
            return r
        except Exception as e:
            last = e
    return None


def norm_url(u):
    if not u:
        return ""
    u = u.split("#")[0]
    u = re.sub(r"[?&](utm_[^&]*|ref=[^&]*)", "", u)
    return u.rstrip("/")


def load_state():
    try:
        with open(STATE_FILE, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {"seen": {}, "mcp_seen": [], "codingplan_date": None}


def save_state(st):
    now = time.time()
    seen = {k: v for k, v in st.get("seen", {}).items() if now - float(v) < 30 * 86400}
    st["seen"] = seen
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(st, f, ensure_ascii=False, indent=1)


# ---------------- collectors ----------------

def c_feeds(window_h):
    items = []
    if not feedparser:
        return items
    cutoff = time.time() - window_h * 3600
    for name, url in FEEDS:
        r = http_get(url)
        if r is None:
            continue
        try:
            d = feedparser.parse(r.content)
        except Exception:
            continue
        for e in d.entries[:25]:
            ts = None
            for k in ("published_parsed", "updated_parsed"):
                if getattr(e, k, None):
                    ts = calendar.timegm(getattr(e, k))
                    break
            if ts and ts < cutoff:
                continue
            title = (e.get("title") or "").strip()
            link = norm_url(e.get("link") or "")
            if not title or not link:
                continue
            items.append({"src": name, "title": title[:150], "url": link,
                          "meta": "", "ts": ts or time.time()})
    # 每源最多 6 条
    per, out = {}, []
    for it in sorted(items, key=lambda x: -x["ts"]):
        n = per.get(it["src"], 0)
        if n < 6:
            out.append(it); per[it["src"]] = n + 1
    return out


def c_hn(window_h):
    items, seen = [], set()
    since = int(time.time() - window_h * 3600)
    for u in [
        f"https://hn.algolia.com/api/v1/search?tags=story&numericFilters=points%3E60,created_at_i%3E{since}&hitsPerPage=40",
        "https://hn.algolia.com/api/v1/search?tags=front_page&hitsPerPage=50",
    ]:
        r = http_get(u)
        if r is None:
            continue
        try:
            for h in r.json().get("hits", []):
                title = (h.get("title") or "").strip()
                link = norm_url(h.get("url") or f"https://news.ycombinator.com/item?id={h.get('objectID')}")
                if not title or link in seen or not AI_KW.search(title):
                    continue
                seen.add(link)
                items.append({"src": "Hacker News", "title": title[:150], "url": link,
                              "meta": f"{h.get('points',0)}分 · {h.get('num_comments',0)}评论",
                              "ts": h.get("created_at_i") or time.time()})
        except Exception:
            pass
    items.sort(key=lambda x: -x["ts"])
    return items[:10]


def _gh_token():
    """获取 GitHub token：优先 gh CLI，回退解析 hosts.yml（老版本 gh 无 token 子命令）"""
    try:
        r = subprocess.run(["gh", "auth", "token"], capture_output=True, text=True, timeout=15)
        if r.returncode == 0 and r.stdout.strip():
            return r.stdout.strip()
    except Exception:
        pass
    try:
        with open(os.path.expanduser("~/.config/gh/hosts.yml"), encoding="utf-8") as f:
            txt = f.read()
        m = re.search(r"oauth_token:\s*(\S+)", txt)
        if m:
            return m.group(1)
    except Exception:
        pass
    return ""


def c_github(window_days=7):
    items = []
    tok = _gh_token()
    h = {"Accept": "application/vnd.github+json"}
    if tok:
        h["Authorization"] = f"Bearer {tok}"
    d = (datetime.now() - timedelta(days=window_days)).strftime("%Y-%m-%d")
    url = f"https://api.github.com/search/repositories?q=created:%3E{d}+stars:%3E80&sort=stars&order=desc&per_page=25"
    r = http_get(url, headers=h)
    if r is None:
        return items
    try:
        for repo in r.json().get("items", []):
            name = repo["full_name"]
            desc = (repo.get("description") or "")
            if not (AI_KW.search(name) or AI_KW.search(desc)):
                continue
            items.append({"src": "GitHub 新项目", "title": f"{name} — {desc[:110]}",
                          "url": repo["html_url"],
                          "meta": f"★{repo['stargazers_count']} · {repo.get('language') or ''} · 创建于 {repo['created_at'][:10]}",
                          "ts": time.time()})
            if len(items) >= 10:
                break
    except Exception:
        pass
    return items


def c_mcp_registry(window_h=72):
    items = []
    st = load_state()
    known = set(st.get("mcp_seen", []))          # 存储为 name|updatedAt 键，更新也会触发提醒
    seen_keys = set()
    since = (datetime.utcnow() - timedelta(hours=window_h)).strftime("%Y-%m-%dT%H:%M:%SZ")
    cursor, pages = None, 0
    while pages < 3:
        u = f"https://registry.modelcontextprotocol.io/v0/servers?limit=100&updated_since={since}"
        if cursor:
            u += "&cursor=" + quote(cursor)
        r = http_get(u)
        if r is None:
            break
        try:
            d = r.json()
        except Exception:
            break
        for it in d.get("servers", []):
            s = it.get("server", {})
            name = s.get("name", "")
            meta = (it.get("_meta", {}) or {}).get("io.modelcontextprotocol.registry/official", {})
            if meta.get("status") != "active":
                continue
            ts = meta.get("updatedAt") or meta.get("publishedAt") or ""
            key = f"{name}|{ts[:16]}"
            if key in known or key in seen_keys:
                continue
            seen_keys.add(key)
            try:
                t = datetime.fromisoformat(ts.replace("Z", "+00:00")).timestamp()
            except Exception:
                t = time.time()
            title = s.get("title") or name
            items.append({"src": "MCP 新收录", "title": f"{title}（{name}）",
                          "url": f"https://registry.modelcontextprotocol.io/v0/servers?search={quote(name)}",
                          "meta": (s.get("description") or "")[:100], "ts": t})
        cursor = (d.get("metadata") or {}).get("nextCursor")
        pages += 1
        if not cursor:
            break
    items.sort(key=lambda x: -x["ts"])
    items = items[:8]
    return items, sorted(seen_keys), None


def c_clawhub(window_h):
    items = []
    r = http_get("https://clawhub.ai/api/v1/skills?limit=40")
    if r is None:
        return items
    cutoff = time.time() - window_h * 3600
    try:
        for it in r.json().get("items", []):
            ts = (it.get("updatedAt") or 0) / 1000
            if ts < cutoff:
                continue
            items.append({"src": "ClawHub 新技能", "title": f"{it.get('displayName') or it.get('slug')}（{it.get('ownerHandle')}/{it.get('slug')}）",
                          "url": f"https://clawhub.ai/skills/{it.get('ownerHandle')}/{it.get('slug')}",
                          "meta": (it.get("summary") or "")[:100], "ts": ts})
            if len(items) >= 6:
                break
    except Exception:
        pass
    return items


def c_openrouter():
    items = []
    r = http_get("https://openrouter.ai/api/v1/models", timeout=40)
    if r is None:
        return items
    cutoff = time.time() - 3 * 86400
    try:
        models = r.json()["data"]
    except Exception:
        return items
    seen = set()
    for m in sorted(models, key=lambda x: -(x.get("created") or 0)):
        if ":batch" in m["id"] or m["id"] in seen:
            continue
        if (m.get("created") or 0) < cutoff:
            break
        seen.add(m["id"])
        p = m.get("pricing") or {}

        def p1m(v):
            try:
                return round(float(v) * 1e6, 3)
            except Exception:
                return None
        items.append({"src": "新模型上架", "title": f"{m['id']}（{m.get('name','')}）"[:130],
                      "url": f"https://openrouter.ai/{m['id']}",
                      "meta": f"输入 ${p1m(p.get('prompt'))}/M · 输出 ${p1m(p.get('completion'))}/M",
                      "ts": m.get("created")})
        if len(items) >= 5:
            break
    return items


def c_codingplan():
    r = http_get("https://cdn.jsdelivr.net/gh/wmpeng/codingplan@main/config.json")
    if r is None:
        return [], None
    try:
        upd = ((r.json().get("header") or {}).get("updateDate") or "").strip()
    except Exception:
        return [], None
    st = load_state()
    if upd and upd != st.get("codingplan_date"):
        return [{"src": "Coding Plan", "title": f"wmpeng/codingplan 数据更新：{upd}",
                 "url": "https://github.com/wmpeng/codingplan",
                 "meta": "国内主流平台套餐对比（智谱/Kimi/MiniMax/百炼/方舟/千帆…）", "ts": time.time()}], upd
    return [], upd


# ---------------- main scan ----------------

def collect(window_h):
    res, errs = {}, {}
    with ThreadPoolExecutor(max_workers=10) as ex:
        futs = {
            ex.submit(c_feeds, window_h): "feeds",
            ex.submit(c_hn, window_h): "hn",
            ex.submit(c_github): "github",
            ex.submit(c_clawhub, window_h): "clawhub",
            ex.submit(c_openrouter): "openrouter",
        }
        mcp_fut = ex.submit(c_mcp_registry)
        cp_fut = ex.submit(c_codingplan)
        for f, name in futs.items():
            try:
                res[name] = f.result()
            except Exception as e:
                errs[name] = str(e)[:120]
        try:
            res["mcp"], mcp_keys, mcp_cursor = mcp_fut.result()
        except Exception as e:
            res["mcp"], mcp_keys, mcp_cursor = [], [], None
            errs["mcp"] = str(e)[:120]
        try:
            res["plans"], cp_date = cp_fut.result()
        except Exception as e:
            res["plans"], cp_date = [], None
            errs["plans"] = str(e)[:120]
    return res, errs, (mcp_keys, cp_date, mcp_cursor)


def build_candidates(res, st):
    seen = st.get("seen", {})
    cands = []
    for key in ("feeds", "hn", "github", "mcp", "clawhub", "openrouter", "plans"):
        for it in res.get(key) or []:
            u = norm_url(it["url"])
            if not u or u in seen:
                continue
            cands.append(it)
    # 控制总量
    return cands[:60]


# ---------------- LLM ----------------

PROMPT_HEAD = """你是资深 AI 行业分析师。以下是过去窗口内从各渠道扫描的候选条目 JSON（字段：id/src/title/url/meta/ts）。请筛选出真正值得关注的，生成每晚「AI 热点速递」。

严格输出 JSON（不要 markdown 代码块包裹），结构：
{
 "overview": "2-3句中文总览，概括今天AI圈最重要的动向",
 "highlights": [{"id":<id>,"title_cn":"中文标题(专有名词保留英文)","note":"一句话点评≤45字"}],
 "sections": [{"key":"news|mcp|skills|models|plans","emoji":"🔥","title":"板块名","items":[{"id":<id>,"title_cn":"...","note":"≤45字"}]}],
 "closing": "一句轻松收尾≤30字"
}
规则：highlights 3-6条最重要；sections 按 key 分组（news=要闻, mcp=MCP生态, skills=Skills技能, models=模型动态, plans=套餐价格），空板块省略，每板块≤4条；总量≤14条；同一事件合并；只选真热点/新增/重要更新，丢弃噪音；全部中文。
候选项："""


def llm_digest(cands, attempts=2):
    payload = [{"id": i, "src": c["src"], "title": c["title"], "url": c["url"],
                "meta": c.get("meta", ""), "ts": c.get("ts")} for i, c in enumerate(cands)]
    body = {
        "model": LLM_MODEL,
        "max_tokens": 12000,
        "messages": [{"role": "user", "content": PROMPT_HEAD + json.dumps(payload, ensure_ascii=False)}],
    }
    # 禁用 thinking：deepseek-v4.1-flash 默认输出超长思维链，会吃满 max_tokens
    # 导致正文 JSON 被截断（2026-10-03 修复，此前连续两晚摘要失败）
    if "deepseek" in LLM_MODEL.lower():
        body["thinking"] = {"type": "disabled"}
    for i in range(attempts):
        try:
            r = requests.post(LLM_BASE + "/v1/messages",
                              headers={"x-api-key": LLM_KEY, "anthropic-version": "2023-06-01",
                                       "content-type": "application/json"},
                              json=body, timeout=300)
            d = r.json()
            text = "".join(b.get("text", "") for b in d.get("content", []) if b.get("type") == "text")
            text = text.strip()
            # 保存原始输出便于排障
            try:
                with open(os.path.join(STATE_DIR, "llm-raw.txt"), "w", encoding="utf-8") as fh:
                    fh.write(text)
            except Exception:
                pass
            if not text:
                raise ValueError(f"empty text output (stop_reason={d.get('stop_reason')})")
            if d.get("stop_reason") == "max_tokens":
                raise ValueError("response truncated at max_tokens before valid JSON ended")
            if text.startswith("```"):
                text = re.sub(r"^```[a-zA-Z]*\n?", "", text)
                text = re.sub(r"\n?```$", "", text)
            m = re.search(r"\{.*\}", text, re.S)
            if not m:
                raise ValueError("no JSON object in LLM output")
            return json.loads(m.group(0))
        except Exception as e:
            print(f"[llm] attempt {i + 1}/{attempts} fail: {e}", file=sys.stderr)
            time.sleep(2)
    return None


def render(d, cands):
    def url_of(cid):
        try:
            return cands[int(cid)]["url"]
        except Exception:
            return ""

    lines = [f"🌅 AI 热点速递 · {datetime.now().strftime('%-m月%-d日')}"]
    lines.append(d.get("overview", "").strip())
    lines.append("")
    hl = d.get("highlights") or []
    if hl:
        lines.append("⭐ 今日精选")
        for n, h in enumerate(hl[:6], 1):
            u = url_of(h.get("id"))
            title = (h.get("title_cn") or "").strip()
            note = (h.get("note") or "").strip()
            lines.append(f"{n}. {title} — {note}")
            if u:
                lines.append(f"　 {u}")
        lines.append("")
    for sec in d.get("sections") or []:
        its = sec.get("items") or []
        if not its:
            continue
        lines.append(f"{sec.get('emoji','')} {sec.get('title','')}".strip())
        for it in its[:4]:
            u = url_of(it.get("id"))
            title = (it.get("title_cn") or "").strip()
            note = (it.get("note") or "").strip()
            lines.append(f"• {title} — {note}" if note else f"• {title}")
            if u:
                lines.append(f"　 {u}")
        lines.append("")
    if d.get("closing"):
        lines.append(d["closing"].strip())
    lines.append("")
    lines.append("—— 来源：HN·量子位·TechCrunch·GitHub·MCP注册表·ClawHub·OpenRouter 等")
    text = "\n".join(l for l in lines if l is not None).strip()
    # 长度控制
    if len(text) > 3400:
        text = text[:3400].rsplit("\n", 1)[0] + "\n…（内容较长已截断）"
    return text


def render_fallback(cands):
    lines = [f"🌅 AI 热点速递 · {datetime.now().strftime('%-m月%-d日')}",
             "（LLM 摘要暂不可用，以下为原始扫描结果）", ""]
    for c in cands[:10]:
        lines.append(f"• [{c['src']}] {c['title']}")
        lines.append(f"　 {c['url']}")
    return "\n".join(lines)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--hours", type=float, default=26)
    ap.add_argument("--no-llm", action="store_true")
    ap.add_argument("--print", action="store_true")
    ap.add_argument("--reuse-candidates", action="store_true", help="debug: use cached candidates")
    ap.add_argument("--stdout", action="store_true", help="print ONLY the push message (for cron delivery)")
    args = ap.parse_args()

    st = load_state()
    if args.reuse_candidates:
        with open(os.path.join(STATE_DIR, "last_candidates.json"), encoding="utf-8") as f:
            cands = json.load(f)
        res, errs, mcp_keys, cp_date, mcp_cursor = {}, {}, [], None, None
    else:
        res, errs, extra = collect(args.hours)
        mcp_keys, cp_date, mcp_cursor = extra
        cands = build_candidates(res, st)
        with open(os.path.join(STATE_DIR, "last_candidates.json"), "w", encoding="utf-8") as f:
            json.dump(cands, f, ensure_ascii=False)
    counts = {k: len(res.get(k) or []) for k in res}

    if not cands:
        with open(MSG_FILE, "w", encoding="utf-8") as f:
            f.write("")
        if args.stdout:
            print("NO_REPLY")
        else:
            print("no candidates -> NO_REPLY;", counts, "errs:", errs)
        save_state(st)
        return

    d = None if args.no_llm else llm_digest(cands)
    text = render(d, cands) if d else render_fallback(cands)

    # 持久化
    with open(MSG_FILE, "w", encoding="utf-8") as f:
        f.write(text)
    md = os.path.join(OUT_DIR, f"ai-hot-{datetime.now().strftime('%Y%m%d')}.md")
    with open(md, "w", encoding="utf-8") as f:
        f.write(text)

    # 更新 seen
    now = time.time()
    for c in cands:
        st.setdefault("seen", {})[norm_url(c["url"])] = now
    mcp_seen = set(st.get("mcp_seen", [])) | set(mcp_keys or [])
    st["mcp_seen"] = sorted(mcp_seen)[-5000:]
    if mcp_cursor:
        st["mcp_cursor"] = mcp_cursor
    if cp_date:
        st["codingplan_date"] = cp_date
    save_state(st)

    if args.stdout:
        print(text)
    else:
        print(f"digest OK: {len(cands)} candidates, {len(text)} chars, sources: {counts}, errs: {errs}")
        print("md:", md)
        if args.print:
            print("-----")
            print(text)


if __name__ == "__main__":
    main()