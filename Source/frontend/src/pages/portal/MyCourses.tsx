import { useQuery } from '@tanstack/react-query'
import { Link } from 'react-router-dom'
import { api } from '../../lib/api'
import { intlLocale, useI18n } from '../../lib/i18n'
import type { EmployeeCard, MyCourse } from '../../lib/types'
import { Badge, Button, Card, Empty, PageHead } from '../../components/ui'

type Feed = { employee: EmployeeCard; courses: MyCourse[] }

/** Мои курсы — портал сотрудника.
 *
 *  Не таблица, а карточки: у человека их три-пять, а не сто, и ему нужно не
 *  сравнивать столбцы, а понять «что мне сделать и до какого числа». Поэтому
 *  срок и кнопка действия — самое заметное на карточке.
 */
export default function MyCourses() {
  const { t, locale } = useI18n()

  const { data, isPending, error } = useQuery<Feed>({
    queryKey: ['my-courses'],
    queryFn: () => api.get('/api/portal/courses'),
  })

  if (isPending) return <PageHead title={t('my.coursesTitle')} subtitle={t('common.loading')} />
  if (error) {
    // Единственная реальная причина — учётная запись не привязана к сотруднику.
    // Показываем текст сервера: он объясняет, что делать, лучше общего «ошибка».
    return (
      <>
        <PageHead title={t('my.coursesTitle')} />
        <Empty title={t('my.noCard')} hint={(error as Error).message} />
      </>
    )
  }

  const courses = data?.courses ?? []
  const pending = courses.filter((c) => c.status !== 'passed').length

  return (
    <>
      <PageHead
        title={t('my.coursesTitle')}
        subtitle={
          pending > 0
            ? t('my.coursesPending', { n: pending })
            : t('my.coursesAllDone')
        }
      />

      {data?.employee && (
        <p className="mb-4 text-[13px] text-ink-muted">
          {data.employee.full_name}
          {data.employee.unit_name && ` · ${data.employee.unit_name}`}
          {data.employee.role_name && ` · ${data.employee.role_name}`}
        </p>
      )}

      {courses.length === 0 ? (
        <Empty title={t('my.emptyTitle')} hint={t('my.emptyHint')} />
      ) : (
        <div className="grid gap-3">
          {courses.map((c) => {
            const overdue =
              c.status !== 'passed' && c.due_at !== null && new Date(c.due_at) < new Date()
            return (
              <Card key={c.assignment_id}>
                <div className="flex flex-wrap items-start justify-between gap-3">
                  <div className="min-w-0">
                    <div className="flex flex-wrap items-center gap-2">
                      <h2 className="text-[15px] font-semibold tracking-[-0.01em]">{c.title}</h2>
                      {c.status === 'passed' && (
                        <Badge tone="accent">{t('my.stPassed', { n: c.best_score ?? 0 })}</Badge>
                      )}
                      {c.status === 'failed' && (
                        <Badge tone="danger">{t('my.stFailed', { n: c.best_score ?? 0 })}</Badge>
                      )}
                      {c.status === 'in_progress' && (
                        <Badge tone="warn">{t('my.stInProgress')}</Badge>
                      )}
                      {c.status === 'assigned' && <Badge tone="info">{t('my.stNew')}</Badge>}
                    </div>
                    {c.summary && (
                      <p className="mt-1 max-w-2xl text-[13px] leading-relaxed text-ink-muted">
                        {c.summary}
                      </p>
                    )}
                    <p className="mt-1.5 text-[12.5px] text-ink-faint">
                      {t('my.meta', {
                        lessons: c.lessons_count,
                        questions: c.questions_count,
                        pass: c.pass_score,
                      })}
                      {c.attempts > 0 && ` · ${t('my.attempts', { n: c.attempts })}`}
                    </p>
                    {c.due_at && (
                      <p
                        className={`mt-1 text-[12.5px] ${
                          overdue ? 'font-medium text-danger' : 'text-ink-muted'
                        }`}
                      >
                        {t(overdue ? 'my.overdue' : 'my.due', {
                          date: new Date(c.due_at).toLocaleDateString(intlLocale(locale), {
                            day: 'numeric',
                            month: 'long',
                          }),
                        })}
                      </p>
                    )}
                  </div>
                  <Link to={`/my/courses/${c.course_id}`}>
                    <Button variant={c.status === 'passed' ? 'outline' : 'primary'}>
                      {t(
                        c.status === 'passed'
                          ? 'my.open'
                          : c.status === 'assigned'
                            ? 'my.start'
                            : 'my.continue',
                      )}
                    </Button>
                  </Link>
                </div>
              </Card>
            )
          })}
        </div>
      )}
    </>
  )
}
