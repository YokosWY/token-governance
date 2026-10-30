import test from "node:test";
import assert from "node:assert/strict";
import { spawnSync } from "node:child_process";
import { existsSync, mkdtempSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { _test as host } from "../lib/index.js";

/* accountPaths() 有 3 秒缓存：每个用例前手动过期 */
function reset() {
  host.accountCfgCache.at = 0;
}

const saved = {};
function setEnv(name, value) {
  if (value === undefined) delete process.env[name];
  else process.env[name] = value;
}

test("路径解析：没配 root 时不编造默认路径（降级而不是指向别人机器）", () => {
  // 本机可能真有 <DSH_HOME>/_local/tg-account.json（装过的机器都有），
  // 所以这条要在空 DSH_HOME 的子进程里跑，否则测的是「这台机器配没配」而不是代码。
  const home = mkdtempSync(join(tmpdir(), "tg-acc-home-"));
  const lib = new URL("../lib/index.js", import.meta.url).href;
  const code = `import { _test } from ${JSON.stringify(lib)};`
    + "console.log(JSON.stringify(_test.accountPaths()));";
  const env = { ...process.env, DSH_HOME: home };
  for (const k of ["TG_ACCOUNT_ROOT", "TG_ACCOUNT_CONFIG", "TG_ACCOUNT_PRICES",
    "TG_ACCOUNT_HANDOFF", "TG_ACCOUNT_ZSTD"]) delete env[k];
  const r = spawnSync(process.execPath, ["--input-type=module", "-e", code], { env, encoding: "utf8" });
  assert.equal(r.status, 0, r.stderr);
  const p = JSON.parse(r.stdout.trim().split("\n").pop());
  assert.equal(p.root, "");
  assert.equal(p.config, "");
  assert.equal(p.prices, "");
  assert.equal(p.handoff, "");
  assert.equal(p.zstd, "zstd"); // 交给 PATH，不留本机绝对路径
});

test("路径解析：环境变量优先，且能只覆盖单项", () => {
  process.env.TG_ACCOUNT_ROOT = "E:/x/tg";
  process.env.TG_ACCOUNT_HANDOFF = "E:/x/h.py";
  reset();
  const p = host.accountPaths();
  assert.equal(p.root, "E:/x/tg");
  assert.equal(p.config, "E:/x/tg/config.yaml");
  assert.equal(p.handoff, "E:/x/h.py");
  setEnv("TG_ACCOUNT_ROOT", saved.TG_ACCOUNT_ROOT);
  setEnv("TG_ACCOUNT_HANDOFF", saved.TG_ACCOUNT_HANDOFF);
  reset();
});

test("能力自检：handoff 缺失时明说 missing，且与文件是否真的存在一致", () => {
  process.env.TG_ACCOUNT_HANDOFF = "Z:/definitely/not/here/handoff.py";
  reset();
  assert.equal(host.capabilities().handoff, "missing");
  setEnv("TG_ACCOUNT_HANDOFF", saved.TG_ACCOUNT_HANDOFF);
  reset();
  const p = host.accountPaths();
  assert.equal(host.capabilities().handoff, existsSync(p.handoff) ? "ready" : "missing");
});

test("能力自检：价表缺失时降级为内置价，不抛", () => {
  process.env.TG_ACCOUNT_PRICES = "Z:/definitely/not/here/prices.json";
  reset();
  const caps = host.capabilities();
  assert.equal(caps.prices, "builtin");
  setEnv("TG_ACCOUNT_PRICES", saved.TG_ACCOUNT_PRICES);
  reset();
});

test("配置缺失也不影响读价：readPriceBook 回退单套价", () => {
  process.env.TG_ACCOUNT_CONFIG = "Z:/definitely/not/here/config.yaml";
  process.env.TG_ACCOUNT_PRICES = "Z:/definitely/not/here/prices.json";
  reset();
  const book = host.readPriceBook();
  assert.ok(book && book.default, "必须有默认价行");
  assert.equal(book.schema, "legacy");
  setEnv("TG_ACCOUNT_CONFIG", saved.TG_ACCOUNT_CONFIG);
  setEnv("TG_ACCOUNT_PRICES", saved.TG_ACCOUNT_PRICES);
  reset();
});
