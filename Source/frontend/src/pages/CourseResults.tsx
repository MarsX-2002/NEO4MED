import { useMemo, useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { api } from '../lib/api'
import { useT } from '../lib/i18n'
import type { AssignmentRow, CourseRow, CourseSummary } from '../lib/types'
import { Empty, Field, Metric, PageHead, Select, Toolbar } from '../components/ui'
import { People } from './Courses'

type Listing = { courses: CourseRow[]; summary: CourseSummary }

/** Результаты обучения: один плоский список прохождений по всей клинике.
 *
 *  Отдельно от «Курсов» потому, что вопросы разные. Там — «что за курс и как
 *  идёт», здесь — «кто именно не прошёл», сквозь все курсы сразу. По умолчанию
 *  показываем именно должников: список из ста сдавших не требует внимания.
 */
export default function CourseResults() {
  const t = useT()
  const [courseFilter, setCourseFilter] = useState('')
  const [search, setSearch] = useState('')
  const [onlyPending, setOnlyPending] = useState(true)

  const { data: listing } = useQuery<Listing>({
    queryKey: ['courses'],
    queryFn: () => api.get('/api/courses'),
  })
  const { data, isPending } = useQuery<{ assignments: AssignmentRow[] }>({
    queryKey: ['course-results'],
    queryFn: () => api.get('/api/courses/results'),
  })

  const rows = useMemo(() => {
    const all = data?.assignments ?? []
    const q = search.trim().toLowerCase()
    return all.filter(
      (a) =>
        (!courseFilter || a.course_id === courseFilter) &&
        (!onlyPending || a.status !== 'passed') &&
        (!q || a.employee_name.toLowerCase().includes(q)),
    )
  }, [data, courseFilter, onlyPending, search])

  if (isPending) return <PageHead title={t('results.title')} subtitle={t('common.loading')} />

  const all = data?.assignments ?? []
  const s = listing?.summary
  const overdue = all.filter(
    (a) => a.status !== 'passed' && a.due_at !== null && new Date(a.due_at) < new Date(),
  ).length

  return (
    <>
      <PageHead title={t('results.title')} subtitle={t('results.subtitle')} />

      {all.length === 0 ? (
        <Empty title={t('results.emptyTitle')} hint={t('results.emptyHint')} />
      ) : (
        <>
          <div className="mb-4 grid gap-2 sm:grid-cols-2 lg:grid-cols-4">
            <Metric label={t('results.mTotal')} value={all.length} />
            <Metric label={t('results.mPassed')} value={s?.passed ?? 0} />
            <Metric
              label={t('results.mPending')} value={all.length - (s?.passed ?? 0)}
              tone={all.length - (s?.passed ?? 0) > 0 ? 'warn' : undefined}
              hint={t('results.mPendingHint')}
            />
            <Metric
              label={t('results.mOverdue')} value={overdue}
              tone={overdue > 0 ? 'danger' : undefined}
              hint={t('results.mOverdueHint')}
            />
          </div>

          <Toolbar>
            <div className="w-[240px]">
              <Field
                placeholder={t('results.search')}
                value={search}
                onChange={(e) => setSearch(e.target.value)}
              />
            </div>
            <div className="w-[280px]">
              <Select
                value={courseFilter}
                onChange={setCourseFilter}
                placeholder={t('results.allCourses')}
                options={(listing?.courses ?? []).map((c) => ({ value: c.id, label: c.title }))}
              />
            </div>
            <label className="flex items-center gap-1.5 text-[12.5px] text-ink-muted">
              <input
                type="checkbox"
                checked={onlyPending}
                onChange={(e) => setOnlyPending(e.target.checked)}
                className="size-3.5 accent-accent"
              />
              {t('results.onlyPending')}
            </label>
            <span className="ml-auto text-[12.5px] text-ink-faint">
              {t('common.rows', { n: rows.length })}
            </span>
          </Toolbar>

          {rows.length === 0 ? (
            <Empty title={t('results.allDone')} hint={t('results.allDoneHint')} />
          ) : (
            <People rows={rows} showCourse />
          )}
        </>
      )}
    </>
  )
}
