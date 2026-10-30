import test from "node:test";
import assert from "node:assert/strict";
import { mkdtempSync, mkdirSync, writeFileSync, utimesSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";

/* readTrim 按 mtime+size 缓存：这里先指到临时 DSH_HOME，再动态 import（模块加载时才读 DSH_HOME）。 */
process.env.DSH_HOME = mkdtempSync(join(tmpdir(), "tg-trimcache-"));
const { _test: host } = await import("../lib/index.js");
const FILE = join(process.env.DSH_HOME, "_local", "tg-trim.json");

function write(v, secsAgo = 0) {
  mkdirSync(join(process.env.DSH_HOME, "_local"), { recursive: true });
  writeFileSync(FILE, JSON.stringify(v, null, 2), "utf8");
  if (secsAgo) {
    const t = Date.now() / 1000 - secsAgo;
    utimesSync(FILE, t, t);
  }
}

test("没有文件：全默认（全开）", () => {
  const p = host.readTrim();
  assert.equal(p.master, true);
  assert.equal(Object.values(p.rules).every(Boolean), true);
});

test("改文件立刻生效（mtime 变了就不再用缓存）", () => {
  write({ version: 2, master: false, rules: { toolEcho: false } });
  const p = host.readTrim();
  assert.equal(p.master, false);
  assert.equal(p.rules.toolEcho, false);
  assert.equal(p.rules.logs, true);
});

test("返回的是克隆：调用方就地改不会污染缓存", () => {
  write({ version: 2, master: true, rules: {} });
  const a = host.readTrim();
  a.master = false;
  a.rules.logs = false;
  a.rules.toolEcho = false;
  const b = host.readTrim();
  assert.equal(b.master, true);
  assert.equal(b.rules.logs, true);
  assert.equal(b.rules.toolEcho, true);
});

test("writeTrim 之后立刻能读到新值（写路径直接失效缓存）", () => {
  write({ version: 2, master: true, rules: {} });
  host.readTrim(); // 先把缓存填上
  const cur = host.readTrim();
  cur.master = false;
  host.writeTrim(cur);
  assert.equal(host.readTrim().master, false);
});

test("v1 老文件：布尔值当全局默认", () => {
  write({ "session-aaaa": true });
  assert.equal(host.readTrim().master, true);
  write({ "session-bbbb": false });
  assert.equal(host.readTrim().master, false);
});
