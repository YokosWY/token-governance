// dsh-loop-guard —— 循环守卫（本地插件 · 零依赖）
//
// 目标：**中断思路，不动会话。**
//
// v0.1：同一「工具 + 参数」连续重复时，往模型上下文追加一条中文提醒：
// 指出第几次、给出该工具专属的「换哪条路」；到 askUserAt 次才允许问用户一次。
//
// v0.2：加「思考级空转」的代理检测（默认关）。模型看不到自己的 reasoning，
// 但能看到它调了什么、结果成没成、有没有落物。于是用三个机械信号兜住「雷霆大思考」：
//   read-storm    连续读/检索到阈值，期间 0 次 write/edit
//   error-streak  连续失败到阈值（先分类错误，再换通道）
//   budget        单轮调用数到阈值且 0 次 write/edit（三行止损收口）
// 三条规则都只提醒、不阻断；每轮每规则最多一次，且两次提醒之间至少隔 cooldownCalls 次调用。
//
// 关键实现细节（2026-10-22 实测）：
//   · 参数在 `exec.arguments`（不是 exec.args，也不是 exec.input）；
//   · 只有 `tools/post-execute` 阶段能拿到解析后的参数；`tools/pre-execute` / `ctx.tools.guard`
//     拿不到，硬拦会误伤（v1 已踩过）；
//   · 提醒必须是完整消息对象：{ id: uuid, role: 'user', content: [...], source: { kind, form: 'notice', summary } }，
//     宿主 createUserMessage = { ...input, role: 'user', id: randomUUID() }（参考 @deepseek-ai/dsh-repeat-tool-reminder）；
//   · 新用户消息到达时清空链与计数（`agent/pre-step`）。
//
// 只依赖 node 内置模块；任何异常 fail-open（不提醒）。
import { appendFileSync, mkdirSync } from 'node:fs'
import { dirname, join } from 'node:path'
import { homedir } from 'node:os'
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
  // 命中计数落盘：空 = <DSH_HOME 或 ~/.dsh>/storages/loop-guard/events.jsonl；null/false = 关闭
  logFile: '',
  // 思考级空转（v0.2）：默认开、阈值保守；关法 thinking.enabled: false。
  // 代码默认与随包 cordis.patch.yml 一致：profile 里没写 thinking 的（如官方那套）也走这一份。
  thinking: {
    enabled: true,
    readStormCalls: 12,     // 连续读/检索到这个次数仍无落物 → 提醒
    readStormMinReads: 8,   // 且本轮读/检索总数至少这么多次（防单次长读误判）
    errorStreak: 3,         // 连续失败到这个次数 → 提醒
    turnCallBudget: 60,     // 单轮工具调用到这个次数且 0 落物 → 提醒
    cooldownCalls: 8,       // 两条思考提醒之间至少隔几次调用
  },
}

const SOURCE_KIND = 'loop-guard'

// 「落物」= 真的写下去：文件或补丁。todo_write 之类不算，否则会被用来刷掉提醒。
const ARTIFACT_TOOLS = new Set([
  'write', 'edit', 'multi_edit', 'apply_patch', 'ops_write', 'ops_edit', 'create_file',
])

const READ_TOOLS = new Set(['read', 'ops_inspect_local_file', 'read_image'])
const SHELL_TOOLS = new Set(['bash', 'pwsh', 'ops_bash', 'run_code'])

/** 读/检索类工具：前缀白名单 + MCP 里带 search/read/fetch 的。宁可漏报，不误伤。 */
function isReadLike(tool) {
  if (/^(read|grep|glob|ops_read|ops_inspect|web_|download_|search_|fetch|kb_)/i.test(tool)) return true
  return /^mcp__.*(search|read|fetch)/i.test(tool)
}

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
    if (!limit) return `(${text.length} chars)`
    return text.length <= limit ? text : text.slice(0, limit) + `… (+${text.length - limit} more chars)`
  } catch {
    return '(unavailable)'
  }
}

/** 命中计数落盘：只记工具名、第几次、参数哈希与长度，不写参数内容。 */
function logPathOf(cfg) {
  if (cfg.logFile === false || cfg.logFile === null) return null
  if (typeof cfg.logFile === 'string' && cfg.logFile) return cfg.logFile
  const home = process.env.DSH_HOME || join(homedir(), '.dsh')
  return join(home, 'storages', 'loop-guard', 'events.jsonl')
}

function record(cfg, entry) {
  try {
    const file = logPathOf(cfg)
    if (!file) return
    mkdirSync(dirname(file), { recursive: true })
    appendFileSync(file, JSON.stringify({ ts: new Date().toISOString(), ...entry }) + '\n', 'utf8')
  } catch {
    /* 统计失败不影响会话 */
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

const THINKING_TEXT = {
  'read-storm': state => ({
    head: `【循环守卫】已连续 ${state.sinceArtifact} 次读取/检索、0 次落物；再读下去不会有新结论。`,
    advice: '够了就动手：写文件、跑一条能定论的命令，或直接回答；也可以直说「我在查什么、还差哪一条」。',
  }),
  'error-streak': state => ({
    head: `【循环守卫】连续 ${state.errStreak} 次调用失败。`,
    advice: '先分类错误（协议错误不重试逻辑：file has not been read / file changed since it was read / unknown tool），再换通道；不要换参数硬试。',
  }),
  budget: state => ({
    head: `【循环守卫】本轮已 ${state.calls} 次工具调用、0 次 write/edit。`,
    advice: '按三行止损收口：假设 / 已排除 / 唯一阻塞点；别把同一个思路再加长。',
  }),
}

function thinkingNotice(kind, state) {
  const item = THINKING_TEXT[kind](state)
  const tail = '\n三行止损：假设=…；已排除=…；唯一阻塞点=…。'
  return {
    id: randomUUID(),
    role: 'user',
    content: [{ type: 'text', text: `${item.head}\n${item.advice}${tail}` }],
    source: { kind: SOURCE_KIND, form: 'notice', summary: `${kind} #${state.calls}` },
  }
}

function mergeConfig(config) {
  const cfg = { ...DEFAULTS, ...(config || {}) }
  const thinking = config && typeof config.thinking === 'object' && config.thinking ? config.thinking : {}
  cfg.thinking = { ...DEFAULTS.thinking, ...thinking }
  return cfg
}

function newTurn() {
  return { calls: 0, reads: 0, artifacts: 0, errStreak: 0, sinceArtifact: 0, fired: new Set(), lastNoticeAt: -999 }
}

/** 思考级规则：命中返回 kind，否则 null。调用方负责记录与提醒。 */
function evaluateThinking(state, tool, result, cfg) {
  const t = cfg.thinking
  if (!t.enabled) return null
  state.calls += 1

  if (ARTIFACT_TOOLS.has(tool)) {
    state.artifacts += 1
    state.sinceArtifact = 0
  } else if (isReadLike(tool)) {
    state.reads += 1
    state.sinceArtifact += 1
  }

  state.errStreak = result && result.isError ? state.errStreak + 1 : 0

  let kind = null
  if (state.errStreak >= t.errorStreak) kind = 'error-streak'
  else if (state.sinceArtifact >= t.readStormCalls && state.reads >= t.readStormMinReads) kind = 'read-storm'
  else if (state.calls >= t.turnCallBudget && state.artifacts === 0) kind = 'budget'
  if (!kind || state.fired.has(kind)) return null

  const cooldown = Math.max(1, Number(t.cooldownCalls) || 1)
  if (state.calls - state.lastNoticeAt < cooldown) return null

  state.fired.add(kind)
  state.lastNoticeAt = state.calls
  return kind
}

export function apply(ctx, config = {}) {
  const cfg = mergeConfig(config)
  if (!cfg.enabled || !ctx || typeof ctx.on !== 'function') return

  const thresholds = new Set((Array.isArray(cfg.thresholds) ? cfg.thresholds : DEFAULTS.thresholds).filter(n => Number.isInteger(n) && n >= 2))
  if (!thresholds.size) return
  const exclude = new Set(Array.isArray(cfg.exclude) ? cfg.exclude : DEFAULTS.exclude)
  const include = new Set(Array.isArray(cfg.include) ? cfg.include : [])

  const chains = new WeakMap()
  const turns = new WeakMap()

  const observe = (exec, result) => {
    try {
      const agent = exec?.agent
      if (!agent) return undefined
      const tool = String(exec?.name ?? '')
      if (!tool) return undefined
      if (include.size && !include.has(tool)) return undefined
      if (exclude.has(tool)) return undefined

      let turn = turns.get(agent)
      if (!turn) {
        turn = newTurn()
        turns.set(agent, turn)
      }
      const thinkingKind = evaluateThinking(turn, tool, result, cfg)
      if (thinkingKind) {
        record(cfg, {
          kind: thinkingKind, tool: '(thinking)', calls: turn.calls, reads: turn.reads,
          artifacts: turn.artifacts, sinceArtifact: turn.sinceArtifact,
        })
        return thinkingNotice(thinkingKind, turn)
      }

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
      record(cfg, { kind: 'loop', tool, count, hash, argsLen: stable(args).length })
      return notice(exec, count, args, cfg)
    } catch {
      return undefined
    }
  }

  ctx.on('tools/post-execute', async (exec, result, next) => {
    let reminder = null
    try {
      reminder = observe(exec, result)
    } catch {
      reminder = null
    }
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

  // 新用户消息 = 新指令，链与计数清零（否则新任务里的正常重复会被当成循环）
  ctx.on('agent/pre-step', ({ agent, messages }, next) => {
    try {
      if (agent && Array.isArray(messages) && messages.some(message => message?.source?.kind === 'user')) {
        chains.delete(agent)
        turns.delete(agent)
      }
    } catch {
      /* 忽略 */
    }
    return next()
  })
}
