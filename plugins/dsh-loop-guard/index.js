// dsh-loop-guard —— 循环守卫（本地插件 · 零依赖）
//
// 目标：**中断思路，不动会话。** 同一「工具 + 参数」连续重复时，往模型上下文追加一条中文提醒：
// 指出第几次、给出该工具专属的「换哪条路」；到 askUserAt 次才允许问用户一次。
// 不阻断调用、不弹窗、不结束会话；提醒 append-only，不破 KV 缓存。
//
// 关键实现细节（2026-10-22 实测）：
//   · 参数在 `exec.arguments`（不是 exec.args，也不是 exec.input）；
//   · 只有 `tools/post-execute` 阶段能拿到解析后的参数；`tools/pre-execute` / `ctx.tools.guard`
//     拿不到，硬拦会误伤（v1 已踩过）；
//   · 提醒必须是完整消息对象：{ id: uuid, role: 'user', content: [...], source: { kind, form: 'notice', summary } }，
//     宿主 createUserMessage = { ...input, role: 'user', id: randomUUID() }（参考 @deepseek-ai/dsh-repeat-tool-reminder）；
//   · 新用户消息到达时清空链（`agent/pre-step`）。
//
// 只依赖 node 内置模块；任何异常 fail-open（不提醒）。
import { createHash, randomUUID } from 'node:crypto'

export const name = 'dsh-loop-guard'
export const inject = ['tools']

const DEFAULTS = {
  enabled: true,
  thresholds: [2, 4, 6],
  exclude: ['todo_write', 'job_output', 'job_list', 'job_kill', 'ask_user_question', 'send_message', 'present'],
  include: [],
  argumentsPreviewChars: 160,
  askUserAt: 6,
}

const SOURCE_KIND = 'loop-guard'

const READ_TOOLS = new Set(['read', 'ops_inspect_local_file', 'read_image'])
const SHELL_TOOLS = new Set(['bash', 'pwsh', 'ops_bash', 'run_code'])

function stable(value, depth = 0) {
  if (value === null || typeof value !== 'object') return JSON.stringify(value) ?? 'undefined'
  if (depth > 3) return '"[deep]"'
  if (Array.isArray(value)) return '[' + value.map(item => stable(item, depth + 1)).join(',') + ']'
  const keys = Object.keys(value).sort()
  return '{' + keys.map(key => JSON.stringify(key) + ':' + stable(value[key], depth + 1)).join(',') + '}'
}

/** 参数在 exec.arguments；兼容字符串形式的 JSON */
function argsOf(exec) {
  let candidate = exec?.arguments ?? exec?.args ?? exec?.input ?? exec?.parameters ?? exec?.call?.args
  if (typeof candidate === 'string') {
    try {
      candidate = JSON.parse(candidate)
    } catch {
      return null
    }
  }
  if (candidate && typeof candidate === 'object' && Object.keys(candidate).length > 0) return candidate
  return null
}

function hashOf(tool, args) {
  return createHash('sha1').update(tool + '|' + stable(args)).digest('hex').slice(0, 16)
}

function suggestionsFor(tool) {
  if (READ_TOOLS.has(tool)) return '先用 grep 关键词定位行号，再 read 带 offset/limit 只读命中段；别整读第二遍'
  if (SHELL_TOOLS.has(tool)) return '换解释器（bash ↔ pwsh）或换通道（内置 shell ↔ ops_*），只改失败原因，不换参数硬试'
  return '换工具、换输入来源，或把这一步拆成最小可验证动作'
}

function preview(args, limit) {
  try {
    const text = stable(args)
    return text.length <= limit ? text : text.slice(0, limit) + `… (+${text.length - limit} more chars)`
  } catch {
    return '(unavailable)'
  }
}

function notice(exec, count, args, cfg) {
  const tool = String(exec?.name ?? 'tool')
  const head = `【循环守卫】${tool} 以完全相同参数连续第 ${count} 次调用；原地重复不会有新信息。`
  const advice = `换路：${suggestionsFor(tool)}。`
  const ask = count >= cfg.askUserAt
    ? '\n如果换两条路都不通，用 ask_user_question 问用户一次（继续/换方法/只停这个子任务），不要停整个会话。'
    : ''
  const detail = `\n参数：${preview(args, cfg.argumentsPreviewChars)}`
  return {
    id: randomUUID(),
    role: 'user',
    content: [{ type: 'text', text: `${head}${advice}${detail}${ask}` }],
    source: { kind: SOURCE_KIND, form: 'notice', summary: `${tool} × ${count}` },
  }
}

export function apply(ctx, config = {}) {
  const cfg = { ...DEFAULTS, ...(config || {}) }
  if (!cfg.enabled || !ctx || typeof ctx.on !== 'function') return

  const thresholds = new Set((Array.isArray(cfg.thresholds) ? cfg.thresholds : DEFAULTS.thresholds).filter(n => Number.isInteger(n) && n >= 2))
  if (!thresholds.size) return
  const exclude = new Set(Array.isArray(cfg.exclude) ? cfg.exclude : DEFAULTS.exclude)
  const include = new Set(Array.isArray(cfg.include) ? cfg.include : [])

  const chains = new WeakMap()

  const observe = exec => {
    try {
      const agent = exec?.agent
      if (!agent) return undefined
      const tool = String(exec?.name ?? '')
      if (!tool) return undefined
      if (include.size && !include.has(tool)) return undefined
      if (exclude.has(tool)) return undefined
      const args = argsOf(exec)
      if (!args) return undefined
      const hash = hashOf(tool, args)
      let chain = chains.get(agent)
      if (!chain || chain.hash !== hash) {
        chain = { hash, count: 0, fired: new Set() }
        chains.set(agent, chain)
      }
      chain.count += 1
      const count = chain.count
      if (!thresholds.has(count) || chain.fired.has(count)) return undefined
      chain.fired.add(count)
      return notice(exec, count, args, cfg)
    } catch {
      return undefined
    }
  }

  ctx.on('tools/post-execute', async (exec, _result, next) => {
    const reminder = observe(exec)
    const downstream = await next()
    try {
      if (!reminder) return downstream
      const base = downstream ?? {}
      if (base.kind === 'block') return base
      const existing = Array.isArray(base.additionalContexts) ? base.additionalContexts : []
      return { ...base, additionalContexts: [reminder, ...existing] }
    } catch {
      return downstream
    }
  })

  // 新用户消息 = 新指令，链清零（否则新任务里的正常重复会被当成循环）
  ctx.on('agent/pre-step', ({ agent, messages }, next) => {
    try {
      if (agent && Array.isArray(messages) && messages.some(message => message?.source?.kind === 'user')) chains.delete(agent)
    } catch {
      /* 忽略 */
    }
    return next()
  })
}
