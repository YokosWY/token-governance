import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";

const here = dirname(fileURLToPath(import.meta.url));
const src = readFileSync(join(here, "..", "lib", "client.js"), "utf8");

/* 用假宿主把浏览器模块跑起来：新 Function 注入 window / require / fetch */
function load({ fetchImpl, services = {} }) {
  let def;
  const window = { __ModuleLoader__: { load: (d) => { def = d; } } };
  const React = {
    createElement: () => null,
    useState: () => [null, () => {}],
    useRef: () => ({ current: 0 }),
    useEffect: () => {},
  };
  const req = (n) => { if (n === "react") return React; throw new Error("unexpected require " + n); };
  new Function("window", "require", "fetch", src)(window, req, fetchImpl);
  const mod = def.factory(req);
  const ctx = { slots: { inject: () => {}, register: () => {} }, get: (n) => services[n] ?? null };
  mod.apply(ctx);
  return mod.__test;
}

const card = { ok: true, sid: "ab12cd34", path: "C:/cards/default-workspace/T/00-card.md", chars: 1200, text: "# 卡\n" };
const okFetch = (body) => () => Promise.resolve({ json: () => Promise.resolve(body) });

test("提示词只一行：带卡片路径，不带复述要求", () => {
  const t = load({ fetchImpl: okFetch(card) });
  const p = t.buildSwitchPrompt("session-ab12cd34", card);
  assert.match(p, /ab12cd34/);
  assert.ok(p.includes(card.path));
  assert.equal(p.split("\n").length, 1, "只留一行");
  assert.doesNotMatch(p, /复述|等我确认/);
});

test("切会话：新建并预填，不自动发送", async () => {
  const calls = [];
  const ws = { startSession: (wid, opts) => calls.push([wid, opts]) };
  const t = load({ fetchImpl: okFetch(card), services: { uiWorkspace: ws } });
  const err = await new Promise((r) => t.switchToNewSession("session-ab12cd34", null, r));
  assert.equal(err, null);
  assert.equal(calls.length, 1);
  const [wid, opts] = calls[0];
  assert.equal(wid, undefined, "交给 uiWorkspace 自己用当前工作区兜底");
  assert.equal(opts.clearPreviousDraft, true);
  assert.ok(String(opts.prompt).includes(card.path));
});

test("没有 uiWorkspace 时 fail-open 报错", async () => {
  const t = load({ fetchImpl: okFetch(card) });
  const err = await new Promise((r) => t.switchToNewSession("session-ab12cd34", null, r));
  assert.ok(err instanceof Error);
  assert.match(err.message, /uiWorkspace/);
});

test("卡片未就绪时报错，不调 startSession", async () => {
  const calls = [];
  const ws = { startSession: () => calls.push(1) };
  const t = load({ fetchImpl: okFetch({ ok: false, error: "未找到交接卡" }), services: { uiWorkspace: ws } });
  const err = await new Promise((r) => t.switchToNewSession("session-ab12cd34", null, r));
  assert.match(String(err && err.message), /交接卡/);
  assert.equal(calls.length, 0);
});

test("取服务：ctx.get 优先，属性兜底", () => {
  const t = load({ fetchImpl: okFetch(card), services: { uiWorkspace: { startSession() {} } } });
  assert.ok(t.getService("uiWorkspace"));
  assert.equal(t.getService("不存在的服务"), null);
});

test("聚合排序：链整块搬到最前成员的位置，链内顺叙（母→子）", () => {
  const t = load({ fetchImpl: okFetch(card) });
  const map = {
    aaaa1111: { g: "c1", n: 1, t: 3 },
    bbbb2222: { g: "c1", n: 2, t: 3 },
    cccc3333: { g: "c1", n: 3, t: 3 },
  };
  const at = (id) => ({ cccc3333: 100, bbbb2222: 90, aaaa1111: 80 }[id] || 50);
  const ids = ["cccc3333", "x1", "bbbb2222", "x2", "aaaa1111", "x3"];
  assert.deepEqual(t.planOrder(ids, map, at), ["aaaa1111", "bbbb2222", "cccc3333", "x1", "x2", "x3"]);
});

test("聚合排序：没有 ≥2 成员的链就不动", () => {
  const t = load({ fetchImpl: okFetch(card) });
  assert.equal(t.planOrder(["x1", "x2"], { aaaa1111: { g: "c1", n: 1, t: 1 } }, () => 0), null);
  assert.equal(t.planOrder(["aaaa1111", "x1"], { aaaa1111: { g: "c1", n: 1, t: 1 } }, () => 0), null);
});

test("聚合排序：dir=desc 时同一条链改回倒叙（最近的先）", () => {
  const t = load({ fetchImpl: okFetch(card) });
  const map = {
    aaaa1111: { g: "c1", n: 1, t: 3 },
    bbbb2222: { g: "c1", n: 2, t: 3 },
    cccc3333: { g: "c1", n: 3, t: 3 },
  };
  const at = (id) => ({ cccc3333: 100, bbbb2222: 90, aaaa1111: 80 }[id] || 50);
  const ids = ["cccc3333", "x1", "bbbb2222", "x2", "aaaa1111", "x3"];
  assert.deepEqual(t.planOrder(ids, map, at, "desc"), ["cccc3333", "bbbb2222", "aaaa1111", "x1", "x2", "x3"]);
  assert.deepEqual(t.planOrder(ids, map, at, "asc"), ["aaaa1111", "bbbb2222", "cccc3333", "x1", "x2", "x3"]);
});

test("聚合排序：两条链各自成块，块内顺叙", () => {
  const t = load({ fetchImpl: okFetch(card) });
  const map = {
    a1: { g: "c1", n: 1, t: 2 }, a2: { g: "c1", n: 2, t: 2 },
    b1: { g: "c2", n: 1, t: 2 }, b2: { g: "c2", n: 2, t: 2 },
  };
  const at2 = (id) => ({ a2: 100, b2: 95, a1: 90, b1: 85 }[id] || 0);
  const out = t.planOrder(["a2", "b2", "a1", "b1"], map, at2);
  assert.deepEqual(out.slice(0, 2), ["a1", "a2"]);
  assert.deepEqual(out.slice(2), ["b1", "b2"]);
});

test("会话解析：优先 sessions 快照的 current（并行会话时不记错人）", () => {
  const t = load({
    fetchImpl: okFetch(card),
    services: { sessions: { list: { getSnapshot: () => ({ current: "aa11bb22", byId: {} }) } } },
  });
  assert.equal(t.resolveSession({}), "aa11bb22");
});

test("会话解析：current 缺失时取主视图持有的会话", () => {
  const t = load({
    fetchImpl: okFetch(card),
    services: { sessions: { list: { getSnapshot: () => ({ byId: {
      zz99: { retainedBy: { mainView: 0 } },
      bb22cc33: { retainedBy: { mainView: 1 } },
    } }) } } },
  });
  assert.equal(t.resolveSession({}), "bb22cc33");
});

test("会话解析：都拿不到才退回 default（宿主再按最近写入猜）", () => {
  const t = load({ fetchImpl: okFetch(card), services: {} });
  assert.equal(t.resolveSession({}), "default");
});

test("交接卡状态：没卡也常显，不再整块消失（2026-10-30 用户要求）", () => {
  const t = load({ fetchImpl: okFetch(card) });
  /* 1. 空会话：跑完一轮才有可交接的内容 */
  const s0 = t.cardState(null, { due: false, cmd: "python handoff.py" }, 0);
  assert.equal(s0.label, "交接卡：跑完一轮后生成");
  assert.equal(s0.tone, "off");
  /* 2. 跑过步但还没到宿主判定：给「等待生成」而不是空白 */
  const s1 = t.cardState(null, { due: true, cmd: "python handoff.py" }, 3);
  assert.equal(s1.label, "交接卡：等待生成");
  /* 3. handoff.py 缺失：说明为什么不可用，且给原因 */
  const s2 = t.cardState(null, { due: false, unavailable: true, reason: "未找到 handoff.py：D:/x" }, 3);
  assert.match(s2.label, /未配置 handoff\.py/);
  assert.match(s2.detail, /缺少 handoff\.py/);
  assert.match(s2.tip, /未找到 handoff\.py/);
  /* 4. 生成失败：把 handoff.py 的原话或可手动执行的命令摆出来 */
  const s3 = t.cardState(null, { due: true, state: "error", detail: "请先设置 memory_root", cmd: "python x.py" }, 3);
  assert.equal(s3.label, "交接卡生成失败");
  assert.match(s3.detail, /memory_root/);
  /* 5. 生成中 / 已生成但路径没回传 */
  assert.equal(t.cardState(null, { due: true, state: "running" }, 3).label, "交接卡生成中…");
  assert.equal(t.cardState(null, { due: true, state: "ok" }, 3).label, "交接卡已生成，路径未回传");
  /* 6. 有卡：字数与路径 */
  const s6 = t.cardState(card, { due: true, state: "ok" }, 3);
  assert.equal(s6.label, "交接卡 · 1200 字");
  assert.equal(s6.tip, card.path);
  /* 7. 宿主连 handoff 字段都没给（老宿主）：不渲染，别凭空造状态 */
  assert.equal(t.cardState(null, null, 3), null);
});
