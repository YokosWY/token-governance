import test from "node:test";
import assert from "node:assert/strict";
import { mkdtempSync, mkdirSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";

/* 路由冒烟：把 apply() 挂到假 ctx，拿到 handler 后逐条打。
   只验「路由在、形状对、错误不吞」，账目口径由其余测试覆盖。 */
process.env.DSH_HOME = mkdtempSync(join(tmpdir(), "tg-http-"));
mkdirSync(join(process.env.DSH_HOME, "sessions"), { recursive: true });
const { apply } = await import("../lib/index.js");

function fakeCtx() {
  const got = { handler: null, events: [], injected: [], hooks: [] };
  const ctx = {
    logger: () => {},
    webServer: {
      register(spec) {
        got.handler = spec.handler;
        return true;
      },
    },
    on(evt, fn) {
      got.events.push([evt, fn]);
      return () => {};
    },
    inject(names, fn) {
      got.injected.push(names);
      void fn;
      return () => {};
    },
    slots: { inject: () => {}, register: () => {} },
    get: () => null,
  };
  apply(ctx);
  return { ctx, got };
}

function req(url, method = "GET", body = null) {
  const handlers = {};
  const r = {
    url,
    method,
    on(evt, fn) {
      (handlers[evt] = handlers[evt] || []).push(fn);
    },
  };
  /* 异步触发 data/end（POST 用），GET 不触发 */
  if (body !== null && method === "POST") {
    setImmediate(() => {
      for (const fn of handlers.data || []) fn(Buffer.from(body));
      for (const fn of handlers.end || []) fn();
    });
  }
  return r;
}

function res() {
  const out = { status: 0, body: "", headers: null, done: null };
  out.writeHead = (s, h) => {
    out.status = s;
    out.headers = h;
  };
  out.end = (b) => {
    out.body = b || "";
    if (out.done) out.done(JSON.parse(out.body || "{}"));
  };
  return out;
}

async function call(handler, url, method = "GET", body = null) {
  const r = req(url, method, body);
  const s = res();
  const p = new Promise((resolve) => {
    s.done = resolve;
  });
  handler(r, s);
  const json = await p; // 先等响应，再读 status（对象字面量里提前读会拿到 0）
  return { status: s.status, json, headers: s.headers };
}

test("apply：注册 /tg-account 前缀路由，并订阅 session/event", () => {
  const { got } = fakeCtx();
  assert.equal(typeof got.handler, "function");
  assert.ok(got.events.some(([e]) => e === "session/event"));
});

test("GET /cost：没会话日志时返回 ok:false，不抛 500", async () => {
  const { got } = fakeCtx();
  const r = await call(got.handler, "/tg-account/api/cost?session=default");
  assert.equal(r.status, 200);
  assert.equal(r.json.ok, false);
  assert.match(r.json.error, /会话日志/);
});

test("GET /trim：带 master / rules / rulesMeta，且是 application/json", async () => {
  const { got } = fakeCtx();
  const r = await call(got.handler, "/tg-account/api/trim");
  assert.equal(r.status, 200);
  assert.equal(r.json.ok, true);
  assert.equal(typeof r.json.master, "boolean");
  assert.equal(r.json.rulesMeta.length, 7);
  assert.match(r.headers["content-type"], /application\/json/);
});

test("POST /trim：改 master 后 GET 能读到新值", async () => {
  const { got } = fakeCtx();
  const w = await call(got.handler, "/tg-account/api/trim", "POST", JSON.stringify({ master: false }));
  assert.equal(w.status, 200);
  assert.equal(w.json.master, false);
  const r = await call(got.handler, "/tg-account/api/trim");
  assert.equal(r.json.master, false);
});

test("POST /trim：坏 JSON → 400 且说明原因", async () => {
  const { got } = fakeCtx();
  const r = await call(got.handler, "/tg-account/api/trim", "POST", "{oops");
  assert.equal(r.status, 400);
  assert.equal(r.json.ok, false);
});

test("GET /lineage：返回 map", async () => {
  const { got } = fakeCtx();
  const r = await call(got.handler, "/tg-account/api/lineage");
  assert.equal(r.status, 200);
  assert.equal(r.json.ok, true);
  assert.equal(typeof r.json.map, "object");
});

test("POST /lineage：记 child←parent，非法 id → 400", async () => {
  const { got } = fakeCtx();
  const ok = await call(got.handler, "/tg-account/api/lineage", "POST",
    JSON.stringify({ child: "abcd1234", parent: "ffff9999" }));
  assert.equal(ok.status, 200);
  assert.ok(ok.json.map.abcd1234, "新记录应出现在 map 里");

  const bad = await call(got.handler, "/tg-account/api/lineage", "POST", JSON.stringify({ child: "" }));
  assert.equal(bad.status, 400);
});

test("GET /autoswitch：形状对（on/sid），POST 必须带布尔 on", async () => {
  const { got } = fakeCtx();
  const r = await call(got.handler, "/tg-account/api/autoswitch?session=abcd1234");
  assert.equal(r.status, 200);
  assert.equal(typeof r.json.on, "boolean");
  assert.equal(r.json.sid, "abcd1234");

  const bad = await call(got.handler, "/tg-account/api/autoswitch?session=abcd1234", "POST", JSON.stringify({ on: "yes" }));
  assert.equal(bad.status, 400);

  const off = await call(got.handler, "/tg-account/api/autoswitch?session=abcd1234", "POST", JSON.stringify({ on: false }));
  assert.equal(off.status, 200);
  assert.equal(off.json.on, false);
});

test("GET /card：没有交接卡时 ok:false，有路径也不崩", async () => {
  const { got } = fakeCtx();
  const r = await call(got.handler, "/tg-account/api/card?session=abcd1234");
  assert.equal(r.status, 200);
  assert.equal(r.json.ok, false);
});

test("未知路径：退回账目（不是 404 也不是挂掉）", async () => {
  const { got } = fakeCtx();
  const r = await call(got.handler, "/tg-account/api/whatever");
  assert.equal(r.status, 200);
  assert.equal(r.json.ok, false);
});
