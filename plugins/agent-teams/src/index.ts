/**
 * AgentTeams for DeepSeek Harness.
 *
 * A host-plane plugin that registers the `agent_teams_*` tools and one usage
 * section into the global system prompt. After installation any session can
 * run multi-agent teamwork through natural language (e.g. "use AgentTeams to research X"):
 * the model creates a team (it becomes the captain), spawns members as
 * durable continuable subagents, breaks the goal into tasks with
 * dependencies, wakes members with messages, relays reports, and collects
 * results.
 *
 * Installation (bundle): `dsh plugin --profile <name> add @nanmicoder/dsh-agent-teams`
 * (or a local path). The bundle patch mounts this plugin row into the host
 * composition; the tools register into the shared `tools` registry and the
 * usage section into the global system prompt, so the plugin needs no realm.
 *
 * @module dsh-agent-teams
 */

import type { Context } from '@deepseek-ai/cordis'
import z from '@deepseek-ai/schemastery'
// Declaration merge only: makes ctx.llm, ctx.subagents and ctx.systemPrompt visible.
import type {} from '@deepseek-ai/dsh-llm'
import type {} from '@deepseek-ai/dsh-subagent'
import type {} from '@deepseek-ai/dsh-system-prompt'
import type {} from '@deepseek-ai/dsh-agent'
import type { WorkspaceRegistry } from '@deepseek-ai/dsh-workspace'
import type { IncomingMessage, ServerResponse } from 'node:http'
import type { SessionId } from '@deepseek-ai/dsh-session/types'
import { registerAgentTeamsTools, steerCaptainReport, type ToolsConfig } from './tools.ts'
import { readFile } from 'node:fs/promises'
import { join } from 'node:path'
import { fileURLToPath } from 'node:url'
import { collectArchivedTeamsActivity, collectTeamsActivity } from './snapshot.ts'
import { archiveTeamDir, readTeam, sanitizeKey, withTeamLock, writeTeam } from './state.ts'
import { interruptMember } from './members.ts'

/**
 * Structural slice of the web server service, compatible with both the
 * published `dsh-host-webserver@0.0.1-rc.1` (`ctx.httpServer` /
 * `HttpServerService`) and the renamed `webServer` / `WebServer` in later
 * builds: the beta transition renames the service without changing the route
 * registration shape.
 */
interface WebRouteHost {
  register(route: {
    kind: 'exact' | 'prefix'
    path: string
    handler: (req: IncomingMessage, res: ServerResponse) => void | Promise<void>
  }): () => void
}

/** Web-server service key candidates, newest first. */
const WEB_SERVER_KEYS = ['webServer', 'httpServer'] as const
/** Workspace registry service key candidates, newest first. */
const WORKSPACE_KEYS = ['workspaceRegistry', 'workspace'] as const

export const name = 'agent-teams'
export const inject = ['tools', 'llm', 'subagents', 'systemPrompt', 'agents']

/** Plugin configuration. */
export interface Config {
  /**
   * State directory name under the captain's workspace; team state lives at
   * `<workspace>/<stateDir>/<teamId>/` (default `.agent-teams`).
   */
  stateDir?: string
  /** `ctx.subagents` provider used to spawn members; must support continuable children and personas (default `spawn`). */
  memberProvider?: string
  /** Optional model override applied to every member. */
  memberModel?: string
  /** Member delegation depth cap (default `1`; `0` forbids delegation entirely). */
  memberMaxDepth?: number
  /** Team size cap in members (default `8`). */
  maxMembers?: number
  /** Prompt-section order for the usage policy (default `117`, after delegation policy). */
  promptSectionOrder?: number
}

export const Config: z<Config> = z.object({
  stateDir: z.string().default('.agent-teams'),
  memberProvider: z.string().default('spawn'),
  memberModel: z.string(),
  memberMaxDepth: z.natural().default(1),
  maxMembers: z.natural().min(1).default(8),
  promptSectionOrder: z.natural().default(117),
})

/** The model-facing usage policy: when and how to drive AgentTeams. */
function usageSectionText(toolNames: string): string {
  return `When the user asks to run something with AgentTeams (e.g. "use AgentTeams to do X"), you are the captain of a multi-agent team. Follow this protocol:
1. Clarify the demand with the requester first: ask about purpose, constraints, success criteria, and role assignments. When you understand the global requirement, submit the demand brief with agent_teams_submit_brief. Then ask the requester to confirm it in the activity panel — dispatch tools stay blocked until the brief is confirmed. Decide serial vs parallel from how clearly the demand is understood and split: when the captain understands it deeply and the split is clean (disjoint files or regions with a merge plan), run parallel; otherwise run serial — waiting members show the queued (orange) wave state. Use the dependency triage to check clarity: output dependency (B consumes A's output), resource conflict (same file, same quota), or knowledge dependency (B needs A's decisions) — any unresolved one means serial.
2. Call agent_teams_create with a team name and the goal as description. You become the captain and may lead one team at a time.
3. Call agent_teams_add_member once per role the goal needs (researcher, engineer, reviewer, ...). Members are durable subagents: they wait for your messages, then work a full turn. By default each member snapshots your current provider, model, and reasoning effort. Never ask the user to choose these per member; only pass provider/model when the user explicitly requests a different route for that role.
4. Break the goal into tasks with agent_teams_create_task; wire dependencies between tasks (a task is claimable only when its dependencies are completed). Assign each task to a member when it fits a role. Do small planning work yourself (shared types, contracts, conventions) instead of turning it into a task. When members work in parallel on one repository, split by file ownership so their edits never overlap, and forbid git commits until you review and commit once.
5. Dispatch work: claim each assigned task (agent_teams_claim_task with assignee) and wake the member with agent_teams_send_message naming its task id and instructions. One task per message keeps turns focused. For each task, the captain first judges its difficulty and whether calling a skill is warranted: trivial tasks proceed directly, no ceremony; when the task is non-trivial or clearly matches a skill's domain, consult the local skill registry configured for your workspace (the single source — grouped index plus detailed commands and pitfalls — its path is environment-specific) and name the exact skill(s) in the member's instructions, e.g. "First load the <skill-name> skill, then follow its process". Skill routing is the captain's duty, never the member's own discovery — do not leave it to the member's discretion. When the requester adds a new requirement mid-flight, reassess the fit against the current roster: if no existing member matches the requirement's role, do not stretch a misfit member — remove the misfit and add a fresh member with the matching role (agent_teams_add_member / agent_teams_remove_member), and give the new member full context in its first message (it has no team history). Balance the load across members — when several members fit a requirement, prefer the idle or least-loaded one; never chain consecutive requirements onto a single member while others sit idle. Re-plan task details across members — split finely so no two members touch the same files; when two requirements share one file, either serialize them with a dependency or split the work first (separate files, or disjoint regions with an explicit merge plan) — clear planning can turn a shared file into parallel work.
6. Review each task the moment its member reports it — never wait for the whole team to finish before accepting delivered work; late members keep working while you review early deliveries. Poll agent_teams_status for the rest; relay member-to-member messages (agent_teams_send_message with from=<sender>) and collect completed tasks' outputs. If a member reports a blocker, reassign the task or adjust the plan. When the requester adds or changes a requirement mid-flight, create a task for it — task state drives the activity panel, and message-only work leaks out of the state machine (the panel would show everyone resting while someone still works).
7. Present the team's results to the user, then agent_teams_delete the team unless the user wants to keep working with it.

Tools: ${toolNames}`
}

export function apply(ctx: Context, config: Config): void {
  const resolved: ToolsConfig = {
    stateDir: config.stateDir ?? '.agent-teams',
    memberProvider: config.memberProvider ?? 'spawn',
    memberModel: config.memberModel,
    memberMaxDepth: config.memberMaxDepth ?? 1,
    maxMembers: config.maxMembers ?? 8,
  }

  // Provider registration is a sibling plugin's effect (`subagent-spawn` /
  // `subagent-fork` rows), which can land after this mount under the Loader's
  // concurrent activation — so capability validation happens at the first
  // member spawn (`spawnMember`), the earliest point the provider list is
  // settled, rather than here.

  const toolNames = [
    'agent_teams_submit_brief',
    'agent_teams_create',
    'agent_teams_add_member',
    'agent_teams_remove_member',
    'agent_teams_create_task',
    'agent_teams_claim_task',
    'agent_teams_update_task',
    'agent_teams_send_message',
    'agent_teams_status',
    'agent_teams_delete',
  ].join(', ')
  ctx.systemPrompt.section({
    name: 'agent-teams:usage',
    order: config.promptSectionOrder ?? 117,
    text: usageSectionText(toolNames),
  })

  registerAgentTeamsTools(ctx, resolved)

  // The activity panel data/artwork routes need the Web server and the
  // workspace registry, which headless profiles do not mount; under
  // concurrent activation they may also bind after this plugin. Register the
  // routes lazily: try now, then on each service binding event. In a webless
  // profile the plugin stays tool-only and never blocks boot.
  let webRegistered = false
  const registerWebSurface = (): void => {
    if (webRegistered) return
    const webServer = (ctx.get(WEB_SERVER_KEYS[0]) ?? ctx.get(WEB_SERVER_KEYS[1])) as WebRouteHost | undefined
    const workspaceRegistry = (ctx.get(WORKSPACE_KEYS[0]) ?? ctx.get(WORKSPACE_KEYS[1])) as WorkspaceRegistry | undefined
    if (webServer === undefined || workspaceRegistry === undefined) return
    webRegistered = true

    // Activity panel data route: the browser floater polls this for team
    // snapshots (disk truth + live subagent activity). Mirrors the Claude
    // Code desktop watcher's server-side snapshot pattern.
    ctx.effect(() => webServer.register({
    kind: 'exact',
    path: '/plugins/dsh-agent-teams/state',
    handler: async (req, res) => {
      const url = new URL(req.url ?? '/', 'http://x')
      const roots = workspaceRegistry.list().map((workspace) => ({
        workspace: workspace.title,
        stateRoot: join(workspace.path, resolved.stateDir),
      }))
      // ?archived=1 serves teams moved to archive/ (post-delete review).
      const snapshots = url.searchParams.get('archived') === '1'
        ? await collectArchivedTeamsActivity(ctx, roots)
        : await collectTeamsActivity(ctx, roots)
      const body = JSON.stringify({ teams: snapshots })
      res.writeHead(200, {
        'content-type': 'application/json; charset=utf-8',
        'cache-control': 'no-store',
      })
      res.end(body)
    },
  }), 'agent-teams: activity route')

  // Demand-brief decision route: the requester confirms or rejects a brief
  // straight from the browser; the captain agent has no tool for this, so the
  // decision right stays physically on the requester side.
  ctx.effect(() => webServer.register({
    kind: 'exact',
    path: '/plugins/dsh-agent-teams/brief',
    handler: (req, res) => {
      if (req.method !== 'POST') {
        res.writeHead(405, { allow: 'POST' })
        res.end()
        return
      }
      let body = ''
      req.on('data', (chunk) => { body += String(chunk) })
      req.on('end', () => {
        void (async () => {
          try {
            const payload: unknown = JSON.parse(body === '' ? '{}' : body)
            if (typeof payload !== 'object' || payload === null) throw new Error('bad payload')
            const workspaceTitle = (payload as Record<string, unknown>)['workspace']
            const teamId = (payload as Record<string, unknown>)['teamId']
            const action = (payload as Record<string, unknown>)['action']
            const reason = (payload as Record<string, unknown>)['reason']
            if (typeof workspaceTitle !== 'string' || typeof teamId !== 'string') throw new Error('workspace and teamId required')
            if (action !== 'confirm' && action !== 'reject') throw new Error('action must be confirm or reject')
            if (teamId !== sanitizeKey(teamId)) throw new Error('bad teamId')
            const root = workspaceRegistry.list().find((workspace) => workspace.title === workspaceTitle)
            if (root === undefined) throw new Error('unknown workspace')
            const stateRoot = join(root.path, resolved.stateDir)
            const decided = await withTeamLock(`brief:${stateRoot}:${teamId}`, async () => {
              const team = await readTeam(stateRoot, teamId)
              if (team === undefined) throw new Error(`no team "${teamId}"`)
              if (team.brief === undefined) throw new Error(`team "${teamId}" has no brief`)
              team.brief.status = action === 'confirm' ? 'confirmed' : 'rejected'
              if (action === 'reject') team.brief.rejectReason = typeof reason === 'string' ? reason : ''
              team.brief.decidedAt = Date.now()
              await writeTeam(stateRoot, team)
              return { status: team.brief.status, captainSessionId: team.captainSessionId, teamName: team.name }
            })
            // 确认/驳回后通过 live delivery 通知 captain，避免 captain 等到下一
            // 次会话推进才发现 brief 已拍板（2026-08-18 体验问题：确认后需用户催一句）。
            // captain 离线时 mailbox 不写（该通知仅为唤醒用途，派发阻塞解除已在
            // 落盘 status=confirmed 时生效），best-effort。
            const liveCaptain = ctx.agents.get(decided.captainSessionId as SessionId)
            if (liveCaptain !== undefined) {
              const verb = action === 'confirm' ? '已确认' : '已驳回'
              const reasonText = action === 'reject' && typeof reason === 'string' && reason !== ''
                ? `（理由：${reason}）`
                : ''
              steerCaptainReport(
                liveCaptain,
                'requester',
                `需求确认单已由需求方${verb}${reasonText} — 团队「${decided.teamName}」(id ${teamId}) 的派发阻塞已解除，可以开始认领并派发任务。`,
              )
            }
            const bodyOut = JSON.stringify({ ok: true, status: decided.status })
            res.writeHead(200, {
              'content-type': 'application/json; charset=utf-8',
              'cache-control': 'no-store',
            })
            res.end(bodyOut)
          } catch (error: unknown) {
            const message = error instanceof Error ? error.message : String(error)
            const bodyOut = JSON.stringify({ ok: false, error: message })
            res.writeHead(message.startsWith('no team') || message.startsWith('unknown workspace') ? 404 : 400, {
              'content-type': 'application/json; charset=utf-8',
              'cache-control': 'no-store',
            })
            res.end(bodyOut)
          }
        })()
      })
    },
  }), 'agent-teams: brief route')

  // Team archive route: the requester closes a team straight from the
  // collapsed badge. Members are interrupted best-effort (only while the
  // captain agent is still live), then the team state moves to the archive
  // directory where the panel keeps it under "历史归档".
  ctx.effect(() => webServer.register({
    kind: 'exact',
    path: '/plugins/dsh-agent-teams/team/archive',
    handler: (req, res) => {
      if (req.method !== 'POST') {
        res.writeHead(405, { allow: 'POST' })
        res.end()
        return
      }
      let body = ''
      req.on('data', (chunk) => { body += String(chunk) })
      req.on('end', () => {
        void (async () => {
          try {
            const payload: unknown = JSON.parse(body === '' ? '{}' : body)
            if (typeof payload !== 'object' || payload === null) throw new Error('bad payload')
            const workspaceTitle = (payload as Record<string, unknown>)['workspace']
            const teamId = (payload as Record<string, unknown>)['teamId']
            const action = (payload as Record<string, unknown>)['action']
            if (typeof workspaceTitle !== 'string' || typeof teamId !== 'string') throw new Error('workspace and teamId required')
            if (action !== 'archive') throw new Error('action must be archive')
            if (teamId !== sanitizeKey(teamId)) throw new Error('bad teamId')
            const root = workspaceRegistry.list().find((workspace) => workspace.title === workspaceTitle)
            if (root === undefined) throw new Error('unknown workspace')
            const stateRoot = join(root.path, resolved.stateDir)
            const archived = await withTeamLock(`archive:${stateRoot}:${teamId}`, async () => {
              const team = await readTeam(stateRoot, teamId)
              if (team === undefined) throw new Error(`no team "${teamId}"`)
              const captain = ctx.agents.get(team.captainSessionId as SessionId)
              if (captain === undefined) {
                ctx.logger.warn(`agent-teams: archive of team "${teamId}" skipped member interrupts (captain not live)`)
              } else {
                for (const member of team.members) {
                  if (member.status !== 'removed' && member.id !== '') interruptMember(ctx, captain, member.id)
                }
              }
              await archiveTeamDir(stateRoot, teamId)
              return true
            })
            const bodyOut = JSON.stringify({ ok: true, archived })
            res.writeHead(200, {
              'content-type': 'application/json; charset=utf-8',
              'cache-control': 'no-store',
            })
            res.end(bodyOut)
          } catch (error: unknown) {
            const message = error instanceof Error ? error.message : String(error)
            const bodyOut = JSON.stringify({ ok: false, error: message })
            res.writeHead(message.startsWith('no team') || message.startsWith('unknown workspace') ? 404 : 400, {
              'content-type': 'application/json; charset=utf-8',
              'cache-control': 'no-store',
            })
            res.end(bodyOut)
          }
        })()
      })
    },
  }), 'agent-teams: archive route')

  // Whale mascot artwork: serve the packaged role/action images to the
  // activity panel. An explicit allowlist guards the route (no path
  // traversal); the images ship with the bundle (files: assets/).
  const artDir = fileURLToPath(new URL('../assets/agent-teams/', import.meta.url))
  const ART_ALLOWLIST = new Set([
    'team-lead.png', 'researcher.png', 'engineer.png', 'designer.png',
    'qa-engineer.png', 'security-reviewer.png', 'data-analyst.png',
    'docs-coordinator.png', 'action-working.png', 'action-thinking.png',
    'action-reporting.png', 'action-celebrating.png', 'action-sleeping.png',
    'action-sending.png',
  ])
    ctx.effect(() => webServer.register({
      kind: 'prefix',
      path: '/plugins/dsh-agent-teams/assets',
    handler: async (req, res) => {
      let name: string
      try {
        name = decodeURIComponent(new URL(req.url ?? '/', 'http://x').pathname.split('/').pop() ?? '')
      } catch {
        // Malformed percent-encoding: treat as an unknown asset, not a 400.
        res.writeHead(404)
        res.end()
        return
      }
      if (!ART_ALLOWLIST.has(name)) {
        res.writeHead(404)
        res.end()
        return
      }
      try {
        const data = await readFile(join(artDir, name))
        res.writeHead(200, {
          'content-type': 'image/png',
          'cache-control': 'public, max-age=86400',
        })
        res.end(data)
      } catch (error: unknown) {
        ctx.logger.warn(`agent-teams: artwork read failed for ${name}: ${String(error)}`)
        res.writeHead(404)
        res.end()
      }
      },
    }), 'agent-teams: artwork route')
  }

  registerWebSurface()
  ctx.on('internal/service', (name) => {
    if (WEB_SERVER_KEYS.includes(name as (typeof WEB_SERVER_KEYS)[number])
      || WORKSPACE_KEYS.includes(name as (typeof WORKSPACE_KEYS)[number])) {
      registerWebSurface()
    }
  })
}
