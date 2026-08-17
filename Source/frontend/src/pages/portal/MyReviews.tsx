import { useQuery } from '@tanstack/react-query'
import { api } from '../../lib/api'
import { intlLocale, useI18n } from '../../lib/i18n'
import type { Dict, MyReview, MyReviewSummary } from '../../lib/types'
import { Badge, Card, Empty, Metric, PageHead, Rating } from '../../components/ui'

type Feed = { reviews: MyReview[]; summary: MyReviewSummary; tags: Dict[] }

/** Отзывы обо мне — портал сотрудника.
 *
 *  Здесь нет ни телефона пациента, ни кнопки «обработано»: сервер их не
 *  отдаёт, а обращения разбирает менеджер. Сотруднику раздел нужен как
 *  обратная связь, и низкие оценки показываются наравне с высокими — иначе он
 *  бесполезен.
 */
export default function MyReviews() {
  const { t, locale } = useI18n()

  const { data, isPending, error } = useQuery<Feed>({
    queryKey: ['my-reviews'],
    queryFn: () => api.get('/api/portal/reviews'),
  })

  if (isPending) return <PageHead title={t('my.reviewsTitle')} subtitle={t('common.loading')} />
  if (error) {
    return (
      <>
        <PageHead title={t('my.reviewsTitle')} />
        <Empty title={t('my.noCard')} hint={(error as Error).message} />
      </>
    )
  }

  const rows = data?.reviews ?? []
  const s = data?.summary
  const tagName = (code: string) => {
    const dict = data?.tags.find((x) => x.code === code)
    if (!dict) return code
    return locale === 'uz' ? dict.name_uz : dict.name_ru
  }

  return (
    <>
      <PageHead title={t('my.reviewsTitle')} subtitle={t('my.reviewsSubtitle')} />

      {rows.length === 0 ? (
        <Empty title={t('my.reviewsEmpty')} hint={t('my.reviewsEmptyHint')} />
      ) : (
        <>
          <div className="mb-4 grid gap-2 sm:grid-cols-3">
            <Metric
              label={t('my.mAvg')} value={s?.avg_rating ?? '—'}
              hint={t('my.mAvgHint', { n: s?.total ?? 0 })}
            />
            <Metric label={t('my.mWeek')} value={s?.last_week ?? 0} hint={t('my.mWeekHint')} />
            <Metric
              label={t('my.mLow')} value={s?.low ?? 0} hint={t('my.mLowHint')}
              tone={(s?.low ?? 0) > 0 ? 'warn' : undefined}
            />
          </div>

          <div className="grid gap-2.5">
            {rows.map((r) => (
              <Card key={r.id}>
                <div className="flex items-start gap-3">
                  <Rating value={r.rating} />
                  <div className="min-w-0 flex-1">
                    {r.comment ? (
                      <p className="text-[13.5px] leading-relaxed">{r.comment}</p>
                    ) : (
                      <p className="text-[13.5px] text-ink-faint">{t('reviews.noComment')}</p>
                    )}
                    {(r.good_tags.length > 0 || r.bad_tags.length > 0) && (
                      <div className="mt-2 flex flex-wrap gap-1.5">
                        {r.good_tags.map((code) => (
                          <Badge key={`g${code}`} tone="accent">
                            {t('reviews.tagGood', { tag: tagName(code) })}
                          </Badge>
                        ))}
                        {r.bad_tags.map((code) => (
                          <Badge key={`b${code}`} tone="warn">
                            {t('reviews.tagBad', { tag: tagName(code) })}
                          </Badge>
                        ))}
                      </div>
                    )}
                  </div>
                  <span className="shrink-0 text-[12px] text-ink-faint">
                    {new Date(r.created_at).toLocaleDateString(intlLocale(locale), {
                      day: 'numeric',
                      month: 'short',
                    })}
                  </span>
                </div>
              </Card>
            ))}
          </div>

          <p className="mt-4 text-[12px] leading-relaxed text-ink-faint">
            {t('my.reviewsNote')}
          </p>
        </>
      )}
    </>
  )
}
