import { useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { api } from '../lib/api'
import { intlLocale, useI18n, useT } from '../lib/i18n'
import type { Review, ReviewAttachment, ReviewSummary, TagStat } from '../lib/types'
import {
  Badge, Button, Card, Empty, Metric, PageHead, Rating, Table, Td, Th, Toolbar, Tr,
} from '../components/ui'

type Feed = { reviews: Review[]; summary: ReviewSummary; tags: TagStat[] }
type Filter = 'all' | 'low' | 'callback'

/** Отзывы — таблица с раскрытием строки.
 *
 *  Список карточек хорош на десяти отзывах и невыносим на тысяче. Таблица даёт
 *  сканирование по столбцам, а подробности — комментарий, теги, вложения —
 *  раскрываются по клику, чтобы не занимать место у всех строк сразу.
 */
/** Вложения отзыва: расшифровка голосового текстом и фото.
 *
 *  Голосовое показываем именно текстом. Прослушать десять записей — десять
 *  минут, прочитать — одна; расшифровка уже лежит в базе, её писал бот.
 *  Фото идёт через прокси кабинета: сам файл живёт в Telegram, у нас только
 *  file_id, и ссылка с токеном бота в браузер попадать не должна.
 */
function Attachments({ items }: { items: ReviewAttachment[] }) {
  const t = useT()
  const voices = items.filter((a) => a.kind === 'voice')
  const photos = items.filter((a) => a.kind === 'photo')

  return (
    <div className="grid gap-2">
      {voices.map((a) => (
        <div key={a.id} className="rounded-[8px] border border-line bg-surface-card px-3 py-2">
          <p className="mb-0.5 text-[11.5px] uppercase tracking-[0.04em] text-ink-faint">
            {a.duration
              ? t('reviews.attVoiceSec', { n: a.duration })
              : t('reviews.attVoice')}
          </p>
          {a.transcript ? (
            <p className="text-[13.5px] leading-relaxed">{a.transcript}</p>
          ) : (
            <a
              className="text-[13px] text-accent hover:underline"
              href={`/api/reviews/attachments/${a.id}`}
              target="_blank"
              rel="noreferrer"
            >
              {t('reviews.attVoiceRaw')}
            </a>
          )}
        </div>
      ))}
      {photos.length > 0 && (
        <div className="flex flex-wrap gap-2">
          {photos.map((a) => (
            <a
              key={a.id}
              href={`/api/reviews/attachments/${a.id}`}
              target="_blank"
              rel="noreferrer"
              onClick={(e) => e.stopPropagation()}
            >
              <img
                src={`/api/reviews/attachments/${a.id}`}
                alt={t('reviews.attPhoto')}
                loading="lazy"
                className="h-24 w-24 rounded-[8px] border border-line object-cover"
              />
            </a>
          ))}
        </div>
      )}
    </div>
  )
}

export default function Reviews() {
  const { t, locale } = useI18n()
  const qc = useQueryClient()
  const [filter, setFilter] = useState<Filter>('all')
  const [open, setOpen] = useState<string | null>(null)

  const query =
    filter === 'low' ? '?max_rating=2&unhandled=true' : filter === 'callback' ? '?callback=true' : ''

  const { data, isPending } = useQuery<Feed>({
    queryKey: ['reviews', filter],
    queryFn: () => api.get(`/api/reviews${query}`),
  })

  const markHandled = useMutation({
    mutationFn: (id: string) => api.post(`/api/reviews/${id}/handled`),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['reviews'] }),
  })

  if (isPending) return <PageHead title={t('reviews.title')} subtitle={t('common.loading')} />

  const s = data?.summary
  const rows = data?.reviews ?? []
  const worst = (data?.tags ?? []).filter((tag) => tag.bad > 0).slice(0, 5)

  return (
    <>
      <PageHead
        title={t('reviews.title')}
        subtitle={t('reviews.subtitle')}
      />

      <div className="mb-3 grid gap-2 sm:grid-cols-2 lg:grid-cols-4">
        <Metric
          label={t('reviews.mAvg')} value={s?.avg_rating ?? '—'}
          hint={t('reviews.mAvgHint', { n: s?.total ?? 0 })}
        />
        <Metric label={t('reviews.mWeek')} value={s?.last_week ?? 0} hint={t('reviews.mWeekHint')} />
        <Metric
          label={t('reviews.mLow')} value={s?.low_unhandled ?? 0} hint={t('reviews.mLowHint')}
          tone={(s?.low_unhandled ?? 0) > 0 ? 'danger' : undefined}
        />
        <Metric
          label={t('reviews.mCallbacks')} value={s?.callbacks_pending ?? 0}
          hint={t('reviews.mCallbacksHint')}
          tone={(s?.callbacks_pending ?? 0) > 0 ? 'warn' : undefined}
        />
      </div>

      {worst.length > 0 && (
        <Card className="mb-4">
          {/* Менеджеру нужен не средний балл, а причина: по какому аспекту
              жалуются чаще. Средним баллом не управляют. */}
          <p className="mb-2 text-[12px] text-ink-muted">{t('reviews.improve')}</p>
          <div className="flex flex-wrap gap-1.5">
            {worst.map((tag) => (
              <span
                key={tag.code}
                className="inline-flex items-center gap-1.5 rounded-[6px] bg-warn-soft px-2 py-1
                           text-[12.5px] text-warn"
              >
                {tag.name_ru}
                <b className="font-mono text-[11.5px]">{tag.bad}</b>
              </span>
            ))}
          </div>
        </Card>
      )}

      <Toolbar>
        {(
          [
            ['all', t('reviews.filterAll')],
            ['low', t('reviews.filterLow')],
            ['callback', t('reviews.filterCallback')],
          ] as [Filter, string][]
        ).map(([key, label]) => (
          <button
            key={key}
            onClick={() => setFilter(key)}
            className={`spring rounded-[8px] px-3 py-1.5 text-[13px] transition ${
              filter === key
                ? 'bg-accent text-white'
                : 'border border-line bg-surface-card text-ink-muted hover:text-ink'
            }`}
          >
            {label}
          </button>
        ))}
        <span className="ml-auto text-[12.5px] text-ink-faint">
          {t('common.rows', { n: rows.length })}
        </span>
      </Toolbar>

      {rows.length === 0 ? (
        <Empty
          title={t(filter === 'all' ? 'reviews.emptyTitle' : 'common.nothingFound')}
          hint={t(filter === 'all' ? 'reviews.emptyHint' : 'reviews.emptyFiltered')}
        />
      ) : (
        <Table>
          <thead>
            <tr>
              <Th align="center" width="70px">{t('reviews.thRating')}</Th>
              <Th>{t('reviews.thTarget')}</Th>
              <Th>{t('reviews.thComment')}</Th>
              <Th align="center" width="110px">{t('reviews.thImprove')}</Th>
              <Th align="center" width="110px">{t('reviews.thWhen')}</Th>
              <Th align="right" width="170px">{t('reviews.thActions')}</Th>
            </tr>
          </thead>
          <tbody>
            {rows.map((r) => (
              <>
                <Tr key={r.id} onClick={() => setOpen(open === r.id ? null : r.id)}>
                  <Td align="center"><Rating value={r.rating} /></Td>
                  <Td>
                    <span className="font-medium">{r.target_title}</span>
                    <span className="ml-1.5 text-[11.5px] text-ink-faint">
                      {r.target_kind === 'unit' ? t('qr.typeUnit') : t('qr.typeEmployee')}
                    </span>
                  </Td>
                  <Td className="max-w-[340px] truncate text-ink-muted">
                    {r.comment || <span className="text-ink-faint">{t('reviews.noComment')}</span>}
                  </Td>
                  <Td align="center">
                    {r.bad_tags.length > 0 ? (
                      <Badge tone="warn">{r.bad_tags.length}</Badge>
                    ) : (
                      <span className="text-ink-faint">—</span>
                    )}
                  </Td>
                  <Td align="center" className="text-[12.5px] text-ink-faint">
                    {new Date(r.created_at).toLocaleDateString(intlLocale(locale), {
                      day: 'numeric',
                      month: 'short',
                    })}
                  </Td>
                  <Td align="right">
                    <div className="flex items-center justify-end gap-1.5">
                      {r.wants_callback && r.contact_phone && (
                        <a
                          href={`tel:${r.contact_phone}`}
                          onClick={(e) => e.stopPropagation()}
                          className="font-mono text-[12px] font-medium text-accent hover:underline"
                        >
                          {r.contact_phone}
                        </a>
                      )}
                      {r.handled_at ? (
                        <Badge tone="accent">{t('reviews.handled')}</Badge>
                      ) : (
                        <Button
                          size="xs"
                          variant="outline"
                          loading={markHandled.isPending && markHandled.variables === r.id}
                          onClick={(e) => {
                            e.stopPropagation()
                            markHandled.mutate(r.id)
                          }}
                        >
                          {t('reviews.markHandled')}
                        </Button>
                      )}
                    </div>
                  </Td>
                </Tr>

                {open === r.id && (
                  <tr key={`${r.id}-details`}>
                    <Td colSpan={6} className="bg-surface-head">
                      <div className="grid gap-2 py-1">
                        {r.comment && (
                          <p className="text-[13.5px] leading-relaxed">{r.comment}</p>
                        )}
                        {(r.bad_tags.length > 0 || r.good_tags.length > 0) && (
                          <div className="flex flex-wrap gap-1.5">
                            {r.bad_tags.map((tag) => (
                              <Badge key={`b${tag}`} tone="warn">{t('reviews.tagBad', { tag })}</Badge>
                            ))}
                            {r.good_tags.map((tag) => (
                              <Badge key={`g${tag}`} tone="accent">
                                {t('reviews.tagGood', { tag })}
                              </Badge>
                            ))}
                          </div>
                        )}
                        {r.attachments?.length > 0 && <Attachments items={r.attachments} />}
                        <p className="text-[11.5px] text-ink-faint">
                          {new Date(r.created_at).toLocaleString(intlLocale(locale))}
                          {r.unit_name && ` · ${r.unit_name}`}
                          {r.employee_name && ` · ${r.employee_name}`}
                        </p>
                      </div>
                    </Td>
                  </tr>
                )}
              </>
            ))}
          </tbody>
        </Table>
      )}
    </>
  )
}
