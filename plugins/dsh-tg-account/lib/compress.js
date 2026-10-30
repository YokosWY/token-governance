/**
 * dsh-tg-account · 压缩核（纯函数，无副作用、无 IO）
 *
 * 从 dsh-plugin-save-token 2.4.1 抽出来的「核心原理」，只用实测在本机有命中的三条路线：
 *   1) 结构感知窗口：管道/制表表格走「头 + 带行号步进采样 + 尾」，不盲切
 *   2) 重复行/空行折叠：无损，先跑，省不够再交给窗口
 *   3) 字节预算窗口：仅在 ≥8KB 或 ≥240 行的大输出上启用
 * 统一红线：
 *   - 双门控：字节 ≤ 原文 72%、省 ≥ 500B、估算 token 严格下降，任一不过 → 原样放行
 *   - 错误行保护：省略区里的 error/fatal/traceback/... 行（含 ±1 上下文）必须保留
 *   - 单行超长（>420 字符）首尾截断，避免一行撑爆
 *   - 可逆性由调用方负责（先落盘原文再替换），本模块不写盘
 *
 * 2026-10-25 弃用项与理由（本机 133 会话实测）：
 *   - TOON/JSONL 无损重编码：200 次压缩里仅 1 次命中，代码量最大、收益最小
 *   - 跨轮去重：0 命中
 *   - 独立 expand 工具：每请求常驻约 110 token 的 schema，换 2 次调用，不划算；
 *     改用「notice 里带原文 locator + 现有 read 工具取回」，零常驻成本
 */

const DEFAULT_CFG = {
  triggerBytes: 1800, // 准入：小于此体积直接放行
  minSavingBytes: 500, // 门控：至少要省这么多字节
  keepRatioMax: 0.72, // 门控：压缩后不得超过原文 72%
  bigBytes: 8000, // 只有 ≥ 此体积才允许「纯窗口截断」（否则只做结构无损）
  bigLines: 240, // 或 ≥ 此行数
  headLines: 40,
  tailLines: 20,
  headBytesBudget: 1400,
  tailBytesBudget: 900,
  strideSamples: 40,
  longLineChars: 420,
  maxErrorAnchors: 25,
};

const ERROR_RE = /(error|fatal|traceback|exception|panic|failed|failure|timeout|refused|denied|ENOENT|EACCES)/i;

export function utf8Bytes(s) {
  let n = 0;
  for (let i = 0; i < s.length; i++) {
    const c = s.charCodeAt(i);
    if (c < 0x80) n += 1;
    else if (c < 0x800) n += 2;
    else if (c >= 0xd800 && c <= 0xdbff) {
      n += 4;
      i++;
    } else n += 3;
  }
  return n;
}

/** 与 save-token 同一估算器：ASCII 3.8 字符/token，CJK 0.75 token/字。只用于「是否更省」的相对比较。 */
export function estTokens(s) {
  if (!s) return 0;
  let cjk = 0;
  let other = 0;
  for (let i = 0; i < s.length; i++) {
    const c = s.charCodeAt(i);
    if (c >= 0x2e80 && c <= 0x9fff) cjk++;
    else if (c >= 0xd800 && c <= 0xdfff) {
      cjk++;
      i++;
    } else other++;
  }
  return Math.ceil(other / 3.8 + cjk * 0.75);
}

export function fnv1a(s) {
  let h = 0x811c9dc5;
  for (let i = 0; i < s.length; i++) {
    h ^= s.charCodeAt(i);
    h = (h + ((h << 1) + (h << 4) + (h << 7) + (h << 8) + (h << 24))) >>> 0;
  }
  return ("0000000" + h.toString(16)).slice(-8);
}

function trimLongLine(line, max) {
  if (line.length <= max) return { line, cut: 0 };
  const keepHead = Math.max(80, Math.floor(max * 0.65));
  const keepTail = Math.max(40, max - keepHead);
  return {
    line: line.slice(0, keepHead) + `…(+${line.length - keepHead - keepTail} 字符)…` + line.slice(-keepTail),
    cut: 1,
  };
}

function collapseBlanks(lines) {
  const out = [];
  let run = 0;
  for (const l of lines) {
    if (l.trim() === "") {
      run++;
      if (run >= 3) continue;
    } else run = 0;
    out.push(l);
  }
  return { lines: out, removed: lines.length - out.length, kind: "blank" };
}

function collapseRepeats(lines) {
  const out = [];
  let removed = 0;
  for (let i = 0; i < lines.length; ) {
    let j = i + 1;
    while (j < lines.length && lines[j] === lines[i]) j++;
    const run = j - i;
    if (run >= 4 && lines[i].trim() !== "") {
      out.push(lines[i], `…[同上 ×${run - 1}，L${i + 2}-L${j}]…`);
      removed += run - 1;
    } else {
      for (let k = i; k < j; k++) out.push(lines[k]);
    }
    i = j;
  }
  return { lines: out, removed, kind: "repeat" };
}

/** ≥70% 的行带同一分隔轮廓（| 或制表），且列数 ≥2 → 当表格处理 */
export function looksTabular(lines) {
  let hit = 0;
  let counted = 0;
  for (const l of lines) {
    if (l.trim() === "") continue;
    counted++;
    const pipes = (l.match(/\|/g) || []).length;
    const tabs = (l.match(/\t/g) || []).length;
    if (pipes >= 2 || tabs >= 2) hit++;
  }
  return counted > 0 && hit / counted >= 0.7;
}

function errorAnchors(lines, cfg) {
  const anchors = new Set();
  for (let i = 0; i < lines.length; i++) {
    if (!ERROR_RE.test(lines[i])) continue;
    for (let d = -1; d <= 1; d++) {
      const k = i + d;
      if (k >= 0 && k < lines.length) anchors.add(k);
    }
    if (anchors.size >= cfg.maxErrorAnchors * 3) break;
  }
  return anchors;
}

/**
 * 结构感知窗口：头 headLines 行原文 + 中段每 step 行取一条并标原始行号 + 尾 tailLines 行；
 * 中段里的错误锚点强制保留。
 */
export function windowLines(lines, cfg, opts = {}) {
  const head = opts.headLines ?? cfg.headLines;
  const tail = opts.tailLines ?? cfg.tailLines;
  if (lines.length <= head + tail + 10) return null;

  const anchors = errorAnchors(lines, cfg);
  const midStart = head;
  const midEnd = lines.length - tail;
  const midCount = midEnd - midStart;
  const step = Math.max(1, Math.ceil(midCount / cfg.strideSamples));

  const body = [];
  let kept = 0;
  let cutLong = 0;
  for (let i = 0; i < lines.length; i++) {
    const inHead = i < head;
    const inTail = i >= midEnd;
    const sampled = !inHead && !inTail && (i - midStart) % step === 0;
    const anchor = !inHead && !inTail && anchors.has(i);
    if (!inHead && !inTail && !sampled && !anchor) continue;
    const t = trimLongLine(lines[i], cfg.longLineChars);
    if (t.cut) cutLong++;
    if (inHead || inTail) body.push(t.line);
    else {
      body.push(`L${i + 1}: ${t.line}`);
      kept++;
    }
  }

  const omittedLines = lines.length - (head + tail + kept);
  const traffic = `…[省略 L${head + 1}-L${midEnd} 共 ${midCount} 行中的 ${omittedLines} 行；中段为每 ${step} 行一条的带行号采样，错误行已强制保留。原文完整路径见尾部 notice]…`;
  const out = body.slice(0, head).concat([traffic], body.slice(head));
  return {
    text: out.join("\n"),
    strategy: "lines-strided",
    stats: { omittedLines, samples: kept, errorAnchors: anchors.size, longLineCuts: cutLong },
  };
}

/** 字节预算窗口（只给大输出用）：头按字节、尾按字节，中段整体省略并留错误行。 */
export function windowByBytes(lines, cfg) {
  if (lines.length <= cfg.headLines + cfg.tailLines + 10) return null;
  const anchors = errorAnchors(lines, cfg);
  const headIdx = [];
  const tailIdx = [];
  let hb = 0;
  for (let i = 0; i < lines.length; i++) {
    hb += utf8Bytes(lines[i]) + 1;
    if (hb > cfg.headBytesBudget || headIdx.length >= cfg.headLines) break;
    headIdx.push(i);
  }
  let tb = 0;
  for (let i = lines.length - 1; i >= 0 && tailIdx.length < cfg.tailLines; i--) {
    tb += utf8Bytes(lines[i]) + 1;
    if (tb > cfg.tailBytesBudget) break;
    tailIdx.unshift(i);
  }
  const keep = new Set([...headIdx, ...tailIdx]);
  for (const a of anchors) if (a > headIdx[headIdx.length - 1] && a < tailIdx[0]) keep.add(a);
  const body = [];
  let last = -1;
  let cutLong = 0;
  for (const i of [...keep].sort((a, b) => a - b)) {
    if (last >= 0 && i > last + 1) body.push(`…[省略 ${i - last - 1} 行]…`);
    const t = trimLongLine(lines[i], cfg.longLineChars);
    if (t.cut) cutLong++;
    body.push(t.line);
    last = i;
  }
  if (last < lines.length - 1) body.push(`…[省略 ${lines.length - 1 - last} 行]…`);
  return {
    text: body.join("\n"),
    strategy: "lines-window",
    stats: { omittedLines: lines.length - keep.size, keptLines: keep.size, longLineCuts: cutLong },
  };
}

function gate(route, text, cfg) {
  if (!route || !route.text) return null;
  const before = utf8Bytes(text);
  const after = utf8Bytes(route.text);
  if (route.text.length < 20) return null;
  if (after > before * cfg.keepRatioMax) return null;
  if (before - after < cfg.minSavingBytes) return null;
  if (estTokens(route.text) >= estTokens(text)) return null;
  return { text: route.text, before, after, strategy: route.strategy, stats: route.stats };
}

/**
 * 主入口。返回 null 表示「原样放行」，否则返回 {text, before, after, strategy, stats}。
 */
export function buildCandidate(text, cfgIn = {}) {
  const cfg = { ...DEFAULT_CFG, ...cfgIn };
  const before = utf8Bytes(text);
  if (before < cfg.triggerBytes) return null;

  const lines = text.split("\n");
  const lineCount = lines.length;
  const big = before >= cfg.bigBytes || lineCount >= cfg.bigLines;

  // 1) 结构无损：空行折叠 / 重复行折叠（对大输出也先跑，命中就省一块）
  const nb = collapseBlanks(lines);
  const rp = collapseRepeats(nb.lines);
  if (rp.removed > 0) {
    const g = gate({ text: rp.lines.join("\n"), strategy: "collapse-repeat" }, text, cfg);
    if (g) {
      g.stats = { ...g.stats, removedLines: rp.removed, blankRemoved: nb.removed };
      return g;
    }
  }

  // 2) 结构感知窗口：表格 / 长输出
  const tabular = looksTabular(lines);
  if (tabular || big) {
    const w = windowLines(lines, cfg, { headLines: tabular ? 20 : cfg.headLines, tailLines: tabular ? 10 : cfg.tailLines });
    const g = w && gate({ ...w, strategy: tabular ? "table-strided" : w.strategy }, text, cfg);
    if (g) return g;
  }

  // 3) 字节预算窗口：只给 ≥8KB 或 ≥240 行的大输出
  if (big) {
    const w = windowByBytes(lines, cfg);
    const g = w && gate({ text: w.text, strategy: w.strategy, stats: w.stats }, text, cfg);
    if (g) return g;
  }
  return null;
}

/** 替换文本尾部 notice：取回通道 = 现有 read 工具 + 原文路径，不注册新工具。 */
export function buildNotice(cand, id, locator, cfgIn = {}) {
  const cfg = { ...DEFAULT_CFG, ...cfgIn };
  const pct = Math.round((1 - cand.after / cand.before) * 100);
  const bits = [];
  if (cand.stats && cand.stats.omittedLines) bits.push(`省略 ${cand.stats.omittedLines} 行`);
  if (cand.stats && cand.stats.removedLines) bits.push(`折叠重复 ${cand.stats.removedLines} 行`);
  if (cand.stats && cand.stats.samples) bits.push(`${cand.stats.samples} 条带行号采样`);
  if (cand.stats && cand.stats.errorAnchors) bits.push(`保留 ${cand.stats.errorAnchors} 条错误行`);
  if (cand.stats && cand.stats.longLineCuts) bits.push(`截断 ${cand.stats.longLineCuts} 条超长行`);
  const shape = bits.length ? ` 形状：${bits.join("，")}。` : "";
  return (
    cand.text +
    `\n\n[tg-trim #${id} compressed: ${cand.before} -> ${cand.after} bytes (~${pct}% 更小，策略 ${cand.strategy})。` +
    shape +
    `需要被省略的细节时，直接 read "${locator}"（或 grep 该路径）取原文，不要根据片段猜。]`
  );
}

export { DEFAULT_CFG };
