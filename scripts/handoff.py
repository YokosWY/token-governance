#!/usr/bin/env python3
"""交接卡：切会话前把「做到哪 + 下一步 + 数字」落成对话夹的 00-card.md。

为什么单列：切会话省的是重发量，唯一会把它赔掉的是「重建上下文」。所以交接卡的硬约束是
**≤3k token**——只写结论、路径、下一步，不回放对话；实测重建超过 40k token，切会话就白省。

用法：
  python scripts/handoff.py --session <id|dir-fragment> --slug tg-account
  python scripts/handoff.py --session <id> --slug tg-account --print-only
  python scripts/handoff.py --session <id> --slug tg-account --workspace academic

产出：
  <memory_root>/<workspace>/<YYYYMMDD>-<slug>/00-card.md   （已存在则覆盖，备份为 .bak-时间戳）

口径与 measure_modules.py 一致：input×P_in + cacheRead×P_cache + output×P_out。
"""
import argparse, datetime, json, os, re, shutil, sys

from tg_common import (config_base, decompress_bytes, load_config, resolve_dsh_home,
                       sessions_root, utf8_stdio)

utf8_stdio()

DEFAULT_PRICES = {"input": 0.15, "cached": 0.003, "output": 0.6}
DEFAULT_RATE = 7.1


def find_session(root, key):
    for dp, _, fs in os.walk(root):
        if key in dp:
            for f in fs:
                if f.endswith(".zstd"):
                    return os.path.join(dp, f)
    return None


WRITE_TOOLS = {"write", "edit", "multiedit", "apply_patch", "ops_write_file", "notebook_edit"}
# 临时路径：目录名 tmp/temp、或文件名带 _tmp_/_temp_ 前缀。比原来只认「目录恰为 tmp」宽，
# 但不误伤 template.py 这类词里带 tmp 的正常文件。
TMP_PATH = re.compile(r"(?i)(^|[\\/_.-])(tmp|temp)([\\/_.-]|$)")


def collect_edits(raw):
    """从会话日志里收「动过哪些文件」+「最后一次改动的代码」。

    同一个文件可能用 / 和 \\ 两种写法出现（MSYS vs Windows），按归一化小写去重，保留绝对路径写法。
    代码片段只留最后一次写入的 new_string/content，行数与字数都设上限——交接卡只放必看的那几行。
    """
    order, count = [], {}
    alias = {}
    candidates = []
    for line in raw.split(b"\n"):
        if not line.strip() or b"tool/call" not in line:
            continue
        try:
            o = json.loads(line)
        except Exception:
            continue
        if o.get("type") != "tool/call":
            continue
        d = o.get("data") or {}
        if str(d.get("name") or "").lower() not in WRITE_TOOLS:
            continue
        try:
            args = json.loads(d.get("arguments") or "{}")
        except Exception:
            args = {}
        path = args.get("file_path") or args.get("path") or ""
        if not path:
            continue
        key = os.path.normcase(path.replace("/", os.sep))
        if key not in count:
            order.append(key)
            alias[key] = path
            count[key] = 0
        elif "\\" in path and "\\" not in alias[key]:
            alias[key] = path  # 优先留绝对路径
        count[key] += 1
        body = args.get("content") or args.get("new_string") or ""
        if not body and isinstance(args.get("edits"), list):
            body = "".join(str(e.get("new_string") or "") for e in args["edits"] if isinstance(e, dict))
        if body:
            candidates.append({"path": path, "text": body})
    # 优先取「最后一段够长的代码」：只改一行的收尾动作往往不是接手的重点；
    # .md/.txt 里的片段常自带围栏，优先级最低，免得把卡片自身的代码块搅乱
    snippet = None
    # 全量倒序扫，不设窗口：长会话末段全是文档时，设窗口就永远取不到代码
    for c in reversed(candidates):
        if TMP_PATH.search(c["path"]):
            continue  # 临时脚本一起淘汰：接手要的是产物，不是一次性脚手架
        ext = os.path.splitext(c["path"])[1].lower()
        if ext in CODE_PREF and len(str(c["text"]).strip().split("\n")) >= 3:
            snippet = c
            break
    if not snippet:
        for c in reversed(candidates):
            if not TMP_PATH.search(c["path"]):
                snippet = c
                break
    edits = [(alias[k], count[k]) for k in order]
    return edits, snippet


def trim_snippet(sn, max_lines=8, max_chars=600):
    if not sn:
        return None
    text = str(sn["text"]).replace("\r\n", "\n").strip("\n")
    lines = text.split("\n")
    cut = False
    if len(lines) > max_lines:
        lines = lines[:max_lines]
        cut = True
    out = "\n".join(lines)
    if len(out) > max_chars:
        out = out[:max_chars]
        cut = True
    return {"path": sn["path"], "text": out, "cut": cut}


LANG_BY_EXT = {
    ".js": "js", ".mjs": "js", ".cjs": "js", ".ts": "ts", ".py": "python",
    ".json": "json", ".yml": "yaml", ".yaml": "yaml", ".md": "md",
    ".css": "css", ".html": "html", ".ps1": "powershell", ".sh": "bash",
}
# 「关键片段」只优先取这些扩展名：正文类（.md/.txt）常自带围栏，且往往不是接手重点
CODE_PREF = {k for k in LANG_BY_EXT if k != ".md"}


def blocks_text(blocks):
    """把 content 块数组拼成纯文本。只认 type=text：推理块和工具调用块不算结论。"""
    if isinstance(blocks, str):
        return blocks  # 日志格式变体：content 直接是字符串
    out = []
    for b in blocks or []:
        if isinstance(b, dict) and b.get("type") == "text" and b.get("text"):
            out.append(str(b["text"]))
    return "\n".join(out)


def squash(text):
    return " ".join(str(text).split())


def cut_sentence(text, limit):
    """按句读切到 limit 以内：能整句收就整句收，硬切才带省略号。
    硬切会把「你认为该方案可行吗」这类尾巴丢掉，读起来像结论已定。"""
    text = squash(text)
    if len(text) <= limit:
        return text
    head = text[:limit - 1]
    for i in range(len(head) - 1, max(len(head) // 3, 0), -1):
        ch = head[i]
        if ch in "。！？；，、":
            return head[:i + 1]
        # 西文标点只在其后是空格/结尾时才算句读：否则会把 C:\x\00-card.md 切成 00-card.
        if ch in ".!?;," and (i + 1 >= len(head) or head[i + 1] == " "):
            return head[:i + 1]
    return head + "…"


WORDY = re.compile(r"[0-9A-Za-z\u4e00-\u9fff]")


def clean_note(text):
    """助手文本压成一行：去掉围栏标记、剥掉行首井号，免得把卡片自己的代码块搅乱。"""
    t = re.sub(r"```+[A-Za-z0-9_+-]*", " ", str(text))
    t = re.sub(r"(?m)^\s*#+\s*", "", t)
    return squash(t)


def note_ok(t, least=12, ratio=0.35):
    """够不够当「最后一句」：纯表格、框线、进度符号洗完只剩标点的不要。"""
    n = len(WORDY.findall(t))
    return n >= least and n / max(len(t), 1) >= ratio


def analyze(path, prices, rate):
    raw = decompress_bytes(path)
    steps = turns = 0
    in_tok = read_tok = out_tok = 0
    first = last = 0
    users, notes, title = [], [], ""
    for line in raw.split(b"\n"):
        if not line.strip():
            continue
        try:
            o = json.loads(line)
        except Exception:
            continue
        t = o.get("type")
        d = o.get("data") or {}
        if t == "session/title":
            if d.get("title"):
                title = str(d["title"])  # LLM 生成的会话名：真人输入全被过滤时用它兜底
        elif t in ("user/message", "user/delta"):
            turns += 1
            if t == "user/message":
                src = (d.get("source") or {}).get("kind") or ""
                body = blocks_text(d.get("content") or (d.get("message") or {}).get("content"))
                # 只认真人输入：宿主注入的 system-reminder / 时间快照 / 技能目录都不算「目标」。
                # 老日志没有 source 字段，用「首字符不是 <」兜一层（本机语料该分支不可达，留作兼容）。
                if body and (src == "user" or (not src and not body.lstrip().startswith("<"))):
                    flat = squash(body)
                    if flat:
                        users.append(flat)
        elif t == "assistant/message":
            note = clean_note(blocks_text((d.get("message") or {}).get("content")))
            if note:
                notes.append(note)
            u = d.get("usage") or {}
            i = u.get("inputTokens", 0) or 0
            c = u.get("cacheReadTokens", 0) or 0
            steps += 1
            in_tok += i
            read_tok += c
            out_tok += u.get("outputTokens", 0) or 0
            if not first:
                first = i + c
            last = i + c
    if not steps:
        return None
    p_in = prices["input"] * rate / 1e6
    p_cache = prices["cached"] * rate / 1e6
    p_out = prices["output"] * rate / 1e6
    cny = in_tok * p_in + read_tok * p_cache + out_tok * p_out
    ctx = in_tok + read_tok
    save_per = max(0.0, (last - first) * p_cache)
    rebuild_cny = (first + 3000) * p_in
    n_star = int(-(-(first * p_in + 3000 * p_in) // save_per)) if save_per > 0 else None
    # 切了到底省多少：毛省（每步省 × 100 步）减掉一次性重建；≤0 就是还没到切的时候
    net100 = save_per * 100 - rebuild_cny
    edits, snip = collect_edits(raw)
    # 目标取「首条」（末条常是「继续」「可以」这类按语），并附末条当「最近指令」；
    # 两条都没有（压缩会话/空壳）才退到会话标题。
    real = [u for u in users if len(u) >= 4]
    goal_src = real[0] if real else title
    # 「最近指令」取最后一条真人输入（哪怕只有「继续」两个字）：它说明上一轮是按语，
    # 接手者据此判断目标还成不成立，比把它顶成「目标」诚实
    recent_src = users[-1] if len(users) > 1 else ""
    # 「最后一句」不是进度：只是最后说了什么。跳过纯表格/框线的文本往前找。
    note_src = ""
    for n in reversed(notes):
        if note_ok(n):
            note_src = n
            break
    if not note_src and notes:
        note_src = notes[-1]
    has_code = any(os.path.splitext(p)[1].lower() in CODE_PREF for p, _ in edits)
    goal = cut_sentence(goal_src, 80) if goal_src else ""
    recent = cut_sentence(recent_src, 80) if recent_src else ""
    if recent == goal:
        recent = ""  # 同一句重复两遍只是占篇幅
    note = cut_sentence(note_src, 160) if note_src else ""
    return {
        "steps": steps, "turns": max(turns, 1), "cny": cny,
        "hit": read_tok / ctx if ctx else 0,
        "miss_per_step": int(in_tok / steps),
        "ctx": last, "start_ctx": first,
        "save_per_step": save_per, "n_star": n_star,
        "rebuild_cny": rebuild_cny, "net100": net100,
        "edits": edits, "snippet": trim_snippet(snip), "has_code": has_code,
        "goal": goal, "recent": recent, "last_note": note,
    }


def render(meta, m):
    edits = m.get("edits") or []
    edits = sorted(edits, key=lambda x: -x[1])  # 改得最多的排前面
    keep = [e for e in edits if not TMP_PATH.search(e[0])]  # 临时脚本不算交接重点
    shown = (keep or edits)[:5]
    rest = len(edits) - len(shown)
    lines = [
        f"# {meta['name']} · 交接卡",
        "",
        f"> 会话 {meta['session']} · {m['turns']} 轮 {m['steps']} 步 · ¥{m['cny']:.4f} · "
        f"ctx {m['ctx']/1000:.0f}k · {meta['when']}",
        "",
        "## 改过什么（本会话写过的文件）",
        "",
    ]
    if meta.get("task"):
        lines.insert(3, f"> 任务夹 `{meta['task']}` · 本卡 `{meta.get('card') or '00-card.md'}`")
    if edits:
        for p, n in shown:
            lines.append(f"- `{p}`" + (f" ×{n}" if n > 1 else ""))
        if rest > 0:
            lines.append(f"- ……另有 {rest} 个（含临时文件），共动 {len(edits)} 个")
    else:
        lines.append("- （本会话没写文件）")
    lines += ["", "## 关键片段", ""]
    snip = m.get("snippet")
    if snip:
        ext = os.path.splitext(snip["path"])[1].lower()
        lang = LANG_BY_EXT.get(ext, "")
        # 围栏要比正文里最长的那串反引号更长，否则片段自带的三反引号会把卡片截断
        runs = [len(x) for x in re.findall(r"`+", snip["text"])]
        fence = "`" * max(3, (max(runs) + 1) if runs else 3)
        # 来源按「本会话有没有改过代码」判，不按「取到的这段是什么扩展名」判：
        # 否则长会话末段全是 .md 时，明明改过 client.js 也会写成「无代码改动」
        if ext in CODE_PREF:
            why = "最后一次代码改动"
        elif m.get("has_code"):
            why = "最后一次改动（代码改动更早，见上面清单）"
        else:
            why = "无代码改动，取最后写入的文本"
        lines.append(f"`{snip['path']}`（{why}）" + ("　…已截断" if snip["cut"] else ""))
        lines += ["", f"{fence}{lang}", snip["text"], fence]
    else:
        lines.append("（贴接手必需的代码/配置；其余给上面的路径 + 行号）")
    lines += [
        "",
        "## 状态",
        "",
        f"- 目标：{m.get('goal') or '（无）'}",
    ]
    if m.get("recent"):
        lines.append(f"- 最近指令：{m['recent']}")
    lines += [
        f"- 最后一句：{m.get('last_note') or '（无）'}",
        "- 卡在哪：",
        "",
        "## 下一步",
        "",
        "1. ",
        "",
    ]
    return "\n".join(lines)


TASK_INDEX = "99-cards.json"


def default_config():
    """手跑时默认读脚本旁的 config.yaml：靠 cwd 解析会在别处静默落到 ~/tg-memory。"""
    cand = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "config.yaml")
    return cand if os.path.isfile(cand) else "config.yaml"


def clean_task(name):
    """任务夹名只能是一个目录名：挡掉分隔符与 ..，否则卡会写到别处去。"""
    name = (name or "").strip()
    if not name or name in (".", "..") or any(c in name for c in "/\\:"):
        sys.exit(f"任务夹名不合法：{name!r}（只允许单层目录名）")
    return name


def read_json(path):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


def lookup_task(path, sid):
    """会话 → 任务夹：先精确 id，再 sid8，最后 `*`（本机默认任务）。都没有就返回空。"""
    d = read_json(path) or {}
    tasks = d.get("tasks") if isinstance(d, dict) else None
    if not isinstance(tasks, dict):
        return ""
    for k in (sid, sid[:8], "*"):
        v = tasks.get(k)
        if isinstance(v, str) and v.strip():
            return clean_task(v)
        if isinstance(v, dict) and str(v.get("task") or "").strip():
            return clean_task(v["task"])
    return ""


def set_task_pointer(path, sid, task):
    """写指针：本会话归到哪个任务夹。人/agent 在开任务时写一次，之后手跑与插件都跟着走。"""
    d = read_json(path)
    if not isinstance(d, dict):
        d = {}
    if not isinstance(d.get("tasks"), dict):
        d["tasks"] = {}
    d["v"] = 1
    d["tasks"][sid] = {"task": task, "at": datetime.datetime.now().strftime("%Y-%m-%d %H:%M")}
    d["updatedAt"] = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        json.dump(d, f, ensure_ascii=False, indent=1)
        f.write("\n")


def has_session_cards(folder):
    try:
        return any(n.startswith("01-") and n.endswith(".md") for n in os.listdir(folder))
    except OSError:
        return False


def update_index(folder, task, workspace, entry):
    """99-cards.json：本夹里有哪些会话卡、哪张最新。不动人手写的 00-card.md。"""
    path = os.path.join(folder, TASK_INDEX)
    d = read_json(path)
    if not isinstance(d, dict):
        d = {}
    if not isinstance(d.get("cards"), dict):
        d["cards"] = {}
    d.update({"v": 1, "task": task, "workspace": workspace,
              "updatedAt": datetime.datetime.now().strftime("%Y-%m-%d %H:%M")})
    d["cards"][entry["session"]] = entry
    try:
        with open(path, "w", encoding="utf-8", newline="\n") as f:
            json.dump(d, f, ensure_ascii=False, indent=1)
            f.write("\n")
    except OSError:
        pass  # 索引写不进不影响卡片本身


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--session", required=True, help="会话 id 或目录片段")
    ap.add_argument("--slug", default="", help="对话夹名后缀，如 tg-account（未绑定任务时用）")
    ap.add_argument("--workspace", default="default-workspace")
    ap.add_argument("--config", default=default_config())
    ap.add_argument("--dsh-home", default="")
    ap.add_argument("--memory-root", default="")
    ap.add_argument("--task", default="", help="任务夹名（如 20261025-tg-account）；不给则查指针文件")
    ap.add_argument("--task-file", default="", help="任务指针文件；缺省 <dsh_home>/_local/tg-current-task.json")
    ap.add_argument("--set-task", default="", help="只写指针（本会话归到该任务夹），不生成卡片")
    ap.add_argument("--no-task", action="store_true", help="忽略指针，强制落 auto-<slug> 夹")
    ap.add_argument("--print-only", action="store_true")
    ap.add_argument("--if-absent", action="store_true",
                    help="只在没有卡片时写（自动触发用这个，避免覆盖手写卡）")
    a = ap.parse_args()

    base = config_base(a.config)
    cfg, _ = load_config(a.config)
    home = resolve_dsh_home(a.dsh_home, cfg=cfg, base=base)
    root = sessions_root(home)
    path = find_session(root, a.session)
    if not path:
        sys.exit(f"找不到会话：{a.session}（在 {root} 下按目录名片段匹配）")

    sid = os.path.basename(os.path.dirname(path)).replace("session-", "") or os.path.basename(
        os.path.dirname(path))[:8]
    task_file = a.task_file or os.path.join(home, "_local", "tg-current-task.json")

    # 只写指针：把本会话归到某个任务夹，供之后手跑与插件共用
    if a.set_task:
        task = clean_task(a.set_task)
        set_task_pointer(task_file, sid, task)
        print(f"任务指针：{task_file}")
        print(f"  会话 {sid[:8]} → 任务夹 {task}")
        return

    if not a.set_task and not a.slug and not a.task and not lookup_task(task_file, sid):
        sys.exit("要么给 --slug（落 auto-<slug> 夹），要么用 --task/指针绑定任务夹")

    prices = dict(DEFAULT_PRICES)
    for k, v in (cfg.get("prices_usd_per_mtok") or {}).items():
        if k in prices and isinstance(v, (int, float)):
            prices[k] = float(v)
    rate = float(cfg.get("rate") or DEFAULT_RATE)

    m = analyze(path, prices, rate)
    if not m:
        sys.exit("该会话没有 usage 记录")

    today = datetime.date.today()
    task = "" if a.no_task else (clean_task(a.task) if a.task else lookup_task(task_file, sid))
    name = task or f"{today:%Y%m%d}-{a.slug}"
    mem = a.memory_root or cfg.get("memory_root") or os.path.join(os.path.expanduser("~"), "tg-memory")
    folder = os.path.join(mem, a.workspace, name)

    # 有任务夹：先认索引（同一会话重跑就写回同一张卡），否则夹里还没卡 → 本会话当主卡，
    # 已有别的卡 → 写 01-<sid8>.md，绝不覆盖别人的现场
    if task:
        main_card = os.path.join(folder, "00-card.md")
        own_card = os.path.join(folder, f"01-{sid[:8]}.md")
        prev = (read_json(os.path.join(folder, TASK_INDEX)) or {}).get("cards") or {}
        mine = str(((prev.get(sid[:8]) or {}) if isinstance(prev, dict) else {}).get("file") or "")
        if mine == "00-card.md":
            card = main_card
        elif mine:
            card = os.path.join(folder, mine)
        elif os.path.isfile(main_card) or has_session_cards(folder):
            card = own_card
        else:
            card = main_card
    else:
        card = os.path.join(folder, "00-card.md")

    meta = {"name": name, "when": datetime.datetime.now().strftime("%Y-%m-%d %H:%M"),
            "session": sid[:8], "task": task, "card": os.path.basename(card)}
    text = render(meta, m)
    if a.print_only:
        print(text)
        return

    os.makedirs(folder, exist_ok=True)
    if os.path.isfile(card):
        if a.if_absent:
            print(f"已有交接卡，未覆盖：{card}")
            return
        shutil.copy2(card, card + ".bak-" + datetime.datetime.now().strftime("%Y%m%d-%H%M%S"))
    with open(card, "w", encoding="utf-8", newline="\n") as f:
        f.write(text)
    if task:
        update_index(folder, task, a.workspace, {
            "file": os.path.basename(card), "session": sid[:8],
            "at": datetime.datetime.now().strftime("%Y-%m-%d %H:%M"),
            "turns": m["turns"], "steps": m["steps"], "cny": round(m["cny"], 4),
            "n_star": m["n_star"], "ctx": m["ctx"],
        })
    print(f"交接卡：{card}")
    print(f"  会话 {meta['session']} · {m['turns']} 轮 {m['steps']} 步 · ¥{m['cny']:.4f} · "
          f"上下文 {m['ctx']/1000:.0f}k · 回本 {m['n_star']} 步")
    if task:
        print(f"  任务夹：{task}（同任务多会话各一张 01-*.md，主卡仍是 00-card.md）")
    print("  下一步：补「卡在哪」与「下一步」两节（目标/做到哪/文件/片段已自动填）")


if __name__ == "__main__":
    main()
