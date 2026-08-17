/** Карточка вакансии: описание, план интервью, отклики.
 *
 *  Три вкладки, потому что это три разных занятия. Описание правят один раз,
 *  план вопросов одобряют один раз, а в отклики заходят каждый день.
 *
 *  Порядок работы жёсткий и подсказывается на экране: вопросы → одобрение →
 *  публикация. Без одобренного плана база не даст опубликовать, и это
 *  правильно: интервью не из чего было бы вести.
 */
import { useState } from 'react'
import { Link, useParams } from 'react-router-dom'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { api, ApiError } from '../lib/api'
import { intlLocale, useI18n, useT, type Key } from '../lib/i18n'
import type {
  ApplicationRow,
  InterviewTurn,
  JobQuestion,
  JobRow,
  RevealedContact,
} from '../lib/types'
import { Badge, Button, Card, Empty, PageHead } from '../components/ui'
import { Modal, SCHEDULE_KEY, salaryText } from './Jobs'

type Detail = { job: JobRow & Record<string, unknown>; questions: JobQuestion[]; deep_link: string | null }

type Tab = 'about' | 'plan' | 'apps'

export default function JobDetail() {
  const t = useT()
  const { jobId = '' } = useParams()
  const [tab, setTab] = useState<Tab>('about')

  const { data, isPending } = useQuery<Detail>({
    queryKey: ['job', jobId],
    queryFn: () => api.get(`/api/jobs/${jobId}`),
  })

  if (isPending) {
    return (
      <Card>
        <p className="py-6 text-center text-[13px] text-ink-faint">{t('common.loadingShort')}</p>
      </Card>
    )
  }
  if (!data) {
    return (
      <Empty
        title={t('job.notFound')}
        action={
          <Link to="/jobs">
            <Button variant="outline">{t('job.toList')}</Button>
          </Link>
        }
      />
    )
  }

  const { job, questions, deep_link } = data
  const canPublish = job.interview_plan_status === 'approved' && questions.length >= 3

  return (
    <>
      <PageHead
        title={job.title}
        subtitle={`${job.specialty_name ?? job.role_category} · ${salaryText(job, t)} ${t('common.currency')}`}
        actions={
          <>
            <Link to="/jobs">
              <Button variant="ghost">{t('job.toList')}</Button>
            </Link>
            {job.status === 'active' ? (
              <CloseButton jobId={jobId} />
            ) : (
              <PublishButton jobId={jobId} disabled={!canPublish} />
            )}
          </>
        }
      />

      {job.status !== 'active' && !canPublish && (
        <div className="mb-4 rounded-[10px] border border-warn/25 bg-warn-soft px-4 py-3">
          <p className="text-[13px] font-medium text-warn">{t('job.notPublished')}</p>
          <p className="mt-0.5 text-[12.5px] leading-relaxed text-ink-muted">
            {t(
              questions.length === 0
                ? 'job.needQuestions'
                : questions.length < 3
                  ? 'job.needThree'
                  : 'job.needApprove',
            )}
          </p>
        </div>
      )}

      {job.status === 'active' && deep_link && <DeepLinkCard link={deep_link} code={job.public_code} />}

      <nav className="mb-4 flex gap-1 border-b border-line" role="tablist">
        {([
          ['about', t('job.tabAbout')],
          ['plan', `${t('job.tabPlan')}${questions.length ? ` · ${questions.length}` : ''}`],
          ['apps', `${t('job.tabApps')}${job.applications_count ? ` · ${job.applications_count}` : ''}`],
        ] as [Tab, string][]).map(([key, label]) => (
          <button
            key={key}
            role="tab"
            aria-selected={tab === key}
            onClick={() => setTab(key)}
            className={`spring -mb-px border-b-2 px-3 py-2 text-[13.5px] transition ${
              tab === key
                ? 'border-accent font-medium text-accent-ink'
                : 'border-transparent text-ink-muted hover:text-ink'
            }`}
          >
            {label}
          </button>
        ))}
      </nav>

      {tab === 'about' && <About job={job} />}
      {tab === 'plan' && <Plan jobId={jobId} questions={questions} planStatus={job.interview_plan_status} />}
      {tab === 'apps' && <Applications jobId={jobId} />}
    </>
  )
}

/* ── Описание ───────────────────────────────────────────────────────────── */

function About({ job }: { job: JobRow & Record<string, unknown> }) {
  const t = useT()
  const list = (v: unknown) => ((v as string[])?.length ? (v as string[]).join(', ') : '—')
  // До года показываем месяцами: «0.5 года» читается как опечатка.
  const months = job.experience_min_months as number | null
  const experience = !months
    ? '—'
    : months < 12
      ? t('job.months', { n: months })
      : t('job.years', { n: Math.floor(months / 12) })

  const rows: [Key, React.ReactNode][] = [
    ['job.aSpecialty', job.specialty_name ?? job.role_category],
    ['job.aExperience', experience],
    ['job.aSalary', `${salaryText(job, t)} ${t('common.currency')}`],
    [
      'job.aSchedule',
      job.schedule.length
        ? job.schedule.map((c) => (SCHEDULE_KEY[c] ? t(SCHEDULE_KEY[c]) : c)).join(', ')
        : '—',
    ],
    ['job.aCity', String(job.city ?? '—')],
    ['job.aDistricts', list(job.districts)],
    ['job.aSkills', list(job.required_skills)],
    ['job.aLanguages', list(job.required_languages)],
    ['job.aCredentials', list(job.credential_requirements)],
  ]
  return (
    <div className="grid gap-4 lg:grid-cols-[1fr_320px]">
      <Card>
        <dl className="grid gap-0">
          {rows.map(([k, v]) => (
            <div key={k} className="grid grid-cols-[140px_1fr] gap-3 border-b border-line py-2 last:border-b-0">
              <dt className="text-[12.5px] text-ink-muted">{t(k)}</dt>
              <dd className="text-[13.5px] text-ink">{v}</dd>
            </div>
          ))}
        </dl>
      </Card>
      {typeof job.source_text === 'string' && job.source_text && (
        <Card>
          <p className="mb-1.5 text-[12px] font-medium text-ink-muted">{t('job.aSourceText')}</p>
          <p className="whitespace-pre-wrap text-[13px] leading-relaxed text-ink-muted">
            {job.source_text}
          </p>
        </Card>
      )}
    </div>
  )
}

/* ── План интервью ──────────────────────────────────────────────────────── */

function Plan({
  jobId,
  questions,
  planStatus,
}: {
  jobId: string
  questions: JobQuestion[]
  planStatus: string
}) {
  const t = useT()
  const [draft, setDraft] = useState<{ question: string; intent: string | null; edited: boolean }[] | null>(null)
  const [error, setError] = useState<string | null>(null)
  const qc = useQueryClient()
  const items = draft ?? questions.map((q) => ({ question: q.question, intent: q.intent, edited: q.edited }))
  const dirty = draft !== null

  const refresh = () => qc.invalidateQueries({ queryKey: ['job', jobId] })

  const suggest = useMutation({
    mutationFn: () => api.post(`/api/jobs/${jobId}/plan/suggest`),
    onSuccess: () => { setDraft(null); setError(null); refresh() },
    onError: (e) => setError(e instanceof ApiError ? e.message : t('plan.errSuggest')),
  })
  const save = useMutation({
    mutationFn: () => api.put(`/api/jobs/${jobId}/plan`, { questions: items }),
    onSuccess: () => { setDraft(null); setError(null); refresh() },
    onError: (e) => setError(e instanceof ApiError ? e.message : t('plan.errSave')),
  })
  const approve = useMutation({
    mutationFn: () => api.post(`/api/jobs/${jobId}/plan/approve`),
    onSuccess: () => { setError(null); refresh() },
    onError: (e) => setError(e instanceof ApiError ? e.message : t('plan.errApprove')),
  })

  return (
    <div className="grid gap-4 lg:grid-cols-[1fr_300px]">
      <div>
        {items.length === 0 ? (
          <Empty
            title={t('plan.emptyTitle')}
            hint={t('plan.emptyHint')}
            action={
              <Button onClick={() => suggest.mutate()} loading={suggest.isPending}>
                {t('plan.suggest')}
              </Button>
            }
          />
        ) : (
          <Card>
            <ol className="grid gap-2.5">
              {items.map((q, i) => (
                <li key={i} className="grid gap-1">
                  <div className="flex items-center justify-between gap-2">
                    <span className="text-[11.5px] font-semibold text-ink-faint">
                      {t('plan.question', { n: i + 1 })}
                      {q.intent && <span className="ml-1.5 font-normal">· {q.intent}</span>}
                    </span>
                    <button
                      onClick={() => setDraft(items.filter((_, j) => j !== i))}
                      className="text-[12px] text-ink-faint transition hover:text-danger"
                    >
                      {t('plan.remove')}
                    </button>
                  </div>
                  <textarea
                    value={q.question}
                    rows={2}
                    onChange={(e) => {
                      const next = [...items]
                      next[i] = { ...q, question: e.target.value, edited: true }
                      setDraft(next)
                    }}
                    className="spring w-full rounded-[8px] border border-line bg-surface-card px-2.5 py-1.5
                               text-[13.5px] leading-relaxed transition hover:border-line-strong"
                  />
                </li>
              ))}
            </ol>

            <div className="mt-3 flex flex-wrap items-center gap-2 border-t border-line pt-3">
              <Button
                size="sm"
                variant="outline"
                onClick={() => setDraft([...items, { question: '', intent: null, edited: true }])}
              >
                {t('plan.addQuestion')}
              </Button>
              <Button
                size="sm"
                variant="ghost"
                onClick={() => suggest.mutate()}
                loading={suggest.isPending}
              >
                {t('plan.suggestAgain')}
              </Button>
              <span className="flex-1" />
              {dirty && (
                <Button size="sm" variant="ghost" onClick={() => setDraft(null)}>
                  {t('plan.discard')}
                </Button>
              )}
              <Button
                size="sm"
                variant={dirty ? 'primary' : 'outline'}
                onClick={() => save.mutate()}
                loading={save.isPending}
                disabled={items.some((q) => q.question.trim().length < 5)}
              >
                {t('plan.save')}
              </Button>
              <Button
                size="sm"
                onClick={() => approve.mutate()}
                loading={approve.isPending}
                disabled={dirty || items.length < 3 || planStatus === 'approved'}
              >
                {t(planStatus === 'approved' ? 'plan.approved' : 'plan.approve')}
              </Button>
            </div>
            {error && <p role="alert" className="mt-2 text-[12.5px] text-danger">{error}</p>}
            {dirty && (
              <p className="mt-2 text-[12px] text-ink-faint">{t('plan.dirtyHint')}</p>
            )}
          </Card>
        )}
      </div>

      <Card>
        <p className="text-[12px] font-medium text-ink-muted">{t('plan.howTitle')}</p>
        <ul className="mt-2 grid gap-2 text-[12.5px] leading-relaxed text-ink-muted">
          <li>{t('plan.how1')}</li>
          <li>{t('plan.how2')}</li>
          <li>{t('plan.how3')}</li>
          <li>{t('plan.how4')}</li>
          <li>{t('plan.how5')}</li>
        </ul>
        <p className="mt-3 border-t border-line pt-2.5 text-[11.5px] leading-relaxed text-ink-faint">
          {t('plan.note')}
        </p>
      </Card>
    </div>
  )
}

/* ── Отклики ────────────────────────────────────────────────────────────── */

function Applications({ jobId }: { jobId: string }) {
  const t = useT()
  const [openTranscript, setOpenTranscript] = useState<string | null>(null)
  const { data, isPending } = useQuery<{ applications: ApplicationRow[] }>({
    queryKey: ['job-apps', jobId],
    queryFn: () => api.get(`/api/jobs/${jobId}/applications`),
  })

  if (isPending) {
    return (
      <Card>
        <p className="py-6 text-center text-[13px] text-ink-faint">{t('common.loadingShort')}</p>
      </Card>
    )
  }
  const apps = data?.applications ?? []
  if (apps.length === 0) {
    return (
      <Empty
        title={t('apps.emptyTitle')}
        hint={t('apps.emptyHint')}
      />
    )
  }

  return (
    <div className="grid gap-3">
      {apps.map((a) => (
        <ApplicationCard
          key={a.application_id}
          app={a}
          jobId={jobId}
          onTranscript={() => a.interview_id && setOpenTranscript(a.interview_id)}
        />
      ))}
      {openTranscript && (
        <TranscriptModal
          jobId={jobId}
          interviewId={openTranscript}
          onClose={() => setOpenTranscript(null)}
        />
      )}
    </div>
  )
}

const APP_STATUS: Record<string, { label: Key; tone: 'neutral' | 'accent' | 'warn' | 'danger' }> = {
  sent: { label: 'apps.statusSent', tone: 'warn' },
  viewed: { label: 'apps.statusViewed', tone: 'neutral' },
  accepted: { label: 'apps.statusAccepted', tone: 'accent' },
  declined: { label: 'apps.statusDeclined', tone: 'neutral' },
  withdrawn: { label: 'apps.statusWithdrawn', tone: 'neutral' },
}

function ApplicationCard({
  app,
  jobId,
  onTranscript,
}: {
  app: ApplicationRow
  jobId: string
  onTranscript: () => void
}) {
  const { t, locale } = useI18n()
  const qc = useQueryClient()
  const setStatus = useMutation({
    mutationFn: (status: string) =>
      api.post(`/api/applications/${app.application_id}/status`, { status }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['job-apps', jobId] })
      qc.invalidateQueries({ queryKey: ['job', jobId] })
    },
  })

  const done = app.interview_status === 'completed'
  const st = APP_STATUS[app.status] ?? APP_STATUS.sent

  return (
    <Card>
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0">
          <div className="flex items-center gap-2">
            {/* Имя показываем, контакт — нет. Позвонить по имени нельзя, а
                «Кандидат 3f2a91c4» не читается как человек: менеджер работает
                с людьми, а не с идентификаторами. Телефон открывается отдельным
                действием после принятия отклика. */}
            <p className="text-[14px] font-medium text-ink">
              {app.candidate_name ?? t('apps.candidate', { id: app.application_id.slice(0, 8) })}
            </p>
            <Badge tone={st.tone}>{t(st.label)}</Badge>
            {done ? (
              <Badge tone="accent">{t('apps.ivDone')}</Badge>
            ) : app.interview_status === 'in_progress' ? (
              <Badge tone="warn">
                {t('apps.ivProgress', { answered: app.answered, total: app.total })}
              </Badge>
            ) : (
              <Badge>{t('apps.ivNone')}</Badge>
            )}
            {app.voice_answers > 0 && (
              <Badge tone="info">{t('apps.voice', { n: app.voice_answers })}</Badge>
            )}
            {app.follow_ups_asked > 0 && (
              <Badge>{t('apps.followUps', { n: app.follow_ups_asked })}</Badge>
            )}
          </div>
          <p className="mt-0.5 text-[12px] text-ink-faint">
            {t('apps.appliedAt', {
              when: new Date(app.applied_at).toLocaleString(intlLocale(locale), {
                dateStyle: 'medium',
                timeStyle: 'short',
              }),
            })}
            {app.experience_months
              ? ` · ${t('apps.experience', { n: Math.round(app.experience_months / 12) })}`
              : ''}
          </p>
        </div>
        <div className="flex gap-1.5">
          {app.interview_id && (
            <Button size="sm" variant="outline" onClick={onTranscript}>
              {t('apps.transcript')}
            </Button>
          )}
          {app.status !== 'accepted' && (
            <Button size="sm" onClick={() => setStatus.mutate('accepted')} loading={setStatus.isPending}>
              {t('apps.accept')}
            </Button>
          )}
          {app.status !== 'declined' && app.status !== 'accepted' && (
            <Button size="sm" variant="ghost" onClick={() => setStatus.mutate('declined')}>
              {t('apps.decline')}
            </Button>
          )}
        </div>
      </div>

      {app.summary && (
        <p className="mt-3 rounded-[8px] bg-surface-head px-3 py-2.5 text-[13px] leading-relaxed text-ink">
          {app.summary}
        </p>
      )}

      {(app.gaps?.length || app.follow_ups?.length) && (
        <div className="mt-2.5 grid gap-2.5 sm:grid-cols-2">
          {!!app.gaps?.length && (
            <div>
              <p className="mb-1 text-[11.5px] font-semibold uppercase tracking-[0.04em] text-ink-faint">
                {t('apps.gaps')}
              </p>
              <ul className="grid gap-1 text-[12.5px] leading-relaxed text-ink-muted">
                {app.gaps.map((g, i) => <li key={i}>· {g}</li>)}
              </ul>
            </div>
          )}
          {!!app.follow_ups?.length && (
            <div>
              <p className="mb-1 text-[11.5px] font-semibold uppercase tracking-[0.04em] text-ink-faint">
                {t('apps.followUpsTitle')}
              </p>
              <ul className="grid gap-1 text-[12.5px] leading-relaxed text-ink-muted">
                {app.follow_ups.map((g, i) => <li key={i}>· {g}</li>)}
              </ul>
            </div>
          )}
        </div>
      )}

      {app.status === 'accepted' && <ContactRow applicationId={app.application_id} />}
    </Card>
  )
}

/** Открытие контакта принятого кандидата.
 *
 *  Кнопка, а не автоматический показ. Разница не косметическая: функция базы
 *  пишет каждое открытие в журнал согласий, и кандидат вправе узнать, кто
 *  посмотрел его телефон. Автопоказ означал бы запись «открыл» всякий раз,
 *  когда менеджер просто пролистывал список.
 */
function ContactRow({ applicationId }: { applicationId: string }) {
  const t = useT()
  const [contact, setContact] = useState<RevealedContact | null>(null)
  const reveal = useMutation<RevealedContact>({
    mutationFn: () => api.post(`/api/applications/${applicationId}/contact`, {}),
    onSuccess: (data) => setContact(data),
  })

  if (contact) {
    const tg = contact.telegram_username
    return (
      <div className="mt-3 grid gap-1 rounded-[8px] bg-accent-soft px-3 py-2.5 text-[13px] text-accent-ink">
        <p className="font-medium">
          {contact.phone ? (
            <a className="underline" href={`tel:${contact.phone}`}>{contact.phone}</a>
          ) : (
            t('apps.contactNoPhone')
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
        <p className="text-[11.5px] opacity-80">{t('apps.contactLogged')}</p>
      </div>
    )
  }

  return (
    <div className="mt-3 rounded-[8px] bg-accent-soft px-3 py-2.5">
      <p className="text-[12.5px] text-accent-ink">{t('apps.acceptedNote')}</p>
      <div className="mt-2 flex flex-wrap items-center gap-2">
        <Button size="sm" onClick={() => reveal.mutate()} loading={reveal.isPending}>
          {t('apps.revealContact')}
        </Button>
        {reveal.isError && (
          <span className="text-[12px] text-danger">{(reveal.error as Error).message}</span>
        )}
      </div>
    </div>
  )
}

function TranscriptModal({
  jobId,
  interviewId,
  onClose,
}: {
  jobId: string
  interviewId: string
  onClose: () => void
}) {
  const t = useT()
  const { data, isPending } = useQuery<{ turns: InterviewTurn[] }>({
    queryKey: ['transcript', interviewId],
    queryFn: () => api.get(`/api/jobs/${jobId}/applications/${interviewId}/transcript`),
  })

  return (
    <Modal title={t('apps.trTitle')} onClose={onClose} wide>
      {isPending ? (
        <p className="py-6 text-center text-[13px] text-ink-faint">{t('common.loadingShort')}</p>
      ) : (
        <ol className="grid gap-3">
          {(data?.turns ?? []).map((turn) => (
            <li key={turn.ord} className="grid gap-1">
              <p className="text-[13px] font-medium text-ink">
                {turn.kind === 'follow_up' && (
                  <span className="mr-1.5 text-[11px] font-semibold uppercase text-ink-faint">
                    {t('apps.trFollowUp')}
                  </span>
                )}
                {turn.question_text}
              </p>
              {turn.answer_kind === 'skipped' ? (
                <p className="text-[13px] text-ink-faint">{t('apps.trSkipped')}</p>
              ) : turn.answer_text ? (
                <p className="rounded-[8px] bg-surface-head px-3 py-2 text-[13px] leading-relaxed text-ink-muted">
                  {turn.answer_text}
                  {turn.answer_kind === 'voice' && (
                    <span className="ml-2 text-[11.5px] text-ink-faint">
                      {turn.voice_seconds
                        ? t('apps.trVoiceSec', { n: turn.voice_seconds })
                        : t('apps.trVoice')}
                    </span>
                  )}
                </p>
              ) : (
                <p className="text-[13px] text-ink-faint">{t('apps.trNoAnswer')}</p>
              )}
            </li>
          ))}
        </ol>
      )}
      <p className="mt-4 border-t border-line pt-3 text-[11.5px] leading-relaxed text-ink-faint">
        {t('apps.trNote')}
      </p>
    </Modal>
  )
}

/* ── Публикация ─────────────────────────────────────────────────────────── */

function PublishButton({ jobId, disabled }: { jobId: string; disabled: boolean }) {
  const t = useT()
  const qc = useQueryClient()
  const publish = useMutation({
    mutationFn: () => api.post(`/api/jobs/${jobId}/publish`),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['job', jobId] })
      qc.invalidateQueries({ queryKey: ['jobs'] })
    },
  })
  return (
    <Button onClick={() => publish.mutate()} loading={publish.isPending} disabled={disabled}>
      {t('job.publish')}
    </Button>
  )
}

function CloseButton({ jobId }: { jobId: string }) {
  const t = useT()
  const qc = useQueryClient()
  const close = useMutation({
    mutationFn: () => api.post(`/api/jobs/${jobId}/close`),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['job', jobId] })
      qc.invalidateQueries({ queryKey: ['jobs'] })
    },
  })
  return (
    <Button variant="outline" onClick={() => close.mutate()} loading={close.isPending}>
      {t('job.closeHiring')}
    </Button>
  )
}

function DeepLinkCard({ link, code }: { link: string; code: string }) {
  const t = useT()
  const [copied, setCopied] = useState(false)
  return (
    <div className="mb-4 rounded-[10px] border border-line bg-surface-card px-4 py-3">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div className="min-w-0">
          <p className="text-[12px] font-medium text-ink-muted">{t('job.linkTitle')}</p>
          <p className="mt-0.5 break-all font-mono text-[12.5px] text-ink">{link}</p>
        </div>
        <div className="flex gap-1.5">
          <Button
            size="sm"
            variant="soft"
            onClick={async () => {
              await navigator.clipboard.writeText(link)
              setCopied(true)
              setTimeout(() => setCopied(false), 1800)
            }}
          >
            {copied ? t('common.copied') : t('common.copy')}
          </Button>
          <a
            href={`/api/jobs/${code}/qr.svg`}
            target="_blank"
            rel="noreferrer"
            className="spring inline-flex h-7.5 items-center justify-center rounded-[8px] border
                       border-line-strong bg-surface-card px-3 text-[13px] font-medium text-ink
                       transition hover:bg-surface-container"
          >
            {t('job.linkQr')}
          </a>
        </div>
      </div>
      <p className="mt-2 text-[11.5px] leading-relaxed text-ink-faint">
        {t('job.linkHint')}
      </p>
    </div>
  )
}
