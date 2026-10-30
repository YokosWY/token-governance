/**
 * dsh-tg-account — 宿主半边
 *
 * 只做一件事：把当前会话的账算出来，挂成同源路由 /tg-account/api/*，给浏览器半边取。
 * 口径与 TG（token-governance）一致：input×P_in + cacheRead×P_cache + output×P_out，
 * 价格读 config.yaml 的 prices_usd_per_mtok 与 rate；读不到就用 flash 默认值。
 *
 * 安全：只读会话日志；trim 开关只写 _local/tg-trim.json。
 *
 * 章节（按文件顺序，找代码直接搜这几个词）：
 *   状态文件原子写        writeFileAtomic
 *   可选依赖路径          accountPaths / capabilities
 *   价表与计价            readPrices / readPriceBook / priceFor / eventCny
 *   会话日志读取          zstdExe / decompress / findSession（走 lib/scan.js 索引）
 *   会话汇总              summarizeSession / analyzeSession / createCompScanner
 *   今日与累计、子代理树   savedFiles / dayTotal / refreshTree / subagentRollup
 *   血统与链级自动切换     readLineage / chainIndex / applyAutoSwitch / turnEndRecord
 *   交接卡                handoffArgs / ensureHandoff / scanCard / cardMeta
 *   输出瘦身              TRIM_RULES / readTrim / trimPromptText / mountTrimHook
 *   HTTP 路由             apply() 内的 handler + sendJson
 */
import { existsSync, readFileSync, readdirSync, renameSync, statSync, unlinkSync, writeFileSync, mkdirSync } from "node:fs";
import { join, dirname, basename } from "node:path";
import { homedir } from "node:os";
import { spawn, spawnSync } from "node:child_process";
import zlib from "node:zlib";
import { buildCandidate, buildNotice, estTokens, utf8Bytes, DEFAULT_CFG } from "./compress.js";
import { LOG_NAME, createSessionIndex } from "./scan.js";

export const inject = ["webServer"];

// home 按安装解析（2026-10-25）：官方版与 NEXT 各存一份状态，不串账。
// NEXT 启动时注入 DSH_HOME=<那套 DSH 的数据目录>；官方版没注入则回落 ~/.dsh（junction → 官方 home）。
const DSH_HOME = (process.env.DSH_HOME || join(homedir(), ".dsh")).replace(/\\/g, "/");
const SESSIONS = join(DSH_HOME, "sessions");
const STATE_DIR = join(DSH_HOME, "_local");
/* sessions 目录只扫一次、2 秒内复用：见 lib/scan.js 头注释（原先一次请求要扫 3–4 遍） */
const sessionIndex = createSessionIndex({ root: SESSIONS });

/* 状态文件原子写：先写同目录 .tmp 再 rename。
   之前直接覆盖写，进程写到一半被杀就留半截 JSON，下次读一律当「没有」——
   自动切换开关、今日缓存、血统会静默重置，且没有任何报错。 */
function writeFileAtomic(file, text) {
  try {
    mkdirSync(dirname(file), { recursive: true });
  } catch {
    /* 目录已在或建不了：交给下面的写操作报错 */
  }
  const tmp = file + ".tmp-" + process.pid;
  try {
    writeFileSync(tmp, text, "utf8");
    renameSync(tmp, file);
    return true;
  } catch {
    /* rename 在个别挂载点/杀软下会失败：退回直接写，不留 .tmp */
    try {
      writeFileSync(file, text, "utf8");
      return true;
    } finally {
      try {
        unlinkSync(tmp);
      } catch {
        /* 没有就没得删 */
      }
    }
  }
}
const TRIM_FILE = join(STATE_DIR, "tg-trim.json");
const LINEAGE_FILE = join(STATE_DIR, "tg-lineage.json");

/*
 * 外部依赖路径（2026-10-29：让别的机器也能用）
 * 解析顺序：环境变量 > $DSH_HOME/_local/tg-account.json（install_account.py 写）。
 * 不硬编码本机路径：没配就 root 为空 → 价表用内置价、handoff 关掉，账目照算。
 * 三样依赖全是可选的：价表缺了用内置价；handoff.py 缺了只关掉交接卡 / 切换 / 自动切换；
 * zstd 缺了先找 PATH，再退 Node 内置 zstd。
 */
const TG_ROOT_DEFAULT = "";
const ACCOUNT_CFG_FILE = join(STATE_DIR, "tg-account.json");
// 原地改属性，别重新赋值：测试要能通过导出引用清缓存
const accountCfgCache = { at: 0, cfg: null };

function accountPaths() {
  const now = Date.now();
  if (accountCfgCache.cfg && now - accountCfgCache.at < 3000) return accountCfgCache.cfg;
  let file = {};
  try {
    const raw = JSON.parse(readFileSync(ACCOUNT_CFG_FILE, "utf8"));
    if (raw && typeof raw === "object" && !Array.isArray(raw)) file = raw;
  } catch {
    /* 没配置就用默认 */
  }
  const env = process.env;
  const pick = (envKey, fileKey, fallback) => {
    const v = env[envKey] || file[fileKey];
    return v && typeof v === "string" ? v.replace(/\\/g, "/") : fallback;
  };
  const root = pick("TG_ACCOUNT_ROOT", "root", TG_ROOT_DEFAULT);
  // root 为空时不要把 "config.yaml" 拼成 "/config.yaml"：给空串，消费者各自降级（读不到 → 内置价 / 关交接卡）。
  const under = (rel) => (root ? root + rel : "");
  const cfg = {
    root,
    config: pick("TG_ACCOUNT_CONFIG", "config", under("/config.yaml")),
    prices: pick("TG_ACCOUNT_PRICES", "prices", under("/data/prices.json")),
    handoff: pick("TG_ACCOUNT_HANDOFF", "handoff", under("/scripts/handoff.py")),
    zstd: pick("TG_ACCOUNT_ZSTD", "zstd", "zstd"),
    python: pick("TG_ACCOUNT_PYTHON", "python", ""),
    memoryRoot: pick("TG_ACCOUNT_MEMORY_ROOT", "memoryRoot", ""),
  };
  accountCfgCache.cfg = cfg;
  accountCfgCache.at = now;
  return cfg;
}
// 自动切换（2026-10-29）：开关按会话存宿主；轮末信号与卡就绪状态落盘，重启后还能判断。
// 前端读 localStorage 就够，但轮末要刷交接卡的决策在宿主，宿主必须自己有一份。
const AUTOSWITCH_FILE = join(STATE_DIR, "tg-autoswitch.json");
const TURNEND_FILE = join(STATE_DIR, "tg-turn-end.json");

const DEFAULTS = { input: 0.15, cached: 0.003, output: 0.6, rate: 7.1, window: 200000 };

/**
 * 压缩通知的统一识别式：两代标记同口径
 *   [save-token #c3po compressed: 4,251 -> 2,077 bytes ...]   ← 2026-10-25 前（dsh-plugin-save-token）
 *   [tg-trim #0ws1 compressed: 10,369 -> 2,083 bytes ...]     ← 2026-10-25 起（TG 内置，见 lib/compress.js）
 * 换实现不换账：历史压缩收益继续按同一口径计入 component。
 */
export const COMPRESS_NOTICE_RE = /\[(?:save-token|tg-trim) #[a-z0-9]+ (?:losslessly re-encoded|compressed): ([\d,]+) -> ([\d,]+) bytes/g;

/**
 * 记账专用：只认「工具结果文本末尾」的整条通知。
 * 旧口径按行跑全局正则，会把 read 到的源码注释、ops_bash 回显的示例当成真实压缩（实测假阳性）。
 */
const NOTICE_TAIL_RE = /\[(?:save-token|tg-trim) #([a-z0-9]+) (?:losslessly re-encoded|compressed): ([\d,]+) -> ([\d,]+) bytes[^\]]*\]\s*$/;

function contentText(content) {
  if (typeof content === "string") return content;
  if (!Array.isArray(content)) return "";
  let out = "";
  for (const b of content) {
    if (!b || b.type !== "text" || typeof b.text !== "string") continue;
    out += b.text;
  }
  return out;
}

/**
 * 压缩记账的唯一入口：按事件还原，只数 tool/result 结果末尾的 notice。
 * - read / save_token_expand 被钩子豁免，历史脏数据也跳过
 * - 同一条通知在日志里重复出现（重放/双写）只计一次；key 带体积，避免 4 位 id 碰撞误伤
 * events 里 [{ savedB, at }]，at = 该通知出现前已完成的 assistant 步数。
 */
export function createCompScanner() {
  const events = [];
  const calls = new Map();
  const seen = new Set();
  let steps = 0;
  return {
    events,
    feed(o) {
      if (!o || !o.type) return;
      const type = o.type;
      if (type === "tool/call") {
        calls.set(o.data && o.data.callId, (o.data && o.data.name) || "");
        return;
      }
      if (type === "assistant/message") {
        steps += 1;
        return;
      }
      if (type !== "tool/result") return;
      const msg = (o.data && o.data.message) || {};
      const name = calls.get(msg.toolCallId) || "";
      if (name === "read" || name === "save_token_expand") return;
      const tail = contentText(msg.content).trimEnd();
      const hit = tail.match(NOTICE_TAIL_RE);
      if (!hit) return;
      const before = Number(hit[2].replace(/,/g, ""));
      const after = Number(hit[3].replace(/,/g, ""));
      if (!(before > after)) return;
      const key = hit[1] + ":" + before + ":" + after;
      if (seen.has(key)) return;
      seen.add(key);
      events.push({ savedB: before - after, at: steps });
    },
  };
}

/** 便捷封装：完整日志 → 通知列表（测试与一次性扫描用；热路径请直接 feed 已解析事件）。 */
export function collectCompNotices(raw) {
  const scanner = createCompScanner();
  for (const line of raw.split("\n")) {
    if (!line.trim()) continue;
    let o;
    try {
      o = JSON.parse(line);
    } catch {
      continue;
    }
    scanner.feed(o);
  }
  return scanner.events;
}

function readPrices() {
  const out = { ...DEFAULTS };
  try {
    const txt = readFileSync(accountPaths().config, "utf8");
    const pick = (re) => {
      const m = txt.match(re);
      return m ? parseFloat(m[1]) : null;
    };
    const i = pick(/^\s*input:\s*([\d.]+)/m);
    const c = pick(/^\s*cached:\s*([\d.]+)/m);
    const o = pick(/^\s*output:\s*([\d.]+)/m);
    if (i > 0) out.input = i;
    if (c > 0) out.cached = c;
    if (o > 0) out.output = o;
    const r = txt.match(/^\s*rate:\s*([\d.]+)/m);
    if (r) out.rate = parseFloat(r[1]);
    const w = txt.match(/context_window:\s*([\d.]+)/m);
    if (w) out.window = parseInt(w[1], 10);
  } catch {
    /* 配置读不到就用默认价 */
  }
  return out;
}

/* ------------------------------------------------------------------
 * 价表：config.yaml 是真源，prices.json 是 ledger.py 导出的归一化结果。
 * 解析顺序与 ledger.py 完全一致（两端必须同一套语义，否则数字会打架）：
 *   provider:model → model → 免费 → 缺 source 按 default → provider 默认 → 家族前缀 → 未定价
 * 未定价返回 [null, "unpriced"]：tokens 照记、钱记 0，并在胶囊里显式报出来。
 * ------------------------------------------------------------------ */
const ZERO_ROW = { cacheMiss: 0, cacheHit: 0, output: 0, peak: null };
let priceBookCache = { sig: "", book: null };

function legacyBook(p) {
  return {
    schema: "legacy",
    legacy: true,
    sourceLabel: "config.yaml 单套价",
    fingerprint: "legacy:" + p.input + "/" + p.cached + "/" + p.output + "@" + p.rate,
    fx: { usdToCny: p.rate },
    models: {},
    providers: {},
    default: { cacheMiss: p.input, cacheHit: p.cached, output: p.output, peak: null },
    free: { models: [], suffixes: [], patterns: [] },
    peak: { enabled: false, windows: [], holidays: [] },
    window: p.window,
  };
}

function readPriceBook() {
  let sig = "";
  try {
    const st = statSync(accountPaths().prices);
    sig = st.mtimeMs + ":" + st.size;
    if (priceBookCache.book && priceBookCache.sig === sig) return priceBookCache.book;
    const j = JSON.parse(readFileSync(accountPaths().prices, "utf8"));
    const book = {
      schema: j.schema || "tg-prices/1",
      legacy: false,
      sourceLabel: "prices.json" + (j.fingerprint ? " " + String(j.fingerprint).slice(5, 13) : ""),
      fingerprint: j.fingerprint || sig,
      fx: { usdToCny: Number((j.fx || {}).usdToCny) || 7.1 },
      models: j.models || {},
      providers: j.providers || {},
      default: j.default || null,
      free: {
        models: (j.free && j.free.models) || [],
        suffixes: (j.free && j.free.suffixes) || [],
        patterns: (j.free && j.free.patterns) || [],
      },
      peak: {
        enabled: !!(j.peak && j.peak.enabled),
        windows: (j.peak && j.peak.windows) || [],
        holidays: (j.peak && j.peak.holidays) || [],
      },
      window: readPrices().window,
    };
    priceBookCache = { sig, book };
    return book;
  } catch {
    const book = legacyBook(readPrices());
    priceBookCache = { sig: "", book };
    return book;
  }
}

function isFreeModel(book, provider, model) {
  const free = book.free || {};
  const full = provider + ":" + model;
  if ((free.models || []).indexOf(model) >= 0 || (free.models || []).indexOf(full) >= 0) return true;
  for (const s of free.suffixes || []) if (s && model.endsWith(s)) return true;
  for (const p of free.patterns || []) {
    if (!p) continue;
    const re = new RegExp("^" + String(p).replace(/[.+?^${}()|[\]\\]/g, "\\$&").replace(/\*/g, ".*") + "$");
    if (re.test(model)) return true;
  }
  return false;
}

/* 家族前缀只认整词边界：deepseek-v4.1-flash 不许吃掉 deepseek-v4.1-flash-free */
function familyMatch(model, name) {
  if (!model || !name) return false;
  if (model === name) return true;
  if (model.startsWith(name)) return "-._/:@".indexOf(model.charAt(name.length)) >= 0;
  return false;
}

function priceFor(book, provider, model) {
  const prov = String(provider || "unknown");
  const mod = String(model || "unknown");
  const full = prov + ":" + mod;
  const models = book.models || {};
  if (models[full]) return [models[full], "exact"];
  if (models[mod]) return [models[mod], "model"];
  if (book.legacy) return [book.default, "legacy"];
  if (isFreeModel(book, prov, mod)) return [ZERO_ROW, "free"];
  if (mod === "unknown" || prov === "unknown") return [book.default, "missing-source"];
  const pd = (book.providers && book.providers[prov] && book.providers[prov].default) || null;
  if (pd) return [pd, "provider-default"];
  const names = Object.keys(models).sort(function (a, b) { return b.length - a.length; });
  for (const name of names) if (familyMatch(mod, name)) return [models[name], "family"];
  return [null, "unpriced"];
}

function isPeakTime(book, ms) {
  if (!book.peak || !book.peak.enabled) return false;
  const d = new Date(ms || Date.now());
  const day = d.getFullYear() + "-" + String(d.getMonth() + 1).padStart(2, "0") + "-" +
    String(d.getDate()).padStart(2, "0");
  if ((book.peak.holidays || []).indexOf(day) >= 0) return false;
  const h = d.getHours();
  return (book.peak.windows || []).some(function (w) { return h >= w[0] && h < w[1]; });
}

/* 单条 usage 的人民币成本；cacheWrite 按 cacheMiss 计（与 ledger.py 同口径） */
function eventCny(book, row, inputWithWrite, cacheRead, output, ms) {
  const r = row.peak && isPeakTime(book, ms) ? row.peak : row;
  const usd = ((inputWithWrite || 0) * (r.cacheMiss || 0) + (cacheRead || 0) * (r.cacheHit || 0) +
    (output || 0) * (r.output || 0)) / 1e6;
  return usd * book.fx.usdToCny;
}

/* "p:model" → 价格行；对不上就退回 default（只给「省了多少」这类反事实用） */
function rowFor(book, key) {
  const i = String(key || "").indexOf(":");
  const prov = i > 0 ? key.slice(0, i) : "unknown";
  const mod = i > 0 ? key.slice(i + 1) : String(key || "unknown");
  const hit = priceFor(book, prov, mod);
  return hit[0] || book.default || { cacheMiss: 0.15, cacheHit: 0.003, output: 0.6, peak: null };
}

function dominantModel(map) {
  let best = "";
  let top = -1;
  for (const entry of map.entries()) {
    const tok = entry[1];
    const n = typeof tok === "number" ? tok
      : (tok.input || 0) + (tok.cached || 0) + (tok.out || 0) + (tok.output || 0);
    if (n > top) { top = n; best = entry[0]; }
  }
  return best || "unknown:unknown";
}

/* 胶囊里的按模型明细：只留有钱或有很多调用的，别把面板撑爆 */
function modelRows(map) {
  const rows = [];
  for (const entry of map.entries()) {
    const v = entry[1];
    rows.push({
      provider: v.provider,
      model: v.model,
      calls: v.calls,
      input: v.input,
      cached: v.cached,
      out: v.out,
      cny: Math.round((v.cny || 0) * 1e4) / 1e4,
      how: v.how,
      free: v.how === "free",
    });
  }
  rows.sort(function (a, b) { return b.cny - a.cny || b.calls - a.calls; });
  return rows;
}

let zstdPick = null;
/* zstd 可执行文件：配置值 → PATH → 常见名。都没成就返回配置值，让调用方走 Node 兜底 */
function zstdExe() {
  if (zstdPick) return zstdPick;
  for (const c of [accountPaths().zstd, "zstd", "zstd.exe"]) {
    try {
      const r = spawnSync(c, ["-V"], { windowsHide: true, timeout: 5000 });
      if (r.status === 0) {
        zstdPick = c;
        return c;
      }
    } catch {
      /* 试下一个 */
    }
  }
  zstdPick = accountPaths().zstd;
  return zstdPick;
}

function decompress(path) {
  // 会话日志是多帧 zstd 拼接：zlib.zstdDecompressSync 只解第一帧（实测 230 B vs 2.8 MB），
  // 必须先走 CLI 的 -dc（会一路解到帧尾），模块只作最后兜底。
  const candidates = [...new Set([zstdExe(), "zstd", "zstd.exe"])];
  for (const exe of candidates) {
    try {
      const r = spawnSync(exe, ["-dc", path], { maxBuffer: 512 * 1024 * 1024 });
      if (r.status === 0 && r.stdout && r.stdout.length) return r.stdout;
    } catch {
      /* 试下一个 */
    }
  }
  if (typeof zlib.zstdDecompressSync === "function") {
    try {
      return zlib.zstdDecompressSync(readFileSync(path));
    } catch {
      /* 兜底也失败 */
    }
  }
  throw new Error("解压失败：找不到可用的 zstd（CLI 与模块都没成）");
}

function findSession(sessionId) {
  const want = String(sessionId || "").toLowerCase();
  if (!want || want === "default") {
    /* 退回最近写入的会话 */
    return sessionIndex.newest();
  }
  return sessionIndex.find(want);
}

const memo = new Map();
const handoffState = new Map();

/* ------------------------------------------------------------------
 * 自动切换（2026-10-29，用户定案）
 *   开关按会话存（默认关，打开后不自动关）；新会话没有记录 = 默认关 → 不会连环切。
 *   触发 = 该会话 turn/end 且 reason.kind === "completed"（报错 / 取消不切）。
 *   切之前必须把交接卡刷到「这一轮之后」：auto 模式不带 --if-absent + 带 --no-task，
 *   落到 auto-<sid8>/00-card.md，不覆盖任务夹里手写的卡。
 * ------------------------------------------------------------------ */
const turnEndState = new Map(); // sid -> { turn, kind, at, cardReady, cardPath, cardPending }
let turnEndLoaded = false;

function loadTurnEnd() {
  if (turnEndLoaded) return;
  turnEndLoaded = true;
  try {
    const raw = JSON.parse(readFileSync(TURNEND_FILE, "utf8"));
    if (raw && typeof raw === "object" && !Array.isArray(raw)) {
      for (const [sid, rec] of Object.entries(raw)) {
        if (!rec || typeof rec !== "object") continue;
        turnEndState.set(sid, {
          turn: typeof rec.turn === "number" ? rec.turn : null,
          kind: typeof rec.kind === "string" ? rec.kind : "unknown",
          at: typeof rec.at === "number" ? rec.at : 0,
          cardReady: !!rec.cardReady,
          cardPath: typeof rec.cardPath === "string" ? rec.cardPath : null,
          cardPending: false,
        });
      }
    }
  } catch {
    /* 没记录就是没记录 */
  }
}

function persistTurnEnd() {
  try {
    // 子代理轮末也会进来，但没开关就不需要长期留：只留最近 300 条
    const entries = [...turnEndState.entries()].sort((a, b) => (b[1].at || 0) - (a[1].at || 0)).slice(0, 300);
    const out = {};
    for (const [sid, rec] of entries) {
      out[sid] = {
        turn: rec.turn, kind: rec.kind, at: rec.at,
        cardReady: !!rec.cardReady, cardPath: rec.cardPath || null,
      };
    }
    mkdirSync(join(DSH_HOME, "_local"), { recursive: true });
    writeFileAtomic(TURNEND_FILE, JSON.stringify(out, null, 1));
  } catch {
    /* 落盘失败不影响本次判断 */
  }
}

let autoSwitchCache = { at: 0, map: {} };
function readAutoSwitch() {
  const now = Date.now();
  if (now - autoSwitchCache.at < 1000) return autoSwitchCache.map;
  let map = {};
  try {
    const raw = JSON.parse(readFileSync(AUTOSWITCH_FILE, "utf8"));
    if (raw && typeof raw === "object" && !Array.isArray(raw)) map = raw;
  } catch {
    /* 没开过 */
  }
  autoSwitchCache = { at: now, map };
  return map;
}

function writeAutoSwitch(map) {
  autoSwitchCache = { at: 0, map };
  try {
    mkdirSync(join(DSH_HOME, "_local"), { recursive: true });
    writeFileAtomic(AUTOSWITCH_FILE, JSON.stringify(map, null, 1));
    return true;
  } catch {
    return false;
  }
}

/* 打开开关时记基线：只对「打开之后结束的轮次」生效，避免点开瞬间拿旧卡切走 */
function armAutoSwitch(prev, lastTurnEndAt) {
  const rec = Object.assign({}, prev || {});
  rec.on = true;
  rec.user = true; // 用户显式操作，优先于「母/子」默认判定
  delete rec.initializedAt;
  rec.armedTurn = lastTurnEndAt && typeof lastTurnEndAt.turn === "number" ? lastTurnEndAt.turn : 0;
  rec.armedAt = Date.now();
  return rec;
}

function autoChildren(map) {
  const list = map && map._children;
  return Array.isArray(list) ? list : [];
}

/* 血统链（2026-10-29 用户口径）：一条链只有一个母对话（链首），链内共用同一个开关。
   子对话再转接出来的还是子对话，链首不变。 */
function rootWalk(sidIn, parents) {
  let cur = sid8(sidIn);
  if (!cur) return "";
  const map = parents && typeof parents === "object" ? parents : {};
  const seen = new Set();
  for (let i = 0; i < 64; i++) {
    if (seen.has(cur)) break;
    seen.add(cur);
    const rec = map[cur];
    const parent = rec && rec.parent ? sid8(rec.parent) : "";
    if (!parent || parent === cur) break;
    cur = parent;
  }
  return cur;
}

function chainRoot(sidIn) {
  let map = {};
  try {
    map = lineageParents();
  } catch {
    map = {};
  }
  return rootWalk(sidIn, map);
}

/* 用户拨一次开关 = 整条链一起改（链内一致）。
   链记录落在链首，各会话只留自己的基线 armedTurn，避免把母对话的基线带到子对话。 */
function applyAutoSwitch(map, sidIn, on, lastTurnEnd) {
  const out = Object.assign({}, map || {});
  const sid = sid8(sidIn);
  if (!sid) return out;
  const root = chainRoot(sid) || sid;
  const now = Date.now();
  const chain = Object.assign({}, (root !== sid && out[root] && typeof out[root] === "object" ? out[root] : {}));
  chain.on = !!on;
  chain.user = true;
  chain.armedAt = now;
  const base = Object.assign({}, out[sid] && typeof out[sid] === "object" ? out[sid] : {});
  if (on) base.armedTurn = armAutoSwitch(base, lastTurnEnd).armedTurn;
  // 同链的其他旧记录只留基线，不再各存一份 on/user
  for (const key of Object.keys(out)) {
    if (key === "_children" || key === root || key === sid) continue;
    const rec = out[key];
    if (!rec || typeof rec !== "object") continue;
    let same = false;
    try {
      same = chainRoot(key) === root;
    } catch {
      same = false;
    }
    if (!same) continue;
    delete rec.on;
    delete rec.user;
    delete rec.armedAt;
    if (Object.keys(rec).length) out[key] = rec;
    else delete out[key];
  }
  if (root === sid) {
    // base 提供基线，chain 提供开关；同名字段以 chain 为准
    out[sid] = Object.assign({}, base, chain);
  } else {
    out[root] = chain;
    if (Object.keys(base).length) out[sid] = base;
    else delete out[sid];
  }
  return out;
}

/* 母子判定（2026-10-29 用户口径）：前面没有相关对话 = 母对话；已有母对话产生 = 子对话。
   依据是血统链：LINEAGE_FILE 里显式记的 parent，或续跑会话首条消息里的卡路径推断。 */
const lineageParentCache = { at: 0, map: {} };
function lineageParents() {
  const now = Date.now();
  if (now - lineageParentCache.at < 5000) return lineageParentCache.map;
  let map = {};
  try {
    map = Object.assign({}, parentIndex(), explicitParents());
  } catch {
    map = {};
  }
  lineageParentCache.map = map;
  lineageParentCache.at = now;
  return map;
}

function hasLineageParent(sid) {
  const key = String(sid || "").toLowerCase();
  if (!key) return false;
  const p = lineageParents()[key];
  return !!(p && p.parent && p.parent !== key);
}

/* 切出来的会话记为「子对话」：默认关，避免连环切；用户在子对话里手动打开会移除该标记 */
function markAutoChild(sid) {
  const key = sid8(sid);
  if (!key) return;
  const map = Object.assign({}, readAutoSwitch());
  const list = autoChildren(map).slice();
  if (list.indexOf(key) < 0) list.push(key);
  map._children = list.slice(-200);
  writeAutoSwitch(map);
}

function unmarkAutoChild(map, sid) {
  const list = autoChildren(map).filter((x) => x !== sid);
  if (list.length) map._children = list;
  else delete map._children;
}

/* 纯函数：从内核 turn/end 事件提记录（reason.kind = completed / error / cancelled 等） */
function turnEndRecord(event) {
  const data = (event && event.data) || {};
  const reason = data.reason || {};
  return {
    turn: typeof data.turn === "number" ? data.turn : null,
    kind: typeof reason.kind === "string" ? reason.kind : "unknown",
    at: Date.now(),
  };
}

/* 被转接/续跑出去的会话：血统里有没有会话把本会话当 parent（2026-10-29 用户口径：
   已经交出去的对话只显示「已转接」，不再参与轮末判定，免得反复弹「待转接」）。 */
function transferredTo(sidIn) {
  const sid = sid8(sidIn);
  if (!sid) return null;
  let map = {};
  try {
    map = lineageParents();
  } catch {
    map = {};
  }
  const kids = [];
  for (const child of Object.keys(map)) {
    const rec = map[child];
    if (!rec || !rec.parent) continue;
    if (child === sid) continue;
    if (sid8(rec.parent) === sid) kids.push(child);
  }
  if (!kids.length) return null;
  kids.sort();
  return kids[kids.length - 1];
}

function autoSwitchView(sidIn) {
  // /cost 的 payload.sid 是完整 UUID，/autoswitch 的是 8 位——统一归一到 sid8，
  // 否则同一会话会写出两条记录，前端读到的 armedTurn 永远是 0。
  const sid = sid8(sidIn);
  const map = readAutoSwitch();
  let rec = map[sid] || null;
  const te = turnEndState.get(sid) || null;
  const root = chainRoot(sid) || sid;
  // 子对话 = 链上有前序（链首不是自己），或本插件记录的切出会话；母对话 = 链首
  const child = root !== sid || autoChildren(map).indexOf(sid) >= 0;
  // 开关是链级的：链首那份记录优先，其次是本会话自己的旧记录（0.9.0 及更早写的）
  const chainRec = root !== sid && map[root] && map[root].user === true ? map[root] : null;
  const selfRec = rec && (rec.user === true || (!rec.initializedAt && rec.armedAt)) ? rec : null;
  const explicit = chainRec || selfRec;

  const transferred = transferredTo(sid);

  const armedTurn = transferred
    ? null
    : rec && typeof rec.armedTurn === "number"
      ? rec.armedTurn
      : te && typeof te.turn === "number"
        ? te.turn
        : 0;

  if (!transferred && (!rec || typeof rec.armedTurn !== "number")) {
    // 首次见到：只落基线。基线必须落盘——每轮重算成最新 turn 就永远不会触发。
    const next = Object.assign({}, map);
    next[sid] = Object.assign({}, rec || {}, {
      armedTurn,
      initializedAt: (rec && rec.initializedAt) || Date.now(),
    });
    writeAutoSwitch(next);
  } else if (selfRec && root !== sid && !chainRec) {
    // 老数据迁移：子对话里单独存过 on → 提升成整条链的开关
    writeAutoSwitch(applyAutoSwitch(map, sid, !!selfRec.on, te));
  }

  // 一条链一个开关，默认开（2026-10-29 用户定）；母/子只表示血统位置，不再决定开关
  const on = explicit ? explicit.on !== false : true;
  return {
    on,
    armedTurn,
    child,
    chainRoot: root,
    transferred: transferred ? { to: transferred } : null,
    lastTurnEnd: te ? { turn: te.turn, kind: te.kind, at: te.at } : null,
    cardReady: !!(te && te.cardReady && te.cardPath),
    card: te && te.cardPath ? { path: te.cardPath } : null,
  };
}

function handoffArgs(sid, force) {
  const P = accountPaths();
  const args = [
    P.handoff,
    "--session", String(sid),
    "--slug", "auto-" + sid8(sid),
    "--config", P.config,
    "--dsh-home", DSH_HOME,
  ];
  // force：每轮刷新（自动切换用），落 auto-<sid8> 夹，不碰任务夹手写卡
  args.push(force ? "--no-task" : "--if-absent");
  return args;
}

function refreshAutoCard(sid) {
  const rec = turnEndState.get(sid);
  if (!rec || rec.cardPending) return;
  // handoff.py 是可选组件：没装就保持「卡未就绪」，前端不切、也不报错
  if (!existsSync(accountPaths().handoff)) {
    persistTurnEnd();
    return;
  }
  rec.cardPending = true;
  rec.cardReady = false;
  rec.cardPath = null;
  try {
    const child = spawn(pyCmd(), handoffArgs(sid, true), { windowsHide: true });
    let out = "";
    child.stdout.on("data", (b) => {
      out += b.toString("utf8");
      if (out.length > 4000) out = out.slice(0, 4000);
    });
    child.on("error", () => {
      rec.cardPending = false;
      rec.cardReady = false;
      persistTurnEnd();
    });
    child.on("close", (code) => {
      const m = out.match(/(?:交接卡：|未覆盖：)\s*(.+)/);
      rec.cardPath = m ? m[1].trim() : null;
      rec.cardReady = code === 0 && !!rec.cardPath;
      rec.cardPending = false;
      if (rec.cardPath) {
        const ok = { due: true, state: "ok", cmd: "", path: rec.cardPath };
        handoffState.set(String(sid), ok);
        handoffState.set(String(sid) + ":force", ok);
      }
      persistTurnEnd();
    });
  } catch {
    rec.cardPending = false;
    rec.cardReady = false;
    persistTurnEnd();
  }
}

function noteTurnEnd(sid, event) {
  if (!sid) return;
  loadTurnEnd();
  const base = turnEndRecord(event);
  turnEndState.set(sid, {
    turn: base.turn,
    kind: base.kind,
    at: base.at,
    cardReady: false,
    cardPath: null,
    cardPending: false,
  });
  // 2026-10-29 用户口径：不管有没有到「该切」，轮末都把卡刷到这一轮之后，
  // 这样你没超限也能随手「切过去」开新对话做特殊操作。
  if (base.kind === "completed") refreshAutoCard(sid);
  else persistTurnEnd();
}


/**
 * 到点自动写交接卡：n* ≤ 15（现在切划算）时跑一次 handoff.py --if-absent。
 * 不阻塞请求：异步 spawn，下一次轮询（5s）就能拿到结果。
 * 失败不影响胶囊——payload.handoff.cmd 里始终带着可手工执行的命令。
 */
function pyCmd() {
  const forced = accountPaths().python;
  if (forced) return forced;
  if (pyCmd._hit) return pyCmd._hit;
  for (const c of ["python", "py", "python3"]) {
    try {
      const r = spawnSync(c, ["-V"], { windowsHide: true });
      if (r.status === 0) {
        pyCmd._hit = c;
        return c;
      }
    } catch {
      /* 试下一个 */
    }
  }
  pyCmd._hit = "python";
  return pyCmd._hit;
}

/* 能力自检：面板与 README 用它说清「缺什么、还能不能用」，别让用户猜 */
function capabilities() {
  const P = accountPaths();
  const has = (p) => {
    try {
      return existsSync(p);
    } catch {
      return false;
    }
  };
  return {
    handoff: has(P.handoff) ? "ready" : "missing",
    config: has(P.config) ? "ready" : "missing",
    prices: has(P.prices) ? "prices.json" : "builtin",
    python: pyCmd(),
    zstd: zstdExe(),
    root: P.root,
    stateDir: STATE_DIR,
  };
}

/* 交接卡：不再等「切了划算」才写（2026-10-29 用户口径：随时都要有卡，方便手动开新对话做特殊操作）。
   不阻塞请求：异步 spawn，下一次轮询（5s）就能拿到结果。
   失败不影响胶囊——payload.handoff.cmd 里始终带着可手工执行的命令。 */
function ensureHandoff(sid, nStar, steps) {
  const slug = "auto-" + String(sid).slice(0, 8);
  // --config 必须显式给：spawn 继承宿主 cwd，相对路径 config.yaml 解析不到 →
  // handoff.py 会回落到 ~/tg-memory（C 盘），卡片就散在系统盘
  // --dsh-home 必须带上：官方版与 NEXT 各有会话目录，不给就会去第一个 home 里找而找不到。
  const P = accountPaths();
  const cmd = `${pyCmd()} "${P.handoff}" --session ${sid} --slug ${slug} --config "${P.config}" --dsh-home "${DSH_HOME}" --if-absent`;
  // handoff.py 是可选组件：没装就明说，别 spawn 一个必然失败的进程
  if (!existsSync(P.handoff)) {
    return { due: false, cmd, unavailable: true, reason: "未找到 handoff.py：" + P.handoff };
  }
  // 一轮都没跑完的会话没有可交接的内容，等它有了再生成
  if (!(typeof steps === "number" && steps > 0)) return { due: false, cmd };
  const cur = handoffState.get(sid);
  if (cur && (cur.state === "ok" || cur.state === "running")) return cur;
  // 失败后 60 秒内不再 spawn：轮询是 5 秒一次，不设冷却会把 python 反复拉起来
  if (cur && cur.state === "error" && Date.now() - (cur.at || 0) < 60000) return cur;
  const rec = { due: true, state: "running", cmd, path: null, at: Date.now() };
  handoffState.set(sid, rec);
  try {
    const child = spawn(pyCmd(), [P.handoff, "--session", String(sid), "--slug", slug, "--config", P.config, "--dsh-home", DSH_HOME, "--if-absent"],
      { windowsHide: true });
    let out = "";
    child.stdout.on("data", (b) => {
      out += b.toString("utf8");
      if (out.length > 4000) out = out.slice(0, 4000);
    });
    child.on("error", () => {
      rec.state = "error";
      handoffState.set(sid, rec);
    });
    child.on("close", (code) => {
      rec.state = code === 0 ? "ok" : "error";
      rec.at = Date.now();
      const m = out.match(/(?:交接卡：|未覆盖：)\s*(.+)/);
      rec.path = m ? m[1].trim() : null;
      rec.detail = out.slice(0, 300);
      handoffState.set(sid, rec);
    });
  } catch {
    rec.state = "error";
    rec.at = Date.now();
  }
  return rec;
}


/*
 * 交接卡定位：给面板上的「复制交接卡」按钮供正文。
 * 两条来源，先信 stdout 的权威路径，再按目录名兜底（重启后 handoffState 是空的）。
 * 目录名规则来自 handoff.py --slug auto-<sid8>；memory_root 读不到时 handoff.py 会落到 ~/tg-memory，
 * 所以两个根都扫，取最新 mtime 的那张。
 */
function cardRoots() {
  const roots = [];
  const P = accountPaths();
  if (P.memoryRoot) roots.push(P.memoryRoot.replace(/\\/g, "/"));
  try {
    const txt = readFileSync(P.config, "utf8");
    const m = txt.match(/^\s*memory_root:\s*["']?([^"'\r\n#]+)/m);
    if (m && m[1].trim()) roots.push(m[1].trim().replace(/\\/g, "/"));
  } catch {
    /* 没配置就只扫兜底根 */
  }
  roots.push(join(homedir(), "tg-memory"));
  return [...new Set(roots)];
}

function sid8(v) {
  return String(v || "")
    .replace(/^session-/, "")
    .replace(/[^0-9a-zA-Z-]/g, "")
    .slice(0, 8);
}

/*
 * 卡片定位：三处来源，取最新的那张
 *   1. 任务夹里的本会话卡 `01-<sid8>.md`（2026-10-25 起：卡绑任务夹，多会话不互盖）
 *   2. 任务夹的 `99-cards.json` 索引指向的卡（改名/手改过也能找到）
 *   3. 老规则：`*-auto-<sid8>/00-card.md`
 */
function scanCard(root, key) {
  let best = null;
  const take = (f) => {
    try {
      const st = statSync(f);
      if (st.isFile() && (!best || st.mtimeMs > best.mtime)) best = { path: f, mtime: st.mtimeMs };
    } catch {
      /* 没有这张卡 */
    }
  };
  const walk = (dir, depth) => {
    if (depth > 3 || !key) return;
    let items = [];
    try {
      items = readdirSync(dir);
    } catch {
      return;
    }
    for (const name of items) {
      const p = join(dir, name);
      let st;
      try {
        st = statSync(p);
      } catch {
        continue;
      }
      if (!st.isDirectory()) continue;
      take(join(p, "01-" + key + ".md"));
      try {
        const idx = JSON.parse(readFileSync(join(p, "99-cards.json"), "utf8"));
        const e = idx && idx.cards && idx.cards[key];
        if (e && e.file) take(join(p, String(e.file)));
      } catch {
        /* 没索引或坏了都算正常 */
      }
      if (name.includes("-auto-" + key)) take(join(p, "00-card.md"));
      walk(p, depth + 1);
    }
  };
  walk(root, 0);
  return best;
}

function findCard(sid) {
  const key = sid8(sid);
  if (!key) return null;
  const rec = handoffState.get(sid) || handoffState.get(key);
  if (rec && rec.path) {
    try {
      statSync(rec.path);
      return { path: rec.path, mtime: 0 };
    } catch {
      /* 路径过期，走扫描 */
    }
  }
  let best = null;
  for (const root of cardRoots()) {
    const hit = scanCard(root, key);
    if (hit && (!best || hit.mtime > best.mtime)) best = hit;
  }
  return best;
}

function cardMeta(sid) {
  const hit = findCard(sid);
  if (!hit) return null;
  try {
    const st = statSync(hit.path);
    return { path: hit.path, mtime: st.mtimeMs, chars: readFileSync(hit.path, "utf8").length };
  } catch {
    return null;
  }
}

function readCard(sid) {
  const hit = findCard(sid);
  if (!hit) return null;
  try {
    const text = readFileSync(hit.path, "utf8");
    return { path: hit.path, chars: text.length, mtime: statSync(hit.path).mtimeMs, text };
  } catch {
    return null;
  }
}

function sessionSid(session) {
  const found = findSession(session);
  if (!found) return sid8(session);
  return sid8(basename(dirname(found.path)));
}

/*
 * 母会话血统：给侧栏行标颜色 + 序号用。
 *   来源 1：对话夹里的 01-<sid8>.md / 99-cards.json —— 同一任务夹 = 同一条链，顺序按 at / 文件名
 *   来源 2：<DSH_HOME>/_local/tg-lineage.json —— 「切过去」新建的子会话，显式记 parent
 *   来源 2 优先（显式 > 推断）
 * 输出：{ "<sid8>": { g: 组名, n: 第几个, t: 组内总数, dir?: 任务夹 } }
 */
function readLineage(file = LINEAGE_FILE) {
  try {
    const d = JSON.parse(readFileSync(file, "utf8"));
    if (d && d.sessions && typeof d.sessions === "object") return d;
  } catch {
    /* 没建档或坏了都算正常 */
  }
  return { v: 1, sessions: {} };
}

function writeLineage(d, file = LINEAGE_FILE) {
  try {
    mkdirSync(dirname(file), { recursive: true });
    writeFileAtomic(file, JSON.stringify(d, null, 1));
  } catch {
    /* 写不进去不影响面板 */
  }
}

function cardGroups(root) {
  const out = [];
  let workspaces = [];
  try {
    workspaces = readdirSync(root, { withFileTypes: true }).filter((d) => d.isDirectory());
  } catch {
    return out;
  }
  for (const ws of workspaces) {
    let tasks = [];
    try {
      tasks = readdirSync(join(root, ws.name), { withFileTypes: true }).filter((d) => d.isDirectory());
    } catch {
      continue;
    }
    for (const task of tasks) {
      const dir = join(root, ws.name, task.name);
      let files = [];
      try {
        files = readdirSync(dir);
      } catch {
        continue;
      }
      const seen = new Map();
      for (const f of files) {
        const m = /^01-([0-9a-zA-Z-]{4,})\.md$/.exec(f);
        if (m) seen.set(m[1], 0);
      }
      try {
        const idx = JSON.parse(readFileSync(join(dir, "99-cards.json"), "utf8"));
        for (const [k, v] of Object.entries((idx && idx.cards) || {})) {
          const at = Date.parse(String((v && v.at) || ""));
          seen.set(k, Number.isFinite(at) ? at : 0);
        }
      } catch {
        /* 没索引就看文件名 */
      }
      if (!seen.size) continue;
      const list = [...seen.keys()].sort((a, b) => (seen.get(a) - seen.get(b)) || a.localeCompare(b));
      out.push({ g: `${ws.name}/${task.name}`, dir, keys: list });
    }
  }
  return out;
}

function chainIndex(reg) {
  const parent = {};
  for (const [k, v] of Object.entries(reg || {})) if (v && v.parent) parent[k] = sid8(v.parent);
  const nodes = new Set([...Object.keys(parent), ...Object.values(parent)]);
  const byRoot = new Map();
  for (const k of nodes) {
    let cur = k;
    let guard = 0;
    const seen = new Set();
    while (parent[cur] && !seen.has(cur) && guard++ < 50) {
      seen.add(cur);
      cur = parent[cur];
    }
    if (!byRoot.has(cur)) byRoot.set(cur, []);
    byRoot.get(cur).push(k);
  }
  const depthOf = (k) => {
    let d = 0;
    let c = k;
    while (parent[c] && d < 50) {
      d += 1;
      c = parent[c];
    }
    return d;
  };
  const out = {};
  for (const [root, list] of byRoot) {
    list.sort((a, b) => depthOf(a) - depthOf(b));
    list.forEach((k, i) => {
      out[k] = { g: `chain:${root}`, n: i + 1, t: list.length };
    });
  }
  return out;
}

/* 显式「切过去」记下的 parent：{ 子sid8: { parent: 父sid8 } }（与 chainIndex 的入参同形） */
function explicitParents(file = LINEAGE_FILE) {
  const out = {};
  for (const [k, v] of Object.entries(readLineage(file).sessions || {})) {
    const c = sid8(k).toLowerCase();
    const p = v && v.parent ? sid8(v.parent).toLowerCase() : "";
    if (c && p && c !== p) out[c] = { parent: p };
  }
  return out;
}

/*
 * 自动推断 parent（2026-10-26）：续跑会话的首条消息会写「先读交接卡 <…>/<slug>/00-card.md」，
 *   slug 形如 `<日期>-auto-<父sid8>`；任务夹则是 `<日期>-<名字>/01-<父sid8>.md`。
 * 首条消息在 projcache 的 titleInput.val.first.text（实测 10 个会话命中），只认这一条 ——
 * 后续轮次 read 别人的卡不会误连。
 */
const PROJ_CACHE = join(DSH_HOME, "storages", "session_projcache", "sessions");

function parentIndex(cacheDir = PROJ_CACHE) {
  const out = {};
  let files = [];
  try {
    files = readdirSync(cacheDir);
  } catch {
    return out;
  }
  for (const f of files) {
    const cm = /^session-([0-9a-f]{8})/i.exec(f);
    if (!cm) continue;
    const child = cm[1].toLowerCase();
    let text = "";
    try {
      const j = JSON.parse(readFileSync(join(cacheDir, f), "utf8"));
      text = String((j && j.record && j.record.rows && j.record.rows.titleInput && j.record.rows.titleInput.val && j.record.rows.titleInput.val.first && j.record.rows.titleInput.val.first.text) || "");
    } catch {
      continue;
    }
    if (!text || !/0\d-[0-9A-Za-z_-]+\.md/i.test(text)) continue;
    const hit = /([0-9A-Za-z._-]{4,})[\\/]{1,2}(0\d-[0-9A-Za-z_-]+\.md)/.exec(text);
    if (!hit) continue;
    const slug = hit[1];
    const file = hit[2];
    let parent = "";
    const auto = /-auto-([0-9a-f]{4,8})$/i.exec(slug);
    if (auto) parent = sid8(auto[1]).toLowerCase();
    else {
      const fm = /^0\d-([0-9a-f]{4,8})\.md$/i.exec(file);
      if (fm) parent = sid8(fm[1]).toLowerCase();
    }
    if (parent && parent !== child) out[child] = { parent };
  }
  return out;
}

function lineageMap(cacheDir = PROJ_CACHE) {
  const map = {};
  for (const root of cardRoots()) {
    for (const group of cardGroups(root)) {
      if (group.keys.length < 2) continue; // 单条不成链，侧栏不标（2026-10-26 定）
      group.keys.forEach((k, i) => {
        map[k] = { g: group.g, n: i + 1, t: group.keys.length, dir: group.dir };
      });
    }
  }
  const parents = Object.assign({}, parentIndex(cacheDir), explicitParents());
  Object.assign(map, chainIndex(parents));
  for (const k of Object.keys(map)) if (!(map[k].t >= 2)) delete map[k];
  return map;
}

function recordLineage(child, parent, file = LINEAGE_FILE) {
  const c = sid8(child);
  const p = sid8(parent);
  if (!c || !p || c === p) return null;
  const d = readLineage(file);
  d.sessions[c] = { parent: p, at: new Date().toISOString().slice(0, 16).replace("T", " ") };
  d.v = 1;
  writeLineage(d, file);
  return d.sessions[c];
}


/* 纯函数：一份会话日志（raw）+ 一份价表 → 全部用量与成本口径。
   拆出来是为了可测：账目逻辑不该只能靠「打开一个真实会话看数字」验证。 */
export function summarizeSession(raw, book) {
  const rate = book.fx.usdToCny;
  const byModel = new Map();
  let cnyAll = 0;
  let unpricedCalls = 0;
  let unpricedTok = 0;
  const turns = [];
  let steps = 0;
  let inTok = 0;
  let readTok = 0;
  let outTok = 0;
  let lastCtx = 0;
  let firstCtx = 0;
  // 压缩记账：只认工具结果末尾的真实通知（见 createCompScanner）。
  // 两代标记同口径 —— 历史是 save-token 的，2026-10-25 起是 TG 自己的 tg-trim。
  const comp = createCompScanner();
  // 今日（2026-10-27）：同一份日志里只计本地日内的步 —— 跨天会话不能把昨天的钱带进今天
  const dayStart = localDayStart();
  let cnyToday = 0;

  for (const line of raw.split("\n")) {
    if (!line.trim()) continue;
    let o;
    try {
      o = JSON.parse(line);
    } catch {
      continue;
    }
    comp.feed(o);
    if (o.type === "turn/start") turns.push({ steps: 0, input: 0, cached: 0, out: 0, cny: 0 });
    if (o.type !== "assistant/message") continue;
    const u = (o.data && o.data.usage) || {};
    const i = u.inputTokens || 0;
    const c = u.cacheReadTokens || 0;
    const cw = u.cacheWriteTokens || 0;
    const ot = u.outputTokens || 0;
    const src = (o.data && o.data.message && o.data.message.source) || {};
    const prov = src.provider || "unknown";
    const model = src.model || "unknown";
    const hit = priceFor(book, prov, model);
    const row = hit[0];
    const cnyEv = row ? eventCny(book, row, i + cw, c, ot, o.time) : 0;
    if (cnyEv) {
      const tEv = Number(o.time || 0);
      if (!tEv || tEv >= dayStart) cnyToday += cnyEv; // 无 time 的旧事件按今天计（与 costInWindow 同口径）
    }
    const mk = prov + ":" + model;
    let bm = byModel.get(mk);
    if (!bm) {
      bm = { provider: prov, model: model, calls: 0, input: 0, cached: 0, out: 0, cny: 0, how: hit[1] };
      byModel.set(mk, bm);
    }
    bm.calls += 1;
    bm.input += i + cw;
    bm.cached += c;
    bm.out += ot;
    if (row) {
      bm.cny += cnyEv;
      cnyAll += cnyEv;
    } else {
      unpricedCalls += 1;
      unpricedTok += i + cw + c + ot;
    }
    if (!turns.length) turns.push({ steps: 0, input: 0, cached: 0, out: 0, cny: 0 });
    const t = turns[turns.length - 1];
    t.steps += 1;
    t.input += i + cw;
    t.cached += c;
    t.out += ot;
    t.cny = (t.cny || 0) + cnyEv;
    steps += 1;
    inTok += i;
    readTok += c;
    outTok += ot;
    lastCtx = i + c;
    if (!firstCtx) firstCtx = i + c;
  }

  /*
   * 本体省（不含切会话）＝压缩/去重省下的钱。
   * 每个被压块：首次入上下文按输入价算，之后每多一步就省一次缓存价（它本来就吃缓存重发）。
   * 纯记账复原，不是估算：字节数来自日志，token 按 4 B/tok 折算。
   * 价按本会话主力模型（token 最多）取，多模型会话不再被一套 DS 价抹平。
   */
  const compRow = rowFor(book, dominantModel(byModel));
  const pIn = (compRow.cacheMiss * rate) / 1e6;
  const pCache = (compRow.cacheHit * rate) / 1e6;
  const notices = comp.events;
  let compBytes = 0;
  let compTok = 0;
  let compCny = 0;
  for (const n of notices) {
    const tok = n.savedB / 4;
    compBytes += n.savedB;
    compTok += tok;
    compCny += tok * pIn + tok * Math.max(0, steps - n.at) * pCache;
  }

  // 换会话的经济性（全部按钱算，不用百分比）：
  //   每步省下 = (C - P0) × 缓存价          —— 换会话后上下文回落，少重发的就是这部分
  //   冷启动代价 = P0 × 输入价 + 重建 × 输入价
  //   回本步数 n* = 冷启动代价 / 每步省下   —— 剩余步数 > n* 才值得切
  const p0 = firstCtx || lastCtx;
  const savePer = Math.max(0, (lastCtx - p0) * pCache);
  const rebuild = 3000;
  const rebuildCny = (p0 + rebuild) * pIn; // 切过去要重发的：首屏上下文 + 重建交接卡预算
  const nStar = savePer > 0 ? Math.ceil(rebuildCny / savePer) : null;
  // 「切了能省多少」——按 100 步算净额（毛省 − 一次性重建），和界面上的「每百步省」同口径
  const horizon = 100;
  const saveGross100 = savePer * horizon;
  const saveNet100 = saveGross100 - rebuildCny;

  return {
    turns, steps, inTok, readTok, outTok, lastCtx, firstCtx,
    byModel, cnyAll, cnyToday, unpricedCalls, unpricedTok,
    compRow, notices, compBytes, compTok, compCny,
    p0, savePer, rebuildCny, nStar, saveGross100, saveNet100,
  };
}

function account(sessionId) {
  const found = findSession(sessionId);
  if (!found) return { ok: false, error: "未找到会话日志" };
  const key = found.path;
  const selfSid = String(found.path).split(/[\\/]/).slice(-2)[0].replace(/^session-/, "") || String(sessionId);
  const st = statSync(found.path);
  const sig = st.mtimeMs + ":" + st.size;
  const cached = memo.get(key);
  if (cached && cached.sig === sig) {
    // 会话没变：重活不再算，但「今日」和「子代理」得跟着别人的消费走（否则要等本会话下一步才更新）
    if (typeof cached.cnyToday === "number") {
      try {
        cached.payload.today = dayTotal(found.path, round4(cached.cnyToday), cached.selfTodaySaved || 0);
      } catch {
        /* 今日算挂了就沿用旧值，不拖垮胶囊 */
      }
      try {
        cached.payload.subagents = subagentRollup(selfSid);
        cached.payload.cnyWithChildren = round4((Number(cached.payload.cny) || 0) + (cached.payload.subagents.cny || 0));
      } catch {
        /* 子代理树挂了就沿用旧值 */
      }
      try {
        // 链上别的会话可能在这期间还在花钱：链级账目每次请求重算（读缓存索引，不解压）
        cached.payload.chain = chainRollup(selfSid, {
          selfPath: key,
          selfCny: cached.payload.cny,
          selfSaved: cached.payload.savedSelf ? cached.payload.savedSelf.cny : null,
        });
      } catch {
        /* 链级账目挂了就沿用旧值 */
      }
    }
    return cached.payload;
  }

  const prices = readPrices(); // 只用于上下文窗口
  const book = readPriceBook(); // 计价：按 provider:model 查价表
  const rate = book.fx.usdToCny;
  const raw = decompress(found.path).toString("utf8");
  const sum = summarizeSession(raw, book);
  const turns = sum.turns;
  const steps = sum.steps;
  const inTok = sum.inTok;
  const readTok = sum.readTok;
  const outTok = sum.outTok;
  const lastCtx = sum.lastCtx;
  const firstCtx = sum.firstCtx;
  const byModel = sum.byModel;
  const cnyAll = sum.cnyAll;
  const cnyToday = sum.cnyToday;
  const unpricedCalls = sum.unpricedCalls;
  const unpricedTok = sum.unpricedTok;
  const compRow = sum.compRow;
  const notices = sum.notices;
  const compBytes = sum.compBytes;
  const compTok = sum.compTok;
  const compCny = sum.compCny;
  const p0 = sum.p0;
  const savePer = sum.savePer;
  const rebuildCny = sum.rebuildCny;
  const horizon = 100; // 与 summarizeSession 内一致：界面按「每百步」算净省
  const nStar = sum.nStar;
  const saveGross100 = sum.saveGross100;
  const saveNet100 = sum.saveNet100;


  const cny = cnyAll;
  for (const t of turns) if (typeof t.cny !== "number") t.cny = 0;
  const ctxTok = inTok + readTok;
  // 本会话实际省下的：配置反事实（工具 schema + 技能目录）+ 工具输出压缩；不含任何预测
  let savedSelf = null;
  let selfTodaySaved = 0;
  try {
    const selfR = analyzeSession(raw);
    if (selfR) {
      selfTodaySaved = Math.max(0, selfR.todaySaved || 0);
      savedSelf = {
        cny: round4(selfR.saveHi + (selfR.compCny || 0)),
        cfg: round4(selfR.saveHi),
        low: round4(selfR.saveLo),
        schema: round4(selfR.schemaHi),
        catalog: round4(selfR.catalogHi),
        comp: round4(selfR.compCny || 0),
        compCount: selfR.compCount || 0,
        spillCount: selfR.spillCount || 0,
      };
    }
  } catch {
    savedSelf = null;
  }

  /* 子代理归并（2026-10-27）：本会话派出去的子会话是真花钱，但记在别的文件里。
     胶囊「本」显示 本体+子树，避免「本 ¥1.40 / 今日 ¥16」这种对不上账的误读。 */
  const subs = (() => {
    try {
      return subagentRollup(selfSid);
    } catch {
      return {
        n: 0, cny: 0, today: 0, steps: 0, maxSteps: 0, maxCny: 0, list: [], stale: true,
        budget: readBudget(), alert: { on: false, reasons: [], worst: null },
      };
    }
  })();

  const payload = {
    ok: true,
    session: sessionId,
    steps,
    turns: turns.length,
    cny: Math.round(cny * 10000) / 10000,
    // 本体 + 子树（同口径：全历史花费）；前端「本」显示这个，悬停再给拆分
    cnyWithChildren: Math.round((cny + (subs.cny || 0)) * 10000) / 10000,
    subagents: subs,
    // 今日消费合计（全量会话按本地日切，再换成本会话「今日」那份；缓存 60 秒，后台算）
    today: (() => {
      try {
        return dayTotal(found.path, round4(cnyToday), selfTodaySaved);
      } catch {
        return { cny: null, saved: null, sessions: 0, stale: true };
      }
    })(),
    hit: ctxTok ? readTok / ctxTok : 0,
    // 按模型明细（2026-10-26）：多模型会话不再被一套 DS 价抹平
    models: modelRows(byModel),
    unpriced: { calls: unpricedCalls, tokens: unpricedTok },
    fx: rate,
    priceSource: book.sourceLabel,
    missPerStep: steps ? Math.round(inTok / steps) : 0,
    ctxRatio: prices.window ? Math.min(1, lastCtx / prices.window) : 0,
    ctxTokens: lastCtx,
    window: prices.window,
    startCtx: p0,
    savePerStep: Math.round(savePer * 1e8) / 1e8,
    nStar,
    // 切对话省多少钱：毛省（每步省×horizon）与净省（扣掉一次性重建），都给前台自己挑
    horizon,
    saveGross100: Math.round(saveGross100 * 1e4) / 1e4,
    rebuildCny: Math.round(rebuildCny * 1e4) / 1e4,
    saveNet100: Math.round(saveNet100 * 1e4) / 1e4,
    // 本体省（不含切会话）：压缩/去重记账复原
    component: {
      hits: notices.length,
      bytes: compBytes,
      tokens: Math.round(compTok),
      cny: Math.round(compCny * 1e4) / 1e4,
    },
    // TG 累计省（历史账，不含切会话）：后台算、缓存 10 分钟
    saved: savedSnapshot(),
    // 本会话自己省下的部分（同一口径）
    savedSelf,
    // 本会话链（2026-10-30）：同一条血统链上所有会话 + 各自子代理的合计
    chain: chainRollup(selfSid, {
      selfPath: key,
      selfCny: cny,
      selfSaved: savedSelf ? savedSelf.cny : null,
    }),
    // 工具输出瘦身（本进程实时计数；历史账已在 component 里按日志统计，别重复加）
    trim: snapshotTrim(),
    // 只看钱：回本步数 ≤ 15 才算「现在切划算」；不看上下文百分比
    shouldSplit: typeof nStar === "number" && nStar <= 15,
    priceNote:
      byModel.size > 1 || unpricedCalls
        ? `${byModel.size} 个模型${unpricedCalls ? ` · 未定价 ${unpricedCalls} 次` : ""}` +
          ` · 主力 ${compRow.cacheMiss}/${compRow.cacheHit}/${compRow.output} USD/M × ${rate}`
        : `${compRow.cacheMiss}/${compRow.cacheHit}/${compRow.output} USD/M × ${rate}`,
    detail: turns.slice(-60).map((t, idx) => ({
      i: Math.max(1, turns.length - turns.slice(-60).length + idx + 1),
      steps: t.steps,
      input: t.input,
      cached: t.cached,
      out: t.out,
      cny: Math.round(t.cny * 10000) / 10000,
    })),
  };
  payload.sid = selfSid;
  memo.set(key, { sig, payload, cnyToday, selfTodaySaved });
  return payload;
}

/*
 * 输出瘦身：面板子菜单 → 写 _local/tg-trim.json → 宿主拼成 systemPrompt 的一段。
 * 规则一条一句、必须可执行；关掉的规则整句不出现（不是写「不要」）。
 */
const TRIM_RULES = [
  { key: "toolEcho", label: "工具输出", desc: "仅列结论、路径与行号，不复述原始输出",
    text: "工具输出不复述：仅保留结论、文件路径与行号；确需原文时给出读取位置（offset/limit），由读者按需调取。" },
  { key: "historyReplay", label: "历史轮次", desc: "前文已陈述内容以「见上」引用",
    text: "前文已陈述的结论、代码与表格以「见上」引用，不再重复展开。" },
  { key: "fileFull", label: "文件全文", desc: "仅给「路径:行号」及不超过 30 行的片段",
    text: "文件不得整篇引用：仅提供「路径:行号」及必要片段（不超过 30 行），其余交由读者自行读取。" },
  { key: "tableJson", label: "表格 / JSON", desc: "超过 20 行仅给表头、关键行与行号范围",
    text: "表格与 JSON 超过 20 行时，仅提供表头、关键行与行号范围，其余注明「见文件」。" },
  { key: "logs", label: "日志", desc: "仅列 error/warn 及前后各 2 行",
    text: "日志仅列 error/warn 级条目及其前后各 2 行，其余折叠为「共 N 行，已省略」。" },
  { key: "codeDiff", label: "代码改动", desc: "以 diff 片段（±15 行）呈现",
    text: "代码修改以 diff 片段（±15 行）与文件路径呈现，不附完整文件。" },
  { key: "filler", label: "客套与总结", desc: "不写寒暄、套话与复述式总结",
    text: "不写寒暄、套话与复述式总结（如「希望对你有帮助」「以上就是」），亦不复述任务本身。" },
];

function defaultTrim() {
  const rules = {};
  for (const r of TRIM_RULES) rules[r.key] = true;
  return { version: 2, master: true, rules };
}

/* 瘦身开关每拼一次 system prompt 就要读一次（每步都可能调）；按 mtime+size 缓存解析结果，
   改文件立刻生效、不重启。返回克隆：/trim POST 会就地改 master/rules，不能动缓存本体。 */
const trimCache = { mtimeMs: -1, size: -1, value: null };

function cloneTrim(v) {
  const rules = {};
  for (const r of TRIM_RULES) rules[r.key] = v.rules[r.key] !== false;
  return { version: 2, master: v.master !== false, rules };
}

function readTrim() {
  let st = null;
  try {
    st = statSync(TRIM_FILE);
  } catch {
    return defaultTrim();
  }
  if (trimCache.value && st.mtimeMs === trimCache.mtimeMs && st.size === trimCache.size) {
    return cloneTrim(trimCache.value);
  }
  let raw = null;
  try {
    raw = JSON.parse(readFileSync(TRIM_FILE, "utf8"));
  } catch {
    return defaultTrim();
  }
  const base = defaultTrim();
  if (!raw || typeof raw !== "object") return base;
  if (raw.version === 2) {
    base.master = raw.master !== false;
    if (raw.rules && typeof raw.rules === "object") {
      for (const r of TRIM_RULES) {
        if (typeof raw.rules[r.key] === "boolean") base.rules[r.key] = raw.rules[r.key];
      }
    }
    trimCache.mtimeMs = st.mtimeMs;
    trimCache.size = st.size;
    trimCache.value = base;
    return cloneTrim(base);
  }
  // v1：{ "session-xxx": true } —— 布尔值当全局默认，避免老文件把瘦身误关
  base.master = Object.values(raw).some((v) => v === true);
  return base;
}

function writeTrim(p) {
  const out = {
    version: 2,
    master: p.master !== false,
    rules: {},
    updatedAt: new Date().toISOString(),
  };
  for (const r of TRIM_RULES) out.rules[r.key] = p.rules[r.key] !== false;
  mkdirSync(dirname(TRIM_FILE), { recursive: true });
  writeFileAtomic(TRIM_FILE, JSON.stringify(out, null, 2));
  trimCache.value = null; // 写路径直接失效，不等 mtime 比对
  return { version: 2, master: out.master, rules: out.rules, updatedAt: out.updatedAt };
}

function trimPromptText(p) {
  if (!p.master) return "";
  const on = TRIM_RULES.filter((r) => p.rules[r.key] !== false);
  if (!on.length) return "";
  return [
    "## 输出规范（词元掌控 · 已启用）",
    "以下各项同时适用于过程说明与最终答复。压缩篇幅不得以损失信息为代价；细节无法确认时，给出文件路径供读者查证。",
    ...on.map((r) => "- " + r.text),
  ].join("\n");
}

/* ------------------------------------------------------------------
 * TG 累计省（历史账）
 *   口径 = 同会话反事实 vs 旧配置，不含切会话、不含第三方 save-token。
 *   实付   = session 日志 usage × flash 价
 *   反事实 = 把「工具 schema」「技能目录」按字节线性放大回旧配置（284–312 kB / 42.5 kB）
 *   省     = 反事实 − 实付，只算 TG 改过的这两块
 * 全量扫几十个会话要半分钟，所以后台异步算 + 落盘缓存 10 分钟，请求只读缓存。
 * ------------------------------------------------------------------ */
const SAVED_FILE = join(DSH_HOME, "_local", "tg-saved.json");
const SAVED_SINCE = new Date(2026, 9, 22, 18, 56).getTime(); // TG 改配置时刻（本机时区）
const SAVED_TTL = 10 * 60 * 1000;
const CF_HIGH = 312000; // 旧配置工具 schema 上界
const CF_LOW = 284000; // 旧配置工具 schema 下界
const CF_CATALOG = 42500; // 旧配置技能目录单条

let savedCur = null;
let savedBusy = false;

const round4 = (v) => Math.round(v * 1e4) / 1e4;

function readSavedCache() {
  try {
    return JSON.parse(readFileSync(SAVED_FILE, "utf8"));
  } catch {
    return null;
  }
}

function savedSnapshot() {
  if (!savedCur) savedCur = readSavedCache();
  // v 变了就重算（v2 的累计掺了预测的切会话净额，v3 只留实际省下的）
  const fresh = !!(savedCur && savedCur.v === 4 && Date.now() - Number(savedCur.at || 0) < SAVED_TTL);
  if (!fresh) refreshSaved();
  if (!savedCur) return { pending: true };
  return { ...savedCur, stale: !fresh };
}

function savedFiles() {
  return sessionIndex.since(SAVED_SINCE).map((f) => f.path);
}

function zstdAsync(path) {
  return new Promise((resolve) => {
    let child;
    try {
      child = spawn(zstdExe(), ["-dc", path], { windowsHide: true });
    } catch {
      resolve(null);
      return;
    }
    const chunks = [];
    let size = 0;
    let done = false;
    const fin = (v) => {
      if (!done) {
        done = true;
        resolve(v);
      }
    };
    child.stdout.on("data", (b) => {
      chunks.push(b);
      size += b.length;
      if (size > 256 * 1024 * 1024) {
        try {
          child.kill();
        } catch {
          /* ignore */
        }
      }
    });
    child.on("error", () => fin(null));
    child.on("close", (code) => fin(code === 0 ? Buffer.concat(chunks) : null));
  });
}

/* python json.dumps 默认分隔符是 ", " / ": "，比 JSON.stringify 每处分隔符多 1 个空格；
 * 不补这 1 个字符，tools_bytes 会偏小 3–4 kB，反事实因子就偏大约 3%。 */
function pyJsonLen(v) {
  const s = JSON.stringify(v);
  let n = s.length;
  let inStr = false;
  let esc = false;
  for (let i = 0; i < s.length; i++) {
    const ch = s[i];
    if (inStr) {
      if (esc) esc = false;
      else if (ch === "\\") esc = true;
      else if (ch === '"') inStr = false;
      continue;
    }
    if (ch === '"') inStr = true;
    else if (ch === ":" || ch === ",") n += 1;
  }
  return n;
}

function msgText(o) {
  const d = o.data || {};
  const m = d.message || d;
  const c = m && m.content;
  if (Array.isArray(c)) return c.map((x) => (x && x.text) || "").join("");
  return typeof c === "string" ? c : "";
}

/* 与 token-governance/scripts/measure_modules.py 的 analyze()+counterfactual() 同算法 */
export function analyzeSession(raw, opts) {
  /* 今日窗口（2026-10-30 用户要「今日省」）：since 之前的事件不计；不传就用本地日 0 点。
     口径与费用侧「今日」一致：无 time 的老事件算今天。 */
  const since = opts && opts.since !== undefined && opts.since !== null ? Number(opts.since) || 0 : localDayStart();
  const acc = Object.create(null);
  const ctx = [];
  const cat = [];
  let toolsBytes = null;
  let steps = 0;
  let turns = 0;
  let inTok = 0;
  let readTok = 0;
  let outTok = 0;
  let firstCtx = 0;
  let lastCtx = 0;
  // 压缩事件：只认工具结果末尾的真实通知（见 createCompScanner），随主循环一次解析
  const compScanner = createCompScanner();
  const tally = new Map(); // provider:model → tokens，用来给「省」挑主力模型价
  // 今日窗口的并行账（同一遍解析里攒，事件时间在 stepTimes 里）
  const stepTimes = [];
  const wAcc = Object.create(null);
  const wCat = [];
  let wSteps = 0;
  let wInTok = 0;
  let wReadTok = 0;
  // DSH 原生落盘（spill-policy）次数：notice 文本固定，直接数
  const spillCount = raw.split("grep this path to search within it").length - 1;
  for (const line of raw.split("\n")) {
    if (!line.trim()) continue;
    let o;
    try {
      o = JSON.parse(line);
    } catch {
      continue;
    }
    compScanner.feed(o);
    const t = o.type;
    if (t === "request/header") {
      if (toolsBytes === null) {
        const tools = (o.data && o.data.header && o.data.header.tools) || [];
        try {
          toolsBytes = pyJsonLen(tools);
        } catch {
          toolsBytes = 0;
        }
      }
      continue;
    }
    if (t === "assistant/message") {
      const u = (o.data && o.data.usage) || {};
      const tEv = Number(o.time || 0);
      const isToday = !tEv || tEv >= since;
      stepTimes.push(tEv);
      for (let k = 0; k < ctx.length; k++) {
        acc[ctx[k][0]] = (acc[ctx[k][0]] || 0) + ctx[k][1];
        if (ctx[k][2]) wAcc[ctx[k][0]] = (wAcc[ctx[k][0]] || 0) + ctx[k][1];
      }
      const sb = toolsBytes || 0;
      if (sb) {
        acc["工具 schema"] = (acc["工具 schema"] || 0) + sb;
        if (isToday) wAcc["工具 schema"] = (wAcc["工具 schema"] || 0) + sb;
      }
      steps += 1;
      if (isToday) {
        wSteps += 1;
        wInTok += u.inputTokens || 0;
        wReadTok += u.cacheReadTokens || 0;
      }
      inTok += u.inputTokens || 0;
      readTok += u.cacheReadTokens || 0;
      outTok += u.outputTokens || 0;
      const srcA = (o.data && o.data.message && o.data.message.source) || {};
      const mkA = (srcA.provider || "unknown") + ":" + (srcA.model || "unknown");
      tally.set(mkA, (tally.get(mkA) || 0) + (u.inputTokens || 0) +
        (u.cacheReadTokens || 0) + (u.outputTokens || 0));
      const stepCtx = (u.inputTokens || 0) + (u.cacheReadTokens || 0);
      if (!firstCtx) firstCtx = stepCtx;
      lastCtx = stepCtx;
      continue;
    }
    if (t === "user/message" || t === "user/delta") turns += 1;
    let mod = null;
    if (t === "tool/result" || t === "tool-result") mod = "工具结果";
    else if (t === "user/message" || t === "user/delta") mod = "用户输入";
    else continue;
    const txt = msgText(o);
    if (!txt) continue;
    const tMsg = Number(o.time || 0);
    const msgToday = !tMsg || tMsg >= since;
    if (txt.indexOf("<available_skills>") >= 0) {
      mod = "技能目录";
      cat.push(txt.length);
      if (msgToday) wCat.push(txt.length);
    } else if (txt.indexOf("system-reminder") >= 0 || txt.indexOf("runtime context") >= 0) {
      mod = "系统提示/提醒";
    }
    ctx.push([mod, txt.length, msgToday]);
  }
  if (!steps) return null;
  const book = readPriceBook();
  const mainRow = rowFor(book, dominantModel(tally));
  const rate = book.fx.usdToCny;
  const pIn = (mainRow.cacheMiss * rate) / 1e6;
  const pCache = (mainRow.cacheHit * rate) / 1e6;
  const pOut = (mainRow.output * rate) / 1e6;
  const real = inTok * pIn + readTok * pCache + outTok * pOut;
  const totalCtx = inTok + readTok;
  let chars = 0;
  for (const k in acc) chars += acc[k];
  let catAvg = 0;
  if (cat.length) {
    let sum = 0;
    for (const n of cat) sum += n;
    catAvg = sum / cat.length;
  }
  let saveHi = 0;
  let saveLo = 0;
  let schemaHi = 0;
  let schemaLo = 0;
  let catalogHi = 0;
  let catalogLo = 0;
  if (totalCtx > 0 && chars > 0) {
    const tpc = totalCtx / chars;
    const blended = (inTok * pIn + readTok * pCache) / totalCtx;
    if (toolsBytes > 0) {
      const c = (acc["工具 schema"] || 0) * tpc * blended;
      schemaHi = (CF_HIGH / toolsBytes - 1) * c;
      schemaLo = (CF_LOW / toolsBytes - 1) * c;
      saveHi += schemaHi;
      saveLo += schemaLo;
    }
    if (catAvg > 0) {
      const c = (acc["技能目录"] || 0) * tpc * blended;
      const f = CF_CATALOG / catAvg - 1;
      catalogHi = f * c;
      catalogLo = f * c;
      saveHi += catalogHi;
      saveLo += catalogLo;
    }
  }
  // 压缩收益：每块首次进上下文按输入价，其后每多一步按缓存价（与本会话 component 同口径）
  const compEvents = compScanner.events;
  let compCny = 0;
  let compBytes = 0;
  for (const e of compEvents) {
    const tok = e.savedB / 4; // 4 B/token，与 component 一致
    compCny += tok * pIn + tok * Math.max(0, steps - e.at) * pCache;
    compBytes += e.savedB;
  }
  /* 今日省（2026-10-30 用户要）：同一套反事实，只算 since 之后进上下文的量 + 今天的步 */
  let wSaveHi = 0;
  const wTotalCtx = wInTok + wReadTok;
  let wChars = 0;
  for (const k in wAcc) wChars += wAcc[k];
  if (wTotalCtx > 0 && wChars > 0) {
    const tpcW = wTotalCtx / wChars;
    const blendedW = (wInTok * pIn + wReadTok * pCache) / wTotalCtx;
    if (toolsBytes > 0) {
      const c = (wAcc["工具 schema"] || 0) * tpcW * blendedW;
      wSaveHi += (CF_HIGH / toolsBytes - 1) * c;
    }
    const wCatAvg = wCat.length ? wCat.reduce((a, b) => a + b, 0) / wCat.length : catAvg;
    if (wCatAvg > 0) {
      const c = (wAcc["技能目录"] || 0) * tpcW * blendedW;
      wSaveHi += (CF_CATALOG / wCatAvg - 1) * c;
    }
  }
  let wComp = 0;
  for (const e of compEvents) {
    const at = Number(e.at) || 0;
    const tEv = at > 0 && at <= stepTimes.length ? stepTimes[at - 1] : 0;
    if (tEv && tEv < since) continue; // 昨天压的块，今天不再算进「今日省」
    let winBefore = 0;
    for (let k = 0; k < at && k < stepTimes.length; k++) {
      const tt = stepTimes[k] || 0;
      if (!tt || tt >= since) winBefore += 1;
    }
    const winAfter = Math.max(0, wSteps - winBefore);
    const tok = e.savedB / 4;
    wComp += tok * pIn + tok * winAfter * pCache;
  }
  const todaySaved = Math.max(0, wSaveHi + wComp);

  // 切会话省（预测值，只用于「回本步数」这个判据，不进钱的账）
  const ctxFirst = firstCtx || lastCtx;
  const savePerStep = Math.max(0, (lastCtx - ctxFirst) * pCache);
  const rebuildCny = (ctxFirst + 3000) * pIn;
  const splitNet = savePerStep * 100 - rebuildCny;
  return {
    real, saveHi: Math.max(0, saveHi), saveLo: Math.max(0, saveLo),
    schemaHi: Math.max(0, schemaHi), catalogHi: Math.max(0, catalogHi),
    compCny: Math.max(0, compCny), compCount: compEvents.length, compBytes, spillCount,
    todaySaved,
    steps, turns, toolsBytes: toolsBytes || 0, catAvg, chars, totalCtx,
    ctxFirst, ctxLast: lastCtx, splitNet100: Math.max(0, splitNet),
  };
}

function refreshSaved() {
  if (savedBusy) return;
  savedBusy = true;
  (async () => {
    const files = savedFiles();
    let hi = 0;
    let lo = 0;
    let sch = 0;
    let cat = 0;
    let real = 0;
    let steps = 0;
    let ok = 0;
    let comp = 0;
    let compN = 0;
    let compB = 0;
    let spill = 0;
    for (const f of files) {
      const buf = await zstdAsync(f);
      if (buf) {
        const r = analyzeSession(buf.toString("utf8"));
        if (r) {
          hi += r.saveHi;
          lo += r.saveLo;
          sch += r.schemaHi;
          cat += r.catalogHi;
          comp += r.compCny || 0;
          compN += r.compCount || 0;
          compB += r.compBytes || 0;
          spill += r.spillCount || 0;
          real += r.real;
          steps += r.steps;
          ok += 1;
        }
      }
      await new Promise((r) => setImmediate(r));
    }
    savedCur = {
      v: 4,
      at: Date.now(),
      // cny = 全部已实现的省（配置反事实 + 工具输出压缩），子项分开给前台逐行显示
      cny: round4(hi + comp),
      cfg: round4(hi),
      low: round4(lo),
      schema: round4(sch),
      catalog: round4(cat),
      comp: round4(comp),
      compCount: compN,
      compBytes: compB,
      spillCount: spill,
      real: round4(real),
      sessions: ok,
      steps,
      since: SAVED_SINCE,
      cf: { schema: [CF_LOW, CF_HIGH], catalog: CF_CATALOG },
    };
    try {
      mkdirSync(dirname(SAVED_FILE), { recursive: true });
      writeFileAtomic(SAVED_FILE, JSON.stringify(savedCur));
    } catch {
      /* 写不进缓存不影响显示 */
    }
    savedBusy = false;
  })().catch(() => {
    savedBusy = false;
  });
}

/* ------------------------------------------------------------------
 * 今日消费（跨会话、跨工作区）
 *   口径 = 本地日内 assistant/message 的 usage × 与「本会话消费」同一套价格；
 *          跨天会话只计今天那部分（按每行 time 归属日子），不是「今天写过就算全量」。
 *   当前会话由 account() 自己算，扫描时跳过它（它每步都在变，重解析最贵）。
 *   后台异步 + 落盘缓存 60 秒：请求只读缓存，不阻塞胶囊。
 * ------------------------------------------------------------------ */
const DAY_FILE = join(DSH_HOME, "_local", "tg-day.json");
const DAY_TTL = 60 * 1000;
const DAY_V = 3; // 缓存格式版本：v3 = 逐文件多存「今日省」（saved）；旧缓存直接当「没有」，免得先显示一个偏低的数

let dayCur = null;
let dayBusy = false;

function localDayKey(d = new Date()) {
  const p = (n) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())}`;
}

/* 本地日 0 点（毫秒）：summarizeSession 与 refreshDay 必须用同一个切点，否则两个口径差一天 */
function localDayStart(d = new Date()) {
  return new Date(d.getFullYear(), d.getMonth(), d.getDate()).getTime();
}

function readDayCache() {
  try {
    return JSON.parse(readFileSync(DAY_FILE, "utf8"));
  } catch {
    return null;
  }
}

/* 只锁「今天窗口内」的那些 message，避免把昨天跑的前半段算进今天 */
/* 窗口内的花费与步数：今日合计只要钱，子代理预算要步数，共用一遍解析（别为步数再解压一次） */
function costStepsInWindow(raw, start, end, book) {
  let cny = 0;
  let steps = 0;
  for (const line of raw.split("\n")) {
    const s = line.trim();
    if (!s || s[0] !== "{") continue;
    let o;
    try {
      o = JSON.parse(s);
    } catch {
      continue;
    }
    if (o.type !== "assistant/message") continue;
    const t = Number(o.time || 0);
    if (t && (t < start || t >= end)) continue;
    steps += 1; // 未定价也记一步：预算按「跑了多少步」算，不按记不记账算
    const u = (o.data && o.data.usage) || {};
    const src = (o.data && o.data.message && o.data.message.source) || {};
    const row = priceFor(book, src.provider || "unknown", src.model || "unknown")[0];
    if (!row) continue; // 未定价：钱记 0，不瞎折价
    cny += eventCny(book, row, (u.inputTokens || 0) + (u.cacheWriteTokens || 0),
      u.cacheReadTokens || 0, u.outputTokens || 0, o.time);
  }
  return { cny, steps };
}

function costInWindow(raw, start, end, book) {
  return costStepsInWindow(raw, start, end, book).cny;
}

/* 全量会话（不再排除调用者）：缓存一旦排除某个会话，别的会话来读就会把自己重复计入。
   2026-10-27 修：排除谁不该写进共享缓存，改由读取时用缓存里的本会话那一项做减法。 */
function dayFiles(since) {
  return sessionIndex.since(since).map((f) => ({ path: f.path, mtimeMs: f.mtimeMs }));
}

/* 纯函数：全量缓存 + 本会话今日额 + 本会话路径 → 今日合计（花费与省，2026-10-30 起省也走这条路）。
   缓存里已经含本会话今天那份，先减掉再加实时值，避免重复计入；本会话不在缓存里（新会话）则不减。 */
export function todayFromCache(cache, selfPath, selfTodayCny, fresh, selfTodaySaved) {
  const files = (cache && cache.files) || {};
  const self = Math.max(0, Number(selfTodayCny) || 0);
  const baked = Number((selfPath && files[selfPath] && files[selfPath].cny) || 0);
  const all = Number((cache && cache.cny) || 0);
  const others = Math.max(0, all - baked);
  const cny = others + self;
  const selfSaved = Math.max(0, Number(selfTodaySaved) || 0);
  const bakedSaved = Number((selfPath && files[selfPath] && files[selfPath].saved) || 0);
  const allSaved = Number((cache && cache.saved) || 0);
  const savedOthers = Math.max(0, allSaved - bakedSaved);
  return {
    cny: round4(cny),
    saved: round4(savedOthers + selfSaved),
    others: round4(others),
    savedOthers: round4(savedOthers),
    // sessions 在缓存里是「今天花过钱的会话数」：本会话没被缓存计到，就补 1
    sessions: Number((cache && cache.sessions) || 0) + (baked > 0 ? 0 : 1),
    at: cache && cache.at,
    stale: !fresh,
  };
}

/* 今日合计 = 全量缓存（按本地日切）− 缓存里本会话那份 + 本会话实时今日额 */
function dayTotal(selfPath, selfTodayCny, selfTodaySaved) {
  const key = localDayKey();
  if (!dayCur) dayCur = readDayCache();
  const have = !!(dayCur && dayCur.day === key && dayCur.v === DAY_V);
  const fresh = have && Date.now() - Number(dayCur.at || 0) < DAY_TTL &&
    dayCur.fp === readPriceBook().fingerprint;
  if (!fresh) refreshDay(key);
  if (!have) return { cny: null, saved: null, sessions: 0, stale: true }; // 首次扫描没回来前显示「…」
  return todayFromCache(dayCur, selfPath, selfTodayCny, fresh, selfTodaySaved);
}

function refreshDay(key) {
  if (dayBusy) return;
  dayBusy = true;
  (async () => {
    const book = readPriceBook();
    const start = localDayStart();
    const end = start + 86400000;
    // 逐文件缓存也带货价指纹：价表换了，旧文件里存的是旧价，必须整个重算
    const prev = dayCur && dayCur.day === key && dayCur.fp === book.fingerprint && dayCur.files
      ? dayCur.files : {};
    const files = {};
    let cny = 0;
    let savedSum = 0;
    let n = 0;
    for (const f of dayFiles(start)) {
      const hit = prev[f.path];
      let c;
      let sv;
      if (hit && Number(hit.mt) === f.mtimeMs && typeof hit.saved === "number") {
        c = Number(hit.cny) || 0;
        sv = Number(hit.saved) || 0;
      } else {
        const buf = await zstdAsync(f.path);
        if (buf) {
          const text = buf.toString("utf8");
          c = costInWindow(text, start, end, book);
          // 今日省：同一段日志按 since 切窗口算（analyzeSession 的 todaySaved）
          const r = analyzeSession(text, { since: start });
          sv = r ? Math.max(0, r.todaySaved || 0) : 0;
        } else {
          c = 0;
          sv = 0;
        }
      }
      files[f.path] = { mt: f.mtimeMs, cny: round4(c), saved: round4(sv) };
      if (c > 0) {
        cny += c;
        n += 1;
      }
      savedSum += sv;
      await new Promise((r) => setImmediate(r));
    }
    // 价格指纹进缓存：价表改了，今天的合计立刻按新价重算（否则会显示旧价）
    dayCur = { v: DAY_V, day: key, at: Date.now(), fp: book.fingerprint, cny: round4(cny), saved: round4(savedSum), sessions: n, files };
    try {
      mkdirSync(dirname(DAY_FILE), { recursive: true });
      writeFileAtomic(DAY_FILE, JSON.stringify(dayCur));
    } catch {
      /* 写不进缓存不影响显示 */
    }
    dayBusy = false;
  })().catch(() => {
    dayBusy = false;
  });
}

/* ------------------------------------------------------------------
 * 子代理树（2026-10-27）
 *   问题：胶囊「本」只算本会话。子代理是**独立会话文件**（头行 parentSession +
 *   origin:"subagent"），钱烧在别的文件里 —— 于是出现「本 ¥1.40、实际 ¥8.51」的账，
 *   用户按「会话」加总永远对不上今日合计。
 *   做法：扫全部会话头行建父树，索引落盘 tg-tree.json、按文件 mtime 增量；
 *   子树花费（全历史 + 今日）归并到父会话，胶囊「本」显示合并值，悬停/面板给拆分。
 *   代价：首建要读全部头行并解压计价（后台、约数秒），之后只处理新变化的文件。
 * ------------------------------------------------------------------ */
const TREE_FILE = join(DSH_HOME, "_local", "tg-tree.json");
const TREE_V = 2; // v2（2026-10-27）：每条带 steps，子代理预算要用；v1 没有步数 → 重建一次
const HEAD_BYTES = 96 * 1024;
const TREE_TTL = 60 * 1000;

/* 子代理预算（2026-10-27）：单个子代理超过任一项就在胶囊上告警。
   可按 `_local/tg-budget.json` 覆盖：{"steps": 40, "cny": 1} */
const SUBAGENT_BUDGET = { steps: 40, cny: 1 };
const BUDGET_FILE = join(DSH_HOME, "_local", "tg-budget.json");

function readBudget() {
  const out = { ...SUBAGENT_BUDGET };
  try {
    const j = JSON.parse(readFileSync(BUDGET_FILE, "utf8"));
    if (Number(j.steps) > 0) out.steps = Number(j.steps);
    if (Number(j.cny) > 0) out.cny = Number(j.cny);
  } catch {
    /* 没配就按默认 */
  }
  return out;
}
let treeCur = null;
let treeBusy = false;
let treeAt = 0;

/* 只取头行：zstd 是流式的，拿到第一个换行就 kill，别把整个会话解开 */
function headAsync(path) {
  return new Promise((resolve) => {
    let child;
    try {
      child = spawn(zstdExe(), ["-dc", path], { windowsHide: true });
    } catch {
      resolve(null);
      return;
    }
    const chunks = [];
    let size = 0;
    let done = false;
    const fin = (v) => {
      if (done) return;
      done = true;
      try {
        child.kill();
      } catch {
        /* 已退出 */
      }
      resolve(v);
    };
    child.stdout.on("data", (b) => {
      chunks.push(b);
      size += b.length;
      if (size >= HEAD_BYTES) fin(Buffer.concat(chunks).toString("utf8"));
    });
    child.on("error", () => fin(null));
    child.on("close", () => fin(Buffer.concat(chunks).toString("utf8")));
  });
}

function parseHead(text) {
  if (!text) return null;
  const line = text.split("\n", 1)[0];
  if (!line || line[0] !== "{") return null;
  try {
    const o = JSON.parse(line);
    if (!o || o.type !== "session") return null;
    return {
      sid: String(o.id || ""),
      parent: String(o.parentSession || ""),
      origin: String(o.origin || "user"),
      depth: Number(o.delegationDepth || 0),
    };
  } catch {
    return null;
  }
}

function treeFiles() {
  return sessionIndex.list().map((f) => ({ path: f.path, mtimeMs: f.mtimeMs }));
}

/* 子会话 id 归一：目录名可能是 "session-xxx" 也可能是纯 uuid，父引用一律 "session-xxx" */
function normSid(v) {
  const s = String(v || "").toLowerCase();
  return s.startsWith("session-") ? s.slice(8) : s;
}

export function treeIndex() {
  if (!treeCur) {
    try {
      treeCur = JSON.parse(readFileSync(TREE_FILE, "utf8"));
    } catch {
      treeCur = null;
    }
    if (!treeCur || treeCur.v !== TREE_V || typeof treeCur.files !== "object") {
      treeCur = { v: TREE_V, at: 0, fp: "", files: {} };
    }
  }
  return treeCur;
}

/* 索引重建：按 mtime 增量，只对新/改的文件读头 + 算全历史花费；后台跑，不阻塞胶囊 */
function refreshTree() {
  if (treeBusy) return;
  if (Date.now() - treeAt < TREE_TTL) return;
  treeAt = Date.now();
  treeBusy = true;
  (async () => {
    const book = readPriceBook();
    const prev = treeIndex();
    const reuse = prev.fp === book.fingerprint ? prev.files || {} : {};
    const files = {};
    let changed = 0;
    for (const f of treeFiles()) {
      const hit = reuse[f.path];
      if (hit && hit.mt === f.mtimeMs) {
        files[f.path] = hit;
        continue;
      }
      const head = parseHead(await headAsync(f.path));
      if (!head) continue;
      const buf = await zstdAsync(f.path);
      const stat = buf
        ? costStepsInWindow(buf.toString("utf8"), 0, Number.POSITIVE_INFINITY, book)
        : { cny: 0, steps: 0 };
      files[f.path] = {
        mt: f.mtimeMs,
        sid: normSid(head.sid),
        parent: normSid(head.parent),
        origin: head.origin,
        depth: head.depth,
        cny: round4(stat.cny),
        steps: stat.steps,
      };
      changed += 1;
      await new Promise((r) => setImmediate(r));
    }
    treeCur = { v: TREE_V, at: Date.now(), fp: book.fingerprint, files };
    try {
      mkdirSync(dirname(TREE_FILE), { recursive: true });
      writeFileAtomic(TREE_FILE, JSON.stringify(treeCur));
    } catch {
      /* 写不进缓存不影响显示 */
    }
    treeBusy = false;
  })().catch(() => {
    treeBusy = false;
  });
}

/* 本会话的全部子孙（按 parentSession 递归，防环） */
export function descendants(sid, tree) {
  const t = tree || treeIndex();
  const self = normSid(sid);
  const byParent = new Map();
  for (const [path, rec] of Object.entries(t.files || {})) {
    if (!rec || !rec.parent) continue;
    if (!byParent.has(rec.parent)) byParent.set(rec.parent, []);
    byParent.get(rec.parent).push({ path, sid: rec.sid });
  }
  const out = [];
  const seen = new Set([self]);
  const stack = [self];
  while (stack.length) {
    const cur = stack.pop();
    for (const c of byParent.get(cur) || []) {
      if (seen.has(c.sid)) continue;
      seen.add(c.sid);
      out.push(c);
      stack.push(c.sid);
    }
  }
  return out;
}

/* 超预算判定（纯函数，便于单测）：按单个子代理判，不是合计 */
export function budgetAlert(maxSteps, maxCny, budget) {
  const b = budget || readBudget();
  const reasons = [];
  if (Number(maxSteps) > b.steps) reasons.push("步数");
  if (Number(maxCny) > b.cny) reasons.push("花费");
  return { on: reasons.length > 0, reasons };
}

/* 子树汇总：{ n, cny(全历史), today(今日), steps, maxSteps, maxCny, list[], budget, alert }
   告警按**单个**子代理判：任务书的「最多 40 步」是对一个子代理说的，不是合计。
   数据不全时标 stale，不编数。 */
function subagentRollup(selfSid) {
  const budget = readBudget();
  const noAlert = { on: false, reasons: [], worst: null };
  const empty = { n: 0, cny: 0, today: 0, steps: 0, maxSteps: 0, maxCny: 0, list: [], stale: true, budget, alert: noAlert };
  try {
    refreshTree();
    const t = treeIndex();
    const kids = descendants(selfSid, t);
    if (!kids.length) return { ...empty, stale: !t.at };
    const cache = readDayCache();
    const okDay = cache && cache.day === localDayKey() && cache.v === DAY_V ? cache.files || {} : null;
    let cny = 0;
    let today = 0;
    let steps = 0;
    let maxSteps = 0;
    let maxCny = 0;
    const list = [];
    for (const k of kids) {
      const rec = (t.files || {})[k.path] || {};
      const c = Number(rec.cny) || 0;
      const st = Number(rec.steps) || 0;
      const dc = okDay ? Number((okDay[k.path] || {}).cny) || 0 : 0;
      cny += c;
      today += dc;
      steps += st;
      if (st > maxSteps) maxSteps = st;
      if (c > maxCny) maxCny = c;
      list.push({
        sid: String(k.sid || "").slice(0, 8),
        cny: round4(c),
        today: round4(dc),
        steps: st,
        origin: rec.origin || "",
      });
    }
    list.sort((a, b) => b.today - a.today || b.cny - a.cny);
    const ba = budgetAlert(maxSteps, maxCny, budget);
    const worst = list.slice().sort((a, b) => b.steps - a.steps)[0] || null;
    return {
      n: kids.length, cny: round4(cny), today: round4(today), steps,
      maxSteps, maxCny, list, stale: !t.at, budget,
      alert: { on: ba.on, reasons: ba.reasons, worst },
    };
  } catch {
    return empty;
  }
}

/* 本会话链（2026-10-30 用户要求：「展开的卡片里要有本会话链」）：
   同一条血统链（链首 + 转接出来的子会话；只有一个会话时也算一条链，n=1）的
   全历史花费与全历史省额，每个成员并上自己的子代理子树。
   花费来自 treeIndex 的逐文件全历史 cny（后台增量缓存，不额外解压）；
   省额没有现成索引，按文件 mtime 做进程内记忆（链上除本会话外基本是闲的，只算一次）。
   索引还没建好 / 链路文件还没进索引时标 stale，前端显示「统计中…」，不拿 0 冒充。 */
const chainSavedMemo = new Map(); // path -> { mt, cny }

function savedOfPath(p, mt) {
  if (!p) return null;
  const hit = chainSavedMemo.get(p);
  if (hit && hit.mt === mt) return hit.cny;
  let cny = null;
  try {
    const r = analyzeSession(decompress(p).toString("utf8"));
    cny = r ? round4(r.saveHi + (r.compCny || 0)) : null;
  } catch {
    cny = null;
  }
  chainSavedMemo.set(p, { mt, cny });
  return cny;
}

function chainRollup(sidIn, deps) {
  const d = deps && typeof deps === "object" ? deps : null;
  const sid = sid8(sidIn);
  if (!sid) return { cny: null, n: 0, members: 0, root: "", stale: true };
  let parents = {};
  if (d && d.parents) parents = d.parents;
  else {
    try {
      parents = lineageParents();
    } catch {
      parents = {};
    }
  }
  const root = rootWalk(sid, parents) || sid;
  let members = [sid];
  try {
    const idx = chainIndex(parents);
    const g = idx[sid] && idx[sid].g ? idx[sid].g : "";
    const set = new Set([root, sid]);
    if (g) for (const [k, v] of Object.entries(idx)) if (v && v.g === g) set.add(k);
    members = [...set];
  } catch {
    members = [...new Set([root, sid])];
  }
  let t = d && d.tree ? d.tree : null;
  if (!t) {
    try {
      refreshTree();
    } catch {
      /* 后台建索引，失败就等下次 */
    }
    t = treeIndex();
  }
  const files = (t && t.files) || {};
  const bySid = new Map();
  // 索引里存的是完整 uuid，血统链用 8 位：两边都归一到 sid8，否则永远查不到（2026-10-30 实测坑）
  for (const [p, rec] of Object.entries(files)) if (rec && rec.sid) bySid.set(sid8(rec.sid), p);
  const seen = new Set();
  let total = 0;
  let counted = 0;
  let savedTotal = 0;
  let savedN = 0;
  // 本会话用实时账（索引最长滞后 60 秒），别的会话用索引值，避免「链合计 < 本会话」这种自相矛盾
  const selfPath = d && typeof d.selfPath === "string" ? d.selfPath : "";
  const selfCny = d && typeof d.selfCny === "number" ? d.selfCny : null;
  const selfSaved = d && typeof d.selfSaved === "number" ? d.selfSaved : null;
  const readSaved = d && typeof d.savedOf === "function" ? d.savedOf : savedOfPath;
  const add = (p, savedOverride) => {
    if (!p || seen.has(p)) return;
    seen.add(p);
    if (selfPath && p === selfPath && selfCny !== null) total += selfCny;
    else total += Number(files[p] && files[p].cny) || 0;
    counted += 1;
    let sv = savedOverride;
    if (sv === undefined) sv = selfPath && p === selfPath && selfSaved !== null ? selfSaved : null;
    if (sv === null) sv = readSaved(p, Number(files[p] && files[p].mt) || 0);
    if (typeof sv === "number") {
      savedTotal += sv;
      savedN += 1;
    }
  };
  for (const m of members) {
    add(bySid.get(m) || (m === sid ? selfPath : ""));
    for (const kid of descendants(m, t)) add(bySid.get(sid8(kid.sid)));
  }
  const stale = !(t && t.at) || counted < members.length;
  return {
    cny: counted ? round4(total) : null,
    savedCny: savedN ? round4(savedTotal) : null,
    savedN,
    n: counted,
    members: members.length,
    root,
    stale,
  };
}

function sendJson(res, status, payload) {
  res.writeHead(status, { "content-type": "application/json; charset=utf-8", "cache-control": "no-store" });
  res.end(JSON.stringify(payload));
}

/* ------------------------------------------------------------------
 * 工具输出瘦身（2026-10-25 融合进 TG 的核心原理）
 *
 * 只做一件事：在「工具结果进上下文」那一瞬间改写一次，之后历史字节不再动
 * （保护供应商前缀缓存），原文先落盘、取回路径写进 notice。
 * 不注册新工具（每条请求常驻 schema 约 110 token，实测换来 2 次调用，不划算）；
 * 取回走现有 read/grep + 原文路径。
 * ------------------------------------------------------------------ */
const TRIM_CFG = { ...DEFAULT_CFG };

const TRIM_CFG_FILE = join(DSH_HOME, "_local", "tg-compress.json");

// 可调阈值：手改 _local/tg-compress.json 覆盖 DEFAULT_CFG 的任意键（5 秒缓存，不用重启）
let trimCfgCache = { at: 0, cfg: TRIM_CFG };
function trimCfg() {
  const now = Date.now();
  if (now - trimCfgCache.at < 5000) return trimCfgCache.cfg;
  let extra = {};
  try {
    const raw = JSON.parse(readFileSync(TRIM_CFG_FILE, "utf8"));
    if (raw && typeof raw === "object" && !Array.isArray(raw)) extra = raw;
  } catch {
    extra = {};
  }
  const cfg = { ...TRIM_CFG, ...extra };
  trimCfgCache = { at: now, cfg };
  return cfg;
}

const TRIM_STATS = { count: 0, bytesBefore: 0, bytesAfter: 0, savedTok: 0, skipped: 0, lastSkip: "" };

// 面板「输出瘦身」总开关同时管机械层：关掉就整条链不动作（2 秒缓存，避免每个工具结果都读文件）
let trimFlagCache = { at: 0, master: true };
function trimMasterOn() {
  const now = Date.now();
  if (now - trimFlagCache.at < 2000) return trimFlagCache.master;
  let master = true;
  try {
    master = readTrim().master !== false;
  } catch {
    master = true;
  }
  trimFlagCache = { at: now, master };
  return master;
}

function snapshotTrim() {
  return {
    on: trimMasterOn(),
    count: TRIM_STATS.count,
    bytesBefore: TRIM_STATS.bytesBefore,
    bytesAfter: TRIM_STATS.bytesAfter,
    savedBytes: TRIM_STATS.bytesBefore - TRIM_STATS.bytesAfter,
    savedTok: Math.round(TRIM_STATS.savedTok),
    skipped: TRIM_STATS.skipped,
    lastSkip: TRIM_STATS.lastSkip,
    cfg: trimCfg(),
  };
}

function flattenPlainText(content) {
  if (typeof content === "string") return content;
  if (!Array.isArray(content)) return undefined;
  let text = "";
  for (const b of content) {
    if (!b || b.type !== "text" || typeof b.text !== "string") return undefined;
    text += b.text;
  }
  return text;
}

function ownerSessionId(exec) {
  try {
    return exec && exec.agent && exec.agent.session && exec.agent.session.header
      ? exec.agent.session.header.id
      : undefined;
  } catch {
    return undefined;
  }
}

async function spillOriginal(ctx, text, exec) {
  const store = typeof ctx.get === "function" ? ctx.get("spillStore") : undefined;
  if (!store || typeof store.saveText !== "function") {
    TRIM_STATS.lastSkip = "没有 spillStore：可逆性没保证，本条放行";
    return null;
  }
  const sessionId = ownerSessionId(exec);
  if (!sessionId) {
    TRIM_STATS.lastSkip = "取不到所属会话 id";
    return null;
  }
  try {
    const ref = await store.saveText({
      owner: { sessionId },
      source: { toolName: exec.name, callId: exec.callId, label: "result" },
      suggestedName: String(exec.name || "tool") + ".txt",
      content: text,
    });
    if (!ref || typeof ref.locator !== "string") {
      TRIM_STATS.lastSkip = "落盘返回里没有 locator";
      return null;
    }
    return ref;
  } catch (e) {
    TRIM_STATS.lastSkip = "落盘失败：" + String(e).slice(0, 120);
    return null;
  }
}

function mountTrimHook(ctx, log) {
  // prepend：抢在其它下游之前拿到最原始的 result，避免二次改写
  ctx.on(
    "tools/post-execute",
    async (exec, result, next) => {
      const decision = await next();
      if (!decision || decision.kind !== "accept") return decision;
      if (Object.prototype.hasOwnProperty.call(decision, "value")) return decision;
      const name = (exec && exec.name) || "";
      // 回读豁免：read 是「写文件再精确读」的正路；压缩自己的取回结果会变成死循环
      if (name === "read" || name === "save_token_expand") return decision;
      if (!trimMasterOn()) return decision; // 面板总开关关闭 = 机械层也不动

      // 任何意外都退回「原样放行」：瘦身是优化，不许它把工具调用弄挂
      try {
        const content = decision.content !== undefined ? decision.content : result && result.content;
        const text = flattenPlainText(content);
        if (text === undefined) return decision; // 含非文本块：宁可不压
        const cfg = trimCfg();
        if (utf8Bytes(text) < cfg.triggerBytes) return decision;

        const cand = buildCandidate(text, cfg);
        if (cand === null) return decision; // 双门控没过

        const ref = await spillOriginal(ctx, text, exec);
        if (!ref) {
          TRIM_STATS.skipped += 1;
          return decision;
        }
        const id = ((((TRIM_STATS.count + 1) * 2654435761) % 1679616) >>> 0).toString(36).padStart(4, "0");
        const finalText = buildNotice(cand, id, ref.locator, cfg);
        TRIM_STATS.count += 1;
        TRIM_STATS.bytesBefore += cand.before;
        TRIM_STATS.bytesAfter += cand.after;
        TRIM_STATS.savedTok += Math.max(0, estTokens(text) - estTokens(finalText));
        log(`压缩 ${name}：${cand.before} -> ${cand.after} B（${cand.strategy}，id ${id}）`);
        return { kind: "accept", content: [{ type: "text", text: finalText }] };
      } catch (e) {
        TRIM_STATS.skipped += 1;
        TRIM_STATS.lastSkip = "异常放行：" + String(e).slice(0, 120);
        return decision;
      }
    },
    { prepend: true }
  );
  log("已挂 tools/post-execute 瘦身钩子");
}

export function apply(ctx) {
  const log = (m) => {
    try {
      ctx.logger ? ctx.logger("[dsh-tg-account] " + m) : console.info("[dsh-tg-account] " + m);
    } catch {
      /* 日志不抛 */
    }
  };

  const handler = (req, res) => {
    try {
      const url = new URL(req.url, "http://127.0.0.1");
      const session = url.searchParams.get("session") || "default";
      if (url.pathname.endsWith("/trim")) {
        if (req.method === "POST") {
          let body = "";
          req.on("data", (c) => {
            body += c;
            if (body.length > 4096) req.destroy();
          });
          req.on("end", () => {
            try {
              const v = JSON.parse(body || "{}");
              const cur = readTrim();
              if (typeof v.master === "boolean") cur.master = v.master;
              if (v.rules && typeof v.rules === "object") {
                for (const r of TRIM_RULES) {
                  if (typeof v.rules[r.key] === "boolean") cur.rules[r.key] = v.rules[r.key];
                }
              }
              const saved = writeTrim(cur);
              sendJson(res, 200, { ok: true, ...saved, rulesMeta: TRIM_RULES.map((r) => ({ key: r.key, label: r.label, desc: r.desc })) });
            } catch {
              sendJson(res, 400, { ok: false, error: "请求体不是合法的 JSON" });
            }
          });
          return;
        }
        const p = readTrim();
        sendJson(res, 200, { ok: true, ...p, rulesMeta: TRIM_RULES.map((r) => ({ key: r.key, label: r.label, desc: r.desc })) });
        return;
      }
      if (url.pathname.endsWith("/lineage")) {
        if (req.method === "POST") {
          let body = "";
          req.on("data", (c) => {
            body += c;
            if (body.length > 4096) req.destroy();
          });
          req.on("end", () => {
            try {
              const v = JSON.parse(body || "{}");
              const rec = recordLineage(v.child, v.parent);
              if (!rec) {
                sendJson(res, 400, { ok: false, error: "child/parent 不合法" });
                return;
              }
              // 切出来的会话 = 子对话：默认关，避免连环切
              markAutoChild(v.child);
              sendJson(res, 200, { ok: true, map: lineageMap() });
            } catch {
              sendJson(res, 400, { ok: false, error: "请求体不是合法的 JSON" });
            }
          });
          return;
        }
        sendJson(res, 200, { ok: true, map: lineageMap() });
        return;
      }
      if (url.pathname.endsWith("/card")) {
        const sid = sessionSid(session);
        const c = readCard(sid);
        if (!c) {
          sendJson(res, 200, { ok: false, error: "未找到交接卡", sid });
          return;
        }
        sendJson(res, 200, { ok: true, sid, path: c.path, chars: c.chars, mtime: c.mtime, text: c.text });
        return;
      }
      if (url.pathname.endsWith("/autoswitch")) {
        const sid = sessionSid(session);
        loadTurnEnd();
        if (req.method === "POST") {
          let body = "";
          req.on("data", (c) => {
            body += c;
            if (body.length > 4096) req.destroy();
          });
          req.on("end", () => {
            try {
              const v = JSON.parse(body || "{}");
              const map = Object.assign({}, readAutoSwitch());
              if (v.on !== true && v.on !== false) {
                sendJson(res, 400, { ok: false, error: "on 必须是布尔值" });
                return;
              }
              // 链级开关：点一次，整条链一起变（子对话不会因为点开就变成母对话）
              writeAutoSwitch(applyAutoSwitch(map, sid, v.on, turnEndState.get(sid)));
              sendJson(res, 200, Object.assign({ ok: true, sid }, autoSwitchView(sid)));
            } catch {
              sendJson(res, 400, { ok: false, error: "请求体不是合法的 JSON" });
            }
          });
          return;
        }
        sendJson(res, 200, Object.assign({ ok: true, sid }, autoSwitchView(sid)));
        return;
      }
      const payload = account(session);
      if (payload && payload.ok) {
        payload.handoff = ensureHandoff(payload.sid, payload.nStar, payload.steps);
        payload.card = cardMeta(payload.sid);
        payload.autoSwitch = autoSwitchView(payload.sid);
        payload.caps = capabilities();
      }
      sendJson(res, 200, payload);
    } catch (e) {
      sendJson(res, 500, { ok: false, error: String(e).slice(0, 200) });
    }
  };

  try {
    ctx.webServer.register({ kind: "prefix", path: "/tg-account", handler }, "dsh-tg-account: 会话账目 API");
    log("已注册 /tg-account/api/*");
  } catch (e) {
    log("路由注册失败：" + String(e));
  }

  // 自动切换：轮末信号。turn/end 只在会话真正结束时落（输出已完整），
  // 比前端轮询 turns 准（turns 在 turn/start 就加）。
  try {
    if (typeof ctx.on === "function") {
      loadTurnEnd();
      ctx.on("session/event", (session, event) => {
        try {
          if (!event || !event.type) return;
          /* 新会话目录 / 新日志只在这两个点出现：顺手让索引下一次读就重扫（省掉最多 2 秒的瞎猜） */
          if (event.type === "turn/start" || event.type === "turn/end") sessionIndex.invalidate();
          if (event.type !== "turn/end") return;
          const sid = sid8(session && session.id);
          if (sid) noteTurnEnd(sid, event);
        } catch {
          /* 轮末通知失败不影响账目 */
        }
      });
      log("已订阅 session/event（turn/end → 自动切换判定）");
    } else {
      log("ctx.on 不可用：自动切换拿不到轮末信号");
    }
  } catch (e) {
    log("session/event 订阅失败：" + String(e));
  }

  // 工具输出瘦身：在「工具结果进上下文」那一刻改写一次（原理见 lib/compress.js 头注释）。
  // 原文先落盘、取回路径写进 notice；不注册新工具，零常驻 schema。
  try {
    if (typeof ctx.on === "function") mountTrimHook(ctx, log);
    else log("ctx.on 不可用：瘦身钩子未挂");
  } catch (e) {
    log("瘦身钩子挂载失败：" + String(e));
  }

  // 瘦身子菜单的实际作用点：每次拼 system prompt 时现读 _local/tg-trim.json，
  // 所以面板里改开关 → 下一步就生效，不必重启。
  // systemPrompt 用 ctx.inject 可选注入（写进 inject 数组会变成硬依赖，缺服务就整插件不加载）。
  try {
    const mountTrim = (sctx) => {
      const sp = sctx && sctx.systemPrompt;
      if (!sp || typeof sp.section !== "function") {
        log("没有 systemPrompt 服务：瘦身开关只记状态，不注入");
        return;
      }
      const mount = () =>
        sp.section({
          name: "tg-account:trim",
          order: 900,
          interpolate: false,
          text: () => trimPromptText(readTrim()),
        });
      if (typeof sctx.effect === "function") sctx.effect(mount, "dsh-tg-account: 输出瘦身纪律");
      else mount();
      log("已挂 systemPrompt 瘦身段");
    };
    if (typeof ctx.inject === "function") ctx.inject(["systemPrompt"], mountTrim);
    else mountTrim(ctx);
  } catch (e) {
    log("瘦身段挂载失败：" + String(e));
  }
}

export default { apply, inject };

// 自测钩子（只给 tmp 桩测用，不对外暴露）：与 TG 的 measure_modules.py 对齐校验
export const _test = { sessionIndex, readTrim, writeTrim, defaultTrim, trimCache, account, analyzeSession, collectCompNotices, readPriceBook, priceFor, eventCny, legacyBook, summarizeSession, modelRows, dominantModel, savedFiles, zstdAsync, refreshSaved, savedSnapshot, trimPromptText, TRIM_RULES, scanCard, cardRoots, cardGroups, chainIndex, lineageMap, parentIndex, explicitParents, PROJ_CACHE, recordLineage, writeLineage, LINEAGE_FILE, readLineage, dayFiles, dayTotal, todayFromCache, localDayKey, localDayStart, costInWindow, costStepsInWindow, budgetAlert, treeIndex, descendants, subagentRollup, chainRollup, rootWalk, normSid, parseHead, treeFiles, headAsync, refreshTree, readBudget, SUBAGENT_BUDGET, SESSIONS, DSH_HOME, LOG_NAME, armAutoSwitch, turnEndRecord, autoSwitchView, noteTurnEnd, refreshAutoCard, handoffArgs, readAutoSwitch, writeAutoSwitch, loadTurnEnd, AUTOSWITCH_FILE, TURNEND_FILE, turnEndState, accountPaths, capabilities, accountCfgCache, ACCOUNT_CFG_FILE, STATE_DIR, markAutoChild, unmarkAutoChild, autoChildren, lineageParentCache, hasLineageParent, chainRoot, applyAutoSwitch, transferredTo, ensureHandoff };
