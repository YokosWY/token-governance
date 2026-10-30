import test from "node:test";
import assert from "node:assert/strict";
import { _test } from "../lib/index.js";

const { normSid, parseHead, descendants } = _test;

/* 子代理归并（2026-10-27）：子代理是独立会话文件，钱记在别的文件里；
 * 胶囊「本」必须把子树并进来，否则「本 ¥1.40 / 今日 ¥16」对不上账。 */

test("normSid：session- 前缀与纯 uuid 归一到同一形态", () => {
  assert.equal(normSid("session-AC3E531-e171"), "ac3e531-e171");
  assert.equal(normSid("ac3e531-e171"), "ac3e531-e171");
  assert.equal(normSid(""), "");
  assert.equal(normSid(null), "");
});

test("parseHead：认 session 头里的 parentSession / origin / depth", () => {
  const line = JSON.stringify({
    type: "session", id: "7a1bff27", parentSession: "session-ace3e531",
    origin: "subagent", delegationDepth: 1,
  });
  const h = parseHead(line + '\n{"type":"step/start"}');
  assert.equal(h.sid, "7a1bff27");
  assert.equal(h.parent, "session-ace3e531");
  assert.equal(h.origin, "subagent");
  assert.equal(h.depth, 1);
});

test("parseHead：主会话没有 parentSession 时给空串，不当成有父", () => {
  const h = parseHead(JSON.stringify({ type: "session", id: "abc" }));
  assert.equal(h.parent, "");
  assert.equal(h.origin, "user");
});

test("parseHead：非 session 头 / 坏 JSON 返回 null，不抛", () => {
  assert.equal(parseHead(""), null);
  assert.equal(parseHead('{"type":"step/start"}'), null);
  assert.equal(parseHead("{坏"), null);
  assert.equal(parseHead("不是 JSON"), null);
});

const tree = (files) => ({ v: 1, at: 1, fp: "", files });

test("descendants：递归整棵子树（子代理还能再派子代理）", () => {
  const t = tree({
    "/a/x": { sid: "aaaaaaaa", parent: "", origin: "user" },
    "/b/x": { sid: "bbbbbbbb", parent: "aaaaaaaa", origin: "subagent" },
    "/c/x": { sid: "cccccccc", parent: "bbbbbbbb", origin: "subagent" },
    "/d/x": { sid: "dddddddd", parent: "aaaaaaaa", origin: "subagent" },
    "/e/x": { sid: "eeeeeeee", parent: "", origin: "user" },
  });
  const kids = descendants("aaaaaaaa", t).map((k) => k.sid).sort();
  assert.deepEqual(kids, ["bbbbbbbb", "cccccccc", "dddddddd"]);
  assert.deepEqual(descendants("eeeeeeee", t), []);
});

test("descendants：父环不死循环，也不把自己算进子孙", () => {
  /* a 声称父是 b、b 声称父是 a：从 a 出发 b 仍是 a 的子，但不能再把 a 展开回来 */
  const t = tree({
    "/a/x": { sid: "aaaaaaaa", parent: "bbbbbbbb", origin: "subagent" },
    "/b/x": { sid: "bbbbbbbb", parent: "aaaaaaaa", origin: "subagent" },
  });
  const kids = descendants("aaaaaaaa", t);
  assert.equal(kids.length, 1);
  assert.equal(kids[0].sid, "bbbbbbbb");
});

test("descendants：父不在索引里的孤儿不归到任何会话（宁可不归，不认错爹）", () => {
  const t = tree({
    "/a/x": { sid: "aaaaaaaa", parent: "", origin: "user" },
    "/b/x": { sid: "bbbbbbbb", parent: "ffffffff", origin: "subagent" },
  });
  /* 从 a 出发：b 的父是 f，不归 a */
  assert.deepEqual(descendants("aaaaaaaa", t), []);
  /* 直接按父 sid 查：父文件丢了也知道它挂在 f 名下，仍能归并（父日志缺失不影响子树） */
  assert.deepEqual(descendants("ffffffff", t).map((k) => k.sid), ["bbbbbbbb"]);
});

test("descendants：同一父下多个子代理全归进来，顺序稳定（按路径遍历）", () => {
  const t = tree({
    "/a/x": { sid: "aaaaaaaa", parent: "", origin: "user" },
    "/b/x": { sid: "b1b1b1b1", parent: "aaaaaaaa", origin: "subagent" },
    "/c/x": { sid: "b2b2b2b2", parent: "aaaaaaaa", origin: "subagent" },
    "/d/x": { sid: "b3b3b3b3", parent: "aaaaaaaa", origin: "subagent" },
  });
  assert.equal(descendants("aaaaaaaa", t).length, 3);
});

/* ---------- 超预算告警（2026-10-27） ---------- */

const { budgetAlert, costStepsInWindow, readPriceBook, SUBAGENT_BUDGET } = _test;

test("budgetAlert：单个子代理超步数 / 超花费各自告警，刚好卡线不告警", () => {
  assert.deepEqual(budgetAlert(41, 0, { steps: 40, cny: 1 }).reasons, ["步数"]);
  assert.deepEqual(budgetAlert(10, 1.5, { steps: 40, cny: 1 }).reasons, ["花费"]);
  assert.deepEqual(budgetAlert(200, 3, { steps: 40, cny: 1 }).reasons, ["步数", "花费"]);
  assert.equal(budgetAlert(40, 1, { steps: 40, cny: 1 }).on, false);
  assert.equal(budgetAlert(0, 0, { steps: 40, cny: 1 }).on, false);
});

test("budgetAlert：不传预算时用默认（40 步 / ¥1）", () => {
  assert.equal(SUBAGENT_BUDGET.steps, 40);
  assert.equal(SUBAGENT_BUDGET.cny, 1);
  assert.equal(budgetAlert(41, 0).on, true);
});

const msg = (time, provider, model, input, cacheRead, output) =>
  JSON.stringify({
    type: "assistant/message",
    time,
    data: {
      usage: { inputTokens: input, cacheReadTokens: cacheRead, outputTokens: output },
      message: { source: { provider, model } },
    },
  });

test("costStepsInWindow：未定价的步也算一步（预算按跑了多少步，不按是否记账）", () => {
  const raw = [
    msg(1000, "deepseek-account", "deepseek-flash", 1000, 0, 100),
    msg(2000, "deepseek-account", "deepseek-flash", 1000, 0, 100),
    msg(3000, "x", "no-such-model", 1000, 0, 100),
  ].join("\n");
  const book = readPriceBook();
  const all = costStepsInWindow(raw, 0, Number.POSITIVE_INFINITY, book);
  assert.equal(all.steps, 3);
  assert.ok(all.cny > 0);
  /* 只算后两步：步数与钱同步收窄 */
  const win = costStepsInWindow(raw, 1500, Number.POSITIVE_INFINITY, book);
  assert.equal(win.steps, 2);
});

test("costStepsInWindow：非 assistant/message 与坏行不计步", () => {
  const raw = [
    JSON.stringify({ type: "step/start", seq: 1 }),
    "坏行不是 JSON",
    msg(1000, "deepseek-account", "deepseek-flash", 10, 0, 1),
  ].join("\n");
  assert.equal(costStepsInWindow(raw, 0, Number.POSITIVE_INFINITY, readPriceBook()).steps, 1);
});
