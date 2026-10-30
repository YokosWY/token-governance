import test from "node:test";
import assert from "node:assert/strict";
import { _test } from "../lib/index.js";

const { chainRollup, rootWalk } = _test;

/* 本会话链（2026-10-30 用户要求）：同一条血统链（链首 + 转接出来的子会话，单会话也算一条链）的
   花费与省额；每个成员并上自己的子代理子树；同一文件只算一次，不重复计。 */

const tree = (files) => ({ v: 1, at: 1, fp: "", files });
// 测试不碰真文件：省额一律注入
const rollup = (sid, opts) => chainRollup(sid, { savedOf: () => null, ...(opts || {}) });

test("rootWalk：沿 parent 走到链首，自环/断链不卡死", () => {
  const parents = { bbbbbbbb: { parent: "aaaaaaaa" }, aaaaaaaa: { parent: "" }, cccccccc: { parent: "cccccccc" } };
  assert.equal(rootWalk("bbbbbbbb", parents), "aaaaaaaa");
  assert.equal(rootWalk("aaaaaaaa", parents), "aaaaaaaa");
  assert.equal(rootWalk("cccccccc", parents), "cccccccc");
  assert.equal(rootWalk("", parents), "");
});

test("chainRollup：整条链只算一次，子代理并进各自成员", () => {
  const parents = { bbbbbbbb: { parent: "aaaaaaaa" } };
  const t = tree({
    "/a": { sid: "aaaaaaaa", parent: "", cny: 1 },
    "/b": { sid: "bbbbbbbb", parent: "aaaaaaaa", cny: 2 },
    "/c": { sid: "cccccccc", parent: "bbbbbbbb", cny: 0.5 }, // 子代理
    "/d": { sid: "dddddddd", parent: "", cny: 9 }, // 另一条链，不许串味
  });
  const r = rollup("bbbbbbbb", { parents, tree: t });
  assert.equal(r.root, "aaaaaaaa");
  assert.equal(r.members, 2);
  assert.equal(r.cny, 3.5);
  assert.equal(r.n, 3);
  assert.equal(r.stale, false);
});

test("chainRollup：没有别的会话时，自己也算一条链（n=1）", () => {
  const t = tree({ "/a": { sid: "aaaaaaaa", parent: "", cny: 1.234 } });
  const r = rollup("aaaaaaaa", { parents: {}, tree: t });
  assert.equal(r.cny, 1.234);
  assert.equal(r.n, 1);
  assert.equal(r.members, 1);
  assert.equal(r.stale, false);
});

test("chainRollup：链省额 = 各成员（含子代理）省额之和，本会话用实时值", () => {
  const parents = { bbbbbbbb: { parent: "aaaaaaaa" } };
  const t = tree({
    "/a": { sid: "aaaaaaaa", parent: "", cny: 1 },
    "/b": { sid: "bbbbbbbb", parent: "aaaaaaaa", cny: 2 },
    "/c": { sid: "cccccccc", parent: "bbbbbbbb", cny: 0.5 },
  });
  const savedOf = (p) => ({ "/a": 0.2, "/c": 0.05 }[p] ?? null);
  const r = rollup("bbbbbbbb", { parents, tree: t, savedOf, selfPath: "/b", selfSaved: 0.3 });
  assert.equal(r.savedCny, 0.55);
  assert.equal(r.savedN, 3);
});

test("chainRollup：省额拿不到时给 null，不写 ¥0", () => {
  const t = tree({ "/a": { sid: "aaaaaaaa", parent: "", cny: 1 } });
  const r = rollup("aaaaaaaa", { parents: {}, tree: t, selfPath: "/a", selfSaved: null });
  assert.equal(r.savedCny, null);
  assert.equal(r.savedN, 0);
});

test("chainRollup：索引里存完整 uuid 也能对上 8 位链 id", () => {
  const t = tree({ "/a": { sid: "18ba8815-6261-40f8-8a6a-71466358da54", parent: "", cny: 0.4 } });
  const r = rollup("18ba8815", { parents: {}, tree: t });
  assert.equal(r.cny, 0.4);
  assert.equal(r.n, 1);
});

test("chainRollup：本会话用实时值，索引滞后也不让链合计小于本会话", () => {
  const t = tree({ "/a": { sid: "aaaaaaaa", parent: "", cny: 0.1 } });
  const r = rollup("aaaaaaaa", { parents: {}, tree: t, selfPath: "/a", selfCny: 0.2921 });
  assert.equal(r.cny, 0.2921);
});

test("chainRollup：索引还没建好标 stale，不拿 0 冒充", () => {
  const r = rollup("aaaaaaaa", { parents: {}, tree: { v: 1, at: 0, files: {} } });
  assert.equal(r.cny, null);
  assert.equal(r.stale, true);
});
