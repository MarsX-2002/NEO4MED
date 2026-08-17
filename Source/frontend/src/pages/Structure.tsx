import { useMemo, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { api } from '../lib/api'
import { useT } from '../lib/i18n'
import type { Unit } from '../lib/types'
import { Badge, Button, Card, Empty, Field, PageHead, Toolbar } from '../components/ui'
import { QrDialog } from '../components/QrDialog'

type TreeResponse = { units: Unit[] }

/** Подразделения — дерево, а не таблица.
 *
 *  Иерархия здесь и есть содержание: филиал → этаж → отделение → кабинет.
 *  В таблице родителя от ребёнка не отличить, а при сотне узлов это критично.
 *  Поэтому свёртывание, поиск и счётчики на узлах.
 */
export default function Structure() {
  const t = useT()
  const qc = useQueryClient()
  const [collapsed, setCollapsed] = useState<Set<string>>(new Set())
  const [search, setSearch] = useState('')
  const [addUnder, setAddUnder] = useState<string | null | undefined>(undefined)
  const [name, setName] = useState('')
  const [qr, setQr] = useState<{ slug: string; title: string; url: string } | null>(null)

  const { data, isPending } = useQuery<TreeResponse>({
    queryKey: ['structure'],
    queryFn: () => api.get('/api/structure'),
  })

  const createUnit = useMutation({
    mutationFn: (body: { name: string; parent_id: string | null }) =>
      api.post('/api/structure/units', body),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['structure'] })
      setName('')
      setAddUnder(undefined)
    },
  })

  const issueQr = useMutation({
    mutationFn: (unitId: string) =>
      api.post<{ slug: string; title: string }>(`/api/structure/units/${unitId}/qr`),
    onSuccess: (t) => {
      qc.invalidateQueries({ queryKey: ['structure'] })
      qc.invalidateQueries({ queryKey: ['review-targets'] })
      setQr({ slug: t.slug, title: t.title, url: '' })
    },
  })

  const removeUnit = useMutation({
    mutationFn: (unitId: string) => api.del(`/api/structure/units/${unitId}`),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['structure'] }),
  })

  const units = data?.units ?? []

  const hasChildren = useMemo(() => {
    const s = new Set<string>()
    units.forEach((u) => u.parent_id && s.add(u.parent_id))
    return s
  }, [units])

  /** При поиске дерево не сворачиваем и не фильтруем «по-плоскому»: показываем
   *  найденные узлы вместе с их предками, иначе результат теряет контекст —
   *  непонятно, «Кабинет 204» какого отделения. */
  const visible = useMemo(() => {
    const q = search.trim().toLowerCase()
    if (q) {
      const byId = new Map(units.map((u) => [u.id, u]))
      const keep = new Set<string>()
      units
        .filter((u) => u.name.toLowerCase().includes(q))
        .forEach((u) => {
          let cur: Unit | undefined = u
          while (cur) {
            keep.add(cur.id)
            cur = cur.parent_id ? byId.get(cur.parent_id) : undefined
          }
        })
      return units.filter((u) => keep.has(u.id))
    }
    // Скрываем детей свёрнутых узлов на любой глубине.
    const hidden = new Set<string>()
    return units.filter((u) => {
      if (u.parent_id && (collapsed.has(u.parent_id) || hidden.has(u.parent_id))) {
        hidden.add(u.id)
        return false
      }
      return true
    })
  }, [units, collapsed, search])

  function toggle(id: string) {
    setCollapsed((prev) => {
      const next = new Set(prev)
      next.has(id) ? next.delete(id) : next.add(id)
      return next
    })
  }

  if (isPending) return <PageHead title={t('structure.title')} subtitle={t('common.loading')} />

  return (
    <>
      <PageHead
        title={t('structure.title')}
        subtitle={t('structure.subtitle')}
        actions={
          <>
            <Button variant="outline" onClick={() => setCollapsed(new Set(hasChildren))}>
              {t('structure.collapseAll')}
            </Button>
            <Button variant="outline" onClick={() => setCollapsed(new Set())}>
              {t('structure.expandAll')}
            </Button>
            <Button onClick={() => setAddUnder(addUnder === null ? undefined : null)}>
              {addUnder === null ? t('common.cancel') : t('structure.addBranch')}
            </Button>
          </>
        }
      />

      <Toolbar>
        <div className="w-[280px]">
          <Field
            placeholder={t('structure.search')}
            value={search}
            onChange={(e) => setSearch(e.target.value)}
          />
        </div>
        <span className="text-[12.5px] text-ink-faint">
          {t('structure.nodes', { n: units.length })}
          {search && ` · ${t('structure.found', { n: visible.length })}`}
        </span>
      </Toolbar>

      {addUnder === null && (
        <Card className="mb-3">
          <AddForm
            label={t('structure.branchName')}
            value={name}
            onChange={setName}
            busy={createUnit.isPending}
            onSubmit={() => createUnit.mutate({ name, parent_id: null })}
          />
        </Card>
      )}

      {units.length === 0 ? (
        <Empty
          title={t('structure.emptyTitle')}
          hint={t('structure.emptyHint')}
          action={<Button onClick={() => setAddUnder(null)}>{t('structure.addBranch')}</Button>}
        />
      ) : (
        <div className="card divide-y divide-line">
          {visible.map((u) => {
            const expandable = hasChildren.has(u.id)
            const isCollapsed = collapsed.has(u.id)
            return (
              <div key={u.id}>
                <div
                  className="spring flex items-center gap-2 px-2 py-2 transition hover:bg-surface-head"
                  style={{ paddingLeft: `${8 + u.level * 22}px` }}
                >
                  {expandable ? (
                    <button
                      onClick={() => toggle(u.id)}
                      aria-label={t(isCollapsed ? 'structure.expand' : 'structure.collapse')}
                      aria-expanded={!isCollapsed}
                      className="grid size-5 shrink-0 place-items-center rounded-[6px] text-ink-faint
                                 hover:bg-surface-container hover:text-ink"
                    >
                      <svg
                        viewBox="0 0 24 24"
                        className={`size-3.5 spring transition ${isCollapsed ? '' : 'rotate-90'}`}
                        fill="none"
                        stroke="currentColor"
                        strokeWidth="2.5"
                        strokeLinecap="round"
                      >
                        <path d="M9 6l6 6-6 6" />
                      </svg>
                    </button>
                  ) : (
                    <span className="size-5 shrink-0" aria-hidden />
                  )}

                  <span
                    className={`min-w-0 flex-1 truncate text-[13.5px] ${
                      u.level === 0 ? 'font-semibold' : 'font-medium'
                    }`}
                  >
                    {u.name}
                  </span>

                  <div className="flex shrink-0 items-center gap-1.5">
                    {u.district_name && <Badge>{u.district_name}</Badge>}
                    {u.employees_count > 0 && (
                      <Badge>{t('structure.people', { n: u.employees_count })}</Badge>
                    )}
                    {u.reviews_count > 0 && (
                      <Badge tone="info">{t('structure.reviews', { n: u.reviews_count })}</Badge>
                    )}
                    <Button
                      size="xs"
                      variant={u.qr_slug ? 'outline' : 'soft'}
                      loading={issueQr.isPending && issueQr.variables === u.id}
                      onClick={() =>
                        u.qr_slug
                          ? setQr({ slug: u.qr_slug, title: u.name, url: '' })
                          : issueQr.mutate(u.id)
                      }
                    >
                      {u.qr_slug ? 'QR' : t('structure.makeQr')}
                    </Button>
                    <Button
                      size="xs"
                      variant="ghost"
                      onClick={() => setAddUnder(addUnder === u.id ? undefined : u.id)}
                    >
                      {addUnder === u.id ? t('common.cancel') : t('structure.addInside')}
                    </Button>
                    {!expandable && u.employees_count === 0 && u.reviews_count === 0 && (
                      <Button size="xs" variant="ghost" onClick={() => removeUnit.mutate(u.id)}>
                        {t('structure.delete')}
                      </Button>
                    )}
                  </div>
                </div>

                {addUnder === u.id && (
                  <div
                    className="bg-surface-head px-3 py-3"
                    style={{ paddingLeft: `${30 + u.level * 22}px` }}
                  >
                    <AddForm
                      label={t('structure.childName', { name: u.name })}
                      value={name}
                      onChange={setName}
                      busy={createUnit.isPending}
                      onSubmit={() => createUnit.mutate({ name, parent_id: u.id })}
                    />
                  </div>
                )}
              </div>
            )
          })}
        </div>
      )}

      {removeUnit.isError && (
        <p role="alert" className="mt-2 text-[12.5px] text-danger">
          {(removeUnit.error as Error).message}
        </p>
      )}

      {qr && <QrDialog slug={qr.slug} title={qr.title} onClose={() => setQr(null)} />}
    </>
  )
}

function AddForm({
  label,
  value,
  onChange,
  busy,
  onSubmit,
}: {
  label: string
  value: string
  onChange: (v: string) => void
  busy: boolean
  onSubmit: () => void
}) {
  const t = useT()
  return (
    <form
      className="flex flex-wrap items-end gap-2"
      onSubmit={(e) => {
        e.preventDefault()
        if (value.trim()) onSubmit()
      }}
    >
      <div className="min-w-[240px] flex-1">
        <Field
          label={label}
          value={value}
          autoFocus
          placeholder={t('structure.namePlaceholder')}
          onChange={(e) => onChange(e.target.value)}
        />
      </div>
      <Button type="submit" loading={busy} disabled={!value.trim()}>
        {t('common.add')}
      </Button>
    </form>
  )
}
