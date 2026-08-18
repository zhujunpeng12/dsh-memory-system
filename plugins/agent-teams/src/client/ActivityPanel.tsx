/**
 * AgentTeams activity panel: the top-right floater monitoring every team.
 *
 * Modeled on the Claude Code desktop SessionActivityPanel: a fixed glass
 * panel at the top-right corner. On wide viewports it cooperatively makes the
 * conversation column yield space; narrow viewports keep overlay mode. It
 * polls the host `/plugins/dsh-agent-teams/state` route for
 * server-side snapshots (durable files + live subagent activity), with a
 * collapsed badge that auto-expands once when activity appears. Archived
 * teams stay available for the owning conversation after live work ends.
 *
 * The floater mounts through a body portal (no top-right slot exists in the
 * web shell); it is not a conversation node — the in-conversation panel was
 * removed in favor of this always-available monitor.
 * @module dsh-agent-teams/client/activity
 */

import { useEffect, useLayoutEffect, useMemo, useRef, useState, useSyncExternalStore } from 'react'
import type { CSSProperties, PointerEvent as ReactPointerEvent, ReactNode } from 'react'
import {
  IconBranchOutline16, IconChevronDownOutline14, IconChevronRightOutline14, IconChevronUpOutline14,
  IconCloseOutline16,
  StateDot, type StateDotState,
} from '@deepseek-ai/dsh-client-ui-primitives'
import type { SessionId } from '@deepseek-ai/dsh-session/types'
import type { ObservableSnapshot, SessionListState } from '@deepseek-ai/dsh-client-runtime/client'
import {
  activityPanelExpandedForSession,
  dependencyFocusTaskId,
  relatedTaskIds,
  taskStages,
} from './activity-model.ts'
import { ACTION_ART, LEAD_ART, memberArtUrl } from './artwork.ts'
import { OPEN_PANEL_EVENT } from './AgentTeamsCard.tsx'
import type { AgentTeamsCardData } from './agent-teams-card-definition.ts'
import css from './ActivityPanel.module.css'

/** Poll cadence for the host snapshot route. */
const POLL_MS = 1000
/** Grace before the panel collapses once no team remains. */
const AUTOCLOSE_GRACE_MS = 2000
/**
 * Page-settle window after mount: activity restored on page load only shows
 * the collapsed badge, so the panel never yanks the conversation column
 * right after load. New activity after this window auto-expands as usual.
 */
const AUTO_OPEN_SETTLE_MS = 4000
/** Host route serving team snapshots. */
const STATE_URL = '/plugins/dsh-agent-teams/state'
/** Root marker shared with the panel CSS while the portal is expanded. */
const PANEL_OPEN_ATTRIBUTE = 'data-agent-teams-panel-open'
/** localStorage key for the user-dragged panel position. */
const PANEL_POS_KEY = 'agent-teams-panel-pos'
/** Viewport margin kept on the left/right; extra room reserved at the bottom. */
const PANEL_MARGIN_X = 8
const PANEL_MARGIN_BOTTOM = 40
/** Badge pointer travel under this counts as a click (expand), not a drag. */
const BADGE_DRAG_THRESHOLD_PX = 6

/** Keep the panel inside the viewport: 8px side margins, 40px reserved at the bottom. */
function clampPanelPos(x: number, y: number, width: number): { x: number; y: number } {
  const maxX = Math.max(PANEL_MARGIN_X, window.innerWidth - width - PANEL_MARGIN_X)
  const maxY = Math.max(PANEL_MARGIN_X, window.innerHeight - PANEL_MARGIN_BOTTOM)
  return {
    x: Math.min(Math.max(x, PANEL_MARGIN_X), maxX),
    y: Math.min(Math.max(y, PANEL_MARGIN_X), maxY),
  }
}

/** Inline position once the panel has been dragged or restored: overrides the
 * CSS right-anchored placement. The width mirrors the module media queries so
 * the panel never changes size between anchored and positioned modes. */
function positionedPanelStyle(pos: { x: number; y: number }): CSSProperties {
  const mobile = window.matchMedia('(max-width: 640px)').matches
  const width = mobile
    ? 'calc(100vw - 20px - var(--dsh-sidebar-width, 0px))'
    : 'min(var(--agent-teams-panel-width), calc(100vw - 24px))'
  return { left: pos.x, top: pos.y, right: 'auto', width }
}

/** Restore the persisted position, clamped to the current viewport. */
function readStoredPanelPos(): { x: number; y: number } | null {
  try {
    const raw = localStorage.getItem(PANEL_POS_KEY)
    if (raw === null) return null
    const parsed: unknown = JSON.parse(raw)
    if (typeof parsed !== 'object' || parsed === null) return null
    const record = parsed as Record<string, unknown>
    const x = record['x']
    const y = record['y']
    if (typeof x !== 'number' || typeof y !== 'number' || !Number.isFinite(x) || !Number.isFinite(y)) return null
    // Approximate the rendered width (desktop panel var is 388px); the resize
    // listener re-clamps with the measured rect once the panel is mounted.
    const width = window.matchMedia('(max-width: 640px)').matches
      ? Math.max(0, window.innerWidth - 20)
      : Math.min(388, Math.max(0, window.innerWidth - 24))
    return clampPanelPos(x, y, width)
  } catch {
    return null
  }
}

/** One member row of a host snapshot. */
export interface ActivityMember {
  readonly id: string
  readonly name: string
  readonly role: string
  readonly activity: 'working' | 'idle' | 'unknown'
  readonly progress: number
  readonly done: number
  readonly total: number
  readonly currentTask: string
  readonly unread: number
}

/** One task row of a host snapshot. */
export interface ActivityTask {
  readonly id: string
  readonly subject: string
  readonly status: string
  readonly state: 'blocked' | 'open' | 'running' | 'completed'
  readonly assignee: string
  readonly dependencies: readonly string[]
  readonly depth: number
}

/** One captain-inbox preview row. */
export interface ActivityMessage {
  readonly from: string
  readonly content: string
}

/** One team snapshot (mirrors the host TeamActivitySnapshot). */
export interface ActivityTeam {
  readonly workspace: string
  readonly teamId: string
  readonly name: string
  readonly description?: string
  readonly captainSessionId: string
  readonly members: readonly ActivityMember[]
  readonly tasks: readonly ActivityTask[]
  readonly messageCount: number
  readonly captainInbox: readonly ActivityMessage[]
  /** Demand confirmation brief; absent on legacy teams. */
  readonly brief?: {
    readonly status: string
    readonly purpose: string
    readonly constraints: string
    readonly successCriteria: string
    readonly plan: string
    readonly roles: string
    readonly rejectReason: string
  }
}

/** Initial-letter fallback for unmatched roles. */
function memberInitial(name: string): string {
  return name.trim().slice(0, 1).toUpperCase() || '?'
}

function stableHash(value: string): number {
  let hash = 0
  for (let index = 0; index < value.length; index += 1) {
    hash = ((hash << 5) - hash + value.charCodeAt(index)) | 0
  }
  return Math.abs(hash)
}

const ACCENTS = [
  'var(--dsw-alias-state-business-primary)',
  'var(--dsw-alias-state-success-primary)',
  'var(--dsw-alias-state-error-primary)',
  'var(--dsw-alias-state-warn-primary)',
  'var(--dsw-alias-label-tertiary)',
] as const

function accentOf(id: string): string {
  return ACCENTS[stableHash(id) % ACCENTS.length] ?? ACCENTS[0]
}

/** Badge text follows the raw task status (finer than the 4 visual states):
 * claimed/pending/failed/cancelled keep their own labels and colors. */
const TASK_STATUS_LABEL: Record<string, string> = {
  pending: '待领取',
  claimed: '已认领',
  in_progress: '进行中',
  completed: '已完成',
  failed: '失败',
  cancelled: '已取消',
}

function taskStatusLabel(status: string): string {
  return TASK_STATUS_LABEL[status] ?? status
}

/** Badge/bar coloring key: visual state, widened for terminal statuses. */
function taskTone(state: ActivityTask['state'], status: string): string {
  if (status === 'failed') return 'failed'
  if (status === 'cancelled') return 'cancelled'
  return state
}

/** Ring state of one collapsed-badge avatar. */
type BadgeRingState = 'undelivered' | 'delivered' | 'working' | 'queued' | 'neutral'

/** One avatar in the collapsed badge strip. */
interface BadgeAvatarEntry {
  readonly key: string
  readonly name: string
  readonly art: string | null
  readonly ring: BadgeRingState
  /** Subagent session id for member entries; absent for the captain entry. */
  readonly memberId?: string
}

/** Cap the collapsed strip; extra members collapse into a "+N" chip. */
const MAX_BADGE_CHIPS = 5

/** Captain ring: green delivery wave only when the whole team has delivered. */
function captainBadgeRing(tasks: readonly ActivityTask[]): BadgeRingState {
  if (tasks.length === 0) return 'neutral'
  return tasks.every((task) => task.status === 'completed') ? 'delivered' : 'neutral'
}

/** Member ring: in-progress tasks = red wave; pending/claimed (waiting to
 * start, no in-progress) = orange wave; all delivered while the team still
 * has open work = green wave; whole team delivered or no tasks = static gray. */
function memberBadgeRing(member: ActivityMember, tasks: readonly ActivityTask[]): BadgeRingState {
  const owned = tasks.filter((task) => task.assignee === member.name)
  if (owned.length === 0) return 'neutral'
  if (owned.some((task) => task.status === 'in_progress')) return 'working'
  if (owned.some((task) => task.status === 'pending' || task.status === 'claimed')) return 'queued'
  const teamDone = tasks.every((task) => task.status === 'completed')
  if (owned.every((task) => task.status === 'completed') && !teamDone) return 'delivered'
  return 'neutral'
}

/** Captain first, then members: one row of rings for a team. */
function teamBadgeAvatars(team: ActivityTeam): readonly BadgeAvatarEntry[] {
  const entries: BadgeAvatarEntry[] = [{
    key: `${team.teamId}:captain`,
    name: '队长',
    art: LEAD_ART,
    ring: captainBadgeRing(team.tasks),
  }]
  for (const member of team.members) {
    entries.push({
      key: `${team.teamId}:${member.id}`,
      name: member.name,
      art: memberArtUrl(member.name, member.role),
      ring: memberBadgeRing(member, team.tasks),
      memberId: member.id,
    })
  }
  return entries
}

/** One collapsed-badge chip: 28px avatar with delivery ring + name label.
 * Member chips navigate to the member's subagent transcript; the captain
 * chip expands the activity panel. Pointer-down never starts a badge drag. */
function BadgeChip({ entry, onNavigate, onExpand }: {
  readonly entry: BadgeAvatarEntry
  readonly onNavigate: (id: SessionId) => void
  readonly onExpand: () => void
}) {
  const isMember = entry.memberId !== undefined
  return (
    <button
      type="button"
      className={css.badgeChip}
      title={entry.name}
      aria-label={isMember ? `打开成员 ${entry.name} 的子会话` : '展开活动面板'}
      onClick={() => {
        if (isMember) {
          if (entry.memberId !== '') onNavigate(entry.memberId as SessionId)
          return
        }
        onExpand()
      }}
      onPointerDown={(event) => { event.stopPropagation() }}
    >
      <span className={css.badgeAvatarWrap} data-ring={entry.ring}>
        {entry.art !== null ? (
          <img className={css.badgeAvatar} src={entry.art} alt="" aria-hidden />
        ) : (
          <span className={css.badgeAvatarFallback} style={{ background: accentOf(entry.key) }}>{memberInitial(entry.name)}</span>
        )}
      </span>
      <span className={css.badgeChipName}>{entry.name}</span>
    </button>
  )
}

/** Expanded-panel avatar (captain node + delegation member rows): carries the
 * same delivery/working/queued ring state as the collapsed badge. The ripple
 * phase comes from the container-level --wave-phase (set once on the panel
 * aside), so dynamically added avatars inherit the grid with no per-element
 * alignment race. Children (artwork, fallback initial, stateArt badge)
 * render unchanged. */
function PanelAvatar({ ring, className, dataUnread, children }: {
  readonly ring: BadgeRingState
  readonly className?: string
  readonly dataUnread?: boolean
  readonly children: ReactNode
}) {
  return (
    <span className={className} data-ring={ring} data-unread={dataUnread}>
      {children}
    </span>
  )
}

/** Collapsed badge: an always-visible corner pill while any team exists. Shows
 * a horizontal chip strip (captain + members, 28px avatars + names) with
 * per-member delivery rings. Dragging the title area moves the badge (shares
 * the panel position); a click under the 6px travel threshold expands the
 * panel. Member chips navigate to their subagent transcript, the captain chip
 * and the far-right chevron expand the panel. Falls back to the legacy
 * dot + count when no team contributes avatars. */
function CollapsedBadge({ teams, count, busy, pos, dragging, nodeRef, onClick, onExpand, onNavigate, onClose, onPointerDown, onPointerMove, onPointerUp, onPointerCancel }: {
  readonly teams: readonly ActivityTeam[]
  readonly count: number
  readonly busy: boolean
  readonly pos: { x: number; y: number } | null
  readonly dragging: boolean
  readonly nodeRef: (node: HTMLElement | null) => void
  readonly onClick: () => void
  readonly onExpand: () => void
  readonly onNavigate: (id: SessionId) => void
  readonly onClose: () => void
  readonly onPointerDown: (event: ReactPointerEvent<HTMLElement>) => void
  readonly onPointerMove: (event: ReactPointerEvent<HTMLElement>) => void
  readonly onPointerUp: (event: ReactPointerEvent<HTMLElement>) => void
  readonly onPointerCancel: (event: ReactPointerEvent<HTMLElement>) => void
}) {
  const avatars = teams.flatMap(teamBadgeAvatars)
  const shown = avatars.slice(0, MAX_BADGE_CHIPS)
  const overflow = avatars.length - shown.length
  // Container-level ripple phase: anchor the badge pill once per mount so
  // every chip ring (including chips added later) inherits the same 1.8s
  // grid value instead of aligning per element.
  const wrapRef = useRef<HTMLDivElement | null>(null)
  useEffect(() => {
    const el = wrapRef.current
    if (el !== null) {
      el.style.setProperty('--wave-phase', `${-(performance.now() % 1800)}ms`)
    }
  }, [])
  return (
    <div
      ref={(node) => { wrapRef.current = node; nodeRef(node) }}
      className={css.badge}
      data-busy={busy}
      data-dragging={dragging}
      style={pos === null ? undefined : { left: pos.x, top: pos.y, right: 'auto' }}
    >
      {teams.length > 0 && (
        <button
          type="button"
          className={css.badgeClose}
          onClick={onClose}
          onPointerDown={(event) => { event.stopPropagation() }}
          aria-label="关闭团队"
          title="关闭团队"
        >
          <IconCloseOutline16 />
        </button>
      )}
      <button
        type="button"
        className={css.badgeMain}
        onClick={onClick}
        onPointerDown={onPointerDown}
        onPointerMove={onPointerMove}
        onPointerUp={onPointerUp}
        onPointerCancel={onPointerCancel}
        aria-label={`AgentTeams 活动，${count} 个团队`}
        title="点击展开 · 拖拽可移动"
      >
        <span className={css.badgeTitle}>Agent Teams</span>
        {avatars.length === 0 && (
          <>
            <span className={css.badgeDot} data-busy={busy} aria-hidden />
            <span className={css.badgeCount}>{count}</span>
          </>
        )}
      </button>
      {avatars.length > 0 && shown.map((entry) => (
        <BadgeChip key={entry.key} entry={entry} onNavigate={onNavigate} onExpand={onExpand} />
      ))}
      {overflow > 0 && <span className={css.badgeMore}>+{overflow}</span>}
      <button
        type="button"
        className={css.badgeExpand}
        onClick={onExpand}
        onPointerDown={(event) => { event.stopPropagation() }}
        aria-label="展开活动面板"
        title="展开活动面板"
      >
        <IconChevronDownOutline14 />
      </button>
    </div>
  )
}

function memberDotState(member: ActivityMember, tasks: readonly ActivityTask[]): StateDotState {
  const owned = tasks.filter((task) => task.assignee === member.name)
  if (member.activity === 'working') return 'ongoing'
  if (owned.some((task) => task.status === 'failed')) return 'error'
  if (owned.length > 0 && owned.every((task) => task.status === 'completed')) return 'done'
  return 'warning'
}

function memberStateLabel(member: ActivityMember, tasks: readonly ActivityTask[]): string {
  const owned = tasks.filter((task) => task.assignee === member.name)
  if (member.activity === 'working') return '工作中'
  if (owned.some((task) => task.status === 'failed')) return '有失败'
  if (owned.some((task) => task.state === 'blocked')) return '等待'
  if (owned.length > 0 && owned.every((task) => task.status === 'completed')) return '已交付'
  if (owned.length > 0) return '待执行'
  return '待派工'
}

function memberStatusText(member: ActivityMember, tasks: readonly ActivityTask[]): string {
  const owned = tasks.filter((task) => task.assignee === member.name)
  const current = owned.find((task) => task.id === member.currentTask)
  const blocked = owned.find((task) => task.state === 'blocked')
  if (member.activity === 'working' && current !== undefined) return `正在执行 ${current.id}`
  if (member.activity === 'working') return '正在处理已派任务'
  if (blocked !== undefined) {
    const dependency = tasks.find((task) => blocked.dependencies.includes(task.id) && task.state !== 'completed')
    if (dependency !== undefined) return `等待 ${dependency.id} · ${dependency.assignee || '待认领'}`
    return '等待前置任务'
  }
  if (member.total === 0) return '等待队长派工'
  if (member.done === member.total) return '任务已交付'
  return member.activity === 'idle' ? '待继续执行' : '状态未知'
}

function dependencyLabel(task: ActivityTask, tasks: readonly ActivityTask[]): string {
  return task.dependencies.map((id) => {
    const dependency = tasks.find((candidate) => candidate.id === id)
    return dependency?.assignee ? `${id}·${dependency.assignee}` : id
  }).join('、')
}

function TaskNode({ task, tasks, focused, dimmed, pinned, onPin, onHover, onFocusChange }: {
  readonly task: ActivityTask
  readonly tasks: readonly ActivityTask[]
  readonly focused: boolean
  readonly dimmed: boolean
  readonly pinned: boolean
  readonly onPin: (id: string) => void
  readonly onHover: (id: string | null) => void
  readonly onFocusChange: (id: string | null) => void
}) {
  const tone = taskTone(task.state, task.status)
  return (
    <button
      type="button"
      className={css.taskNode}
      data-task-id={task.id}
      data-state={tone}
      data-focused={focused}
      data-dimmed={dimmed}
      aria-pressed={pinned}
      title={`${task.id} · ${task.subject}（悬停高亮依赖链 · 点击固定）`}
      onClick={() => { onPin(task.id) }}
      onMouseEnter={() => { onHover(task.id) }}
      onMouseLeave={() => { onHover(null) }}
      onFocus={() => { onFocusChange(task.id) }}
      onBlur={() => { onFocusChange(null) }}
    >
      <span className={css.taskNodeHead}>
        <span className={css.taskId}>{task.id}</span>
        <span className={css.taskBadge} data-state={tone}>{taskStatusLabel(task.status)}</span>
      </span>
      <span className={css.taskSubject}>{task.subject}</span>
      <span className={css.taskRoute}>
        <span className={css.taskOwner}>{task.assignee || '待认领'}</span>
        {task.dependencies.length === 0
          ? <span className={css.taskStart}>起点</span>
          : <span className={css.taskDeps}>依赖 {dependencyLabel(task, tasks)}</span>}
      </span>
    </button>
  )
}

function DependencyMap({ tasks }: { readonly tasks: readonly ActivityTask[] }) {
  const [hoverTaskId, setHoverTaskId] = useState<string | null>(null)
  const [keyboardTaskId, setKeyboardTaskId] = useState<string | null>(null)
  const [pinnedTaskId, setPinnedTaskId] = useState<string | null>(null)
  // Collapsed by default: the dependency map only opens on demand.
  const [expanded, setExpanded] = useState(false)
  const hoverTimer = useRef<ReturnType<typeof setTimeout> | null>(null)
  const focusedTaskId = dependencyFocusTaskId(pinnedTaskId, keyboardTaskId, hoverTaskId)
  const stages = useMemo(() => taskStages(tasks), [tasks])
  const related = useMemo(
    () => focusedTaskId === null ? null : relatedTaskIds(focusedTaskId, tasks),
    [focusedTaskId, tasks],
  )
  const scheduleHover = (id: string | null): void => {
    if (hoverTimer.current !== null) {
      clearTimeout(hoverTimer.current)
      hoverTimer.current = null
    }
    if (id === null) {
      setHoverTaskId(null)
      return
    }
    hoverTimer.current = setTimeout(() => {
      hoverTimer.current = null
      setHoverTaskId(id)
    }, 180)
  }
  useEffect(() => () => {
    if (hoverTimer.current !== null) clearTimeout(hoverTimer.current)
  }, [])
  useEffect(() => {
    const onKeyDown = (event: KeyboardEvent): void => {
      if (event.key === 'Escape') setPinnedTaskId(null)
    }
    window.addEventListener('keydown', onKeyDown)
    return () => { window.removeEventListener('keydown', onKeyDown) }
  }, [])
  if (tasks.length === 0) return null
  return (
    <section className={css.dependencySection} aria-label="任务依赖链" data-dependency-map>
      <header className={css.sectionHead}>
        <button
          type="button"
          className={css.sectionToggle}
          onClick={() => { setExpanded((current) => !current) }}
          aria-expanded={expanded}
          title={expanded ? '收起任务依赖' : '展开任务依赖'}
        >
          {expanded ? <IconChevronDownOutline14 /> : <IconChevronRightOutline14 />}
          <span className={css.sectionTitle}><IconBranchOutline16 /> 任务依赖</span>
        </button>
        {expanded && <span className={css.sectionHint}>{pinnedTaskId === null ? '悬停高亮依赖链 · 点击固定' : `${pinnedTaskId} 已固定 · Esc 取消`}</span>}
      </header>
      {expanded && (
        <div className={css.stageFlow}>
        {stages.map((stage, index) => (
          <div key={stage.depth} className={css.stageGroup} data-depth={stage.depth}>
            {index > 0 && (
              <span className={css.stageConnector} aria-hidden>
                <span className={css.stageLine} />
                <IconChevronRightOutline14 />
              </span>
            )}
            <div className={css.stageColumn}>
              <span className={css.stageLabel}>
                {stage.depth === 0 ? '起点' : `依赖层 ${stage.depth}`}
                <span>{stage.tasks.length}</span>
              </span>
              <div className={css.stageTasks}>
                {stage.tasks.map((task) => (
                  <TaskNode
                    key={task.id}
                    task={task}
                    tasks={tasks}
                    focused={related?.has(task.id) ?? false}
                    dimmed={related !== null && !related.has(task.id)}
                    pinned={pinnedTaskId === task.id}
                    onPin={(id) => { setPinnedTaskId((current) => current === id ? null : id) }}
                    onHover={scheduleHover}
                    onFocusChange={setKeyboardTaskId}
                  />
                ))}
              </div>
            </div>
          </div>
        ))}
        </div>
      )}
    </section>
  )
}

/** Demand confirmation card: shows the five brief fields plus the lifecycle
 * status; while submitted it offers the requester confirm/reject actions. */
function BriefCard({ team, onDecide, pending }: {
  readonly team: ActivityTeam
  readonly onDecide?: (action: 'confirm' | 'reject') => void
  /** In-flight decision ('confirm' | 'reject') for instant button feedback. */
  readonly pending?: 'confirm' | 'reject' | null
}) {
  const brief = team.brief
  if (brief === undefined) return null
  const statusLabel: Record<string, string> = {
    draft: '草稿', submitted: '待拍板', confirmed: '已确认', rejected: '已驳回',
  }
  return (
    <div className={css.briefCard} data-status={brief.status}>
      <div className={css.briefHead}>
        <span className={css.briefTitle}>需求确认单</span>
        <span className={css.briefStatus}>{statusLabel[brief.status] ?? brief.status}</span>
      </div>
      <div className={css.briefField}><span>目标</span><span>{brief.purpose}</span></div>
      {brief.constraints !== '' && <div className={css.briefField}><span>约束</span><span>{brief.constraints}</span></div>}
      <div className={css.briefField}><span>成功标准</span><span>{brief.successCriteria}</span></div>
      <div className={css.briefField}><span>任务计划</span><span>{brief.plan}</span></div>
      <div className={css.briefField}><span>角色</span><span>{brief.roles}</span></div>
      {brief.rejectReason !== '' && (
        <div className={css.briefField} data-reject><span>驳回理由</span><span>{brief.rejectReason}</span></div>
      )}
      {brief.status === 'submitted' && onDecide !== undefined && (
        <div className={css.briefActions}>
          <button
            type="button"
            className={css.briefConfirm}
            disabled={pending !== null && pending !== undefined}
            onClick={() => { onDecide('confirm') }}
          >
            {pending === 'confirm' ? '确认中…' : '确认'}
          </button>
          <button
            type="button"
            className={css.briefReject}
            disabled={pending !== null && pending !== undefined}
            onClick={() => { onDecide('reject') }}
          >
            {pending === 'reject' ? '提交中…' : '驳回'}
          </button>
        </div>
      )}
    </div>
  )
}

function TeamSection({ team, onNavigate, onBriefDecide, briefPending, historic = false }: {
  readonly team: ActivityTeam
  /** Navigate to a member transcript (floater hides immediately). */
  readonly onNavigate: (id: SessionId) => void
  /** Decide the demand brief (confirm/reject); absent for read-only renderings. */
  readonly onBriefDecide?: (action: 'confirm' | 'reject') => void
  /** In-flight brief decision for instant button feedback. */
  readonly briefPending?: 'confirm' | 'reject' | null
  readonly historic?: boolean
}) {
  const busyCount = team.members.filter((member) => member.activity === 'working').length
  const assignedCount = team.tasks.filter((task) => task.assignee !== '').length
  const completedCount = team.tasks.filter((task) => task.status === 'completed').length
  const allCompleted = team.tasks.length > 0 && completedCount === team.tasks.length
  const unclaimed = team.tasks.filter((task) => {
    if (task.status === 'completed' || task.status === 'failed' || task.status === 'cancelled') return false
    if (task.assignee === '') return true
    return !team.members.some((member) => member.name === task.assignee)
  })
  return (
    <section className={css.team} data-team-id={team.teamId}>
      <header className={css.teamHead}>
        <span className={css.teamName} title={team.name}>{team.name}</span>
        {historic && <span className={css.historicPill}>历史归档</span>}
        <span className={css.teamStats}>
          <span data-stat="members">{team.members.length} 成员</span>
          <span data-stat="tasks">{completedCount}/{team.tasks.length} 完成</span>
          <span data-stat="messages">{team.messageCount} 消息</span>
        </span>
      </header>

      <BriefCard team={team} onDecide={onBriefDecide} pending={briefPending} />

      <section className={css.delegationSection} aria-label="队长派工关系" data-delegation-map>
        <div className={css.captainNode}>
          <PanelAvatar ring={captainBadgeRing(team.tasks)} className={css.captainAvatar}>
            <img className={css.leadAvatar} src={LEAD_ART} alt="" aria-hidden />
          </PanelAvatar>
          <span className={css.captainInfo}>
            <span className={css.captainLine}>
              <span className={css.captainName}>队长</span>
              <span className={css.captainRole}>拆解 · 派发 · 汇总</span>
            </span>
            <span className={css.captainSummary}>已派发 {assignedCount} 项任务给 {team.members.length} 名成员</span>
          </span>
          <span className={css.captainState} data-busy={busyCount > 0}>
            <StateDot state={busyCount > 0 ? 'ongoing' : allCompleted ? 'done' : 'warning'} />
            {busyCount > 0 ? `${busyCount} 人执行中` : allCompleted ? '已收齐' : '等待回报'}
          </span>
        </div>

        <div className={css.delegationTree}>
          {team.members.length === 0 && <span className={css.emptyHint}>暂无成员，等待队长组建团队</span>}
          {team.members.map((member) => {
            const owned = team.tasks.filter((task) => task.assignee === member.name)
            return (
              <div key={member.id} className={css.memberBlock} data-activity={member.activity}>
                <span className={css.memberBranch} aria-hidden><span /></span>
                <button
                  type="button"
                  className={css.memberRow}
                  data-activity={member.activity}
                  onClick={() => { if (member.id !== '') onNavigate(member.id as SessionId) }}
                >
                  <PanelAvatar ring={memberBadgeRing(member, team.tasks)} className={css.memberAvatar} dataUnread={member.unread > 0}>
                    {memberArtUrl(member.name, member.role) !== null ? (
                      <img className={css.memberArt} src={memberArtUrl(member.name, member.role) ?? ''} alt="" aria-hidden />
                    ) : (
                      <span className={css.memberInitial} style={{ background: accentOf(member.id) }}>{memberInitial(member.name)}</span>
                    )}
                    <img className={css.stateArt} data-activity={member.activity} src={ACTION_ART[member.activity]} alt="" aria-hidden />
                  </PanelAvatar>
                  <span className={css.memberInfo}>
                    <span className={css.memberLine}>
                      <span className={css.memberName}>{member.name}</span>
                      {member.role !== '' && <span className={css.memberRole}>{member.role}</span>}
                      <span className={css.memberState} data-activity={member.activity}>
                        <StateDot state={memberDotState(member, team.tasks)} />
                        {memberStateLabel(member, team.tasks)}
                      </span>
                    </span>
                    <span className={css.memberStatusLine}>{memberStatusText(member, team.tasks)}</span>
                  </span>
                  <span className={css.memberCount}>{member.done}/{member.total}</span>
                </button>
                <div className={css.assignmentLine}>
                  <span className={css.assignmentLabel}>队长派发</span>
                  <span className={css.assignmentTasks}>
                    {owned.length === 0
                      ? <span className={css.taskEmpty}>暂无任务</span>
                      : owned.map((task) => (
                        <span key={task.id} className={css.assignmentChip} data-state={taskTone(task.state, task.status)} title={task.subject}>
                          {task.id}
                        </span>
                      ))}
                  </span>
                  {member.unread > 0 && <span className={css.unreadPill}>{member.unread} 条消息</span>}
                </div>
              </div>
            )
          })}
        </div>
      </section>

      <DependencyMap tasks={team.tasks} />

      {unclaimed.length > 0 && (
        <section className={css.unclaimed} aria-label="待认领任务">
          <span className={css.unclaimedTitle}>待队长认领或改派</span>
          <span className={css.assignmentTasks}>
            {unclaimed.map((task) => (
              <span key={task.id} className={css.assignmentChip} data-state={taskTone(task.state, task.status)} title={task.subject}>
                {task.id} · {task.assignee || '未分配'}
              </span>
            ))}
          </span>
        </section>
      )}
    </section>
  )
}

/** The top-right activity floater. Teams follow the current session: live
 * snapshots and historic card summaries are only shown while their captain
 * session is the one currently open. */
export function ActivityPanel({ sessionsList, openSession }: {
  readonly sessionsList: ObservableSnapshot<SessionListState>
  readonly openSession: (id: SessionId) => void
}) {
  // Navigating to a member's subagent transcript is an explicit departure:
  // hide the floater immediately instead of waiting out the autocollapse
  // grace, so the panel never lingers over the member session.
  const navigateToSession = (id: SessionId): void => {
    setOpen(false)
    setWasActive(false)
    openSession(id)
  }
  const [teams, setTeams] = useState<readonly ActivityTeam[]>([])
  const [archivedTeams, setArchivedTeams] = useState<readonly ActivityTeam[]>([])
  /** In-flight brief decision, giving the confirm/reject buttons instant feedback. */
  const [briefPending, setBriefPending] = useState<'confirm' | 'reject' | null>(null)
  const [open, setOpen] = useState(false)
  const [openOwner, setOpenOwner] = useState<SessionId | undefined>()
  const [autoOpened, setAutoOpened] = useState(false)
  const [wasActive, setWasActive] = useState(false)
  const [historic, setHistoric] = useState<ReadonlyMap<string, { data: AgentTeamsCardData; owner: string }>>(new Map())
  const [archivedExpanded, setArchivedExpanded] = useState(false)
  const current = useSyncExternalStore(
    sessionsList.subscribe,
    sessionsList.getSnapshot,
  ).current
  const currentRef = useRef(current)
  useEffect(() => { currentRef.current = current }, [current])
  const mountedAtRef = useRef(performance.now())
  /** Latest poll function, hoisted so brief decisions can force an immediate refresh. */
  const tickRef = useRef<() => Promise<void>>(async () => {})
  /**
   * Drag session for moving the panel or the collapsed badge: pointer start,
   * element start, measured width, and the live clamped position (mirrored so
   * pointer-up can persist it without depending on render timing). `initialPos`
   * restores the pre-gesture position when a badge gesture stays under the
   * click threshold.
   */
  const dragSessionRef = useRef<{
    readonly pointerX: number
    readonly pointerY: number
    readonly panelX: number
    readonly panelY: number
    readonly width: number
    readonly initialPos: { x: number; y: number } | null
    currentX: number
    currentY: number
  } | null>(null)
  const [dragging, setDragging] = useState(false)
  const [panelPos, setPanelPos] = useState<{ x: number; y: number } | null>(readStoredPanelPos)
  const panelRef = useRef<HTMLElement | null>(null)
  /** Click swallow after a drag-ended badge gesture (browsers still fire click after pointerup). */
  const suppressBadgeClickRef = useRef(false)
  const badgeRef = useRef<HTMLElement | null>(null)

  const handlePanelHeadPointerDown = (event: ReactPointerEvent<HTMLElement>): void => {
    // Only the primary button (or touch) starts a drag; the collapse button
    // stops propagation so it never drags.
    if (event.button !== 0) return
    const panel = panelRef.current
    if (panel === null) return
    try {
      event.currentTarget.setPointerCapture(event.pointerId)
    } catch {
      return
    }
    const rect = panel.getBoundingClientRect()
    dragSessionRef.current = {
      pointerX: event.clientX,
      pointerY: event.clientY,
      panelX: rect.left,
      panelY: rect.top,
      width: rect.width,
      currentX: rect.left,
      currentY: rect.top,
      initialPos: panelPos,
    }
    setDragging(true)
  }

  const handleDragPointerMove = (event: ReactPointerEvent<HTMLElement>): void => {
    const session = dragSessionRef.current
    if (session === null) return
    const next = clampPanelPos(
      session.panelX + event.clientX - session.pointerX,
      session.panelY + event.clientY - session.pointerY,
      session.width,
    )
    session.currentX = next.x
    session.currentY = next.y
    setPanelPos(next)
  }

  const handlePanelHeadPointerEnd = (): void => {
    const session = dragSessionRef.current
    if (session === null) return
    dragSessionRef.current = null
    setDragging(false)
    try {
      localStorage.setItem(PANEL_POS_KEY, JSON.stringify({ x: session.currentX, y: session.currentY }))
    } catch {
      // Storage unavailable (private mode): keep the position for this session only.
    }
  }

  const handleBadgePointerDown = (event: ReactPointerEvent<HTMLElement>): void => {
    if (event.button !== 0) return
    try {
      event.currentTarget.setPointerCapture(event.pointerId)
    } catch {
      return
    }
    suppressBadgeClickRef.current = false
    // Position math keys off the positioned pill (the div), not the inner
    // button, so the 4px inner padding never drifts into the position.
    const target = badgeRef.current ?? event.currentTarget
    const rect = target.getBoundingClientRect()
    dragSessionRef.current = {
      pointerX: event.clientX,
      pointerY: event.clientY,
      panelX: rect.left,
      panelY: rect.top,
      width: rect.width,
      currentX: rect.left,
      currentY: rect.top,
      initialPos: panelPos,
    }
    setDragging(true)
  }

  const handleBadgePointerEnd = (event: ReactPointerEvent<HTMLElement>): void => {
    const session = dragSessionRef.current
    if (session === null) return
    const moved = Math.hypot(
      event.clientX - session.pointerX,
      event.clientY - session.pointerY,
    ) >= BADGE_DRAG_THRESHOLD_PX
    dragSessionRef.current = null
    setDragging(false)
    if (moved) {
      // Real drag: the click that follows must not expand the panel.
      suppressBadgeClickRef.current = true
      try {
        localStorage.setItem(PANEL_POS_KEY, JSON.stringify({ x: session.currentX, y: session.currentY }))
      } catch {
        // Storage unavailable (private mode): keep the position for this session only.
      }
      return
    }
    // Sub-threshold gesture: revert any tiny drift and let the click expand.
    setPanelPos(session.initialPos)
  }
  const expanded = activityPanelExpandedForSession(open, openOwner, current)

  // This portal survives conversation route changes. Gate expansion by its
  // owning session during render, then clear stale state before paint. This
  // removes the old panel immediately instead of waiting for the no-team
  // autoclose grace period on the destination page.
  useLayoutEffect(() => {
    if (openOwner === undefined || openOwner === current) return
    setOpen(false)
    setOpenOwner(undefined)
    setWasActive(false)
    setAutoOpened(false)
  }, [current, openOwner])

  // The activity panel is a body portal, so announce its open state on body.
  // CSS can then make the conversation column yield space without knowing the
  // host shell's hashed module class names. Narrow viewports keep overlay mode.
  useLayoutEffect(() => {
    const root = document.documentElement
    if (expanded) root.setAttribute(PANEL_OPEN_ATTRIBUTE, '')
    else root.removeAttribute(PANEL_OPEN_ATTRIBUTE)
    return () => { root.removeAttribute(PANEL_OPEN_ATTRIBUTE) }
  }, [expanded])

  // Container-level ripple phase for the expanded panel: anchor the panel
  // aside once per mount so every member ring (including members added
  // later) inherits the same 1.8s grid value instead of aligning per
  // element. The panel aside mounts together with the expanded flag.
  useEffect(() => {
    if (!expanded) return
    const container = panelRef.current
    if (container !== null) {
      container.style.setProperty('--wave-phase', `${-(performance.now() % 1800)}ms`)
    }
  }, [expanded])

  useEffect(() => {
    let cancelled = false
    let inFlight = false
    const tick = async (): Promise<void> => {
      if (inFlight || cancelled) return
      inFlight = true
      try {
        const [liveResponse, archivedResponse] = await Promise.all([
          fetch(STATE_URL, { cache: 'no-store' }),
          fetch(`${STATE_URL}?archived=1`, { cache: 'no-store' }),
        ])
        if (liveResponse.ok) {
          const body = (await liveResponse.json()) as { teams?: unknown }
          if (!cancelled && Array.isArray(body.teams)) setTeams(body.teams as readonly ActivityTeam[])
        }
        if (archivedResponse.ok) {
          const body = (await archivedResponse.json()) as { teams?: unknown }
          if (!cancelled && Array.isArray(body.teams)) setArchivedTeams(body.teams as readonly ActivityTeam[])
        }
      } catch {
        // Host restarting; keep the last snapshot.
      } finally {
        inFlight = false
      }
    }
    tickRef.current = tick
    void tick()
    const timer = setInterval(() => { void tick() }, POLL_MS)
    return () => {
      cancelled = true
      clearInterval(timer)
    }
  }, [])

  // A viewport resize can strand a dragged/restored panel off-screen:
  // re-clamp against the measured rect (never while a drag is in flight).
  useEffect(() => {
    const reClamp = (): void => {
      if (dragSessionRef.current !== null) return
      const target = panelRef.current ?? badgeRef.current
      if (target === null) return
      setPanelPos((current) => current === null
        ? null
        : clampPanelPos(current.x, current.y, target.getBoundingClientRect().width))
    }
    window.addEventListener('resize', reClamp)
    return () => { window.removeEventListener('resize', reClamp) }
  }, [])

  useEffect(() => {
    const onOpenPanel = (event: Event): void => {
      const activeSession = currentRef.current
      if (activeSession === undefined) return
      setOpenOwner(activeSession)
      setOpen(true)
      const detail = (event as CustomEvent<AgentTeamsCardData>).detail
      if (detail?.teamId !== undefined) {
        // A card from a log that predates captainSessionId belongs to the
        // session that activated it (the current one at injection time).
        const owner = detail.captainSessionId !== '' ? detail.captainSessionId : currentRef.current ?? ''
        const teamKey = `${owner}:${detail.teamId}`
        setHistoric((previous) => {
          const next = new Map(previous)
          next.set(teamKey, { data: detail, owner })
          return next
        })
      }
    }
    window.addEventListener(OPEN_PANEL_EVENT, onOpenPanel)
    return () => {
      window.removeEventListener(OPEN_PANEL_EVENT, onOpenPanel)
    }
  }, [])

  // Teams follow the current session: live snapshots and historic card
  // summaries are visible only while their captain session is current.
  const visibleTeams = useMemo(
    // No current session (initial load): show nothing until one is picked,
    // so cross-session teams never leak into the floater.
    () => (current === undefined ? [] : teams.filter((team) => team.captainSessionId === current)),
    [teams, current],
  )
  const visibleHistoric = useMemo(
    () => (current === undefined ? [] : [...historic.values()].filter(({ data, owner }) =>
      owner === current && !teams.some((live) =>
        live.captainSessionId === current && live.teamId === data.teamId,
      ) && !archivedTeams.some((archived) =>
        archived.captainSessionId === current && archived.teamId === data.teamId,
      ),
    )),
    [historic, current, teams, archivedTeams],
  )
  const visibleArchived = useMemo(
    () => (current === undefined ? [] : archivedTeams.filter((team) =>
      team.captainSessionId === current && !teams.some((live) =>
        live.captainSessionId === current && live.teamId === team.teamId,
      ),
    )),
    [archivedTeams, current, teams],
  )
  const visibleCount = visibleTeams.length + visibleArchived.length + visibleHistoric.length

  /** POST a demand-brief decision, give instant button feedback, optimistically
   * update the card, then force an immediate snapshot refresh. */
  const decideBrief = async (team: ActivityTeam, action: 'confirm' | 'reject'): Promise<void> => {
    if (briefPending !== null) return
    const reason = action === 'reject' ? (window.prompt('驳回理由（可空）') ?? '') : ''
    setBriefPending(action)
    try {
      const response = await fetch('/plugins/dsh-agent-teams/brief', {
        method: 'POST',
        headers: { 'content-type': 'application/json' },
        body: JSON.stringify({ workspace: team.workspace, teamId: team.teamId, action, reason }),
      })
      if (!response.ok) {
        window.alert(`操作失败：HTTP ${response.status}`)
        return
      }
      // 乐观更新：就地改 brief 状态，React 立即重渲染（按钮消失、徽章即时变化），
      // 不等 1 秒轮询——点击确认「没反应」的根因就是缺这一步即时反馈。
      const nextStatus = action === 'confirm' ? 'confirmed' : 'rejected'
      const patch = (list: readonly ActivityTeam[]): readonly ActivityTeam[] => list.map(item =>
        item.teamId === team.teamId && item.brief !== undefined
          ? { ...item, brief: { ...item.brief, status: nextStatus, ...(action === 'reject' ? { rejectReason: reason } : {}) } }
          : item)
      setTeams(patch)
      setArchivedTeams(patch)
      void tickRef.current()
    } finally {
      setBriefPending(null)
    }
  }

  /** Close (archive) the current badge team: confirm, POST, then refresh so
   * the team moves into the archived section and the active badge shrinks. */
  const closeBadgeTeam = async (): Promise<void> => {
    const team = visibleTeams[0]
    if (team === undefined) return
    const name = visibleTeams.length === 1 ? team.name : '当前团队'
    if (!window.confirm(`关闭团队「${name}」？成员将被中断，团队移入历史归档。`)) return
    const response = await fetch('/plugins/dsh-agent-teams/team/archive', {
      method: 'POST',
      headers: { 'content-type': 'application/json' },
      body: JSON.stringify({ workspace: team.workspace, teamId: team.teamId, action: 'archive' }),
    })
    if (!response.ok) {
      window.alert(`关闭团队失败：HTTP ${response.status}`)
      return
    }
    void tickRef.current()
  }

  useEffect(() => {
    if (visibleCount > 0) {
      setWasActive(true)
      // Auto-expand only after the page-settle window: opening (and its
      // main-column yield) right after load reads as a whole-page flicker.
      const settled = performance.now() - mountedAtRef.current >= AUTO_OPEN_SETTLE_MS
      if (!autoOpened && settled) {
        setOpenOwner(current)
        setOpen(true)
        setAutoOpened(true)
      }
      return
    }
    if (!wasActive) return
    const timer = setTimeout(() => {
      setOpen(false)
      setOpenOwner(undefined)
      setWasActive(false)
      // Re-arm auto-expand: a later activity (new team, new session) may
      // open the panel on its own again.
      setAutoOpened(false)
    }, AUTOCLOSE_GRACE_MS)
    return () => { clearTimeout(timer) }
  }, [visibleCount, autoOpened, wasActive])

  const busy = useMemo(
    () => visibleTeams.some((team) => team.members.some((member) => member.activity === 'working')),
    [visibleTeams],
  )
  const hasTeams = visibleCount > 0

  if (!hasTeams && !expanded) return null

  return (
    <>
      {!expanded && (
        <CollapsedBadge
          teams={visibleTeams}
          count={visibleCount}
          busy={busy}
          pos={panelPos}
          dragging={dragging}
          nodeRef={(node) => { badgeRef.current = node }}
          onClick={() => {
            if (suppressBadgeClickRef.current) {
              suppressBadgeClickRef.current = false
              return
            }
            if (current === undefined) return
            setOpenOwner(current)
            setOpen(true)
          }}
          onExpand={() => {
            suppressBadgeClickRef.current = false
            if (current === undefined) return
            setOpenOwner(current)
            setOpen(true)
          }}
          onNavigate={navigateToSession}
          onClose={() => { void closeBadgeTeam() }}
          onPointerDown={handleBadgePointerDown}
          onPointerMove={handleDragPointerMove}
          onPointerUp={handleBadgePointerEnd}
          onPointerCancel={handleBadgePointerEnd}
        />
      )}
      {expanded && (
        <aside
          ref={panelRef}
          className={css.panel}
          data-agent-teams-activity
          data-dragging={dragging}
          style={panelPos === null ? undefined : positionedPanelStyle(panelPos)}
        >
          <header
            className={css.panelHead}
            onPointerDown={handlePanelHeadPointerDown}
            onPointerMove={handleDragPointerMove}
            onPointerUp={handlePanelHeadPointerEnd}
            onPointerCancel={handlePanelHeadPointerEnd}
            onLostPointerCapture={handlePanelHeadPointerEnd}
          >
            <span className={css.panelTitle}>
              AgentTeams 活动
              <span className={css.panelDot} data-busy={busy} aria-hidden />
            </span>
            <button
              type="button"
              className={css.closeButton}
              onClick={() => {
                setOpen(false)
                setOpenOwner(undefined)
              }}
              onPointerDown={(event) => { event.stopPropagation() }}
              aria-label="向上折叠"
              title="向上折叠"
            >
              <IconChevronUpOutline14 />
            </button>
          </header>
          <div className={css.teams}>
            {visibleCount === 0
              ? <span className={css.emptyHint}>暂无团队活动</span>
              : (
                <>
                  {visibleTeams.map((team) => (
                    <TeamSection
                      key={team.teamId}
                      team={team}
                      onNavigate={navigateToSession}
                      onBriefDecide={(action) => { void decideBrief(team, action) }}
                      briefPending={briefPending}
                    />
                  ))}
                  {visibleArchived.length > 0 && (
                    <section className={css.archivedSection} aria-label="历史归档">
                      <button
                        type="button"
                        className={css.sectionToggle}
                        onClick={() => { setArchivedExpanded((current) => !current) }}
                        aria-expanded={archivedExpanded}
                        title={archivedExpanded ? '收起历史归档' : '展开历史归档'}
                      >
                        {archivedExpanded ? <IconChevronDownOutline14 /> : <IconChevronRightOutline14 />}
                        <span className={css.sectionTitle}>历史归档 · {visibleArchived.length} 个团队</span>
                      </button>
                      {archivedExpanded && visibleArchived.map((team) => (
                        <div key={`${team.captainSessionId}:${team.teamId}`} data-team-id={team.teamId} data-historic className={css.archivedWrap}>
                          <TeamSection team={team} onNavigate={navigateToSession} historic />
                        </div>
                      ))}
                    </section>
                  )}
                  {visibleHistoric.map(({ data: team, owner }) => {
                    const teamKey = `${owner}:${team.teamId}`
                    return (
                    <section key={teamKey} className={css.team} data-team-id={team.teamId} data-historic>
                      <header className={css.teamHead}>
                        <span className={css.teamName} title={team.teamName}>
                          <img className={css.leadAvatar} src={LEAD_ART} alt="" aria-hidden /> {team.teamName}
                        </span>
                        <span className={css.historicPill}>历史归档</span>
                      </header>
                      <div className={css.members}>
                        {team.members.map((member) => (
                          <button
                            type="button"
                            key={member.id}
                            className={css.memberRow}
                            data-activity="idle"
                            onClick={() => { if (member.id !== '') navigateToSession(member.id as SessionId) }}
                          >
                            <span className={css.memberAvatar}>
                              {memberArtUrl(member.name, member.role) !== null ? (
                                <img className={css.memberArt} src={memberArtUrl(member.name, member.role) ?? ''} alt="" aria-hidden />
                              ) : (
                                <span className={css.memberInitial} style={{ background: accentOf(member.id) }}>{memberInitial(member.name)}</span>
                              )}
                            </span>
                            <span className={css.memberInfo}>
                              <span className={css.memberLine}>
                                <span className={css.memberName}>{member.name}</span>
                                {member.role !== '' && <span className={css.memberRole}>{member.role}</span>}
                              </span>
                            </span>
                          </button>
                        ))}
                      </div>
                    </section>
                    )
                  })}
                </>
              )}
          </div>
        </aside>
      )}
    </>
  )
}
