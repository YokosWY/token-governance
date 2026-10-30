import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";

const here = dirname(fileURLToPath(import.meta.url));
const src = readFileSync(join(here, "..", "lib", "client.js"), "utf8");

/* 同一个假宿主：只需要拿到 __test.trimStat，不跑渲染 */
function load() {
  let def;
  const window = { __ModuleLoader__: { load: (d) => { def = d; } } };
  const React = {
    createElement: () => null,
    useState: () => [null, () => {}],
    useRef: () => ({ current: 0 }),
    useEffect: () => {},
  };
  const req = (n) => { if (n === "react") return React; throw new Error("unexpected require " + n); };
  new Function("window", "require", "fetch", src)(window, req, () => Promise.resolve({ json: () => Promise.resolve({}) }));
  const mod = def.factory(req);
  mod.apply({ slots: { inject: () => {}, register: () => {} }, get: () => null });
  return mod.__test;
}

/* 合计：76 个会话 / 10008 步 / 188 次压缩省 ¥1.4002（本会话只占 4 次 ¥0.01） */
const AGG = { v: 4, cny: 44.8785, comp: 1.4002, compCount: 188, compBytes: 1129829, sessions: 76, steps: 10008, stale: false,
  since: new Date(2026, 9, 22, 18, 56).getTime() };
const SELF = { cny: 0.0104, comp: 0.0104, compCount: 4, schema: 0, catalog: 0 };

test("J1 有合计账：数字取全部对话，不是本会话", () => {
  const t = load();
  const s = t.trimStat({ saved: AGG, savedSelf: SELF });
  assert.equal(s.text, "全部对话 省 ¥1.40 · 188 次");
  assert.ok(!s.text.includes("0.01"), "不能把本会话的 1 分钱放行内");
});

test("D1 合计很小（¥0.0026）时不许显示 ¥0.00，字节按 KB 说", () => {
  const t = load();
  const tiny = { ...AGG, comp: 0.0026, compCount: 1, compBytes: 4000, cny: 0.0026 };
  const s = t.trimStat({ saved: tiny, savedSelf: SELF });
  assert.equal(s.text, "全部对话 省 ¥0.003 · 1 次");
  assert.ok(!/¥0\.00(?!\d)/.test(s.text), "0 与「1 次」不能同时出现");
  assert.match(s.tip, /省下 4 KB/);
});

test("J2 合计账没算完 / 扫出 0 个会话：降级标「本次」，不冒充合计、不出 0", () => {
  const t = load();
  const pending = t.trimStat({ saved: { pending: true }, savedSelf: SELF });
  assert.equal(pending.text, "本次对话 省 ¥0.01 · 4 次");
  assert.match(pending.tip, /正在统计/);
  /* 宿主解压全挂时仍会写 sessions:0 / comp:0 的缓存，客户端不能当它是「合计为 0」 */
  const failed = t.trimStat({ saved: { ...AGG, sessions: 0, comp: 0, compCount: 0 }, savedSelf: SELF });
  assert.equal(failed.text, "本次对话 省 ¥0.01 · 4 次");
  const none = t.trimStat({ saved: null, savedSelf: null });
  assert.equal(none.text, "统计中…");
});

test("J3 悬停要素齐全：合计 / 会话数 / 本会话 / 口径 / 起点", () => {
  const t = load();
  const tip = t.trimStat({ saved: AGG, savedSelf: SELF }).tip;
  assert.match(tip, /全部对话合计（76 个会话 · 10008 步 · 自 10-22 起）/);
  assert.match(tip, /工具输出压缩 ¥1\.40 · 188 次 · 省下 1\.1 MB/);
  assert.match(tip, /本会话 压缩 ¥0\.01 · 4 次/);
  assert.match(tip, /口径：/);
});

test("J3b 缺字段不写假数：无 since 不补日期，无 steps 不写「0 步」", () => {
  const t = load();
  const tip = t.trimStat({ saved: { ...AGG, since: undefined, steps: undefined }, savedSelf: SELF }).tip;
  assert.match(tip, /全部对话合计（76 个会话）/);
  assert.ok(!tip.includes("自 "), "无起点不补日期");
  assert.ok(!tip.includes("0 步"), "无步数不写 0");
});

test("真有会话但一次没压缩过：行内不写 ¥0.000，悬停也不写", () => {
  const t = load();
  const s = t.trimStat({ saved: { ...AGG, comp: 0, compCount: 0, compBytes: 0, cny: 43.4783 }, savedSelf: SELF });
  assert.equal(s.text, "全部对话 暂无压缩");
  assert.match(s.tip, /全部对话合计（76 个会话/);
  assert.match(s.tip, /工具输出压缩 暂未触发/);
  assert.ok(!s.tip.includes("¥0.000"), "零压缩时悬停不许出现 ¥0.000");
});

test("D1b 金额低于 ¥0.001 也不出 ¥0.000：写 ¥<0.001，字节按 B 说", () => {
  const t = load();
  const micro = { ...AGG, comp: 0.0003, compCount: 3, compBytes: 600, cny: 0.0003 };
  const s = t.trimStat({ saved: micro, savedSelf: SELF });
  assert.equal(s.text, "全部对话 省 ¥<0.001 · 3 次");
  assert.match(s.tip, /省下 600 B/);
  assert.ok(!/¥0\.000(?!\d)/.test(s.text + s.tip), "任何位置都不许把正数写成 0");
});

test("D8 本会话侧也不许把正数写成 ¥0.00", () => {
  const t = load();
  const fallback = t.trimStat({ saved: { pending: true }, savedSelf: { comp: 0.0003, compCount: 3 } });
  assert.equal(fallback.text, "本次对话 省 ¥<0.01 · 3 次");
  assert.match(fallback.tip, /本会话 压缩 ¥<0\.01 · 3 次/);
  const agg = t.trimStat({ saved: AGG, savedSelf: { comp: 0.0003, compCount: 3 } });
  assert.match(agg.tip, /本会话 压缩 ¥<0\.01 · 3 次/);
});

test("D9 字节进位：1048575 B 显示 1.0 MB，不是 1024 KB", () => {
  const t = load();
  const tip = t.trimStat({ saved: { ...AGG, compBytes: 1048575 }, savedSelf: SELF }).tip;
  assert.match(tip, /省下 1\.0 MB/);
  assert.ok(!tip.includes("1024 KB"));
});

test("J5 与气泡两处「省」不混：点明气泡「累计省」与「省」各自口径", () => {
  const t = load();
  const tip = t.trimStat({ saved: AGG, savedSelf: SELF }).tip;
  assert.match(tip, /另：schema \+ 技能目录 累计省 ¥43\.5/);
  assert.match(tip, /气泡「累计省」¥44\.9 ≈ 压缩 \+ 这两项/);
  assert.match(tip, /气泡「省」只含本会话/);
});

test("缓存过期时提示重算，不装新数", () => {
  const t = load();
  const tip = t.trimStat({ saved: { ...AGG, stale: true }, savedSelf: SELF }).tip;
  assert.match(tip, /缓存 10 分钟，后台重算中/);
});

test("D5 接线断言：面板确实用 trimStat 的结果渲染（防只测纯函数）", () => {
  assert.match(src, /var ts = trimStat\(d\);/);
  assert.match(src, /title: ts\.tip/);
  assert.match(src, /\}, ts\.text\)/);
  assert.ok(!/d\.savedSelf && d\.savedSelf\.comp > 0/.test(src), "旧的「本会话 0 分」写法必须消失");
});

/* 2026-10-30 用户要求：胶囊把「累计」改叫「累计省」；展开卡片摆全
   本会话 / 本会话链 / 今日花费 / 省多少 / 命中率，金额与命中率 3 位小数。 */
test("P1 胶囊写「累计省」，旧写法「累计 」不许留", () => {
  assert.match(src, /"· 累计省 "/);
  assert.ok(!/"· 累计 "/.test(src), "旧的「· 累计 」必须消失");
});

test("P2 展开卡片摆全：本会话 / 本会话链 / 本会话链省 / 今日花费 / 今日省 / 累计省 / 命中率", () => {
  for (const label of ["本会话合计", "本会话链", "本会话链省", "今日花费", "今日省", "本会话省", "累计省", "命中率"]) {
    assert.ok(src.includes('"' + label + '"'), "卡片缺行：" + label);
  }
  assert.match(src, /fmtCny3\(d\.cny\)/);
  assert.match(src, /fmtChain\(d\.chain\)/);
  assert.match(src, /fmtChainSaved\(d\.chain\)/);
  assert.match(src, /fmtRate\(d\.hit\)/);
});

/* 2026-10-30 追加：同类型的花费和省挨着（本会话→本会话省、本会话链→本会话链省、今日花费→今日省） */
test("P3 卡片行序：花费和省成对，别把两类拆散", () => {
  const at = (s) => src.indexOf('"' + s + '"');
  const order = ["本会话合计", "本会话省", "本会话链", "本会话链省", "今日花费", "今日省", "累计省"];
  for (let i = 0; i < order.length; i++) {
    assert.ok(at(order[i]) > 0, "卡片缺行：" + order[i]);
    if (i) assert.ok(at(order[i - 1]) < at(order[i]), order[i - 1] + " 应排在 " + order[i] + " 前面");
  }
});

