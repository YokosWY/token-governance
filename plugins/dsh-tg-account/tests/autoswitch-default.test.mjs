import test from "node:test";
import assert from "node:assert/strict";
import { mkdtempSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";

/* 这个文件会写 tg-autoswitch.json / tg-lineage.json，所以先把 DSH_HOME 指到临时目录，
   再动态 import（模块加载时才会读 DSH_HOME）。 */
process.env.DSH_HOME = mkdtempSync(join(tmpdir(), "tg-default-"));
const { _test: host } = await import("../lib/index.js");

/* 建一条链：parent ← child；血缘写进 LINEAGE_FILE，并清掉 5 秒血统缓存 */
function chain(...ids) {
  for (let i = 1; i < ids.length; i++) host.recordLineage(ids[i], ids[i - 1]);
  host.lineageParentCache.at = 0;
  return ids;
}
function view(sid) {
  host.lineageParentCache.at = 0;
  return host.autoSwitchView(sid);
}
function toggle(sid, on, turn) {
  host.writeAutoSwitch(host.applyAutoSwitch(host.readAutoSwitch(), sid, on, turn === undefined ? null : { turn }));
  host.lineageParentCache.at = 0;
}

test("默认开：没记录、也没有前序对话（母对话）→ on=true", () => {
  const v = view("aa000000");
  assert.equal(v.on, true);
  assert.equal(v.child, false);
  assert.equal(v.armedTurn, 0, "没有轮末记录时基线 0（第一轮结束就切）");
  assert.equal(v.transferred, null);
});

test("链默认开：子对话跟链首一致，且链首只有一个", () => {
  chain("aa000001", "aa000002");
  const v = view("aa000002");
  assert.equal(v.child, true);
  assert.equal(v.chainRoot, "aa000001", "链首才是母对话");
  assert.equal(v.on, true, "一条链一个开关，默认开");
  assert.equal(typeof v.armedTurn, "number", "子对话也要有基线，否则永远不会触发");
});

test("插件切出来的会话也算子对话（_children 兜底）", () => {
  host.markAutoChild("aa000003");
  const v = view("aa000003");
  assert.equal(v.child, true);
  assert.equal(v.on, true, "兜底标记不影响链默认");
});

test("链内一致：在子对话里关掉 = 整条链（含链首）一起关", () => {
  chain("aa000010", "aa000011");
  toggle("aa000011", false);
  assert.equal(view("aa000010").on, false, "链首跟着关");
  assert.equal(view("aa000011").on, false, "子对话关");
});

test("链内一致：在链首打开 = 子对话也开，基线各记各的", () => {
  chain("aa000020", "aa000021");
  toggle("aa000020", true, 7);
  const root = view("aa000020");
  const child = view("aa000021");
  assert.equal(root.on, true);
  assert.equal(child.on, true, "链内一致");
  assert.equal(root.armedTurn, null, "链首已被转出，不参与判定");
  assert.notEqual(child.armedTurn, 7, "子对话不许继承链首的基线");
});

test("子对话再转接：还是同一条链，链首不变（链内只有一个母对话）", () => {
  chain("aa000030", "aa000031", "aa000032");
  const v = view("aa000032");
  assert.equal(v.chainRoot, "aa000030", "链首仍是 aa000030");
  assert.equal(v.child, true, "子对话切出来的还是子对话");
});

test("子对话里手动打开不再升级成母对话", () => {
  chain("aa000040", "aa000041");
  const map = Object.assign({}, host.readAutoSwitch());
  map["aa000041"] = host.armAutoSwitch(map["aa000041"], { turn: 3 });
  host.writeAutoSwitch(map);
  const v = view("aa000041");
  assert.equal(v.child, true, "血统上仍是子对话");
  assert.equal(v.chainRoot, "aa000040", "链首不会变成它");
  assert.equal(v.on, true, "开关跟着链走");
  assert.equal(v.armedTurn, 3);
});

test("显式关掉的母对话不会再被默认打开", () => {
  const map = Object.assign({}, host.readAutoSwitch());
  map["dddd4444"] = { on: false, user: true, armedAt: Date.now() };
  host.writeAutoSwitch(map);
  assert.equal(view("dddd4444").on, false);
});

test("被转接过的对话：标「已转接」、不再触发转接（armedTurn 为空）", () => {
  chain("aa000050", "aa000051");
  const src = view("aa000050");
  assert.equal(src.transferred && src.transferred.to, "aa000051");
  assert.equal(src.armedTurn, null, "源会话不再参与轮末判定");
  assert.equal(src.on, true, "开关仍跟链走，不因为转出而变色");
});

test("没转出过的对话不误标「已转接」", () => {
  chain("aa000060", "aa000061");
  assert.equal(view("aa000061").transferred, null, "末端会话没有子对话");
});

test("子对话列表去重且不重复", () => {
  const map = Object.assign({}, host.readAutoSwitch());
  delete map._children;
  host.writeAutoSwitch(map);
  host.markAutoChild("eeee5555");
  host.markAutoChild("eeee5555");
  assert.equal(host.autoChildren(host.readAutoSwitch()).filter((x) => x === "eeee5555").length, 1);
});

test("完整 UUID 归一到 sid8：同一会话不写两条记录", () => {
  const full = "9999aaaa-1111-2222-3333-444455556666";
  const v = host.autoSwitchView(full);
  const map = host.readAutoSwitch();
  assert.equal(map[full], undefined, "不许拿完整 id 当 key");
  assert.ok(map["9999aaaa"], "应落到 8 位 key");
  assert.equal(v.on, true);
});

test("transferredTo：转出目标按 sid8 归一并取最后一个", () => {
  chain("aa000070", "aa000071");
  chain("aa000071", "aa000072");
  assert.equal(host.transferredTo("aa000071"), "aa000072");
  assert.equal(host.transferredTo("aa000072"), null);
  assert.equal(host.transferredTo(""), null);
});

test("交接卡不再等上限：没跑过步的会话跳过，跑过就按需生成", () => {
  const noSteps = host.ensureHandoff("aa000080", 999, 0);
  assert.equal(noSteps.due, false, "一轮都没跑完不生成");
  assert.ok(noSteps.cmd.includes("--if-absent"), "命令始终带得出来，可手工跑");
});
