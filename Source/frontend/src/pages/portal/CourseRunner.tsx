import { useState } from 'react'
import { Link, useParams } from 'react-router-dom'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { api } from '../../lib/api'
import { useT } from '../../lib/i18n'
import type {
  AttemptQuestion,
  AttemptResult,
  MyCourseDetail,
} from '../../lib/types'
import { Badge, Button, Card, Empty, PageHead } from '../../components/ui'

function optionText(
  questions: AttemptQuestion[] | undefined,
  questionId: string,
  optionId: string | null,
): string | null {
  if (!optionId) return null
  const q = questions?.find((x) => x.question_id === questionId)
  return q?.options.find((o) => o.id === optionId)?.text ?? null
}

/** Прохождение курса: сначала материал, потом тест.
 *
 *  Два шага, а не один экран со всем сразу. Уроки открываются по одному —
 *  человек видит, где он и сколько осталось, — и кнопка «к тесту» появляется
 *  только на последнем. Это не защита от пролистывания: пролистать можно, и
 *  запрещать бессмысленно. Это подсказка о порядке.
 *
 *  Правильных ответов на этом экране нет ни в одном состоянии: вопросы
 *  приходят из product.attempt_questions без флага, а проверяет их
 *  product.grade_attempt в базе. Разбор с верными вариантами появляется только
 *  после отправки — вместе с пояснениями, потому что тест не только проверяет.
 */
export default function CourseRunner() {
  const t = useT()
  const { courseId = '' } = useParams()
  const qc = useQueryClient()

  const [stage, setStage] = useState<'material' | 'test'>('material')
  const [lesson, setLesson] = useState(0)
  const [answers, setAnswers] = useState<Record<string, string>>({})
  const [attempt, setAttempt] = useState<{ id: string; questions: AttemptQuestion[] } | null>(null)
  const [result, setResult] = useState<AttemptResult | null>(null)

  const { data, isPending, error } = useQuery<MyCourseDetail>({
    queryKey: ['my-course', courseId],
    queryFn: () => api.get(`/api/portal/courses/${courseId}`),
  })

  const start = useMutation({
    mutationFn: () =>
      api.post<{ attempt_id: string; questions: AttemptQuestion[] }>(
        `/api/portal/courses/${courseId}/attempt`,
      ),
    onSuccess: (r) => {
      setAttempt({ id: r.attempt_id, questions: r.questions })
      setAnswers({})
      setResult(null)
      setStage('test')
    },
  })

  const submit = useMutation({
    mutationFn: () =>
      api.post<AttemptResult>(`/api/portal/attempts/${attempt?.id}/submit`, { answers }),
    onSuccess: (r) => {
      setResult(r)
      // Список курсов и карточку перечитываем: изменились статус и лучший балл.
      qc.invalidateQueries({ queryKey: ['my-courses'] })
      qc.invalidateQueries({ queryKey: ['my-course', courseId] })
    },
  })

  if (isPending) return <PageHead title={t('common.loading')} />
  if (error || !data) {
    return (
      <>
        <PageHead title={t('my.coursesTitle')} />
        <Empty title={t('my.notAssigned')} hint={(error as Error | null)?.message} />
        <div className="mt-3">
          <Link to="/my/courses"><Button variant="outline">{t('my.back')}</Button></Link>
        </div>
      </>
    )
  }

  const lessons = data.lessons
  const current = lessons[Math.min(lesson, Math.max(lessons.length - 1, 0))]
  const last = lesson >= lessons.length - 1

  return (
    <>
      <PageHead
        title={data.title}
        subtitle={t('my.meta', {
          lessons: lessons.length,
          questions: data.questions_count,
          pass: data.pass_score,
        })}
        actions={
          <Link to="/my/courses">
            <Button variant="ghost">{t('my.back')}</Button>
          </Link>
        }
      />

      {data.best_score !== null && (
        <p className="mb-4 text-[13px] text-ink-muted">
          {t('my.bestScore', { n: data.best_score })}
          {data.status === 'passed' && ` · ${t('my.alreadyPassed')}`}
        </p>
      )}

      {/* ── Материал ─────────────────────────────────────────────────────── */}
      {stage === 'material' && (
        <>
          <div className="mb-3 flex flex-wrap gap-1.5">
            {lessons.map((l, i) => (
              <button
                key={l.id}
                onClick={() => setLesson(i)}
                className={`spring rounded-[8px] px-2.5 py-1 text-[12.5px] transition ${
                  i === lesson
                    ? 'bg-accent text-white'
                    : i < lesson
                      ? 'bg-accent-soft text-accent-ink'
                      : 'border border-line text-ink-muted hover:text-ink'
                }`}
              >
                {i + 1}
              </button>
            ))}
          </div>

          {current ? (
            <Card>
              <p className="mb-1 text-[11.5px] uppercase tracking-[0.04em] text-ink-faint">
                {t('my.lessonOf', { n: lesson + 1, all: lessons.length })}
              </p>
              <h2 className="mb-3 text-[17px] font-semibold tracking-[-0.015em]">{current.title}</h2>
              <div className="whitespace-pre-line text-[14px] leading-[1.7] text-ink">
                {current.content}
              </div>

              <div className="mt-5 flex flex-wrap items-center gap-2 border-t border-line pt-4">
                {lesson > 0 && (
                  <Button variant="outline" onClick={() => setLesson((v) => v - 1)}>
                    {t('my.prevLesson')}
                  </Button>
                )}
                {!last ? (
                  <Button onClick={() => setLesson((v) => v + 1)}>{t('my.nextLesson')}</Button>
                ) : (
                  <Button loading={start.isPending} onClick={() => start.mutate()}>
                    {t('my.toTest')}
                  </Button>
                )}
                {last && (
                  <span className="text-[12.5px] text-ink-faint">
                    {t('my.toTestHint', { pass: data.pass_score })}
                  </span>
                )}
              </div>
              {start.isError && (
                <p role="alert" className="mt-3 rounded-[8px] bg-danger-soft px-3 py-2 text-[12.5px] text-danger">
                  {(start.error as Error).message}
                </p>
              )}
            </Card>
          ) : (
            <Empty title={t('my.noMaterial')} hint={t('my.noMaterialHint')} />
          )}
        </>
      )}

      {/* ── Тест ─────────────────────────────────────────────────────────── */}
      {stage === 'test' && attempt && !result && (
        <Card>
          <p className="mb-4 text-[13px] text-ink-muted">
            {t('my.testIntro', { n: attempt.questions.length, pass: data.pass_score })}
          </p>

          <div className="grid gap-4">
            {attempt.questions.map((q) => (
              <fieldset key={q.question_id} className="grid gap-1.5">
                <legend className="mb-1 text-[14px] font-medium">
                  {q.ord}. {q.text}
                </legend>
                {q.options.map((o) => (
                  <label
                    key={o.id}
                    className={`spring flex cursor-pointer items-start gap-2 rounded-[8px] border px-3 py-2
                                text-[13.5px] transition ${
                                  answers[q.question_id] === o.id
                                    ? 'border-accent bg-accent-soft text-accent-ink'
                                    : 'border-line hover:border-line-strong'
                                }`}
                  >
                    <input
                      type="radio"
                      name={q.question_id}
                      value={o.id}
                      checked={answers[q.question_id] === o.id}
                      onChange={() => setAnswers((a) => ({ ...a, [q.question_id]: o.id }))}
                      className="mt-0.5 size-3.5 accent-accent"
                    />
                    <span>{o.text}</span>
                  </label>
                ))}
              </fieldset>
            ))}
          </div>

          <div className="mt-5 flex flex-wrap items-center gap-2 border-t border-line pt-4">
            <Button
              loading={submit.isPending}
              disabled={Object.keys(answers).length < attempt.questions.length}
              onClick={() => submit.mutate()}
            >
              {t('my.submitTest')}
            </Button>
            <Button variant="ghost" onClick={() => setStage('material')}>
              {t('my.backToMaterial')}
            </Button>
            <span className="text-[12.5px] text-ink-faint">
              {t('my.answered', {
                n: Object.keys(answers).length,
                all: attempt.questions.length,
              })}
            </span>
          </div>
          {submit.isError && (
            <p role="alert" className="mt-3 rounded-[8px] bg-danger-soft px-3 py-2 text-[12.5px] text-danger">
              {(submit.error as Error).message}
            </p>
          )}
        </Card>
      )}

      {/* ── Результат и разбор ───────────────────────────────────────────── */}
      {result && (
        <Card>
          <div className="flex flex-wrap items-center gap-3 border-b border-line pb-4">
            <span
              className={`text-[30px] font-semibold tracking-[-0.02em] ${
                result.passed ? 'text-accent' : 'text-danger'
              }`}
            >
              {result.score}%
            </span>
            <div>
              <p className="text-[14px] font-medium">
                {t(result.passed ? 'my.resultPassed' : 'my.resultFailed')}
              </p>
              <p className="text-[12.5px] text-ink-muted">
                {t('my.resultCounts', {
                  n: result.correct_count,
                  all: result.total_count,
                  pass: data.pass_score,
                })}
              </p>
            </div>
            {!result.passed && (
              <Button
                className="ml-auto"
                loading={start.isPending}
                onClick={() => start.mutate()}
              >
                {t('my.retake')}
              </Button>
            )}
            {result.passed && (
              <Link to="/my/courses" className="ml-auto">
                <Button variant="outline">{t('my.toCourses')}</Button>
              </Link>
            )}
          </div>

          <div className="mt-4 grid gap-3">
            {result.review.map((r) => {
              // Разбор из БД приходит идентификаторами вариантов, а не текстом:
              // функция отдаёт ровно то, что нужно для проверки. Подписи берём
              // из вопросов этой же попытки — они уже в состоянии экрана.
              const correct = optionText(attempt?.questions, r.question_id, r.correct_id)
              return (
                <div
                  key={r.question_id}
                  className={`rounded-[10px] border p-3.5 ${
                    r.is_right ? 'border-line bg-surface-head' : 'border-danger/30 bg-danger-soft/40'
                  }`}
                >
                  <div className="mb-1.5 flex items-start justify-between gap-2">
                    <p className="text-[13.5px] font-medium">
                      {r.ord}. {r.text}
                    </p>
                    {r.is_right ? (
                      <Badge tone="accent">{t('my.right')}</Badge>
                    ) : (
                      <Badge tone="danger">{t('my.wrong')}</Badge>
                    )}
                  </div>
                  {!r.is_right && correct && (
                    <p className="text-[13px] text-ink-muted">
                      {t('my.correctWas')}: <b className="font-medium text-ink">{correct}</b>
                    </p>
                  )}
                  {r.explanation && (
                    <p className="mt-1 text-[12.5px] leading-relaxed text-ink-faint">
                      {r.explanation}
                    </p>
                  )}
                </div>
              )
            })}
          </div>
        </Card>
      )}
    </>
  )
}
