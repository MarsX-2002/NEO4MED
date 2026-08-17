import { useMemo, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { api } from '../lib/api'
import { useT } from '../lib/i18n'
import type { Dictionaries, Employee, Unit } from '../lib/types'
import {
  Badge, Button, Card, Empty, Field, Metric, PageHead, Rating, Select, Table, Td, Th, Toolbar, Tr,
} from '../components/ui'
import { QrDialog } from '../components/QrDialog'

type Listing = {
  employees: Employee[]
  summary: { active: number; onboarding: number; dismissed: number; seats_open: number }
}

/** Сотрудники — таблица.
 *
 *  Здесь важна не иерархия, а сравнение: кто, где, с какой оценкой. При сотне
 *  человек взгляд должен идти по столбцу, а не по карточкам.
 */
export default function Employees() {
  const t = useT()
  const qc = useQueryClient()
  const [showForm, setShowForm] = useState(false)
  const [unitFilter, setUnitFilter] = useState('')
  const [search, setSearch] = useState('')
  const [showDismissed, setShowDismissed] = useState(false)
  const [qr, setQr] = useState<{ slug: string; title: string } | null>(null)

  const { data, isPending } = useQuery<Listing>({
    queryKey: ['employees'],
    queryFn: () => api.get('/api/employees'),
  })
  const { data: structure } = useQuery<{ units: Unit[] }>({
    queryKey: ['structure'],
    queryFn: () => api.get('/api/structure'),
  })
  const { data: dicts } = useQuery<Dictionaries>({
    queryKey: ['dictionaries'],
    queryFn: () => api.get('/api/structure/dictionaries'),
    staleTime: 10 * 60_000,
  })

  const invalidate = () => {
    qc.invalidateQueries({ queryKey: ['employees'] })
    qc.invalidateQueries({ queryKey: ['structure'] })
  }

  const hire = useMutation({
    mutationFn: (body: Record<string, unknown>) => api.post('/api/employees', body),
    onSuccess: () => { invalidate(); setShowForm(false) },
  })
  const dismiss = useMutation({
    mutationFn: (id: string) => api.post(`/api/employees/${id}/dismiss`),
    onSuccess: invalidate,
  })
  const issueQr = useMutation({
    mutationFn: (id: string) =>
      api.post<{ slug: string; title: string }>(`/api/employees/${id}/qr`),
    onSuccess: (t) => {
      invalidate()
      qc.invalidateQueries({ queryKey: ['review-targets'] })
      setQr({ slug: t.slug, title: t.title })
    },
  })

  const rows = useMemo(() => {
    const all = data?.employees ?? []
    const q = search.trim().toLowerCase()
    return all.filter(
      (e) =>
        (showDismissed || e.status !== 'dismissed') &&
        (!unitFilter || e.unit_id === unitFilter) &&
        (!q || e.full_name.toLowerCase().includes(q)),
    )
  }, [data, search, unitFilter, showDismissed])

  if (isPending) return <PageHead title={t('employees.title')} subtitle={t('common.loading')} />

  const s = data?.summary
  const units = structure?.units ?? []

  return (
    <>
      <PageHead
        title={t('employees.title')}
        subtitle={t('employees.subtitle')}
        actions={
          <Button onClick={() => setShowForm((v) => !v)}>
            {showForm ? t('common.cancel') : t('employees.hire')}
          </Button>
        }
      />

      <div className="mb-4 grid gap-2 sm:grid-cols-3">
        <Metric label={t('employees.mActive')} value={s?.active ?? 0} />
        <Metric label={t('employees.mOnboarding')} value={s?.onboarding ?? 0} />
        <Metric label={t('employees.mDismissed')} value={s?.dismissed ?? 0} />
      </div>

      {showForm && (
        <Card className="mb-3">
          <HireForm
            units={units}
            dicts={dicts}
            busy={hire.isPending}
            error={hire.isError ? (hire.error as Error).message : null}
            onSubmit={(body) => hire.mutate(body)}
          />
        </Card>
      )}

      <Toolbar>
        <div className="w-[240px]">
          <Field
            placeholder={t('employees.search')}
            value={search}
            onChange={(e) => setSearch(e.target.value)}
          />
        </div>
        <div className="w-[240px]">
          <Select
            value={unitFilter}
            onChange={setUnitFilter}
            placeholder={t('employees.allUnits')}
            options={units.map((u) => ({
              value: u.id,
              label: `${'· '.repeat(u.level)}${u.name}`,
            }))}
          />
        </div>
        <label className="flex items-center gap-1.5 text-[12.5px] text-ink-muted">
          <input
            type="checkbox"
            checked={showDismissed}
            onChange={(e) => setShowDismissed(e.target.checked)}
            className="size-3.5 accent-accent"
          />
          {t('employees.showDismissed')}
        </label>
        <span className="ml-auto text-[12.5px] text-ink-faint">
          {t('common.rows', { n: rows.length })}
        </span>
      </Toolbar>

      {rows.length === 0 ? (
        <Empty
          title={t(search || unitFilter ? 'common.nothingFound' : 'employees.emptyTitle')}
          hint={t(search || unitFilter ? 'employees.emptyFiltered' : 'employees.emptyHint')}
          action={
            !search && !unitFilter ? (
              <Button onClick={() => setShowForm(true)}>{t('employees.hire')}</Button>
            ) : undefined
          }
        />
      ) : (
        <Table>
          <thead>
            <tr>
              <Th>{t('employees.thName')}</Th>
              <Th>{t('employees.thUnit')}</Th>
              <Th>{t('employees.thCategory')}</Th>
              <Th>{t('employees.thPhone')}</Th>
              <Th align="center" width="90px">{t('employees.thRating')}</Th>
              <Th align="center" width="90px">{t('employees.thStatus')}</Th>
              <Th align="right" width="180px">{t('employees.thActions')}</Th>
            </tr>
          </thead>
          <tbody>
            {rows.map((e) => (
              <Tr key={e.id} muted={e.status === 'dismissed'}>
                <Td className="font-medium">{e.full_name}</Td>
                <Td className="text-ink-muted">{e.unit_name ?? '—'}</Td>
                <Td className="text-ink-muted">{e.specialty_name ?? e.role_name ?? '—'}</Td>
                <Td className="font-mono text-[12.5px] text-ink-muted">{e.work_phone ?? '—'}</Td>
                <Td align="center">
                  {e.avg_rating !== null ? (
                    <span className="inline-flex items-center gap-1">
                      <Rating value={e.avg_rating} />
                      <span className="text-[11.5px] text-ink-faint">{e.reviews_count}</span>
                    </span>
                  ) : (
                    <span className="text-ink-faint">—</span>
                  )}
                </Td>
                <Td align="center">
                  {e.status === 'active' && <Badge tone="accent">{t('employees.stActive')}</Badge>}
                  {e.status === 'onboarding' && (
                    <Badge tone="info">{t('employees.stOnboarding')}</Badge>
                  )}
                  {e.status === 'suspended' && (
                    <Badge tone="warn">{t('employees.stSuspended')}</Badge>
                  )}
                  {e.status === 'dismissed' && (
                    <Badge tone="danger">{t('employees.stDismissed')}</Badge>
                  )}
                </Td>
                <Td align="right">
                  {e.status !== 'dismissed' && (
                    <div className="flex justify-end gap-1.5">
                      <Button
                        size="xs"
                        variant={e.qr_slug ? 'outline' : 'soft'}
                        loading={issueQr.isPending && issueQr.variables === e.id}
                        onClick={() =>
                          e.qr_slug
                            ? setQr({ slug: e.qr_slug, title: e.full_name })
                            : issueQr.mutate(e.id)
                        }
                      >
                        {e.qr_slug ? 'QR' : t('structure.makeQr')}
                      </Button>
                      <Button
                        size="xs"
                        variant="ghost"
                        loading={dismiss.isPending && dismiss.variables === e.id}
                        onClick={() => dismiss.mutate(e.id)}
                      >
                        {t('employees.dismiss')}
                      </Button>
                    </div>
                  )}
                </Td>
              </Tr>
            ))}
          </tbody>
        </Table>
      )}

      {qr && <QrDialog slug={qr.slug} title={qr.title} onClose={() => setQr(null)} />}
    </>
  )
}

function HireForm({
  units,
  dicts,
  busy,
  error,
  onSubmit,
}: {
  units: Unit[]
  dicts?: Dictionaries
  busy: boolean
  error: string | null
  onSubmit: (body: Record<string, unknown>) => void
}) {
  const t = useT()
  const [fullName, setFullName] = useState('')
  const [unitId, setUnitId] = useState('')
  const [role, setRole] = useState('')
  const [specialty, setSpecialty] = useState('')
  const [phone, setPhone] = useState('')

  // Специальности показываем только выбранной категории: полный список из
  // пятнадцати позиций заставляет искать глазами то, чего в этой роли нет.
  const specialties = (dicts?.specialties ?? []).filter((s) => !role || s.role_category === role)

  return (
    <form
      className="grid gap-3"
      onSubmit={(e) => {
        e.preventDefault()
        if (!fullName.trim()) return
        onSubmit({
          full_name: fullName,
          unit_id: unitId || null,
          role_category: role || null,
          specialty: specialty || null,
          work_phone: phone || null,
          status: 'active',
        })
      }}
    >
      {error && (
        <p role="alert" className="rounded-[8px] bg-danger-soft px-3 py-2 text-[12.5px] text-danger">
          {error}
        </p>
      )}

      <div className="grid gap-3 sm:grid-cols-3">
        <Field label={t('employees.fName')} value={fullName} autoFocus
               placeholder={t('employees.fNamePlaceholder')}
               onChange={(e) => setFullName(e.target.value)} />
        <Select
          label={t('employees.fUnit')} value={unitId} onChange={setUnitId}
          options={units.map((u) => ({ value: u.id, label: `${'· '.repeat(u.level)}${u.name}` }))}
        />
        <Field label={t('employees.fPhone')} value={phone} placeholder="998 90 123 45 67"
               onChange={(e) => setPhone(e.target.value)} />
        <Select
          label={t('employees.fCategory')} value={role}
          onChange={(v) => { setRole(v); setSpecialty('') }}
          options={(dicts?.roles ?? []).map((r) => ({ value: r.code, label: r.name_ru }))}
        />
        <Select
          label={t('employees.fSpecialty')} value={specialty} onChange={setSpecialty}
          options={specialties.map((s) => ({ value: s.code, label: s.name_ru }))}
        />
        <div className="flex items-end">
          <Button type="submit" loading={busy} disabled={!fullName.trim()}>
            {t('employees.fSubmit')}
          </Button>
        </div>
      </div>
    </form>
  )
}
