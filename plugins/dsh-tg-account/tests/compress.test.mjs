import test from "node:test";
import assert from "node:assert/strict";
import { buildCandidate, buildNotice, estTokens, looksTabular, utf8Bytes, DEFAULT_CFG } from "../lib/compress.js";

const table = (rows) => {
  const out = ["| model | input | output |", "|---|---|---|"];
  for (let i = 0; i < rows; i++) out.push(`| model-${i} | ${(i * 0.11).toFixed(3)} | ${(i * 0.37).toFixed(3)} |`);
  return out.join("\n");
};

test("低于触发体积放行", () => {
  assert.equal(buildCandidate("x".repeat(1000)), null);
});

test("400 行管道表格：结构感知采样，≤72%，带行号", () => {
  const text = table(400);
  const c = buildCandidate(text);
  assert.ok(c, "应当产出候选");
  assert.ok(c.after <= c.before * DEFAULT_CFG.keepRatioMax, "字节门");
  assert.ok(estTokens(c.text) < estTokens(text), "token 门");
  assert.match(c.text, /省略 L\d+-L\d+/);
  assert.match(c.text, /^L\d+: \| model-/m, "中段采样带原始行号");
  assert.equal(c.strategy, "table-strided");
  const n = buildNotice(c, "abc123", "C:\\\\tmp\\\\orig.txt");
  assert.match(n, /\[tg-trim #abc123 compressed: \d+ -> \d+ bytes/);
  assert.match(n, /read "C:\\\\tmp\\\\orig.txt"/);
});

test("错误行在省略区也必须保留（含 ±1 上下文）", () => {
  const lines = [];
  for (let i = 0; i < 600; i++) lines.push(`step ${i} ok`);
  lines[301] = "FATAL: traceback at module X";
  lines[302] = "line after error";
  const text = lines.join("\n");
  const c = buildCandidate(text);
  assert.ok(c);
  assert.match(c.text, /FATAL: traceback at module X/);
  assert.match(c.text, /line after error/);
});

test("重复行折叠走无损路线", () => {
  const lines = [];
  for (let r = 0; r < 6; r++) for (let i = 0; i < 30; i++) lines.push(`same line ${r}`);
  lines.push("z".repeat(50));
  const text = lines.join("\n");
  const c = buildCandidate(text);
  assert.ok(c, "重复行应当被折叠");
  assert.ok(c.after < c.before);
});

test("弱压缩被门控拒绝：50 行短文本不足 72%", () => {
  const lines = Array.from({ length: 50 }, (_, i) => `line ${i} ${"y".repeat(30)}`);
  assert.equal(buildCandidate(lines.join("\n")), null);
});

test("大 JSON 单行超长：首尾截断且不过门时放行", () => {
  const big = "[" + Array.from({ length: 3000 }, (_, i) => `{"k":${i},"v":"${"z".repeat(20)}"}`).join(",") + "]";
  const c = buildCandidate(big);
  if (c) {
    assert.ok(c.after <= c.before * DEFAULT_CFG.keepRatioMax);
    assert.ok(c.text.length >= 20);
  }
});

test("中文长输出可压且不破坏 UTF-8 字节计数", () => {
  const lines = Array.from({ length: 500 }, (_, i) => `第 ${i} 行：这是用于测试的中文内容，重复程度很低。`);
  const text = lines.join("\n");
  const c = buildCandidate(text);
  assert.ok(c, "中文长输出应当压得动");
  assert.ok(c.after <= c.before * DEFAULT_CFG.keepRatioMax);
  assert.equal(utf8Bytes("中文"), 6);
});

test("表格识别", () => {
  assert.equal(looksTabular(table(20).split("\n")), true);
  assert.equal(looksTabular(["a", "b", "c"]), false);
});
