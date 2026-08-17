/** Разделы, у которых бэкенд ещё не готов.
 *
 *  Показываем честное пустое состояние с описанием того, что здесь появится,
 *  а не выдуманные карточки. На репетиции демо фальшивые данные легко принять
 *  за настоящие, и это худший способ узнать, что раздел не работает.
 */
import { useT, type Key } from '../lib/i18n'
import { Card, Empty, PageHead } from '../components/ui'

export function Overview() {
  const t = useT()
  const cards: [Key, Key][] = [
    ['stub.ovReviews', 'stub.ovReviewsHint'],
    ['stub.ovLow', 'stub.ovLowHint'],
    ['stub.ovTraining', 'stub.ovTrainingHint'],
    ['stub.ovSeats', 'stub.ovSeatsHint'],
  ]
  return (
    <>
      <PageHead title={t('stub.overviewTitle')} subtitle={t('stub.overviewSubtitle')} />
      <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
        {cards.map(([title, hint]) => (
          <Card key={title} className="grid gap-1">
            <p className="text-[13px] text-ink-muted">{t(title)}</p>
            <p className="text-[28px] font-semibold leading-none tracking-[-0.03em] text-ink-faint">
              —
            </p>
            <p className="text-[12px] text-ink-faint">{t(hint)}</p>
          </Card>
        ))}
      </div>
      <div className="mt-4">
        <Empty title={t('stub.ovEmptyTitle')} hint={t('stub.ovEmptyHint')} />
      </div>
    </>
  )
}

export function Knowledge() {
  const t = useT()
  return (
    <>
      <PageHead title={t('stub.knowledgeTitle')} subtitle={t('stub.knowledgeSubtitle')} />
      <Empty title={t('stub.knowledgeEmpty')} hint={t('stub.knowledgeHint')} />
    </>
  )
}

export function Settings() {
  const t = useT()
  return (
    <>
      <PageHead title={t('stub.settingsTitle')} subtitle={t('stub.settingsSubtitle')} />
      <Empty title={t('stub.settingsEmpty')} />
    </>
  )
}
