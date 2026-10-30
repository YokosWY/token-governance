import test from "node:test";
import assert from "node:assert/strict";
import { apply } from "../lib/index.js";

function fakeCtx() {
  const handlers = new Map();
  const spills = [];
  return {
    handlers,
    spills,
    logger: () => {},
    on(name, fn, opts) {
      handlers.set(name, { fn, opts });
    },
    get(name) {
      if (name === "spillStore")
        return {
          saveText: async (o) => {
            spills.push(o);
            return { locator: "C:\\\\Temp\\\\tg-spill\\\\orig.txt" };
          },
        };
      return undefined;
    },
    webServer: { register: () => {} },
    inject: (deps, fn) => fn({ systemPrompt: { section: () => {} }, effect: (f) => f() }),
    effect: () => {},
  };
}

const table = (rows) => {
  const out = ["| model | input | output |", "|---|---|---|"];
  for (let i = 0; i < rows; i++) out.push(`| model-${i} | ${(i * 0.11).toFixed(3)} | ${(i * 0.37).toFixed(3)} |`);
  return out.join("\n");
};

test("apply 挂上 tools/post-execute（prepend）并压缩大工具结果", async () => {
  const ctx = fakeCtx();
  apply(ctx);
  const entry = ctx.handlers.get("tools/post-execute");
  assert.ok(entry, "必须挂到 tools/post-execute");
  assert.equal(entry.opts && entry.opts.prepend, true, "必须 prepend");

  const text = table(400);
  const exec = { name: "ops_bash", callId: "c1", agent: { session: { header: { id: "s-1" } } } };
  const result = { content: [{ type: "text", text }] };
  const out = await entry.fn(exec, result, async () => ({ kind: "accept", content: result.content }));
  assert.equal(out.kind, "accept");
  const got = out.content[0].text;
  assert.match(got, /\[tg-trim #[a-z0-9]{4} compressed: \d+ -> \d+ bytes/);
  assert.ok(got.length < text.length);
  assert.equal(ctx.spills.length, 1, "替换前必须落盘原文");
  assert.equal(ctx.spills[0].content, text, "落盘的是完整原文");
});

test("read / save_token_expand 永不处理", async () => {
  const ctx = fakeCtx();
  apply(ctx);
  const { fn } = ctx.handlers.get("tools/post-execute");
  for (const name of ["read", "save_token_expand"]) {
    const text = table(400);
    const out = await fn({ name, callId: "c2", agent: { session: { header: { id: "s-1" } } } }, { content: [{ type: "text", text }] }, async () => ({
      kind: "accept",
      content: [{ type: "text", text }],
    }));
    assert.equal(out.content[0].text, text, name + " 应当原样放行");
  }
  assert.equal(ctx.spills.length, 0);
});

test("没有 spillStore 时放弃压缩（可逆性优先）", async () => {
  const ctx = fakeCtx();
  ctx.get = () => undefined;
  apply(ctx);
  const { fn } = ctx.handlers.get("tools/post-execute");
  const text = table(400);
  const out = await fn({ name: "ops_bash", callId: "c3", agent: { session: { header: { id: "s-1" } } } }, { content: [{ type: "text", text }] }, async () => ({
    kind: "accept",
    content: [{ type: "text", text }],
  }));
  assert.equal(out.content[0].text, text, "没落盘能力就必须原样放行");
});

test("含非文本块的工具结果不压", async () => {
  const ctx = fakeCtx();
  apply(ctx);
  const { fn } = ctx.handlers.get("tools/post-execute");
  const content = [{ type: "text", text: table(400) }, { type: "image", data: "x" }];
  const out = await fn({ name: "shot", callId: "c4", agent: { session: { header: { id: "s-1" } } } }, { content }, async () => ({ kind: "accept", content }));
  assert.equal(out.content, content);
});
