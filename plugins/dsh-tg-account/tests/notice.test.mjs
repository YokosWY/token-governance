import test from "node:test";
import assert from "node:assert/strict";
import { COMPRESS_NOTICE_RE, collectCompNotices } from "../lib/index.js";

const scan = (line) => {
  const out = [];
  const re = new RegExp(COMPRESS_NOTICE_RE.source, "g");
  let m;
  while ((m = re.exec(line)) !== null) out.push([Number(m[1].replace(/,/g, "")), Number(m[2].replace(/,/g, ""))]);
  return out;
};

test("正则：识别 TG 自己的 tg-trim 通知", () => {
  const line = '前缀内容 [tg-trim #0ws1 compressed: 10,369 -> 2,083 bytes (~80% 更小，策略 table-strided)。 需要时 read "C:\\\\tmp\\\\x.txt"]';
  assert.deepEqual(scan(line), [[10369, 2083]]);
});

test("正则：继续识别历史 save-token 通知（换实现不换账）", () => {
  const a = '[save-token #c3po compressed: 4,251 -> 2,077 bytes (~51% smaller). Need any omitted detail?]';
  const b = '[save-token #ca1h losslessly re-encoded: 34,000 -> 19,935 bytes (~41% smaller), zero information loss.]';
  assert.deepEqual(scan(a), [[4251, 2077]]);
  assert.deepEqual(scan(b), [[34000, 19935]]);
});

test("正则：非压缩标记不误计", () => {
  assert.equal(scan('[save-token #d1 deduped: this ops_bash call returned BYTE-IDENTICAL output]').length, 0);
  assert.equal(scan("tg-trim 只是聊到这个词").length, 0);
});

/* ---------------- 记账口径：只认工具结果末尾 ---------------- */

const ev = (o) => JSON.stringify(o);
const call = (id, name) => ev({ type: "tool/call", data: { callId: id, name } });
const result = (id, text) => ev({ type: "tool/result", data: { message: { toolCallId: id, content: [{ type: "text", text }] } } });
const step = () => ev({ type: "assistant/message", data: { usage: {} } });
const notice = (id, before, after) =>
  `已压缩。\n\n[tg-trim #${id} compressed: ${before} -> ${after} bytes (~80% 更小，策略 lines-strided)。需要被省略的细节时，直接 read "C:\\\\tmp\\\\x.txt"（或 grep 该路径）取原文，不要根据片段猜。]`;

test("记账：tool/result 末尾的真实通知计入，at 取通知前步数", () => {
  const raw = [step(), step(), call("c1", "ops_bash"), result("c1", notice("a1", "10,369", "2,083"))].join("\n");
  assert.deepEqual(collectCompNotices(raw), [{ savedB: 8286, at: 2 }]);
});

test("记账：read 到源码注释里的示例不算（历史假阳性）", () => {
  const src = 'const re = /\\[tg-trim #0ws1 compressed: 10,369 -> 2,083 bytes/;\n// 例：[tg-trim #a1 compressed: 1,000 -> 100 bytes]';
  const raw = [call("c1", "read"), result("c1", src)].join("\n");
  assert.deepEqual(collectCompNotices(raw), []);
});

test("记账：ops_bash 回显的示例在文本中间也不算", () => {
  const out = 'grep 输出：\n[tg-trim #0ws1 compressed: 10,369 -> 2,083 bytes]\n后面还有别的行';
  const raw = [call("c1", "ops_bash"), result("c1", out)].join("\n");
  assert.deepEqual(collectCompNotices(raw), []);
});

test("记账：同一 id 重复出现（重放/双写）只计一次", () => {
  const raw = [
    call("c1", "ops_bash"),
    result("c1", notice("a1", "10,000", "2,000")),
    call("c2", "grep"),
    result("c2", notice("a1", "10,000", "2,000")),
  ].join("\n");
  assert.deepEqual(collectCompNotices(raw), [{ savedB: 8000, at: 0 }]);
});

test("记账：read / save_token_expand 即使末尾有通知也跳过", () => {
  const raw = [
    call("c1", "read"),
    result("c1", notice("a1", "10,000", "2,000")),
    call("c2", "save_token_expand"),
    result("c2", notice("a2", "10,000", "2,000")),
  ].join("\n");
  assert.deepEqual(collectCompNotices(raw), []);
});

test("记账：旧 save-token 的 losslessly 通知也认", () => {
  const tail = '[save-token #ca1h losslessly re-encoded: 34,000 -> 19,935 bytes (~41% smaller), zero information loss.]';
  const raw = [call("c1", "web_fetch"), result("c1", tail)].join("\n");
  assert.deepEqual(collectCompNotices(raw), [{ savedB: 14065, at: 0 }]);
});

test("记账：空日志 / 缺 tool/call 映射不崩", () => {
  assert.deepEqual(collectCompNotices(""), []);
  const raw = [result("c9", notice("z1", "9,000", "1,000"))].join("\n");
  assert.deepEqual(collectCompNotices(raw), [{ savedB: 8000, at: 0 }]);
});

test("记账：content 无文本块时不计", () => {
  const raw = ev({ type: "tool/result", data: { message: { toolCallId: "c1", content: [{ type: "image", data: "x" }] } } });
  assert.deepEqual(collectCompNotices(raw), []);
});

test("记账：同 id 不同体积（极端碰撞）各自保留", () => {
  const raw = [
    call("c1", "ops_bash"),
    result("c1", notice("a1", "10,000", "2,000")),
    call("c2", "grep"),
    result("c2", notice("a1", "9,000", "2,000")),
  ].join("\n");
  assert.equal(collectCompNotices(raw).length, 2);
});
