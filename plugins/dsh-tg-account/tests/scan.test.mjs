import test from "node:test";
import assert from "node:assert/strict";
import { mkdtempSync, mkdirSync, writeFileSync, rmSync, utimesSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { createSessionIndex, sidOf, LOG_NAME } from "../lib/scan.js";

/* 造一棵和真机同形的树：<root>/<shard>/<uuid>/session.v4.jsonl.zstd */
function tree() {
  const root = mkdtempSync(join(tmpdir(), "tg-scan-"));
  const mk = (shard, sid, mtimeMs) => {
    const dir = join(root, shard, sid);
    mkdirSync(dir, { recursive: true });
    const p = join(dir, LOG_NAME);
    writeFileSync(p, "x");
    if (mtimeMs) utimesSync(p, mtimeMs / 1000, mtimeMs / 1000);
    return p;
  };
  const a = mk("--ws-a--", "12402039-aaaa-4bbb-8ccc-000000000001", 1000);
  const b = mk("--ws-a--", "9dff556a-bbbb-4ccc-8ddd-000000000002", 2000);
  const c = mk("--ws-b--", "263844f6-cccc-4ddd-8eee-000000000003", 3000);
  return { root, a, b, c, cleanup: () => rmSync(root, { recursive: true, force: true }) };
}

test("sidOf：目录名就是 sid，session- 前缀被去掉、统一小写", () => {
  assert.equal(sidOf("/x/--ws--/12402039-AAAA/session.v4.jsonl.zstd"), "12402039-aaaa");
  assert.equal(sidOf("/x/--ws--/session-abcd/session.v4.jsonl.zstd"), "abcd");
});

test("全量列表：深度内只认日志文件，忽略别的文件", () => {
  const t = tree();
  try {
    writeFileSync(join(t.root, "--ws-a--", "README.md"), "x");
    const idx = createSessionIndex({ root: t.root });
    const all = idx.list();
    assert.equal(all.length, 3);
    assert.deepEqual(all.map((f) => f.path).sort(), [t.a, t.b, t.c].sort());
  } finally {
    t.cleanup();
  }
});

test("newest：按日志 mtime 取最近的那个", () => {
  const t = tree();
  try {
    const idx = createSessionIndex({ root: t.root });
    assert.equal(idx.newest().path, t.c);
  } finally {
    t.cleanup();
  }
});

test("find：支持全 id 与 8 位前缀，大小写不敏感；未命中返回 null", () => {
  const t = tree();
  try {
    const idx = createSessionIndex({ root: t.root });
    assert.equal(idx.find("9dff556a-bbbb-4ccc-8ddd-000000000002").path, t.b);
    assert.equal(idx.find("9DFF556A").path, t.b);
    assert.equal(idx.find("deadbeef"), null);
  } finally {
    t.cleanup();
  }
});

test("find：目录名带 session- 前缀（新版本布局）时，带/不带前缀都能命中", () => {
  const root = mkdtempSync(join(tmpdir(), "tg-scan-prefix-"));
  try {
    const sid = "8d25805d-358b-4556-919d-5103347cbce9";
    const dir = join(root, "--ws--", "session-" + sid);
    mkdirSync(dir, { recursive: true });
    const p = join(dir, LOG_NAME);
    writeFileSync(p, "x");
    const idx = createSessionIndex({ root });
    assert.equal(idx.find("session-" + sid).path, p, "传事件里的 session-<uuid>");
    assert.equal(idx.find(sid).path, p, "传裸 uuid");
    assert.equal(idx.find("8d25805d").path, p, "传 8 位前缀");
    assert.equal(idx.find(sid.toUpperCase()).path, p, "大小写不敏感");
  } finally {
    rmSync(root, { recursive: true, force: true });
  }
});

test("find 命中后只 stat 那一个文件，不再扫目录（walks 不涨）", () => {
  const t = tree();
  try {
    const idx = createSessionIndex({ root: t.root });
    idx.list();
    const before = idx.stats().walks;
    idx.find("12402039");
    idx.find("12402039");
    assert.equal(idx.stats().walks, before);
  } finally {
    t.cleanup();
  }
});

test("TTL 内复用同一份列表，过期后才重扫", async () => {
  const t = tree();
  try {
    const idx = createSessionIndex({ root: t.root, ttl: 40 });
    idx.list();
    idx.list();
    assert.equal(idx.stats().walks, 1);
    await new Promise((r) => setTimeout(r, 60));
    idx.list();
    assert.equal(idx.stats().walks, 2);
  } finally {
    t.cleanup();
  }
});

test("invalidate：不等 TTL，下次读立刻重扫（新会话目录能被看到）", () => {
  const t = tree();
  try {
    const idx = createSessionIndex({ root: t.root, ttl: 60000 });
    assert.equal(idx.list().length, 3);
    const dir = join(t.root, "--ws-c--", "abcd1234-0000-4000-8000-000000000004");
    mkdirSync(dir, { recursive: true });
    writeFileSync(join(dir, LOG_NAME), "y");
    assert.equal(idx.list().length, 3, "TTL 内不重扫");
    idx.invalidate();
    assert.equal(idx.list().length, 4);
    assert.ok(idx.find("abcd1234"));
  } finally {
    t.cleanup();
  }
});

test("会话被删：find 返回 null，不抛错", () => {
  const t = tree();
  try {
    const idx = createSessionIndex({ root: t.root });
    const hit = idx.find("12402039");
    assert.ok(hit);
    rmSync(hit.path);
    assert.equal(idx.find("12402039"), null);
  } finally {
    t.cleanup();
  }
});

test("since：按 mtime 过滤（savedFiles / dayFiles 的口径）", () => {
  const t = tree();
  try {
    const idx = createSessionIndex({ root: t.root });
    assert.deepEqual(idx.since(2000).map((f) => f.path).sort(), [t.b, t.c].sort());
    assert.equal(idx.since(9999).length, 0);
  } finally {
    t.cleanup();
  }
});
