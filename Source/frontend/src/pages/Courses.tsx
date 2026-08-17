import { useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { api } from '../lib/api'
import { intlLocale, useI18n, useT } from '../lib/i18n'
import type { AssignmentRow, CourseDetail, CourseRow, CourseSummary } from '../lib/types'
import {
  Badge, Button, Card, Empty, Metric, PageHead, Table, Td, Th, Toolbar, Tr,
} from '../components/ui'

type Listing = { courses: CourseRow[]; summary: CourseSummary }

/** Курсы клиники.
 *
 *  Раздел отвечает на один вопрос: кто ещё не прошёл. Поэтому у каждого курса
 *  на виду не описание, а разбивка по статусам, а внутри — материал, тест с
 *  отмеченными верными ответами и таблица людей.
 *
 *  Редактора курса на пилоте нет: курсы заводятся сидом. Ответ «кто не прошёл»
 *  нужен раньше, чем конструктор вопросов, и честная кнопка «нельзя» лучше
 *  формы, которая ничего не сохраняет.
 */
export default function Courses() {
  const t = useT()
  const [openId, setOpenId] = useState<string | null>(null)

  const { data, isPending } = useQuery<Listing>({
    queryKey: ['courses'],
    queryFn: () => api.get('/api/courses'),
  })

  if (isPending) return <PageHead title={t('courses.title')} subtitle={t('common.loading')} />

  const rows = data?.courses ?? []
  const s = data?.summary

  return (
    <>
      <PageHead title={t('courses.title')} subtitle={t('courses.subtitle')} />

      {rows.length === 0 ? (
        <Empty title={t('courses.emptyTitle')} hint={t('courses.emptyHint')} />
      ) : (
        <>
          <div className="mb-4 grid gap-2 sm:grid-cols-2 lg:grid-cols-4">
            <Metric label={t('courses.mPublished')} value={s?.published ?? 0} />
            <Metric
              label={t('courses.mAssigned')} value={s?.assigned ?? 0}
              hint={t('courses.mAssignedHint')}
            />
            <Metric
              label={t('courses.mPassed')} value={s?.passed ?? 0}
              hint={t('courses.mPassedHint', {
                n: (s?.assigned ?? 0) - (s?.passed ?? 0),
              })}
            />
            <Metric
              label={t('courses.mFailed')} value={s?.failed ?? 0}
              tone={(s?.failed ?? 0) > 0 ? 'danger' : undefined}
              hint={t('courses.mFailedHint')}
            />
          </div>

          <div className="grid gap-3">
            {rows.map((c) => (
              <CourseCard
                key={c.id}
                course={c}
                open={openId === c.id}
                onToggle={() => setOpenId(openId === c.id ? null : c.id)}
              />
            ))}
          </div>
        </>
      )}
    </>
  )
}

function CourseCard({
  course,
  open,
  onToggle,
}: {
  course: CourseRow
  open: boolean
  onToggle: () => void
}) {
  const t = useT()
  const done = course.assigned > 0 ? Math.round((course.passed * 100) / course.assigned) : 0

  return (
    <Card>
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0">
          <div className="flex items-center gap-2">
            <h2 className="text-[15px] font-semibold tracking-[-0.01em]">{course.title}</h2>
            {course.status === 'published' ? (
              <Badge tone="accent">{t('courses.stPublished')}</Badge>
            ) : course.status === 'draft' ? (
              <Badge tone="info">{t('courses.stDraft')}</Badge>
            ) : (
              <Badge>{t('courses.stArchived')}</Badge>
            )}
          </div>
          {course.summary && (
            <p className="mt-1 max-w-2xl text-[13px] leading-relaxed text-ink-muted">
              {course.summary}
            </p>
          )}
          <p className="mt-1.5 text-[12.5px] text-ink-faint">
            {t('courses.meta', {
              lessons: course.lessons_count,
              questions: course.questions_count,
              pass: course.pass_score,
            })}
            {course.role_name ? ` · ${course.role_name}` : ` · ${t('courses.forEveryone')}`}
          </p>
        </div>
        <Button variant="outline" size="sm" onClick={onToggle}>
          {t(open ? 'courses.hide' : 'courses.show')}
        </Button>
      </div>

      {/* Полоса прогресса, а не пирог: нужно одно число — сколько людей уже
          прошли, — и оно должно читаться, не наводя мышь. */}
      <div className="mt-3">
        <div className="mb-1 flex justify-between text-[12px] text-ink-muted">
          <span>{t('courses.progress', { done: course.passed, all: course.assigned })}</span>
          <span className="font-medium">{done}%</span>
        </div>
        <div className="h-1.5 overflow-hidden rounded-full bg-surface-container">
          <div className="h-full rounded-full bg-accent transition-[width]" style={{ width: `${done}%` }} />
        </div>
        <div className="mt-2 flex flex-wrap gap-1.5">
          {course.failed > 0 && (
            <Badge tone="danger">{t('courses.cntFailed', { n: course.failed })}</Badge>
          )}
          {course.in_progress > 0 && (
            <Badge tone="warn">{t('courses.cntInProgress', { n: course.in_progress })}</Badge>
          )}
          {course.not_started > 0 && (
            <Badge tone="info">{t('courses.cntNotStarted', { n: course.not_started })}</Badge>
          )}
          {course.avg_score !== null && (
            <Badge>{t('courses.cntAvg', { n: course.avg_score })}</Badge>
          )}
        </div>
      </div>

      {open && <CourseInside courseId={course.id} />}
    </Card>
  )
}

function CourseInside({ courseId }: { courseId: string }) {
  const t = useT()
  const [tab, setTab] = useState<'material' | 'test' | 'people'>('people')

  const { data, isPending } = useQuery<CourseDetail>({
    queryKey: ['course', courseId],
    queryFn: () => api.get(`/api/courses/${courseId}`),
  })

  if (isPending) return <p className="mt-4 text-[13px] text-ink-faint">{t('common.loading')}</p>
  if (!data) return null

  return (
    <div className="mt-4 border-t border-line pt-3">
      <Toolbar>
        {(
          [
            ['people', t('courses.tabPeople')],
            ['material', t('courses.tabMaterial')],
            ['test', t('courses.tabTest')],
          ] as ['people' | 'material' | 'test', string][]
        ).map(([key, label]) => (
          <button
            key={key}
            onClick={() => setTab(key)}
            className={`spring rounded-[8px] px-3 py-1.5 text-[13px] transition ${
              tab === key
                ? 'bg-accent text-white'
                : 'border border-line bg-surface-card text-ink-muted hover:text-ink'
            }`}
          >
            {label}
          </button>
        ))}
      </Toolbar>

      {tab === 'people' && <People rows={data.assignments} />}

      {tab === 'material' && (
        <div className="grid gap-3">
          {data.lessons.map((l) => (
            <div key={l.id} className="rounded-[10px] border border-line bg-surface-head p-3.5">
              <p className="mb-1 text-[11.5px] uppercase tracking-[0.04em] text-ink-faint">
                {t('courses.lessonN', { n: l.ord })}
              </p>
              <h3 className="mb-1.5 text-[14px] font-semibold">{l.title}</h3>
              <p className="whitespace-pre-line text-[13.5px] leading-relaxed text-ink-muted">
                {l.content}
              </p>
            </div>
          ))}
        </div>
      )}

      {tab === 'test' && (
        <div className="grid gap-3">
          <p className="text-[12.5px] text-ink-faint">{t('courses.testHint')}</p>
          {data.questions.map((q) => (
            <div key={q.id} className="rounded-[10px] border border-line bg-surface-head p-3.5">
              <p className="mb-2 text-[13.5px] font-medium">
                {q.ord}. {q.text}
              </p>
              <ul className="grid gap-1">
                {q.options.map((o) => (
                  <li
                    key={o.id}
                    className={`rounded-[7px] px-2.5 py-1.5 text-[13px] ${
                      o.is_correct
                        ? 'bg-accent-soft font-medium text-accent-ink'
                        : 'text-ink-muted'
                    }`}
                  >
                    {o.text}
                  </li>
                ))}
              </ul>
              {q.explanation && (
                <p className="mt-2 text-[12.5px] leading-relaxed text-ink-faint">
                  {t('courses.explanation')}: {q.explanation}
                </p>
              )}
            </div>
          ))}
        </div>
      )}
    </div>
  )
}

/** Таблица людей по курсу. Сортировка с сервера: сначала не прошедшие. */
export function People({ rows, showCourse }: { rows: AssignmentRow[]; showCourse?: boolean }) {
  const { t, locale } = useI18n()
  if (rows.length === 0) return <Empty title={t('courses.noPeople')} hint={t('courses.noPeopleHint')} />

  const date = (v: string | null) =>
    v ? new Date(v).toLocaleDateString(intlLocale(locale), { day: 'numeric', month: 'short' }) : '—'

  return (
    <Table>
      <thead>
        <tr>
          <Th>{t('courses.thEmployee')}</Th>
          {showCourse && <Th>{t('courses.thCourse')}</Th>}
          <Th>{t('courses.thUnit')}</Th>
          <Th align="center" width="120px">{t('courses.thStatus')}</Th>
          <Th align="center" width="80px">{t('courses.thScore')}</Th>
          <Th align="center" width="80px">{t('courses.thAttempts')}</Th>
          <Th align="center" width="100px">{t('courses.thDue')}</Th>
        </tr>
      </thead>
      <tbody>
        {rows.map((a) => {
          const overdue =
            a.status !== 'passed' && a.due_at !== null && new Date(a.due_at) < new Date()
          return (
            <Tr key={a.id}>
              <Td className="font-medium">{a.employee_name}</Td>
              {showCourse && <Td className="text-ink-muted">{a.course_title}</Td>}
              <Td className="text-ink-muted">{a.unit_name ?? '—'}</Td>
              <Td align="center"><StatusBadge status={a.status} /></Td>
              <Td align="center" className="font-mono text-[12.5px]">
                {a.best_score === null ? (
                  <span className="text-ink-faint">—</span>
                ) : (
                  <span className={a.best_score >= a.pass_score ? '' : 'text-danger'}>
                    {a.best_score}%
                  </span>
                )}
              </Td>
              <Td align="center" className="text-[12.5px] text-ink-muted">{a.attempts}</Td>
              <Td align="center" className="text-[12.5px]">
                <span className={overdue ? 'font-medium text-danger' : 'text-ink-faint'}>
                  {date(a.due_at)}
                </span>
              </Td>
            </Tr>
          )
        })}
      </tbody>
    </Table>
  )
}

export function StatusBadge({ status }: { status: AssignmentRow['status'] }) {
  const t = useT()
  if (status === 'passed') return <Badge tone="accent">{t('courses.stPassed')}</Badge>
  if (status === 'failed') return <Badge tone="danger">{t('courses.stFailed')}</Badge>
  if (status === 'in_progress') return <Badge tone="warn">{t('courses.stInProgress')}</Badge>
  return <Badge tone="info">{t('courses.stAssigned')}</Badge>
}
