#!/usr/bin/env python3
"""压缩质量闸门：压缩以后，模型还用不用得上？

不花模型钱：不问「答对没」，看**行为证据**——
  1) 回取率：notice 之后 5 条事件内是否 read/grep 回同一个原文路径。压过头才会回取。
  2) 形状：省略行 / 采样 / 错误行 / 超长行截断，判断结构还够不够定位。
  3) 锚点：原文 spill 还在时（近期会话）核对 `path:line` 与数字覆盖率；不在就降级 notice-only。

用法：
  python scripts/quality_gate.py --days 14         # 全量体检
  python scripts/quality_gate.py --ab --days 30    # 按策略分组的边际表
  python scripts/quality_gate.py --session <id>    # 单会话（id 前缀或文件路径）
  python scripts/quality_gate.py --json

边界：只读会话日志与 spill 文件，不写任何状态；改阈值要人裁决。
"""
import argparse, json, os, re, sys

from tg_common import utf8_stdio, resolve_dsh_home, sessions_root, decompress_bytes, load_config

NOTICE_RE = re.compile(
    r"\[(?:save-token|tg-trim) #([a-z0-9]+) (losslessly re-encoded|compressed): "
    r"([\d,]+) -> ([\d,]+) bytes(?: \(~(\d+)% 更小(?:，策略 ([\w-]+))?\))?"
)
OMIT_RE = re.compile(r"省略 (\d+) 行")
REP_RE = re.compile(r"折叠重复 (\d+) 行")
SAMP_RE = re.compile(r"(\d+) 条带行号采样")
ERR_RE = re.compile(r"保留 (\d+) 条错误行")
CUT_RE = re.compile(r"截断 (\d+) 条超长行")
ANCHOR_RE = re.compile(r"[\w./\\-]+\.[A-Za-z0-9]{1,8}:\d+")
NUM_RE = re.compile(r"\b\d{3,}\b")

RETRIEVE_WINDOW = 5      # notice 之后看几条事件
RETRIEVE_PENALTY = 45    # 回取一次扣多少分
CUT_PENALTY = 12
ANCHOR_PENALTY = 20
GRADES = [(95, "S"), (85, "A"), (70, "B"), (55, "C"), (40, "D"), (0, "F")]


def text_of(o):
    d = o.get("data") or {}
    m = d.get("message") or d
    c = m.get("content") if isinstance(m, dict) else None
    if isinstance(c, list):
        return "".join(x.get("text") or "" for x in c if isinstance(x, dict))
    return c if isinstance(c, str) else ""


def parse_shape(seg):
    """从 notice 尾巴解析形状位。缺省 0。"""
    def one(rx):
        m = rx.search(seg)
        return int(m.group(1)) if m else 0
    return dict(omitted=one(OMIT_RE), repeated=one(REP_RE), samples=one(SAMP_RE),
                errors=one(ERR_RE), cuts=one(CUT_RE))


def unescape(s):
    return s.replace('\\"', '"').replace("\\\\", "\\")


def find_locator(line_text, at):
    """notice 之后找 read "路径"；会话 jsonl 里引号被转义过。

    只认「像路径」的串：至少 8 字符、带 / 或 \\。否则会把 JSON 结构的引号
    （`},{`、`string`）当成 spill 路径，后面所有分类都会跟着错。
    """
    seg = unescape(line_text[at:at + 900])
    i = seg.find("read ")
    if i < 0:
        return ""
    j = seg.find('"', i)
    k = seg.find('"', j + 1) if j >= 0 else -1
    cand = seg[j + 1:k] if 0 <= j < k else ""
    if len(cand) < 8 or ("/" not in cand and "\\" not in cand):
        return ""
    return cand


def read_original(path):
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            return f.read()
    except Exception:
        return ""


def anchor_coverage(original, compressed):
    """原文里能定位的锚点，压缩后还剩多少。两类：path:line、≥3 位数字。"""
    out = {}
    for name, rx in (("anchor", ANCHOR_RE), ("number", NUM_RE)):
        src = set(rx.findall(original))
        if not src:
            continue
        kept = sum(1 for a in src if a in compressed)
        out[name] = round(kept / len(src), 3)
    return out


def grade_of(score):
    for floor, g in GRADES:
        if score >= floor:
            return g
    return "F"


def scan_session(path, want_lines=True):
    """返回 [(event_dict, line_index, line_text)]；解压失败返回 []。"""
    raw = decompress_bytes(path)
    if not raw:
        return []
    lines = raw.split(b"\n")
    out = []
    for idx, lb in enumerate(lines):
        if not lb.strip():
            continue
        s = lb.decode("utf-8", "replace")
        for m in NOTICE_RE.finditer(s):
            kind = m.group(2)
            before = int(m.group(3).replace(",", ""))
            after = int(m.group(4).replace(",", ""))
            ev = dict(id=m.group(1), kind=kind, before=before, after=after,
                      pct=int(m.group(5)) if m.group(5) else round((1 - after / max(before, 1)) * 100),
                      strategy=m.group(6) or ("legacy（10-25 前，无策略位）" if kind == "compressed" else "lossless"),
                      shape=parse_shape(s[m.end():m.end() + 200]),
                      locator=find_locator(s, m.end()))
            out.append((ev, idx, s))
    return out


def find_retrieval(lines, idx, locator):
    """notice 之后找指向同一 spill 的调用，并判它是「补缺口」还是「本来就要重读」。

    返回 (kind, line_index)。kind：
      gap   真回取 —— grep，或 read 带 offset>1 / 小 limit，冲着被砍掉的那段去
      whole 伪回取 —— read 整个文件（offset 缺省或 0），多半是改了文件后重读
      text  认得出路径、认不出参数（旧日志 / 拼进命令里）
    """
    base = os.path.basename(locator) if locator else ""
    if not base:
        return None, -1
    mentioned = False
    for k in range(idx + 1, min(idx + 1 + RETRIEVE_WINDOW * 4, len(lines))):
        raw = lines[k].decode("utf-8", "replace")
        if base not in unescape(raw):
            continue
        mentioned = True
        try:
            o = json.loads(raw)
        except Exception:
            continue
        if o.get("type") != "tool/call":
            continue  # 只是回显了路径，不是调用
        d = o.get("data") or {}
        try:
            args = json.loads(d.get("arguments") or "{}")
        except Exception:
            args = {}
        if d.get("name") == "grep":
            return "gap", k
        off, lim = args.get("offset"), args.get("limit")
        if (isinstance(off, int) and off > 1) or (isinstance(lim, int) and lim <= 200):
            return "gap", k
        return "whole", k
    return ("text" if mentioned else None), -1


def build_event(path, ev, idx, line_text, lines):
    """补齐回取 / 锚点 / 评分。"""
    rkind, _at = find_retrieval(lines, idx, ev["locator"])
    retrieved = rkind in ("gap", "whole", "text")

    cov = {}
    alive = bool(ev["locator"]) and os.path.exists(ev["locator"])
    if alive:
        original = read_original(ev["locator"])
        try:
            compressed = text_of(json.loads(line_text))
        except Exception:
            compressed = ""
        if original and compressed:
            cov = anchor_coverage(original, compressed)

    score = 100.0
    if rkind == "gap":
        score -= RETRIEVE_PENALTY            # 真回取：被砍掉的细节模型补不回来
    elif rkind == "text":
        score -= RETRIEVE_PENALTY * 0.55     # 认不出参数，折中
    elif rkind == "whole":
        score -= 8                           # 整读：多本来就要重读，轻罚
    if ev["shape"]["cuts"]:
        score -= CUT_PENALTY
    if cov and min(cov.values()) < 0.9:
        score -= ANCHOR_PENALTY
    if not cov:
        score = min(score, 84.9)  # 原文已清、核不了锚点：封顶 A，不给 S
    score = max(0.0, score)
    ev.update(retrieved=retrieved, retrieval=rkind or "", locator_alive=alive, coverage=cov,
              score=score, grade=grade_of(score),
              session=os.path.basename(os.path.dirname(path)) or os.path.basename(path))
    return ev


def sessions_for(home, days, limit=0):
    root = sessions_root(home)
    if not os.path.isdir(root):
        return []
    files, now = [], __import__("time").time()
    for dirpath, _dirs, names in os.walk(root):
        for n in names:
            if not n.endswith(".jsonl.zstd"):
                continue
            p = os.path.join(dirpath, n)
            try:
                if days and (now - os.path.getmtime(p)) > days * 86400:
                    continue
            except OSError:
                continue
            files.append(p)
    files.sort(key=lambda p: os.path.getmtime(p), reverse=True)
    return files[:limit] if limit else files


def summarize(events):
    lossless = [e for e in events if e["kind"] == "losslessly re-encoded"]
    events = [e for e in events if e["kind"] == "compressed"]
    n = len(events)
    if not n:
        return dict(events=0, lossless=len(lossless), grade="-", score=0,
                    retrieval_rate=0.0, saved=0, avg_pct=0, locator_alive=0,
                    by_strategy=[], worst=[])
    saved = sum(e["before"] - e["after"] for e in events)
    total_before = sum(e["before"] for e in events)
    gap = sum(1 for e in events if e.get("retrieval") == "gap")
    whole = sum(1 for e in events if e.get("retrieval") == "whole")
    text = sum(1 for e in events if e.get("retrieval") == "text")
    retrieved = gap + whole + text
    scores = sum(e["score"] for e in events) / n
    groups = {}
    for e in events:
        g = groups.setdefault(e["strategy"], dict(n=0, saved=0, retrieved=0, gap=0, score=0.0, cuts=0))
        g["n"] += 1
        g["saved"] += e["before"] - e["after"]
        g["retrieved"] += int(e["retrieved"])
        g["gap"] += int(e.get("retrieval") == "gap")
        g["score"] += e["score"]
        g["cuts"] += int(bool(e["shape"]["cuts"]))
    by_strategy = []
    for k, g in sorted(groups.items(), key=lambda kv: -kv[1]["n"]):
        by_strategy.append(dict(strategy=k, n=g["n"], saved=g["saved"],
                                retrieval_rate=round(g["retrieved"] / g["n"], 3),
                                gap_rate=round(g["gap"] / g["n"], 3),
                                cuts=g["cuts"], score=round(g["score"] / g["n"], 1)))
    # 阈值不拍脑袋：用同批「够样本的策略」真回取率中位数 ×2.5，夹在 5%–30%。
    # 想让它更松/更严，改的是基线倍率，不是绝对百分数。
    rates = sorted(r["gap_rate"] for r in by_strategy if r["n"] >= 5)
    threshold = max(0.05, min(0.30, round(rates[len(rates) // 2] * 2.5, 3))) if rates else 0.10
    dist = {}
    for e in events:
        dist[e["grade"]] = dist.get(e["grade"], 0) + 1
    worst = sorted(events, key=lambda e: (e["score"], -e["before"]))[:3]
    return dict(
        events=n, lossless=len(lossless), saved=saved, bytes_before=total_before,
        avg_pct=round((1 - sum(e["after"] for e in events) / max(total_before, 1)) * 100),
        retrieval_rate=round(retrieved / n, 3), retrieved=retrieved,
        gap=gap, whole=whole, text=text, gap_rate=round(gap / n, 3), threshold=threshold,
        locator_alive=sum(1 for e in events if e["locator_alive"]),
        score=round(scores, 1), grade=grade_of(scores), dist=dist,
        by_strategy=by_strategy,
        worst=[dict(id=w["id"], session=w["session"], strategy=w["strategy"],
                    before=w["before"], grade=w["grade"], score=w["score"],
                    retrieved=w["retrieved"], retrieval=w.get("retrieval", "")) for w in worst],
    )


def advice(s):
    tips = []
    th = s.get("threshold") or 0.10
    if s.get("gap_rate", 0) > th:
        tips.append(f"真回取率 {s['gap_rate']*100:.1f}% 高于本批基线 {th*100:.0f}%（同批策略中位×2.5）"
                    "→ 抬高该策略的压缩阈值，或对高频路径加白名单不压")
    if s["dist"].get("D") or s["dist"].get("F"):
        tips.append("有 D/F 事件（多为超长行被截断）→ 检查 codeDiff/logs 规则的 maxLine")
    if not tips:
        tips.append(f"真回取率 {s.get('gap_rate', 0)*100:.1f}% 低于基线 {th*100:.0f}%，当前阈值可以不动")
    return tips


def main():
    utf8_stdio()
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=14)
    ap.add_argument("--limit", type=int, default=0, help="最多扫几个会话（0=全部）")
    ap.add_argument("--session", default="", help="只看一个会话：id 前缀或文件路径")
    ap.add_argument("--ab", action="store_true", help="按压缩策略输出边际表")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--config", default="config.yaml",
                    help="兼容 doctor 透传；本脚本只读会话日志，不读价格表")
    ap.add_argument("--dsh-home", "--home", dest="dsh_home", default="")
    a = ap.parse_args()

    # 与其余脚本同一条解析链：显式 --dsh-home > config.yaml > $DSH_HOME > ~/.dsh。
    # 坏配置直接退出（strict），否则会安静地扫错目录、报一个偏小的数字。
    cfg, base = {}, None
    if a.config and os.path.isfile(a.config):
        cfg, base = load_config(a.config)
    home = resolve_dsh_home(a.dsh_home, cfg=cfg, base=base)
    files = sessions_for(home, a.days, a.limit)
    if a.session:
        key = a.session
        if os.path.isfile(key):
            files = [key]
        else:
            files = [p for p in files if key in os.path.basename(os.path.dirname(p)) or key in os.path.basename(p)]

    all_events = []
    for p in files:
        try:
            raw = decompress_bytes(p)
        except Exception:
            continue
        if not raw:
            continue
        lines = raw.split(b"\n")
        for ev, idx, s in scan_session(p):
            all_events.append(build_event(p, ev, idx, s, lines))

    s = summarize(all_events)
    s.update(window_days=a.days, sessions_scanned=len(files),
             evidence="anchors" if s.get("locator_alive") else "notice-only",
             advice=advice(s) if s["events"] else [])

    if a.json:
        print(json.dumps(s, ensure_ascii=False, indent=2))
        return 0

    if not s["events"]:
        print(f"压缩质量闸门 · 近 {a.days} 天\n  没有找到 tg-trim / save-token 压缩事件（扫了 {len(files)} 个会话）")
        return 0

    print(f"压缩质量闸门 · 近 {a.days} 天（扫 {len(files)} 会话）\n")
    print(f"  事件     {s['events']} 次有损 | 省 {s['saved']/1024:.1f} KB"
          f"（平均 -{s['avg_pct']}%）| 无损重编码 {s.get('lossless', 0)} 次（不计分）| 证据 {s['evidence']}")
    print(f"  回取     真 {s['gap_rate']*100:.1f}%（{s['gap']} 次，基线 {s['threshold']*100:.0f}%）"
          f" | 整读 {s['whole']} | 认不出 {s['text']} | 原文存活 {s['locator_alive']}/{s['events']}")
    print(f"  评分     {s['grade']}（均分 {s['score']}）  " +
          " / ".join(f"{g} {s['dist'].get(g, 0)}" for g in "SABCDF"))
    print("\n  按策略")
    for r in s["by_strategy"]:
        print(f"    {r['strategy']:<16} {r['n']:>3} 次  省 {r['saved']/1024:>7.1f} KB"
              f"  真回取 {r['gap_rate']*100:>5.1f}%  截断 {r['cuts']}  均分 {r['score']}")
    if s["worst"]:
        print("\n  最差")
        for w in s["worst"]:
            tag = {"gap": "真回取", "whole": "整读", "text": "认不出"}.get(w.get("retrieval"), "")
            print(f"    #{w['id']} {w['strategy'][:14]:<14} {w['before']:>7} B  {w['grade']}({w['score']})"
                  f"  {tag}  {w['session'][:28]}")
    if a.ab:
        th = s["threshold"]
        print(f"\n  边际（按真回取率降序，阈值 {th*100:.0f}% = 同批中位×2.5）")
        for r in sorted(s["by_strategy"], key=lambda x: -x["gap_rate"]):
            flag = "压过头" if r["gap_rate"] > th else ("有截断" if r["cuts"] else "可留")
            print(f"    {r['strategy'][:22]:<22} {r['n']:>3} 次  真回取 {r['gap_rate']*100:>5.1f}%"
                  f"  省 {r['saved']/1024:>7.1f} KB  {flag}")
    print("\n  修法")
    for t in s["advice"]:
        print(f"    · {t}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
