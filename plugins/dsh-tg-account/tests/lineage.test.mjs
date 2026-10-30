import test from "node:test";
import assert from "node:assert/strict";
import { mkdtempSync, mkdirSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { _test } from "../lib/index.js";

const tmp = () => mkdtempSync(join(tmpdir(), "tg-lineage-"));

test("任务夹里的 01-<sid8>.md 归到同一组，按 99-cards 的 at 排序", () => {
  const root = tmp();
  const dir = join(root, "default-workspace", "20261025-tg");
  mkdirSync(dir, { recursive: true });
  writeFileSync(join(dir, "01-aaaa1111.md"), "x");
  writeFileSync(join(dir, "01-bbbb2222.md"), "x");
  writeFileSync(join(dir, "99-cards.json"), JSON.stringify({
    cards: {
      "bbbb2222": { file: "01-bbbb2222.md", at: "2026-10-25 10:00" },
      "aaaa1111": { file: "01-aaaa1111.md", at: "2026-10-25 12:00" },
    },
  }));
  const groups = _test.cardGroups(root);
  assert.equal(groups.length, 1);
  assert.equal(groups[0].g, "default-workspace/20261025-tg");
  assert.deepEqual(groups[0].keys, ["bbbb2222", "aaaa1111"], "早的先");
});

test("没有 99-cards.json 时按文件名兜底", () => {
  const root = tmp();
  const dir = join(root, "default-workspace", "t2");
  mkdirSync(dir, { recursive: true });
  writeFileSync(join(dir, "01-cccc3333.md"), "x");
  const g = _test.cardGroups(root)[0];
  assert.deepEqual(g.keys, ["cccc3333"]);
});

test("显式 parent 链：色组按根、序号按深度", () => {
  const idx = _test.chainIndex({
    bbbb2222: { parent: "aaaa1111" },
    cccc3333: { parent: "bbbb2222" },
  });
  assert.equal(idx["aaaa1111"].g, idx["cccc3333"].g, "同一根同色");
  assert.equal(idx["aaaa1111"].n, 1);
  assert.equal(idx["bbbb2222"].n, 2);
  assert.equal(idx["cccc3333"].n, 3);
  assert.equal(idx["cccc3333"].t, 3);
});

test("recordLineage 落盘并拒非法输入", () => {
  const file = join(tmp(), "tg-lineage.json");
  const rec = _test.recordLineage("session-aaaa1111-xxxx", "session-bbbb2222-yyyy", file);
  assert.ok(rec && rec.parent === "bbbb2222");
  const again = _test.readLineage(file);
  assert.ok(again.sessions["aaaa1111"]);
  assert.equal(_test.recordLineage("aaaa1111", "aaaa1111", file), null, "自指拒绝");
  assert.equal(_test.recordLineage("", "bbbb2222", file), null, "空值拒绝");
});

/* 2026-10-26：侧栏没点的根因——只认任务夹，续跑时首条消息里的交接卡路径没被读。
   下面两条锁住新的推断口径。 */
test("parentIndex 从首条消息的交接卡路径推父会话", () => {
  const cache = tmp();
  const put = (sid, text) =>
    writeFileSync(
      join(cache, `session-${sid}-1111-2222-3333-444455556666.json`),
      JSON.stringify({ record: { rows: { titleInput: { val: { first: { seq: 3, text } } } } } })
    );
  put("dddd4444", "接续上一个会话（aaaa1111）。先读交接卡：D:\\kb\\sessions\\ws\\20261026-auto-aaaa1111\\00-card.md\n读完复述");
  put("eeee5555", "先读交接卡 D:/kb/sessions/ws/20261025-tg-account/01-bbbb2222.md\n继续");
  put("ffff6666", "没有卡片引用的一条消息");
  const idx = _test.parentIndex(cache);
  assert.deepEqual(idx["dddd4444"], { parent: "aaaa1111" }, "auto 目录名带父 sid8");
  assert.deepEqual(idx["eeee5555"], { parent: "bbbb2222" }, "任务夹看 01-<sid8>.md 文件名");
  assert.equal(idx["ffff6666"], undefined, "没引用就不连");
});

test("explicitParents 与 chainIndex 同形，合流后按深度编号", () => {
  const file = join(tmp(), "tg-lineage.json");
  _test.writeLineage({ v: 1, sessions: { dddd4444: { parent: "aaaa1111" }, eeee5555: { parent: "dddd4444" } } }, file);
  const ex = _test.explicitParents(file);
  assert.deepEqual(ex["dddd4444"], { parent: "aaaa1111" });
  const idx = _test.chainIndex(ex);
  assert.equal(idx["aaaa1111"].n, 1);
  assert.equal(idx["eeee5555"].n, 3);
  assert.equal(idx["eeee5555"].t, 3);
});

test("lineageMap 只回 ≥2 成员的链（单条不标）", () => {
  const map = _test.lineageMap(tmp());
  for (const [k, v] of Object.entries(map)) {
    assert.ok(v.t >= 2, `${k} 的 t 应 ≥2，实际 ${v.t}`);
    assert.ok(v.n >= 1 && v.n <= v.t, `${k} 的 n 越界`);
  }
});
