import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";
import { _test as host } from "../lib/index.js";

const here = dirname(fileURLToPath(import.meta.url));
const src = readFileSync(join(here, "..", "lib", "client.js"), "utf8");

/* 与 switch.test.mjs 同款假宿主：只把 __test 里的纯函数取出来跑 */
function load() {
  let def;
  const window = { __ModuleLoader__: { load: (d) => { def = d; } } };
  const React = {
    createElement: () => null,
    useState: () => [null, () => {}],
    useRef: () => ({ current: 0 }),
    useEffect: () => {},
  };
  const req = (n) => { if (n === "react") return React; throw new Error("unexpected require " + n); };
  new Function("window", "require", "fetch", src)(window, req, () => Promise.resolve({ json: () => Promise.resolve({}) }));
  const mod = def.factory(req);
  mod.apply({ slots: { inject: () => {}, register: () => {} }, get: () => null });
  return mod.__test;
}

const client = load();
const SID = "ab12cd34";

const view = (patch) => Object.assign({
  on: true,
  armedTurn: 3,
  lastTurnEnd: { turn: 4, kind: "completed", at: 1 },
  cardReady: true,
  card: { path: "D:/x/00-card.md" },
}, patch);

test("转接判定：该切 + completed + 晚于基线 + 卡就绪 → 返回去重 key", () => {
  assert.equal(client.shouldTransfer(view(), SID, true), SID + ":4");
});

test("转接判定：不该切（hot=false）时即使开着也不转", () => {
  assert.equal(client.shouldTransfer(view(), SID, false), null);
});

test("转接判定：报错 / 取消的轮次不转", () => {
  assert.equal(client.shouldTransfer(view({ lastTurnEnd: { turn: 4, kind: "error", at: 1 } }), SID, true), null);
  assert.equal(client.shouldTransfer(view({ lastTurnEnd: { turn: 4, kind: "cancelled", at: 1 } }), SID, true), null);
});

test("转接判定：开关关、卡未就绪、或轮次不晚于基线都不转", () => {
  assert.equal(client.shouldTransfer(view({ on: false }), SID, true), null);
  assert.equal(client.shouldTransfer(view({ cardReady: false }), SID, true), null);
  assert.equal(client.shouldTransfer(view({ armedTurn: 4 }), SID, true), null);
  assert.equal(client.shouldTransfer(view({ armedTurn: null }), SID, true), null);
  assert.equal(client.shouldTransfer(null, SID, true), null);
});

test("转接判定：同一轮只触发一次（key 稳定，换轮次才变）", () => {
  assert.equal(client.shouldTransfer(view(), SID, true), client.shouldTransfer(view(), SID, true));
  assert.notEqual(
    client.shouldTransfer(view({ lastTurnEnd: { turn: 5, kind: "completed", at: 2 } }), SID, true),
    client.shouldTransfer(view(), SID, true),
  );
});

test("转接提示词：交接卡一行 + 用户输入，空输入只留卡", () => {
  const card = { path: "D:/x/00-card.md" };
  const withText = client.buildSwitchPrompt("session-ab12cd34", card, "帮我看下这个报错");
  assert.ok(withText.startsWith("接续上一个会话（ab12cd34）。先读交接卡：" + card.path));
  assert.ok(withText.endsWith("\n\n帮我看下这个报错"));
  const noText = client.buildSwitchPrompt("session-ab12cd34", card, "   ");
  assert.equal(noText, "接续上一个会话（ab12cd34）。先读交接卡：" + card.path, "空输入不许多空行");
});

test("turnEndRecord：turn 与 reason.kind 原样带出，缺字段给 unknown", () => {
  const a = host.turnEndRecord({ type: "turn/end", data: { turn: 7, reason: { kind: "completed" } } });
  assert.equal(a.turn, 7);
  assert.equal(a.kind, "completed");
  const b = host.turnEndRecord({ type: "turn/end", data: {} });
  assert.equal(b.turn, null);
  assert.equal(b.kind, "unknown");
});

test("armAutoSwitch：基线取最后一轮 turn；没有记录就是 0", () => {
  assert.equal(host.armAutoSwitch({}, { turn: 9 }).armedTurn, 9);
  assert.equal(host.armAutoSwitch({ on: false }, null).armedTurn, 0);
  const rec = host.armAutoSwitch({ on: true, armedTurn: 2 }, { turn: 9 });
  assert.equal(rec.on, true);
  assert.ok(typeof rec.armedTurn === "number");
});

test("handoffArgs：自动模式每轮刷新（--no-task，不带 --if-absent）", () => {
  const auto = host.handoffArgs(SID, true);
  assert.ok(auto.includes("--no-task"));
  assert.ok(!auto.includes("--if-absent"));
  const normal = host.handoffArgs(SID, false);
  assert.ok(normal.includes("--if-absent"));
  assert.ok(!normal.includes("--no-task"));
  assert.ok(auto.includes("auto-" + SID));
});
