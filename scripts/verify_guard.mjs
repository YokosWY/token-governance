#!/usr/bin/env node
// 循环守卫 · 离线烟雾测试（不启动 DSH，不联网）
// 用法：node scripts/verify_guard.mjs
import assert from 'node:assert/strict'
import { createHash } from 'node:crypto'
import { existsSync, readFileSync } from 'node:fs'
import { fileURLToPath } from 'node:url'
import path from 'node:path'

const here = path.dirname(fileURLToPath(import.meta.url))
const repo = path.dirname(here)
const vendored = path.join(repo, 'plugins', 'dsh-loop-guard', 'index.js')
const upstream = 'D:/AI/DSH-NEXT/local-plugins/dsh-loop-guard/index.js'

const { apply } = await import(new URL('../plugins/dsh-loop-guard/index.js', import.meta.url))

const handlers = new Map()
const ctx = { on: (event, handler) => handlers.set(event, handler) }
apply(ctx, { thresholds: [2, 4, 6], askUserAt: 6 })

const post = handlers.get('tools/post-execute')
const pre = handlers.get('agent/pre-step')
assert.ok(typeof post === 'function', '未注册 tools/post-execute')
assert.ok(typeof pre === 'function', '未注册 agent/pre-step')

const agent = { id: 'smoke-agent' }
const next = async () => ({})
let lastNotice = null

async function fire(name, args) {
  const out = await post({ name, arguments: args, agent }, null, next)
  const contexts = out?.additionalContexts ?? []
  lastNotice = contexts[0] ?? lastNotice
  return contexts.length
}

const results = []
function check(label, actual, expected) {
  assert.equal(actual, expected, `${label}: 期望 ${expected}，实际 ${actual}`)
  results.push(`${label} …… OK`)
}

// 1) 同一工具 + 相同参数：第 2 次提醒
check('第 1 次不提醒', await fire('ops_bash', { command: 'ls' }), 0)
check('第 2 次提醒', await fire('ops_bash', { command: 'ls' }), 1)
check('第 3 次不提醒（阈值 2/4/6）', await fire('ops_bash', { command: 'ls' }), 0)
check('第 4 次提醒', await fire('ops_bash', { command: 'ls' }), 1)
check('第 5 次不提醒', await fire('ops_bash', { command: 'ls' }), 0)
check('第 6 次提醒', await fire('ops_bash', { command: 'ls' }), 1)

// 2) 换参数 = 新链（严格哈希，不做相似度）
check('参数不同 → 不提醒', await fire('ops_bash', { command: 'ls -la' }), 0)
check('新参数第 2 次 → 提醒', await fire('ops_bash', { command: 'ls -la' }), 1)

// 3) 排除工具不计数
check('exclude 工具不提醒', await fire('todo_write', { todos: [] }), 0)
check('exclude 工具仍不提醒', await fire('todo_write', { todos: [] }), 0)

// 4) 新用户消息清链
await pre({ agent, messages: [{ role: 'user', source: { kind: 'user' } }] }, next)
check('清链后同参数重新计数', await fire('ops_bash', { command: 'ls -la' }), 0)

// 5) 提醒消息形状
assert.ok(lastNotice, '没有捕获到提醒消息')
assert.equal(lastNotice.role, 'user')
assert.equal(lastNotice.source?.kind, 'loop-guard')
assert.equal(lastNotice.source?.form, 'notice')
assert.match(lastNotice.content?.[0]?.text ?? '', /【循环守卫】/)
results.push('提醒消息形状 …… OK')

// 6) 与上游代码是否一致（有上游才算）
if (existsSync(upstream)) {
  const sha = f => createHash('sha256').update(readFileSync(f)).digest('hex').slice(0, 12)
  const a = sha(vendored)
  const b = sha(upstream)
  results.push(a === b ? `与上游一致（${a}）…… OK` : `与上游不一致：本地 ${a} / 上游 ${b}（若有意改动请改上游并同步）`)
} else {
  results.push('上游路径不存在，跳过一致性检查')
}

for (const line of results) console.log('  ' + line)
console.log(`\n通过 ${results.length} 项检查`)
