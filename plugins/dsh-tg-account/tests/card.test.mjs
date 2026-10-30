import test from "node:test";
import assert from "node:assert/strict";
import { mkdtempSync, mkdirSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { _test } from "../lib/index.js";

const tmp = () => mkdtempSync(join(tmpdir(), "tg-card-"));

test("任务夹里的 01-<sid8>.md 能被扫描到", () => {
  const root = tmp();
  const dir = join(root, "default-workspace", "20261025-task");
  mkdirSync(dir, { recursive: true });
  writeFileSync(join(dir, "00-card.md"), "主卡\n");
  writeFileSync(join(dir, "01-ab12cd34.md"), "会话卡\n");
  const hit = _test.scanCard(root, "ab12cd34");
  assert.ok(hit && hit.path.endsWith("01-ab12cd34.md"), String(hit && hit.path));
});

test("老规则：auto 夹的 00-card.md 仍认", () => {
  const root = tmp();
  const dir = join(root, "default-workspace", "20261025-auto-ab12cd34");
  mkdirSync(dir, { recursive: true });
  writeFileSync(join(dir, "00-card.md"), "自动卡\n");
  const hit = _test.scanCard(root, "ab12cd34");
  assert.ok(hit && hit.path.endsWith(join("20261025-auto-ab12cd34", "00-card.md")), String(hit && hit.path));
});

test("99-cards.json 索引指向的卡也认（改过名也能找）", () => {
  const root = tmp();
  const dir = join(root, "default-workspace", "20261025-task");
  mkdirSync(dir, { recursive: true });
  writeFileSync(join(dir, "renamed.md"), "改名的卡\n");
  writeFileSync(join(dir, "99-cards.json"),
    JSON.stringify({ v: 1, cards: { ab12cd34: { file: "renamed.md" } } }));
  const hit = _test.scanCard(root, "ab12cd34");
  assert.ok(hit && hit.path.endsWith("renamed.md"), String(hit && hit.path));
});

test("没有本会话的卡时不乱指别人", () => {
  const root = tmp();
  const dir = join(root, "default-workspace", "20261025-task");
  mkdirSync(dir, { recursive: true });
  writeFileSync(join(dir, "00-card.md"), "别人的主卡\n");
  writeFileSync(join(dir, "01-99999999.md"), "别人的会话卡\n");
  assert.equal(_test.scanCard(root, "ab12cd34"), null);
});
