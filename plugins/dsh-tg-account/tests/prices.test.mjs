import test from "node:test";
import assert from "node:assert/strict";
import { existsSync } from "node:fs";
import { _test } from "../lib/index.js";

const { priceFor, eventCny, legacyBook, summarizeSession, readPriceBook, modelRows } = _test;

/* 合成价表：DS flash（有峰时价）、v4-pro（无峰时价）、openai provider 默认、免费规则 */
const BOOK = {
  schema: "tg-prices/1",
  legacy: false,
  sourceLabel: "test",
  fingerprint: "sha1:test",
  fx: { usdToCny: 6.67 },
  models: {
    "deepseek-flash": { cacheMiss: 0.15, cacheHit: 0.003, output: 0.6,
      peak: { cacheMiss: 0.3, cacheHit: 0.006, output: 1.2 } },
    "deepseek-v4-pro": { cacheMiss: 0.66, cacheHit: 0.022, output: 1.98, peak: null },
  },
  providers: { openai: { default: { cacheMiss: 2.5, cacheHit: 1.25, output: 10, peak: null } } },
  default: { cacheMiss: 0.15, cacheHit: 0.003, output: 0.6, peak: null },
  free: { models: ["kilo-auto/free"], suffixes: ["-free", ":free"], patterns: ["*/free"] },
  peak: { enabled: false, windows: [[1, 4], [6, 10]], holidays: [] },
};

const clone = (b) => JSON.parse(JSON.stringify(b));

function line(provider, model, i, cr, o, time) {
  return JSON.stringify({
    type: "assistant/message",
    time: time || 0,
    data: {
      turn: 1, step: 1,
      message: { role: "assistant", source: { kind: "model", provider, model } },
      usage: { inputTokens: i, cacheReadTokens: cr, cacheWriteTokens: 0, outputTokens: o },
    },
  });
}

test("价表：provider:model 精确命中优先于其它规则", () => {
  const b = clone(BOOK);
  b.models["deepseek-account:deepseek-flash"] = { cacheMiss: 9, cacheHit: 9, output: 9, peak: null };
  const [row, how] = priceFor(b, "deepseek-account", "deepseek-flash");
  assert.equal(how, "exact");
  assert.equal(row.cacheMiss, 9);
});

test("价表：免费后缀不会被家族前缀吃掉（-free 是零价，不是付费）", () => {
  const b = clone(BOOK);
  b.models["deepseek-v4.1-flash"] = { cacheMiss: 0.15, cacheHit: 0.003, output: 0.6, peak: null };
  const [row, how] = priceFor(b, "our-free-model", "deepseek-v4.1-flash-free");
  assert.equal(how, "free");
  assert.equal(row.cacheMiss, 0);
});

test("价表：免费名单与通配都认", () => {
  assert.equal(priceFor(BOOK, "kilo", "kilo-auto/free")[1], "free");
  assert.equal(priceFor(BOOK, "kilo", "some-new-model/free")[1], "free");
  assert.equal(priceFor(BOOK, "nvidia", "nemotron-3.5-lightning:free")[1], "free");
});

test("价表：有名有姓但没登记 = 未定价（不许静默套 DS 价）", () => {
  const b = clone(BOOK);
  b.free = { models: [], suffixes: [], patterns: [] };
  const [row, how] = priceFor(b, "glm", "glm-5.3-flash");
  assert.equal(how, "unpriced");
  assert.equal(row, null);
});

test("价表：provider 默认价只在显式配置时兜底", () => {
  const [row, how] = priceFor(BOOK, "openai", "gpt-9-not-listed");
  assert.equal(how, "provider-default");
  assert.equal(row.cacheMiss, 2.5);
  const [row2, how2] = priceFor(BOOK, "anthropic", "claude-not-listed");
  assert.equal(how2, "unpriced");
  assert.equal(row2, null);
});

test("价表：缺 source 才按 default 折算（与「未定价」分开）", () => {
  const [row, how] = priceFor(BOOK, "unknown", "unknown");
  assert.equal(how, "missing-source");
  assert.equal(row.cacheMiss, 0.15);
});

test("价表：家族前缀只认整词边界", () => {
  assert.equal(priceFor(BOOK, "deepseek-account", "deepseek-v4-pro-2027")[1], "family");
  assert.equal(priceFor(BOOK, "deepseek-account", "deepseek-v4-proX")[1], "unpriced");
});

test("价表：没有 prices.json 时退回单套价（旧行为，绝不更糟）", () => {
  const b = legacyBook({ input: 0.15, cached: 0.003, output: 0.6, rate: 7.1, window: 200000 });
  const [row, how] = priceFor(b, "deepseek-account", "deepseek-flash");
  assert.equal(how, "legacy");
  assert.equal(row.output, 0.6);
  assert.equal(b.fx.usdToCny, 7.1);
});

test("计价：峰时价只对声明了 peak 的模型生效", () => {
  const b = clone(BOOK);
  b.peak.enabled = true;
  const at2am = new Date(2026, 9, 20, 2, 0, 0).getTime();
  const at12 = new Date(2026, 9, 20, 12, 0, 0).getTime();
  const flash = b.models["deepseek-flash"];
  const pro = b.models["deepseek-v4-pro"];
  assert.equal(eventCny(b, flash, 1e6, 1e6, 1e6, at2am), (0.3 + 0.006 + 1.2) * 6.67);
  assert.equal(eventCny(b, flash, 1e6, 1e6, 1e6, at12), (0.15 + 0.003 + 0.6) * 6.67);
  // v4-pro 没写峰时价：任何时段都按原价，不会被全局窗口翻倍
  assert.equal(eventCny(b, pro, 1e6, 0, 0, at2am), 0.66 * 6.67);
});

test("汇总：多模型会话按各自价格算，未定价只记 tokens 不记钱", () => {
  const raw = [
    JSON.stringify({ type: "turn/start", data: {} }),
    line("deepseek-account", "deepseek-flash", 1e6, 1e6, 1e6),
    JSON.stringify({ type: "turn/start", data: {} }),
    line("our-free-model", "glm-5.3-flash-free", 1e6, 0, 1e6),
    JSON.stringify({ type: "turn/start", data: {} }),
    line("glm", "glm-5.3-flash", 1e6, 0, 1e6),
  ].join("\n");
  const s = summarizeSession(raw, BOOK);
  assert.equal(s.steps, 3);
  assert.equal(s.turns.length, 3);
  assert.ok(Math.abs(s.cnyAll - (0.15 + 0.003 + 0.6) * 6.67) < 1e-9, String(s.cnyAll));
  assert.equal(s.unpricedCalls, 1);
  assert.equal(s.unpricedTok, 2e6);
  assert.equal(s.byModel.size, 3);
  const rows = modelRows(s.byModel);
  assert.equal(rows[0].model, "deepseek-flash");
  assert.ok(rows[0].cny > 5);
  const free = rows.find((r) => r.model === "glm-5.3-flash-free");
  assert.equal(free.cny, 0);
  assert.equal(free.free, true);
  const unp = rows.find((r) => r.model === "glm-5.3-flash");
  assert.equal(unp.how, "unpriced");
  assert.equal(unp.cny, 0);
});

test("汇总：同一会话混 provider 时，钱和 tokens 都不串", () => {
  const raw = [
    line("deepseek-account", "deepseek-v4-pro", 1e6, 0, 0),
    line("openai", "gpt-9-not-listed", 0, 0, 1e6),
  ].join("\n");
  const s = summarizeSession(raw, BOOK);
  const expect = 0.66 * 6.67 + 10 * 6.67;
  assert.ok(Math.abs(s.cnyAll - expect) < 1e-9, String(s.cnyAll));
  assert.equal(s.unpricedCalls, 0);
});

test("真价表：prices.json 存在时可读（指纹/模型数）", () => {
  const book = readPriceBook();
  if (book.legacy) return; // 没导出过价表：本机没跑 ledger.py prices export，跳过
  assert.match(String(book.fingerprint), /^sha1:/);
  assert.ok(Object.keys(book.models).length >= 1);
  assert.ok(book.fx.usdToCny > 0);
});
