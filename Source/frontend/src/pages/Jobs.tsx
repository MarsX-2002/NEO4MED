/** Вакансии клиники.
 *
 *  Смысл публикации — авто-интервью, поэтому у вакансии три состояния и они
 *  видны в таблице: черновик, план вопросов одобрен, опубликована. Публиковать
 *  без одобренного плана нельзя — иначе кандидат откликнется, начнёт
 *  собеседование, а спрашивать будет нечего. Это правило держит база, здесь
 *  мы только показываем, чего не хватает.
 */
import { useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { api, ApiError } from '../lib/api'
import { useT, type Key } from '../lib/i18n'
import type { JobFields, JobRow, JobSummary } from '../lib/types'
import {
  Badge, Button, Card, Empty, Field, Metric, PageHead, Select, Table, Td, Th, Toolbar, Tr,
} from '../components/ui'

type Listing = { jobs: JobRow[]; summary: JobSummary; bot_username: string }
type Dicts = { roles: { code: string }[]; specialties: { code: string; name_ru: string; role_category: string }[] }

/** Код графика → ключ словаря. Подписи переводятся в месте показа. */
/** Коды графика из `product.schedule_kinds`.
 *
 *  Список обязан совпадать со словарём в базе. Раньше здесь был `weekend`,
 *  которого в словаре нет, и не было `day` с `rotational`, которые в нём есть:
 *  вакансия с «выходными» проходила проверку API, но подбор по графику её ни с
 *  кем не сводил — у кандидата такого кода взяться негде. Неизвестный код
 *  показываем как есть, чтобы старые данные не превращались в пустоту.
 */
export const SCHEDULE_KEY: Record<string, Key> = {
  day: 'schedule.day',
  night: 'schedule.night',
  shift: 'schedule.shift',
  full_time: 'schedule.full_time',
  part_time: 'schedule.part_time',
  rotational: 'schedule.rotational',
}

export function money(v: number | null): string {
  if (v === null || v === undefined) return '—'
  // Разделитель разрядов — пробел в обоих языках, поэтому формат один.
  return new Intl.NumberFormat('ru-RU').format(v)
}

/** Зарплата строкой. `t` передаётся снаружи: функция вызывается и из карточки
 *  вакансии, и из таблицы, а хук в обычной функции не поднять. */
export function salaryText(
  job: { salary_min_uzs: number | null; salary_max_uzs: number | null },
  t: (key: Key, vars?: Record<string, string | number>) => string,
) {
  const { salary_min_uzs: lo, salary_max_uzs: hi } = job
  if (lo && hi) return `${money(lo)} — ${money(hi)}`
  if (lo) return t('common.from', { value: money(lo) })
  if (hi) return t('common.to', { value: money(hi) })
  return '—'
}

const STATUS: Record<string, { label: Key; tone: 'neutral' | 'accent' | 'warn' | 'danger' }> = {
  draft: { label: 'jobs.statusDraft', tone: 'neutral' },
  active: { label: 'jobs.statusActive', tone: 'accent' },
  paused: { label: 'jobs.statusPaused', tone: 'warn' },
  closed: { label: 'jobs.statusClosed', tone: 'neutral' },
}

const PLAN: Record<string, { label: Key; tone: 'neutral' | 'accent' | 'warn' }> = {
  none: { label: 'jobs.planNone', tone: 'warn' },
  draft: { label: 'jobs.planDraft', tone: 'warn' },
  approved: { label: 'jobs.planApproved', tone: 'accent' },
}

export default function Jobs() {
  const t = useT()
  const [creating, setCreating] = useState(false)
  const [filter, setFilter] = useState('')
  const navigate = useNavigate()

  const { data, isPending } = useQuery<Listing>({
    queryKey: ['jobs', filter],
    queryFn: () => api.get(`/api/jobs${filter ? `?status=${filter}` : ''}`),
  })

  const jobs = data?.jobs ?? []
  const s = data?.summary

  return (
    <>
      <PageHead
        title={t('jobs.title')}
        subtitle={t('jobs.subtitle')}
        actions={<Button onClick={() => setCreating(true)}>{t('jobs.new')}</Button>}
      />

      {s && (
        <div className="mb-4 grid grid-cols-2 gap-2.5 sm:grid-cols-4">
          <Metric label={t('jobs.mDrafts')} value={s.drafts} hint={t('jobs.mDraftsHint')} />
          <Metric label={t('jobs.mActive')} value={s.active} hint={t('jobs.mActiveHint')} />
          <Metric label={t('jobs.mApplications')} value={s.applications} />
          <Metric
            label={t('jobs.mInterviews')}
            value={s.interviews_done}
            hint={t('jobs.mInterviewsHint')}
          />
        </div>
      )}

      <Toolbar>
        <Select
          value={filter}
          onChange={setFilter}
          placeholder={t('jobs.filterAll')}
          options={[
            { value: 'draft', label: t('jobs.filterDrafts') },
            { value: 'active', label: t('jobs.filterActive') },
            { value: 'closed', label: t('jobs.filterClosed') },
          ]}
        />
      </Toolbar>

      {isPending ? (
        <Card>
          <p className="py-6 text-center text-[13px] text-ink-faint">{t('common.loadingShort')}</p>
        </Card>
      ) : jobs.length === 0 ? (
        <Empty
          title={t('jobs.emptyTitle')}
          hint={t('jobs.emptyHint')}
          action={<Button onClick={() => setCreating(true)}>{t('jobs.new')}</Button>}
        />
      ) : (
        <Table>
          <thead>
            <tr>
              <Th>{t('jobs.thPosition')}</Th>
              <Th>{t('jobs.thStatus')}</Th>
              <Th>{t('jobs.thPlan')}</Th>
              <Th align="right">{t('jobs.thQuestions')}</Th>
              <Th align="right">{t('jobs.thApplications')}</Th>
              <Th align="right">{t('jobs.thInterviews')}</Th>
              <Th>{t('jobs.thSalary')}</Th>
            </tr>
          </thead>
          <tbody>
            {jobs.map((j) => (
              <Tr key={j.id} onClick={() => navigate(`/jobs/${j.id}`)}>
                <Td>
                  <p className="font-medium text-ink">{j.title}</p>
                  <p className="text-[12px] text-ink-faint">
                    {j.specialty_name ?? j.role_category}
                    {j.schedule.length > 0 &&
                      ` · ${j.schedule.map((c) => (SCHEDULE_KEY[c] ? t(SCHEDULE_KEY[c]) : c)).join(', ')}`}
                  </p>
                </Td>
                <Td>
                  <Badge tone={STATUS[j.status]?.tone ?? 'neutral'}>
                    {STATUS[j.status] ? t(STATUS[j.status].label) : j.status}
                  </Badge>
                </Td>
                <Td>
                  <Badge tone={PLAN[j.interview_plan_status]?.tone ?? 'neutral'}>
                    {PLAN[j.interview_plan_status]
                      ? t(PLAN[j.interview_plan_status].label)
                      : j.interview_plan_status}
                  </Badge>
                </Td>
                <Td align="right">{j.questions_count}</Td>
                <Td align="right">{j.applications_count}</Td>
                <Td align="right">
                  {j.interviews_done > 0 ? (
                    <span className="font-medium text-ink">{j.interviews_done}</span>
                  ) : (
                    <span className="text-ink-faint">—</span>
                  )}
                </Td>
                <Td>{salaryText(j, t)}</Td>
              </Tr>
            ))}
          </tbody>
        </Table>
      )}

      {creating && <NewJobDialog onClose={() => setCreating(false)} />}
    </>
  )
}

/* ── Создание вакансии ──────────────────────────────────────────────────── */

/** Два шага: текст и проверка полей.
 *
 *  Разбор намеренно отделён от сохранения. Модель ошибается — то придумает
 *  навык, которого в тексте не было, то склеит два требования. Менеджер должен
 *  увидеть результат до того, как он попадёт в базу и в вопросы интервью.
 */
function NewJobDialog({ onClose }: { onClose: () => void }) {
  const t = useT()
  const [text, setText] = useState('')
  const [fields, setFields] = useState<JobFields | null>(null)
  const [error, setError] = useState<string | null>(null)
  const qc = useQueryClient()
  const navigate = useNavigate()

  const { data: dicts } = useQuery<Dicts>({
    queryKey: ['job-dicts'],
    queryFn: () => api.get('/api/jobs/dictionaries'),
  })

  const parse = useMutation({
    mutationFn: () => api.post<{ fields: JobFields }>('/api/jobs/parse', { text }),
    onSuccess: (d) => {
      setError(null)
      setFields({ ...d.fields, source_text: text })
    },
    onError: (e) => setError(e instanceof ApiError ? e.message : t('newJob.parseFailed')),
  })

  const create = useMutation({
    mutationFn: () => api.post<{ id: string }>('/api/jobs', fields),
    onSuccess: (d) => {
      qc.invalidateQueries({ queryKey: ['jobs'] })
      onClose()
      navigate(`/jobs/${d.id}`)
    },
    onError: (e) => setError(e instanceof ApiError ? e.message : t('newJob.saveFailed')),
  })

  // На бэкенде title — от 3 до 200 символов (JobIn). Проверяем здесь же, чтобы
  // короткое название ловилось у поля, а не приходило ошибкой от сервера после
  // нажатия «Сохранить». Модель, кстати, регулярно возвращает обрывок вроде
  // «фф», если менеджер надиктовал невнятно.
  const title = (fields?.title ?? '').trim()
  const titleError = title && title.length < 3 ? t('newJob.fTitleShort') : null

  return (
    <Modal
      title={t(fields ? 'newJob.checkTitle' : 'newJob.title')}
      onClose={onClose}
      wide={!!fields}
    >
      {!fields ? (
        <>
          <p className="mb-2 text-[13px] text-ink-muted">{t('newJob.describe')}</p>
          <textarea
            value={text}
            onChange={(e) => setText(e.target.value)}
            rows={7}
            autoFocus
            placeholder={t('newJob.placeholder')}
            className="spring w-full rounded-[8px] border border-line bg-surface-card px-3 py-2
                       text-[13.5px] leading-relaxed text-ink transition
                       placeholder:text-ink-faint hover:border-line-strong"
          />
          {error && <p role="alert" className="mt-2 text-[12.5px] text-danger">{error}</p>}
          <div className="mt-3 flex justify-end gap-2">
            <Button variant="ghost" onClick={onClose}>{t('common.cancel')}</Button>
            <Button
              onClick={() => parse.mutate()}
              loading={parse.isPending}
              disabled={text.trim().length < 20}
            >
              {t('newJob.parse')}
            </Button>
          </div>
          <p className="mt-2 text-[11.5px] text-ink-faint">{t('newJob.parseHint')}</p>
        </>
      ) : (
        <>
          <div className="grid gap-3 sm:grid-cols-2">
            <Field
              label={t('newJob.fTitle')}
              value={fields.title ?? ''}
              error={titleError ?? undefined}
              onChange={(e) => setFields({ ...fields, title: e.target.value })}
            />
            <Select
              label={t('newJob.fSpecialty')}
              value={fields.specialty ?? ''}
              onChange={(v) => {
                const spec = dicts?.specialties.find((s) => s.code === v)
                setFields({
                  ...fields,
                  specialty: v || null,
                  role_category: spec?.role_category ?? fields.role_category,
                })
              }}
              options={(dicts?.specialties ?? []).map((s) => ({ value: s.code, label: s.name_ru }))}
            />
            <Field
              label={t('newJob.fExperience')}
              type="number"
              value={fields.experience_min_months ?? ''}
              onChange={(e) =>
                setFields({
                  ...fields,
                  experience_min_months: e.target.value ? Number(e.target.value) : null,
                })
              }
            />
            <Field
              label={t('newJob.fSchedule')}
              value={fields.schedule
                .map((c) => (SCHEDULE_KEY[c] ? t(SCHEDULE_KEY[c]) : c))
                .join(', ')}
              readOnly
              hint={t('newJob.fScheduleHint')}
            />
            <Field
              label={t('newJob.fSalaryFrom')}
              type="number"
              value={fields.salary_min_uzs ?? ''}
              onChange={(e) =>
                setFields({ ...fields, salary_min_uzs: e.target.value ? Number(e.target.value) : null })
              }
            />
            <Field
              label={t('newJob.fSalaryTo')}
              type="number"
              value={fields.salary_max_uzs ?? ''}
              onChange={(e) =>
                setFields({ ...fields, salary_max_uzs: e.target.value ? Number(e.target.value) : null })
              }
            />
          </div>

          <EditableList
            label={t('newJob.fSkills')}
            items={fields.required_skills}
            onChange={(v) => setFields({ ...fields, required_skills: v })}
            hint={t('newJob.fSkillsHint')}
          />
          <EditableList
            label={t('newJob.fCredentials')}
            items={fields.credential_requirements}
            onChange={(v) => setFields({ ...fields, credential_requirements: v })}
          />

          {!fields.role_category && (
            <p className="mt-3 rounded-[8px] bg-warn-soft px-3 py-2 text-[12.5px] text-warn">
              {t('newJob.noRole')}
            </p>
          )}
          {error && <p role="alert" className="mt-2 text-[12.5px] text-danger">{error}</p>}

          <div className="mt-4 flex justify-between gap-2">
            <Button variant="ghost" onClick={() => setFields(null)}>{t('newJob.back')}</Button>
            <div className="flex gap-2">
              <Button variant="ghost" onClick={onClose}>{t('common.cancel')}</Button>
              <Button
                onClick={() => create.mutate()}
                loading={create.isPending}
                disabled={!title || !!titleError || !fields.role_category}
              >
                {t('newJob.save')}
              </Button>
            </div>
          </div>
          <p className="mt-2 text-[11.5px] text-ink-faint">{t('newJob.nextHint')}</p>
        </>
      )}
    </Modal>
  )
}

/* ── Мелкие детали интерфейса ───────────────────────────────────────────── */

export function Modal({
  title,
  onClose,
  children,
  wide,
}: {
  title: string
  onClose: () => void
  children: React.ReactNode
  wide?: boolean
}) {
  const t = useT()
  return (
    <div
      className="fixed inset-0 z-50 grid place-items-center overflow-y-auto bg-ink/30 p-4"
      onClick={onClose}
      role="presentation"
    >
      <div
        className={`card my-auto w-full p-5 ${wide ? 'max-w-[720px]' : 'max-w-[520px]'}`}
        onClick={(e) => e.stopPropagation()}
        role="dialog"
        aria-modal="true"
        aria-label={title}
      >
        <div className="mb-3 flex items-start justify-between gap-3">
          <h2 className="text-[16px] font-semibold tracking-[-0.01em]">{title}</h2>
          <Button size="xs" variant="ghost" onClick={onClose} aria-label={t('common.close')}>
            ✕
          </Button>
        </div>
        {children}
      </div>
    </div>
  )
}

/** Список строк с добавлением и удалением: требования, документы.
 *  Ввод через запятую был бы короче в коде, но пользователь неизбежно
 *  напишет запятую внутри требования. */
function EditableList({
  label,
  items,
  onChange,
  hint,
}: {
  label: string
  items: string[]
  onChange: (v: string[]) => void
  hint?: string
}) {
  const t = useT()
  const [draft, setDraft] = useState('')
  return (
    <div className="mt-3">
      <p className="mb-1 text-[12.5px] font-medium text-ink-muted">{label}</p>
      <div className="flex flex-wrap gap-1.5">
        {items.map((it, i) => (
          <span
            key={`${it}-${i}`}
            className="inline-flex items-center gap-1.5 rounded-[6px] bg-surface-container
                       px-2 py-1 text-[12.5px] text-ink"
          >
            {it}
            <button
              onClick={() => onChange(items.filter((_, j) => j !== i))}
              className="text-ink-faint transition hover:text-danger"
              aria-label={t('common.removeItem', { item: it })}
            >
              ✕
            </button>
          </span>
        ))}
        {items.length === 0 && (
          <span className="text-[12.5px] text-ink-faint">{t('common.notSet')}</span>
        )}
      </div>
      <div className="mt-1.5 flex gap-1.5">
        <input
          value={draft}
          onChange={(e) => setDraft(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === 'Enter' && draft.trim()) {
              e.preventDefault()
              onChange([...items, draft.trim()])
              setDraft('')
            }
          }}
          placeholder={t('common.addAndEnter')}
          className="spring h-8 flex-1 rounded-[8px] border border-line bg-surface-card px-2.5
                     text-[13px] transition placeholder:text-ink-faint hover:border-line-strong"
        />
      </div>
      {hint && <p className="mt-1 text-[11.5px] text-ink-faint">{hint}</p>}
    </div>
  )
}
