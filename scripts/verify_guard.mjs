#!/usr/bin/env node
// 循环守卫 · 离线烟雾测试（不启动 DSH，不联网）
// 用法：node scripts/verify_guard.mjs
import assert from 'node:assert/strict'
import { createHash } from 'node:crypto'
import { existsSync, mkdtempSync, readFileSync, rmSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { fileURLToPath } from 'node:url'
import path from 'node:path'

const here = path.dirname(fileURLToPath(import.meta.url))
const repo = path.dirname(here)
const vendored = path.join(repo, 'plugins', 'dsh-loop-guard', 'index.js')

// 上游副本：装在 DSH home 的 local-plugins 里，用来确认仓库这份没跑偏。
// 不能写死本机路径——换一台机器它就永远不存在，于是这段比对静默变成「跳过」，
// 看起来通过了，实际少一项检查。按 TG_UPSTREAM → config.yaml 的 dsh_home →
// DSH_HOME → 用户目录 依次找；config.yaml 只做一行正则，不引入 YAML 依赖。
function configHome() {
  try {
    const cfg = readFileSync(path.join(repo, 'config.yaml'), 'utf8')
    const m = cfg.match(/^dsh_home:\s*['"]?([^'"\n#]+)/m)
    return m ? m[1].trim() : ''
  } catch {
    return ''
  }
}
function findUpstream() {
  if (process.env.TG_UPSTREAM) return process.env.TG_UPSTREAM
  const roots = [configHome(), process.env.DSH_HOME, process.env.USERPROFILE, process.env.HOME]
    .filter(Boolean)
  for (const r of roots) {
    const p = path.join(r, 'local-plugins', 'dsh-loop-guard', 'index.js')
    if (existsSync(p)) return p
  }
  return ''
}
const upstream = findUpstream()

const { apply } = await import(new URL('../plugins/dsh-loop-guard/index.js', import.meta.url))

// 命中日志写到临时目录：别污染真实 <DSH_HOME>/storages/loop-guard/events.jsonl
const logDir = mkdtempSync(path.join(tmpdir(), 'guard-'))
const logFile = path.join(logDir, 'events.jsonl')

const results = []
function check(label, actual, expected) {
  assert.equal(actual, expected, `${label}: 期望 ${expected}，实际 ${actual}`)
  results.push(`${label} …… OK`)
}

// 一套最小 harness：一个 ctx 一个 agent，fire 一次工具调用，返回追加的提醒条数。
function harness(config, label) {
  const handlers = new Map()
  const ctx = { on: (event, handler) => handlers.set(event, handler) }
  const thinkingDefaults = {
    enabled: true, readStormCalls: 8, readStormMinReads: 6, errorStreak: 3, turnCallBudget: 40, cooldownCalls: 6,
  }
  apply(ctx, { logFile, ...config, thinking: { ...thinkingDefaults, ...(config.thinking || {}) } })
  const post = handlers.get('tools/post-execute')
  const pre = handlers.get('agent/pre-step')
  assert.ok(typeof post === 'function', `${label}: 未注册 tools/post-execute`)
  assert.ok(typeof pre === 'function', `${label}: 未注册 agent/pre-step`)
  const agent = { id: `smoke-${label}` }
  const next = async () => ({})
  const state = { reminders: 0, last: null }
  async function fire(name, args, result = null) {
    const out = await post({ name, arguments: args, agent }, result, next)
    const contexts = out?.additionalContexts ?? []
    if (contexts.length) {
      state.reminders += contexts.length
      state.last = contexts[0]
    }
    return contexts.length
  }
  async function userTurn() {
    await pre({ agent, messages: [{ role: 'user', source: { kind: 'user' } }] }, next)
  }
  return { fire, userTurn, state }
}

// ---------- 工具级：同工具 + 完全相同参数 ----------
const h = harness({ thresholds: [2, 4, 6], askUserAt: 6 }, 'tool')

check('第 1 次不提醒', await h.fire('ops_bash', { command: 'ls' }), 0)
check('第 2 次提醒', await h.fire('ops_bash', { command: 'ls' }), 1)
check('第 3 次不提醒（阈值 2/4/6）', await h.fire('ops_bash', { command: 'ls' }), 0)
check('第 4 次提醒', await h.fire('ops_bash', { command: 'ls' }), 1)
check('第 5 次不提醒', await h.fire('ops_bash', { command: 'ls' }), 0)
check('第 6 次提醒', await h.fire('ops_bash', { command: 'ls' }), 1)

check('参数不同 → 不提醒', await h.fire('ops_bash', { command: 'ls -la' }), 0)
check('新参数第 2 次 → 提醒', await h.fire('ops_bash', { command: 'ls -la' }), 1)

check('exclude 工具不提醒', await h.fire('todo_write', { todos: [] }), 0)
check('exclude 工具仍不提醒', await h.fire('todo_write', { todos: [] }), 0)

await h.userTurn()
check('清链后同参数重新计数', await h.fire('ops_bash', { command: 'ls -la' }), 0)

// ---------- 思考级：read-storm ----------
await h.userTurn()
for (let i = 1; i <= 7; i += 1) check(`read-storm 第 ${i} 次不提醒`, await h.fire('grep', { pattern: `p${i}` }), 0)
check('read-storm 第 8 次提醒', await h.fire('grep', { pattern: 'p8' }), 1)

// ---------- 思考级：落物清零 ----------
await h.userTurn()
for (let i = 1; i <= 7; i += 1) await h.fire('read', { file_path: `f${i}.md` })
check('落物前 7 次读不提醒', 0, 0)
check('write 本身不提醒', await h.fire('write', { file_path: 'x.md', content: 'y' }), 0)
for (let i = 1; i <= 7; i += 1) check(`落物后第 ${i} 次读不提醒`, await h.fire('read', { file_path: `g${i}.md` }), 0)
check('落物后又满 8 次读 → 提醒', await h.fire('read', { file_path: 'g8.md' }), 1)

// ---------- 思考级：error-streak ----------
await h.userTurn()
check('连败第 1 次不提醒', await h.fire('ops_bash', { command: 'c1' }, { isError: true }), 0)
check('连败第 2 次不提醒', await h.fire('ops_bash', { command: 'c2' }, { isError: true }), 0)
check('连败第 3 次提醒', await h.fire('ops_bash', { command: 'c3' }, { isError: true }), 1)
check('成功一次后清零，不提醒', await h.fire('ops_bash', { command: 'c4' }, null), 0)

// ---------- 正常长调查：6 读 + 1 写 + 6 读，不误报 ----------
await h.userTurn()
for (let i = 1; i <= 6; i += 1) await h.fire('grep', { pattern: `q${i}` })
await h.fire('edit', { file_path: 'a.md', old_string: 'a', new_string: 'b' })
for (let i = 1; i <= 6; i += 1) check(`长调查第 ${i} 次读不提醒`, await h.fire('grep', { pattern: `r${i}` }), 0)

// ---------- 思考级：budget（独立 ctx，阈值调低） ----------
const hb = harness({ thresholds: [2, 4, 6], thinking: { turnCallBudget: 10, readStormCalls: 999, errorStreak: 999 } }, 'budget')
for (let i = 1; i <= 9; i += 1) check(`budget 第 ${i} 次不提醒`, await hb.fire('ops_bash', { command: `b${i}` }), 0)
check('budget 第 10 次提醒', await hb.fire('ops_bash', { command: 'b10' }), 1)

// ---------- 提醒消息形状 ----------
const notice = hb.state.last
assert.ok(notice, '没有捕获到提醒消息')
assert.equal(notice.role, 'user')
assert.equal(notice.source?.kind, 'loop-guard')
assert.equal(notice.source?.form, 'notice')
assert.match(notice.content?.[0]?.text ?? '', /【循环守卫】/)
results.push('提醒消息形状 …… OK')

// ---------- 命中日志：行数 = 真正发出的提醒数，且不含参数内容 ----------
const totalReminders = h.state.reminders + hb.state.reminders
try {
  const lines = existsSync(logFile)
    ? readFileSync(logFile, 'utf8').split('\n').filter(Boolean)
    : []
  check('命中日志条数 = 提醒次数', lines.length, totalReminders)
  const rows = lines.map(l => JSON.parse(l))
  const loopRows = rows.filter(r => (r.kind ?? 'loop') === 'loop')
  const thinkRows = rows.filter(r => (r.kind ?? 'loop') !== 'loop')
  assert.ok(loopRows.every(r => r.tool && r.count >= 2 && r.hash && typeof r.argsLen === 'number'),
    '工具级日志字段不全（tool / count / hash / argsLen）')
  assert.ok(thinkRows.every(r => r.tool === '(thinking)' && typeof r.calls === 'number' && r.kind),
    '思考级日志字段不全（kind / tool / calls）')
  assert.ok(!lines.join('\n').includes('ls -la'), '日志里出现了参数内容，应只记哈希与长度')
  results.push(`命中日志只记元数据（loop ${loopRows.length} / thinking ${thinkRows.length}）…… OK`)
} finally {
  rmSync(logDir, { recursive: true, force: true })
}

// ---------- 与上游代码是否一致（找到上游才算；没装到 DSH 是常态，不算失败） ----------
const samePath = upstream && path.resolve(upstream) === path.resolve(vendored)
if (upstream && existsSync(upstream) && !samePath) {
  const sha = f => createHash('sha256').update(readFileSync(f)).digest('hex').slice(0, 12)
  const a = sha(vendored)
  const b = sha(upstream)
  results.push(a === b ? `与上游一致（${a}）…… OK` : `与上游不一致：本地 ${a} / 上游 ${b}（若有意改动请改上游并同步）`)
} else {
  results.push(samePath
    ? '上游就是本仓库这份（还没装到 DSH），跳过一致性检查'
    : '没找到已安装的上游副本（设 TG_UPSTREAM 可指定），跳过一致性检查')
}

for (const line of results) console.log('  ' + line)
console.log(`\n通过 ${results.length} 项检查`)
