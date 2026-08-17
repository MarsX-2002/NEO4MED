import { useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { api } from '../lib/api'
import { useT } from '../lib/i18n'
import type { ReviewTarget } from '../lib/types'
import {
  Badge, Button, Card, Empty, PageHead, Rating, Table, Td, Th, Toolbar, Tr,
} from '../components/ui'
import { QrDialog } from '../components/QrDialog'

/** Отдельный раздел, а не вкладка внутри отзывов.
 *
 *  QR — это инвентарь: что напечатано, где висит, работает ли ещё. Отзывы —
 *  поток событий. Смешивать их в одном экране значит каждый раз искать нужное
 *  среди чужого.
 */
export default function QrCodes() {
  const t = useT()
  const qc = useQueryClient()
  const [qr, setQr] = useState<{ slug: string; title: string } | null>(null)

  const { data, isPending } = useQuery<ReviewTarget[]>({
    queryKey: ['review-targets'],
    queryFn: () => api.get('/api/reviews/targets'),
  })

  const setActive = useMutation({
    mutationFn: ({ id, is_active }: { id: string; is_active: boolean }) =>
      api.patch(`/api/reviews/targets/${id}`, { is_active }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['review-targets'] }),
  })

  if (isPending) return <PageHead title={t('qr.title')} subtitle={t('common.loading')} />

  const rows = data ?? []
  const units = rows.filter((r) => r.kind === 'unit').length
  const people = rows.length - units

  return (
    <>
      <PageHead title={t('qr.title')} subtitle={t('qr.subtitle')} />

      <Card className="mb-4">
        <p className="text-[12.5px] leading-relaxed text-ink-muted">
          {t('qr.intro1')} <b className="text-ink">@ishmedsifatbot</b> {t('qr.intro2')}
        </p>
      </Card>

      <Toolbar>
        <span className="text-[12.5px] text-ink-faint">
          {t('qr.total', { total: rows.length, units, people })}
        </span>
      </Toolbar>

      {rows.length === 0 ? (
        <Empty title={t('qr.emptyTitle')} hint={t('qr.emptyHint')} />
      ) : (
        <Table>
          <thead>
            <tr>
              <Th>{t('qr.thTarget')}</Th>
              <Th align="center" width="130px">{t('qr.thType')}</Th>
              <Th>{t('qr.thCode')}</Th>
              <Th align="center" width="90px">{t('qr.thReviews')}</Th>
              <Th align="center" width="90px">{t('qr.thAvg')}</Th>
              <Th align="center" width="100px">{t('qr.thSurvey')}</Th>
              <Th align="right" width="170px">{t('qr.thActions')}</Th>
            </tr>
          </thead>
          <tbody>
            {rows.map((target) => (
              <Tr key={target.id} muted={!target.is_active}>
                <Td className="font-medium">{target.title}</Td>
                <Td align="center">
                  <Badge>{target.kind === 'unit' ? t('qr.typeUnit') : t('qr.typeEmployee')}</Badge>
                </Td>
                <Td className="font-mono text-[12.5px] text-ink-muted">{target.slug}</Td>
                <Td align="center">{target.reviews_count || <span className="text-ink-faint">—</span>}</Td>
                <Td align="center">
                  {target.avg_rating !== null ? (
                    <Rating value={target.avg_rating} />
                  ) : (
                    <span className="text-ink-faint">—</span>
                  )}
                </Td>
                <Td align="center">
                  {target.is_active ? (
                    <Badge tone="accent">{t('qr.surveyOpen')}</Badge>
                  ) : (
                    <Badge tone="danger">{t('qr.surveyClosed')}</Badge>
                  )}
                </Td>
                <Td align="right">
                  <div className="flex justify-end gap-1.5">
                    <Button
                      size="xs"
                      variant="outline"
                      onClick={() => setQr({ slug: target.slug, title: target.title })}
                    >
                      {t('qr.show')}
                    </Button>
                    <Button
                      size="xs"
                      variant="ghost"
                      loading={setActive.isPending && setActive.variables?.id === target.id}
                      onClick={() => setActive.mutate({ id: target.id, is_active: !target.is_active })}
                    >
                      {target.is_active ? t('qr.close') : t('qr.open')}
                    </Button>
                  </div>
                </Td>
              </Tr>
            ))}
          </tbody>
        </Table>
      )}

      <p className="mt-2 text-[11.5px] leading-relaxed text-ink-faint">{t('qr.footnote')}</p>

      {qr && <QrDialog slug={qr.slug} title={qr.title} onClose={() => setQr(null)} />}
    </>
  )
}
