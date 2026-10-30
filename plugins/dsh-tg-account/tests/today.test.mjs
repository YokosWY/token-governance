import test from "node:test";
import assert from "node:assert/strict";
import { _test } from "../lib/index.js";

const { summarizeSession, todayFromCache, localDayStart, localDayKey } = _test;

/* 合成价表：单套价，便于手算 */
const BOOK = {
  schema: "legacy",
  legacy: true,
  sourceLabel: "test",
  fingerprint: "legacy:test",
  fx: { usdToCny: 7 },
  models: {},
  providers: {},
  default: { cacheMiss: 0.15, cacheHit: 0.003, output: 0.6, peak: null },
  free: { models: [], suffixes: [], patterns: [] },
  peak: { enabled: false, windows: [], holidays: [] },
  window: 200000,
};

function line(input, output, time) {
  return JSON.stringify({
    type: "assistant/message",
    time,
    data: {
      usage: { inputTokens: input, cacheReadTokens: 0, outputTokens: output },
      message: { source: { provider: "deepseek", model: "deepseek-chat" } },
    },
  });
}

/* 1M input 每 1M 0.15 USD × 7 = ¥1.05；1M output × 0.6 × 7 = ¥4.2 */
const NOW = Date.now();
const TODAY_MS = NOW;
const YESTERDAY_MS = localDayStart() - 3600 * 1000;

test("summarizeSession：今日额只算本地日内，不把昨天带进今天", () => {
  const raw = [
    line(1_000_000, 0, YESTERDAY_MS), // ¥1.05（昨天）
    line(0, 1_000_000, TODAY_MS), // ¥4.2（今天）
  ].join("\n");
  const s = summarizeSession(raw, BOOK);
  assert.ok(Math.abs(s.cnyAll - (1.05 + 4.2)) < 1e-6, "整段累计应含两天");
  assert.ok(Math.abs(s.cnyToday - 4.2) < 1e-6, "今日额只含今天那一步");
});

test("todayFromCache：缓存已含本会话时，减掉再加实时值（不重复计入）", () => {
  const cache = {
    day: localDayKey(),
    at: NOW,
    cny: 10.2863,
    sessions: 18,
    files: { "D:\\s\\ace3e531\\session.v4.jsonl.zstd": { mt: 1, cny: 1.4034 } },
  };
  const t = todayFromCache(cache, "D:\\s\\ace3e531\\session.v4.jsonl.zstd", 1.4034, true);
  assert.ok(Math.abs(t.cny - 10.2863) < 1e-6, "本会话只该计一次");
  assert.ok(Math.abs(t.others - (10.2863 - 1.4034)) < 1e-6);
  assert.equal(t.sessions, 18, "本会话已在缓存里计数，不再补 1");
});

test("todayFromCache：别人建的缓存 / 新会话都不会把本会话算两遍", () => {
  const cache = {
    day: localDayKey(),
    at: NOW,
    cny: 10.2863,
    sessions: 18,
    files: { "D:\\s\\other\\session.v4.jsonl.zstd": { mt: 1, cny: 5 } },
  };
  const t = todayFromCache(cache, "D:\\s\\mine\\session.v4.jsonl.zstd", 0.1064, true);
  assert.ok(Math.abs(t.cny - 10.3927) < 0.0001, "缓存里没有本会话 → 直接加");
  assert.equal(t.sessions, 19);
});

test("todayFromCache：跨天会话只带今日那份，昨天的钱不进今日", () => {
  const p = "D:\\s\\old\\session.v4.jsonl.zstd";
  const cache = { day: localDayKey(), at: NOW, cny: 10.2863, sessions: 18, files: { [p]: { mt: 1, cny: 0.2 } } };
  // 该会话整段 ¥5.0，其中今天只有 ¥0.2 —— 传进来的 selfToday 必须是日切后的 0.2
  const t = todayFromCache(cache, p, 0.2, true);
  assert.ok(Math.abs(t.cny - 10.2863) < 1e-6, "今日合计不该因为会话整段 5.0 而变高");
  assert.ok(Math.abs(t.others - (10.2863 - 0.2)) < 1e-6);
});

test("todayFromCache：不会出负数", () => {
  const cache = { day: localDayKey(), at: NOW, cny: 1, sessions: 1, files: { p: { mt: 1, cny: 3 } } };
  const t = todayFromCache(cache, "p", 0.2, true);
  assert.equal(t.cny, 0.2);
});

/* 2026-10-30：今日省（saved）也走同一条「缓存 − 本会话 + 实时」的路 */
test("todayFromCache：今日省同样减掉本会话缓存那份再加实时值", () => {
  const p = "D:\\s\\ace3e531\\session.v4.jsonl.zstd";
  const cache = {
    day: localDayKey(), at: NOW, cny: 10, saved: 2.0, sessions: 3,
    files: { [p]: { mt: 1, cny: 1.4, saved: 0.4 } },
  };
  const t = todayFromCache(cache, p, 1.4, true, 0.5);
  assert.ok(Math.abs(t.saved - 2.1) < 1e-6, "2.0 − 0.4 + 0.5 = 2.1");
  assert.ok(Math.abs(t.savedOthers - 1.6) < 1e-6);
});

test("todayFromCache：缓存里没有本会话时，今日省直接相加", () => {
  const cache = { day: localDayKey(), at: NOW, cny: 10, saved: 2.0, sessions: 3, files: {} };
  const t = todayFromCache(cache, "D:\\s\\mine", 1, true, 0.5);
  assert.ok(Math.abs(t.saved - 2.5) < 1e-6);
});

test("todayFromCache：没有省额时给 0，不抛也不给 NaN", () => {
  const cache = { day: localDayKey(), at: NOW, cny: 1, sessions: 1, files: {} };
  const t = todayFromCache(cache, "p", 0.2, true);
  assert.equal(t.saved, 0);
});
