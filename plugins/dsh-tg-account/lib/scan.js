/**
 * 会话日志索引 —— 一次遍历，四处复用。
 *
 * 背景（2026-10-29）：findSession / savedFiles / dayFiles / treeFiles 各走一遍 sessions 目录，
 * 每次约 450 次 statSync ≈ 20 ms（Windows Defender 下更慢）；而 /tg-account/api/cost 每 5 秒
 * 被拉一次，一次请求里 findSession 还要跑两遍（account + sessionSid）。加起来是宿主事件循环里
 * 每 5 秒一次的 20–45 ms 同步阻塞。
 *
 * 现在两层：
 *   1) 按 sid 的常驻路径表：命中后只 stat 那一个文件（≈20 µs），不再扫目录 —— /cost 走的就是这条；
 *   2) 全量列表：2 秒 TTL，savedFiles / dayFiles / treeFiles / newest 共用。
 *
 * 语义与旧实现一致：深度 ≤ 3、只认 session.v4.jsonl.zstd、命中判定是目录名包含会话 id。
 * 新鲜度：TTL 内可能漏掉刚新建的会话目录（最多 2 秒）；turn/start、turn/end 会调 invalidate()。
 */
import { readdirSync, statSync } from "node:fs";
import { join, dirname, basename } from "node:path";

export const LOG_NAME = "session.v4.jsonl.zstd";

/* 目录 session-<sid8> → sid8（小写、去掉 session- 前缀） */
export function sidOf(path) {
  return basename(dirname(String(path || ""))).replace(/^session-/, "").toLowerCase();
}

export function createSessionIndex({ root, logName = LOG_NAME, ttl = 2000, maxDepth = 3 } = {}) {
  let at = 0;
  let files = null;
  let walks = 0;
  let lastMs = 0;
  const bySid = new Map(); // sid -> path（常驻，扫到过就记住）

  function walk() {
    const t0 = Date.now();
    const out = [];
    const rec = (dir, depth) => {
      if (depth > maxDepth) return;
      let items;
      try {
        items = readdirSync(dir, { withFileTypes: true });
      } catch {
        return; /* 目录不在 / 没权限：当空 */
      }
      for (const it of items) {
        const p = join(dir, it.name);
        let isDir = it.isDirectory();
        if (it.isSymbolicLink()) {
          /* 会话目录偶尔是 junction：要 stat 一次才知道能不能进 */
          try {
            isDir = statSync(p).isDirectory();
          } catch {
            continue;
          }
        }
        if (isDir) {
          rec(p, depth + 1);
          continue;
        }
        if (it.name !== logName) continue;
        try {
          const st = statSync(p);
          const entry = { path: p, sid: sidOf(p), mtimeMs: st.mtimeMs, size: st.size };
          out.push(entry);
          bySid.set(entry.sid, p);
        } catch {
          /* 刚被删：跳过 */
        }
      }
    };
    rec(root, 0);
    at = Date.now();
    walks += 1;
    lastMs = at - t0;
    return out;
  }

  /* force=true 时忽略 TTL 重扫（索引重建路径用） */
  function list(force = false) {
    if (force || !files || Date.now() - at > ttl) files = walk();
    return files;
  }

  /* 路径 → 当前文件状态（只 1 次 stat）；文件没了就把它从路径表里剔掉 */
  function fresh(path) {
    let st;
    try {
      st = statSync(path);
    } catch {
      bySid.delete(sidOf(path));
      return null;
    }
    return { path, sid: sidOf(path), mtimeMs: st.mtimeMs, size: st.size };
  }

  return {
    list,
    /* 会话有任何写入就调一次：下次读立刻重扫 */
    invalidate() {
      at = 0;
    },
    /* 最近写入的会话（旧 findSession("default") 的语义） */
    newest() {
      let best = null;
      for (const f of list()) if (!best || f.mtimeMs > best.mtimeMs) best = f;
      return best;
    },
    /* 目录名包含 id 即命中（与旧 walk 一致，大小写不敏感）；先走路径表，未命中再扫一次。
       注意：新版本会话目录叫 `session-<uuid>`、老版本叫 `<uuid>`，而宿主/前端传过来的 id
       两种形态都有（事件里是 `session-<uuid>`，前端快照里是裸 uuid），所以查询先去前缀、
       再按「键包含查询」匹配——2026-10-30 胶囊「不跳数」就是这个：传 `session-…` 时
       键被剥了前缀，反向 includes 永远不成立。 */
    find(sid) {
      const s = String(sid || "")
        .toLowerCase()
        .replace(/^session-/, "");
      if (!s) return null;
      const match = () => {
        const exact = bySid.get(s);
        if (exact) return exact;
        for (const [k, v] of bySid) if (k.includes(s) || k === s) return v;
        return null;
      };
      let path = match();
      if (!path) {
        list(); // TTL 内不会重复扫
        path = match();
      }
      return path ? fresh(path) : null;
    },
    since(ms) {
      return list().filter((f) => f.mtimeMs >= ms);
    },
    stats() {
      return { walks, lastMs, ageMs: at ? Date.now() - at : -1, size: files ? files.length : 0, sids: bySid.size, ttl };
    },
  };
}
