/** Подбор: поиск по базе медиков, совпадения под вакансию, приглашения.
 *
 *  Три вкладки, потому что это три разных вопроса менеджера. «Кто вообще есть» —
 *  поиск с фильтрами. «Кто подходит под эту вакансию» — расчёт с объяснением
 *  каждого совпадения. «Кого я уже позвал» — приглашения и их судьба.
 *
 *  Карточки анонимны, и это не решение интерфейса: `product.pool_candidates`
 *  физически не отдаёт ни имени, ни телефона. Контакт открывается кнопкой после
 *  того, как человек принял приглашение, и каждое открытие пишется в журнал
 *  согласий. Автопоказ означал бы запись «посмотрел телефон» на каждое
 *  пролистывание списка.
 *
 *  Балла (`score_internal`) в интерфейсе нет. Он нужен, чтобы отсортировать, но
 *  показывать его — значит утверждать, что 62 объективно лучше 58. Клиника видит
 *  уровень, причины и пробелы, а решение принимает сама.
 */
import { useMemo, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { api } from '../lib/api'
import { intlLocale, useI18n, useT, type Key } from '../lib/i18n'
import {
  Badge, Button, Card, Empty, Field, Metric, PageHead, Select, Toolbar,
} from '../components/ui'
import { Modal, money } from './Jobs'
import type {
  Dict, InvitationRow, MatchDictionaries, MatchRow, MatchableJob,
  PoolCandidate, RecomputeResult, RevealedContact, SpecialtyDict,
} from '../lib/types'

type Tab = 'pool' | 'job' | 'invites'

type JobsPayload = { jobs: MatchableJob[]; dictionaries: MatchDictionaries }
type PoolPayload = { candidates: PoolCandidate[]; total: number }

/** Человека без имени всё равно надо как-то называть. Восемь символов uuid
 *  читаются и не создают ложного впечатления, что мы знаем, кто это. */
function shortId(id: string): string {
  return id.slice(0, 8)
}

function years(months: number | null | undefined): number | null {
  if (months === null || months === undefined) return null
  return Math.round(months / 12)
}

/** Код причины или пробела в человеческую фразу.
 *
 *  В базе лежат коды вида `experience:48`, потому что матч видят обе стороны,
 *  каждая на своём языке. Разбираем здесь, а не в SQL.
 */
function codeText(
  code: string,
  kind: 'r' | 'g',
  t: (key: Key, vars?: Record<string, string | number>) => string,
): string {
  const [name, ...rest] = code.split(':')
  const value = rest.join(':')
  const key = `match.${kind}.${name}` as Key
  const text = t(key, { v: value })
  // Неизвестный код виден как [ключ] — это лучше, чем пустая строка: значит,
  // алгоритм научился новой причине, а словарь об этом не знает.
  return text
}

export default function Matching() {
  const t = useT()
  const [tab, setTab] = useState<Tab>('pool')

  const { data, isPending } = useQuery<JobsPayload>({
    queryKey: ['matching-jobs'],
    queryFn: () => api.get('/api/matching/jobs'),
  })

  const jobs = data?.jobs ?? []
  const dicts = data?.dictionaries

  const tabs: [Tab, Key][] = [
    ['pool', 'match.tabPool'],
    ['job', 'match.tabJob'],
    ['invites', 'match.tabInvites'],
  ]

  return (
    <>
      <PageHead title={t('match.title')} subtitle={t('match.subtitle')} />

      <div className="mb-4 flex flex-wrap gap-1.5">
        {tabs.map(([id, label]) => (
          <Button
            key={id}
            size="sm"
            variant={tab === id ? 'soft' : 'ghost'}
            onClick={() => setTab(id)}
          >
            {t(label)}
          </Button>
        ))}
      </div>

      <p className="mb-4 rounded-[8px] bg-surface-head px-3 py-2.5 text-[12.5px] leading-relaxed text-ink-muted">
        {t('match.anonymous')}
      </p>

      {isPending || !dicts ? (
        <Card>
          <p className="py-6 text-center text-[13px] text-ink-faint">{t('common.loadingShort')}</p>
        </Card>
      ) : tab === 'pool' ? (
        <PoolSearch dicts={dicts} jobs={jobs} />
      ) : tab === 'job' ? (
        <JobMatching jobs={jobs} />
      ) : (
        <Invitations />
      )}
    </>
  )
}

/* ── Поиск по базе ──────────────────────────────────────────────────────── */

type Filters = {
  role_category: string
  specialty: string
  district: string
  schedule: string
  experience_min: string
  salary_max: string
}

const EMPTY_FILTERS: Filters = {
  role_category: '', specialty: '', district: '',
  schedule: '', experience_min: '', salary_max: '',
}

function dictOptions(rows: Dict[], locale: string) {
  return rows.map((r) => ({
    value: r.code,
    label: locale === 'uz' ? r.name_uz : r.name_ru,
  }))
}

function PoolSearch({ dicts, jobs }: { dicts: MatchDictionaries; jobs: MatchableJob[] }) {
  const { t, locale } = useI18n()
  const [filters, setFilters] = useState<Filters>(EMPTY_FILTERS)
  const [inviting, setInviting] = useState<string | null>(null)

  const query = useMemo(() => {
    const params = new URLSearchParams()
    // Опыт вводится в годах — так думает менеджер, а база хранит месяцы.
    if (filters.role_category) params.set('role_category', filters.role_category)
    if (filters.specialty) params.set('specialty', filters.specialty)
    if (filters.district) params.set('district', filters.district)
    if (filters.schedule) params.set('schedule', filters.schedule)
    if (filters.experience_min) params.set('experience_min', String(Number(filters.experience_min) * 12))
    if (filters.salary_max) params.set('salary_max', filters.salary_max)
    return params.toString()
  }, [filters])

  const { data, isPending } = useQuery<PoolPayload>({
    queryKey: ['matching-pool', query],
    queryFn: () => api.get(`/api/matching/pool${query ? `?${query}` : ''}`),
  })

  // Специальности сужаем по выбранной профессии: список из 25 позиций, где
  // половина не относится к делу, читается хуже, чем короткий.
  const specialties: SpecialtyDict[] = filters.role_category
    ? dicts.specialties.filter((s) => s.role_category === filters.role_category)
    : dicts.specialties

  const rows = data?.candidates ?? []
  const dirty = Object.values(filters).some(Boolean)

  return (
    <>
      <Card className="mb-4">
        <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
          <Select
            label={t('match.filterRole')}
            value={filters.role_category}
            onChange={(v) => setFilters((f) => ({ ...f, role_category: v, specialty: '' }))}
            options={dictOptions(dicts.roles, locale)}
          />
          <Select
            label={t('match.filterSpecialty')}
            value={filters.specialty}
            onChange={(v) => setFilters((f) => ({ ...f, specialty: v }))}
            options={specialties.map((s) => ({
              value: s.code,
              label: locale === 'uz' ? s.name_uz : s.name_ru,
            }))}
          />
          <Select
            label={t('match.filterDistrict')}
            value={filters.district}
            onChange={(v) => setFilters((f) => ({ ...f, district: v }))}
            options={dictOptions(dicts.districts, locale)}
          />
          <Select
            label={t('match.filterSchedule')}
            value={filters.schedule}
            onChange={(v) => setFilters((f) => ({ ...f, schedule: v }))}
            options={dictOptions(dicts.schedules, locale)}
          />
          <Field
            label={t('match.filterExperience')}
            type="number"
            min={0}
            max={60}
            value={filters.experience_min}
            onChange={(e) => setFilters((f) => ({ ...f, experience_min: e.target.value }))}
          />
          <Field
            label={t('match.filterSalary')}
            type="number"
            min={0}
            step={500000}
            value={filters.salary_max}
            onChange={(e) => setFilters((f) => ({ ...f, salary_max: e.target.value }))}
          />
        </div>
        {dirty && (
          <Toolbar className="mb-0 mt-3">
            <Button size="sm" variant="ghost" onClick={() => setFilters(EMPTY_FILTERS)}>
              {t('match.filterReset')}
            </Button>
            <span className="text-[12.5px] text-ink-faint">
              {t('match.found', { n: data?.total ?? 0 })}
            </span>
          </Toolbar>
        )}
      </Card>

      {isPending ? (
        <Card>
          <p className="py-6 text-center text-[13px] text-ink-faint">{t('common.loadingShort')}</p>
        </Card>
      ) : rows.length === 0 ? (
        <Empty
          title={t(dirty ? 'match.emptyFilteredTitle' : 'match.emptyPoolTitle')}
          hint={t(dirty ? 'match.emptyFilteredHint' : 'match.emptyPoolHint')}
        />
      ) : (
        <div className="grid gap-3">
          {rows.map((c) => (
            <PoolCard
              key={c.candidate_id}
              c={c}
              onInvite={() => setInviting(c.candidate_id)}
            />
          ))}
        </div>
      )}

      {inviting && (
        <InviteDialog
          candidateId={inviting}
          jobs={jobs}
          onClose={() => setInviting(null)}
        />
      )}
    </>
  )
}

function PoolCard({ c, onInvite }: { c: PoolCandidate; onInvite: () => void }) {
  const { t, locale } = useI18n()
  const y = years(c.experience_months)
  const role = locale === 'uz' ? c.role_name_uz : c.role_name_ru
  const spec = locale === 'uz' ? c.specialty_name_uz : c.specialty_name_ru

  return (
    <Card>
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0">
          <div className="flex flex-wrap items-center gap-2">
            <p className="text-[14px] font-medium text-ink">
              {spec || role || t('match.candidate', { id: shortId(c.candidate_id) })}
            </p>
            <Badge>{t('match.candidate', { id: shortId(c.candidate_id) })}</Badge>
            {c.has_contact ? (
              <Badge tone="accent">{t('match.hasPhone')}</Badge>
            ) : (
              <Badge tone="warn">{t('match.noPhone')}</Badge>
            )}
          </div>
          <p className="mt-0.5 text-[12.5px] text-ink-faint">
            {y === null ? t('match.experienceNone') : t('match.experience', { n: y })}
            {' · '}
            {c.salary_min_uzs
              ? t('match.salaryFrom', { value: money(Number(c.salary_min_uzs)) })
              : t('match.salaryNone')}
          </p>
        </div>
        <Button size="sm" onClick={onInvite}>{t('match.invite')}</Button>
      </div>
      <CardFacts
        districts={c.districts}
        schedule={c.schedule}
        skills={c.skills}
        languages={c.languages}
        credentials={c.credential_claims}
      />
    </Card>
  )
}

/** Факты карточки одним блоком: их состав одинаков в поиске и в подборе, а
 *  два похожих куска разметки однажды разойдутся. */
function CardFacts({
  districts, schedule, skills, languages, credentials,
}: {
  districts: string[]
  schedule: string[]
  skills: string[]
  languages: string[]
  credentials: string[]
}) {
  const t = useT()
  const items: [Key, string[]][] = [
    ['match.districtsLabel', districts],
    ['match.scheduleLabel', schedule],
    ['match.skillsLabel', skills],
    ['match.languagesLabel', languages],
    ['match.credentialsLabel', credentials],
  ]
  const shown = items.filter(([, v]) => v && v.length)
  if (!shown.length) return null

  return (
    <dl className="mt-2.5 grid gap-1.5 text-[12.5px] sm:grid-cols-2">
      {shown.map(([label, values]) => (
        <div key={label} className="flex gap-1.5">
          <dt className="shrink-0 text-ink-faint">{t(label)}:</dt>
          <dd className="min-w-0 text-ink-muted">{values.slice(0, 6).join(', ')}</dd>
        </div>
      ))}
    </dl>
  )
}

/* ── Подбор под вакансию ────────────────────────────────────────────────── */

function JobMatching({ jobs }: { jobs: MatchableJob[] }) {
  const t = useT()
  const qc = useQueryClient()
  const [jobId, setJobId] = useState<string>(jobs[0]?.job_id ?? '')
  const [summary, setSummary] = useState<RecomputeResult | null>(null)

  const job = jobs.find((j) => j.job_id === jobId)

  const { data, isPending } = useQuery<{ matches: MatchRow[] }>({
    queryKey: ['matching-matches', jobId],
    queryFn: () => api.get(`/api/matching/jobs/${jobId}/matches`),
    enabled: !!jobId,
  })

  const recompute = useMutation<RecomputeResult>({
    mutationFn: () => api.post(`/api/matching/jobs/${jobId}/recompute`, {}),
    onSuccess: (result) => {
      setSummary(result)
      qc.invalidateQueries({ queryKey: ['matching-matches', jobId] })
      qc.invalidateQueries({ queryKey: ['matching-jobs'] })
    },
  })

  if (!jobs.length) {
    return <Empty title={t('match.noJobsTitle')} hint={t('match.noJobsHint')} />
  }

  const matches = data?.matches ?? []
  const canInvite = job?.status === 'active' && job?.interview_plan_status === 'approved'

  return (
    <>
      <Card className="mb-4">
        <div className="grid gap-3 sm:grid-cols-[1fr_auto] sm:items-end">
          <Select
            label={t('match.pickJob')}
            value={jobId}
            onChange={(v) => { setJobId(v); setSummary(null) }}
            options={jobs.map((j) => ({ value: j.job_id, label: j.title }))}
          />
          <Button
            onClick={() => recompute.mutate()}
            loading={recompute.isPending}
            disabled={!jobId}
          >
            {recompute.isPending
              ? t('match.recomputing')
              : t(matches.length ? 'match.recomputeAgain' : 'match.recompute')}
          </Button>
        </div>
        {!canInvite && (
          <p className="mt-3 rounded-[8px] bg-warn-soft px-3 py-2 text-[12.5px] text-warn">
            {t('match.inviteNeedsPublished')}
          </p>
        )}
        {recompute.isError && (
          <p className="mt-3 text-[12.5px] text-danger">{(recompute.error as Error).message}</p>
        )}
      </Card>

      {summary && (
        <div className="mb-4 grid gap-3 grid-cols-2 sm:grid-cols-4">
          <Metric label={t('match.metricShown')} value={summary.matches.length} />
          <Metric label={t('match.strong')} value={summary.strong} />
          <Metric label={t('match.wantsMore')} value={summary.wants_more_money} tone="warn" />
          <Metric label={t('match.metricExcluded')} value={summary.excluded_total} />
        </div>
      )}

      {summary && summary.excluded_total > 0 && <ExcludedNote excluded={summary.excluded} />}

      {isPending ? (
        <Card>
          <p className="py-6 text-center text-[13px] text-ink-faint">{t('common.loadingShort')}</p>
        </Card>
      ) : matches.length === 0 ? (
        <Empty
          title={t(summary ? 'match.noMatchesTitle' : 'match.emptyMatchesTitle')}
          hint={t(summary ? 'match.noMatchesHint' : 'match.emptyMatchesHint')}
        />
      ) : (
        <div className="grid gap-3">
          {matches.map((m) => (
            <MatchCard key={m.match_id} m={m} jobId={jobId} canInvite={!!canInvite} />
          ))}
        </div>
      )}
    </>
  )
}

/** Почему в подборе мало людей. Без этой сводки пустой список читается как
 *  поломка кабинета, а не как слишком узкие требования. */
function ExcludedNote({ excluded }: { excluded: Record<string, number> }) {
  const t = useT()
  const parts = Object.entries(excluded)
    .filter(([, n]) => n > 0)
    .map(([code, n]) => t(`match.x.${code}` as Key, { n }))
  if (!parts.length) return null

  return (
    <Card className="mb-4">
      <p className="text-[12.5px] leading-relaxed text-ink-muted">{parts.join(' · ')}</p>
    </Card>
  )
}

const INVITE_TONE: Record<string, 'neutral' | 'accent' | 'warn' | 'danger'> = {
  sent: 'warn',
  accepted: 'accent',
  declined: 'neutral',
  expired: 'neutral',
  withdrawn: 'neutral',
}

function MatchCard({ m, jobId, canInvite }: { m: MatchRow; jobId: string; canInvite: boolean }) {
  const { t } = useI18n()
  const [inviting, setInviting] = useState(false)
  const y = years(m.experience_months)
  const wantsMore = m.gaps.some((g) => g.startsWith('salary:'))

  return (
    <Card>
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0">
          <div className="flex flex-wrap items-center gap-2">
            <p className="text-[14px] font-medium text-ink">
              {m.specialty_name || m.role_name || t('match.candidate', { id: shortId(m.candidate_id) })}
            </p>
            <Badge tone={m.level === 'strong' ? 'accent' : 'neutral'}>
              {t(m.level === 'strong' ? 'match.strong' : 'match.possible')}
            </Badge>
            {wantsMore && <Badge tone="warn">{t('match.wantsMore')}</Badge>}
            <Badge tone="info">
              {t(m.self_filled ? 'match.selfFilled' : 'match.fromInterview')}
            </Badge>
            {m.invitation_status && (
              <Badge tone={INVITE_TONE[m.invitation_status] ?? 'neutral'}>
                {t(`match.status.${m.invitation_status}` as Key)}
              </Badge>
            )}
            {m.has_application && <Badge tone="accent">{t('match.interviewDone')}</Badge>}
          </div>
          <p className="mt-0.5 text-[12.5px] text-ink-faint">
            {t('match.candidate', { id: shortId(m.candidate_id) })}
            {' · '}
            {y === null ? t('match.experienceNone') : t('match.experience', { n: y })}
            {' · '}
            {m.salary_min_uzs
              ? t('match.salaryFrom', { value: money(Number(m.salary_min_uzs)) })
              : t('match.salaryNone')}
          </p>
        </div>
        {!m.invitation_id && canInvite && (
          <Button size="sm" onClick={() => setInviting(true)}>{t('match.invite')}</Button>
        )}
      </div>

      {/* Причины и пробелы рядом: менеджер решает, а для решения нужны обе
          колонки. Показывать только плюсы — это продавать, а не подбирать. */}
      <div className="mt-3 grid gap-2.5 sm:grid-cols-2">
        <div>
          <p className="mb-1 text-[11.5px] font-semibold uppercase tracking-[0.04em] text-ink-faint">
            {t('match.reasons')}
          </p>
          <ul className="grid gap-1 text-[12.5px] leading-relaxed text-ink-muted">
            {m.reasons.map((r) => <li key={r}>· {codeText(r, 'r', t)}</li>)}
          </ul>
        </div>
        {m.gaps.length > 0 && (
          <div>
            <p className="mb-1 text-[11.5px] font-semibold uppercase tracking-[0.04em] text-ink-faint">
              {t('match.gaps')}
            </p>
            <ul className="grid gap-1 text-[12.5px] leading-relaxed text-ink-muted">
              {m.gaps.map((g) => <li key={g}>· {codeText(g, 'g', t)}</li>)}
            </ul>
          </div>
        )}
      </div>

      <CardFacts
        districts={m.districts}
        schedule={m.schedule}
        skills={m.skills}
        languages={m.languages}
        credentials={m.credential_claims}
      />

      {m.invitation_id && m.invitation_status === 'accepted' && (
        <ContactRow invitationId={m.invitation_id} />
      )}

      {inviting && (
        <InviteDialog
          candidateId={m.candidate_id}
          fixedJobId={jobId}
          onClose={() => setInviting(false)}
        />
      )}
    </Card>
  )
}

/* ── Приглашение ────────────────────────────────────────────────────────── */

function InviteDialog({
  candidateId, jobs, fixedJobId, onClose,
}: {
  candidateId: string
  jobs?: MatchableJob[]
  fixedJobId?: string
  onClose: () => void
}) {
  const t = useT()
  const qc = useQueryClient()
  const [jobId, setJobId] = useState(fixedJobId ?? '')
  const [message, setMessage] = useState('')

  // Приглашать можно только по опубликованной вакансии с одобренным планом:
  // приглашение ведёт человека в собеседование. Отфильтровываем здесь, чтобы
  // менеджер не выбирал то, что база всё равно отвергнет.
  const eligible = (jobs ?? []).filter(
    (j) => j.status === 'active' && j.interview_plan_status === 'approved',
  )

  const send = useMutation({
    mutationFn: () =>
      api.post('/api/matching/invitations', {
        job_id: jobId,
        candidate_id: candidateId,
        message: message.trim() || null,
      }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['matching-matches'] })
      qc.invalidateQueries({ queryKey: ['matching-invitations'] })
      qc.invalidateQueries({ queryKey: ['matching-jobs'] })
      onClose()
    },
  })

  return (
    <Modal title={t('match.inviteDialogTitle')} onClose={onClose}>
      <div className="grid gap-3">
        {!fixedJobId && (
          <>
            <Select
              label={t('match.pickJob')}
              value={jobId}
              onChange={setJobId}
              options={eligible.map((j) => ({ value: j.job_id, label: j.title }))}
              placeholder={t('match.inviteNeedsJob')}
            />
            {eligible.length === 0 && (
              <p className="rounded-[8px] bg-warn-soft px-3 py-2 text-[12.5px] text-warn">
                {t('match.inviteNeedsPublished')}
              </p>
            )}
          </>
        )}
        <div className="grid gap-1">
          <label className="text-[12.5px] font-medium text-ink-muted">
            {t('match.inviteMessage')}
          </label>
          <textarea
            value={message}
            onChange={(e) => setMessage(e.target.value)}
            maxLength={600}
            rows={4}
            className="spring w-full rounded-[8px] border border-line bg-surface-card px-3 py-2
                       text-[13.5px] text-ink transition hover:border-line-strong"
          />
          <p className="text-[12.5px] text-ink-faint">{t('match.inviteMessageHint')}</p>
        </div>
        {send.isError && (
          <p className="text-[12.5px] text-danger">{(send.error as Error).message}</p>
        )}
        <div className="flex justify-end gap-2">
          <Button variant="ghost" onClick={onClose}>{t('common.cancel')}</Button>
          <Button onClick={() => send.mutate()} loading={send.isPending} disabled={!jobId}>
            {t('match.inviteSend')}
          </Button>
        </div>
      </div>
    </Modal>
  )
}

/* ── Приглашения ────────────────────────────────────────────────────────── */

function Invitations() {
  const { t, locale } = useI18n()
  const { data, isPending } = useQuery<{ invitations: InvitationRow[] }>({
    queryKey: ['matching-invitations'],
    queryFn: () => api.get('/api/matching/invitations'),
  })

  if (isPending) {
    return (
      <Card>
        <p className="py-6 text-center text-[13px] text-ink-faint">{t('common.loadingShort')}</p>
      </Card>
    )
  }

  const rows = data?.invitations ?? []
  if (!rows.length) {
    return <Empty title={t('match.emptyInvitesTitle')} hint={t('match.emptyInvitesHint')} />
  }

  return (
    <div className="grid gap-3">
      {rows.map((inv) => (
        <Card key={inv.invitation_id}>
          <div className="flex flex-wrap items-start justify-between gap-3">
            <div className="min-w-0">
              <div className="flex flex-wrap items-center gap-2">
                <p className="text-[14px] font-medium text-ink">{inv.job_title}</p>
                <Badge tone={INVITE_TONE[inv.invitation_status] ?? 'neutral'}>
                  {t(`match.status.${inv.invitation_status}` as Key)}
                </Badge>
                {inv.has_application && <Badge tone="accent">{t('match.interviewDone')}</Badge>}
              </div>
              <p className="mt-0.5 text-[12.5px] text-ink-faint">
                {inv.specialty_name ?? t('match.candidate', { id: shortId(inv.candidate_id) })}
                {' · '}
                {t('match.invitedAt', {
                  when: new Date(inv.sent_at).toLocaleString(intlLocale(locale), {
                    dateStyle: 'medium',
                    timeStyle: 'short',
                  }),
                })}
              </p>
            </div>
          </div>
          {inv.message && (
            <p className="mt-3 rounded-[8px] bg-surface-head px-3 py-2.5 text-[13px] leading-relaxed text-ink">
              {inv.message}
            </p>
          )}
          {inv.invitation_status === 'accepted' && (
            <ContactRow invitationId={inv.invitation_id} />
          )}
        </Card>
      ))}
    </div>
  )
}

/** Открытие контакта принявшего приглашение.
 *
 *  Кнопка, а не автопоказ: функция базы пишет каждое открытие в журнал
 *  согласий, и человек вправе узнать, кто смотрел его телефон. Автопоказ
 *  означал бы запись «открыл» на каждое пролистывание списка.
 */
function ContactRow({ invitationId }: { invitationId: string }) {
  const t = useT()
  const [contact, setContact] = useState<RevealedContact | null>(null)
  const reveal = useMutation<RevealedContact>({
    mutationFn: () => api.post(`/api/matching/invitations/${invitationId}/contact`, {}),
    onSuccess: setContact,
  })

  if (contact) {
    const tg = contact.telegram_username
    return (
      <div className="mt-3 grid gap-1 rounded-[8px] bg-accent-soft px-3 py-2.5 text-[13px] text-accent-ink">
        <p className="font-medium">
          {contact.phone ? (
            <a className="underline" href={`tel:${contact.phone}`}>{contact.phone}</a>
          ) : (
            t('match.contactNoPhone')
          )}
          {tg && (
            <>
              {' · '}
              <a className="underline" href={`https://t.me/${tg}`} target="_blank" rel="noreferrer">
                @{tg}
              </a>
            </>
          )}
        </p>
        <p className="text-[11.5px] opacity-80">{t('match.contactLogged')}</p>
      </div>
    )
  }

  return (
    <div className="mt-3 flex flex-wrap items-center gap-2">
      <Button size="sm" onClick={() => reveal.mutate()} loading={reveal.isPending}>
        {t('match.revealContact')}
      </Button>
      {reveal.isError && (
        <span className="text-[12px] text-danger">{(reveal.error as Error).message}</span>
      )}
    </div>
  )
}
