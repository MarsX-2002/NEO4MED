/** Оболочка кабинета: плавающая левая панель и рабочая область.
 *
 *  Панель — карточка, отделённая отступом со всех сторон, а не прижатая к краю
 *  экрана. Сворачивается в иконки: на ноутбуке 13 дюймов рабочая область важнее
 *  подписей. Состояние сворачивания запоминается — переключать его каждый раз
 *  раздражает больше, чем сама узкая панель.
 */
import { useEffect, useState } from 'react'
import { NavLink, Outlet, useNavigate } from 'react-router-dom'
import { useQueryClient } from '@tanstack/react-query'
import { auth, isManager, type Me } from '../lib/api'
import { LOCALE_NAMES, useI18n, type Key, type Locale } from '../lib/i18n'

type NavItem = { to: string; label: Key; icon: string; end?: boolean }
type NavGroup = { label?: Key; items: NavItem[] }

/** Меню сгруппировано по смыслу: штат, обучение, найм.
 *  Плоский список из восьми пунктов читается как свалка — глазу не за что
 *  зацепиться. Заголовки групп дают структуру и заодно показывают, что
 *  продукт про весь путь сотрудника, а не только про вакансии.
 *
 *  В таблице лежат ключи словаря, а не готовые подписи: список статический,
 *  а язык меняется на лету. */
const NAV: NavGroup[] = [
  { items: [{ to: '/', label: 'nav.overview', icon: 'home', end: true }] },
  {
    label: 'nav.groupStaff',
    items: [
      { to: '/structure', label: 'nav.structure', icon: 'org' },
      { to: '/employees', label: 'nav.employees', icon: 'people' },
    ],
  },
  {
    label: 'nav.groupQuality',
    items: [
      { to: '/reviews', label: 'nav.reviews', icon: 'chat' },
      { to: '/qr', label: 'nav.qr', icon: 'qr' },
    ],
  },
  {
    label: 'nav.groupTraining',
    items: [
      { to: '/courses', label: 'nav.courses', icon: 'book' },
      { to: '/knowledge', label: 'nav.knowledge', icon: 'doc' },
      { to: '/results', label: 'nav.results', icon: 'chart' },
    ],
  },
  {
    label: 'nav.groupHiring',
    items: [
      { to: '/jobs', label: 'nav.jobs', icon: 'work' },
      { to: '/candidates', label: 'nav.candidates', icon: 'search' },
    ],
  },
]

/** Меню сотрудника. Два пункта — и это не урезанный кабинет, а другой продукт:
 *  человек заходит пройти курс и прочитать, что о нём написали пациенты.
 *  Прятать остальное важно не для безопасности (её держат RLS и 403), а чтобы
 *  не показывать двенадцать пунктов, из которых работают два. */
const EMPLOYEE_NAV: NavGroup[] = [
  {
    items: [
      { to: '/my/courses', label: 'nav.myCourses', icon: 'book' },
      { to: '/my/reviews', label: 'nav.myReviews', icon: 'chat' },
    ],
  },
]

const COLLAPSE_KEY = 'ishmed.nav.collapsed'

export function AppShell({ me }: { me: Me }) {
  const [collapsed, setCollapsed] = useState(
    () => localStorage.getItem(COLLAPSE_KEY) === '1',
  )
  const { t, locale, setLocale } = useI18n()
  const navigate = useNavigate()
  const qc = useQueryClient()
  const manager = isManager(me)
  const nav = manager ? NAV : EMPLOYEE_NAV

  useEffect(() => {
    localStorage.setItem(COLLAPSE_KEY, collapsed ? '1' : '0')
  }, [collapsed])

  async function signOut() {
    await auth.logout()
    qc.clear()
    navigate('/login', { replace: true })
  }

  return (
    <div className="flex min-h-screen gap-0 bg-surface">
      <aside
        className={`spring sticky top-0 m-3 flex h-[calc(100vh-1.5rem)] shrink-0 flex-col
                    rounded-[14px] border border-line bg-surface-card p-2.5 transition-[width]
                    ${collapsed ? 'w-[64px]' : 'w-[220px]'}`}
      >
        <div className="mb-2 flex items-center gap-2 px-2 py-1.5">
          <Logo />
          {!collapsed && (
            <div className="min-w-0">
              <p className="truncate text-[15px] font-semibold tracking-[-0.01em]">IshMed</p>
              <p className="truncate text-[12px] text-ink-faint">
                {t(manager ? 'shell.cabinet' : 'shell.portal')}
              </p>
            </div>
          )}
        </div>

        <nav className="flex flex-1 flex-col gap-0.5 overflow-y-auto">
          {nav.map((group, gi) => (
            <div key={group.label ?? `g${gi}`} className={gi > 0 ? 'mt-3' : undefined}>
              {group.label &&
                (collapsed ? (
                  // В свёрнутом виде подпись не поместится: вместо неё тонкая
                  // черта, чтобы группы всё равно читались как группы.
                  <div className="mx-3 my-2 border-t border-line" aria-hidden />
                ) : (
                  <p className="px-3 pb-1 text-[11px] font-semibold uppercase tracking-[0.07em] text-ink-faint">
                    {t(group.label)}
                  </p>
                ))}
              <div className="flex flex-col gap-0.5">
                {group.items.map((item) => (
                  <NavLink
                    key={item.to}
                    to={item.to}
                    end={item.end}
                    title={collapsed ? t(item.label) : undefined}
                    className={({ isActive }) =>
                      `spring flex items-center gap-3 rounded-[8px] px-2.5 py-1.5 text-[13.5px] transition
                       ${
                         isActive
                           ? 'bg-accent-soft font-medium text-accent-ink'
                           : 'text-ink-muted hover:bg-surface-container hover:text-ink'
                       }`
                    }
                  >
                    <Icon name={item.icon} />
                    {!collapsed && <span className="truncate">{t(item.label)}</span>}
                  </NavLink>
                ))}
              </div>
            </div>
          ))}
        </nav>

        <div className="mt-2 border-t border-line pt-2">
          <LanguageSwitch collapsed={collapsed} locale={locale} onPick={setLocale} />
          {!collapsed && (
            <div className="px-3 pb-2 pt-2">
              <p className="truncate text-[13px] font-medium text-ink">
                {me.clinic_name ?? t('shell.clinic')}
              </p>
              <p className="truncate text-[12px] text-ink-faint">{me.email}</p>
            </div>
          )}
          <div className="flex gap-1">
            <button
              onClick={() => setCollapsed((v) => !v)}
              className="spring flex flex-1 items-center justify-center gap-2 rounded-full px-3 py-2
                         text-[13px] text-ink-muted transition hover:bg-surface-container"
              aria-label={t(collapsed ? 'shell.expandPanel' : 'shell.collapsePanel')}
            >
              <Icon name={collapsed ? 'expand' : 'collapse'} />
              {!collapsed && t('shell.collapse')}
            </button>
            {!collapsed && (
              <button
                onClick={signOut}
                className="spring rounded-full px-3 py-2 text-[13px] text-ink-muted
                           transition hover:bg-surface-container"
              >
                {t('shell.signOut')}
              </button>
            )}
          </div>
        </div>
      </aside>

      <main className="min-w-0 flex-1 px-4 py-6 sm:px-8">
        <div className="mx-auto max-w-[1180px]">
          <Outlet />
        </div>
      </main>
    </div>
  )
}

/** Переключатель языка.
 *
 *  Два языка — значит переключатель, а не выпадающий список: выбор из двух
 *  вариантов через список требует двух кликов вместо одного. В свёрнутой
 *  панели места нет, поэтому там одна кнопка с кодом другого языка.
 */
function LanguageSwitch({
  collapsed,
  locale,
  onPick,
}: {
  collapsed: boolean
  locale: Locale
  onPick: (l: Locale) => void
}) {
  const { t } = useI18n()
  const other: Locale = locale === 'ru' ? 'uz' : 'ru'

  if (collapsed) {
    return (
      <button
        onClick={() => onPick(other)}
        title={LOCALE_NAMES[other]}
        aria-label={`${t('shell.language')}: ${LOCALE_NAMES[other]}`}
        className="spring mx-auto mb-1 flex w-full items-center justify-center rounded-full px-2 py-1.5
                   text-[12px] font-semibold uppercase text-ink-muted transition
                   hover:bg-surface-container hover:text-ink"
      >
        {other}
      </button>
    )
  }

  return (
    <div className="px-1.5 pb-1">
      <div
        role="group"
        aria-label={t('shell.language')}
        className="flex gap-0.5 rounded-full bg-surface-container p-0.5"
      >
        {(['ru', 'uz'] as Locale[]).map((code) => (
          <button
            key={code}
            onClick={() => onPick(code)}
            aria-pressed={locale === code}
            className={`spring flex-1 rounded-full px-2 py-1 text-[12px] font-medium transition ${
              locale === code
                ? 'bg-surface-card text-ink shadow-[0_1px_2px_rgba(0,0,0,0.06)]'
                : 'text-ink-faint hover:text-ink'
            }`}
          >
            {LOCALE_NAMES[code]}
          </button>
        ))}
      </div>
    </div>
  )
}

function Logo() {
  return (
    <span
      aria-hidden
      className="grid size-9 shrink-0 place-items-center rounded-2xl bg-accent text-white"
    >
      <svg viewBox="0 0 24 24" className="size-5" fill="none" stroke="currentColor" strokeWidth="2.2">
        <path d="M3 12h3.5l2-4.5 3 9 2.5-6 1.5 3H21" strokeLinecap="round" strokeLinejoin="round" />
      </svg>
    </span>
  )
}

/** Иконки инлайном: шесть штук не стоят зависимости и лишнего запроса. */
function Icon({ name }: { name: string }) {
  const p: Record<string, string> = {
    home: 'M4 10.5 12 4l8 6.5V20h-5v-5.5H9V20H4z',
    work: 'M4 8h16v11H4zM9 8V6a2 2 0 0 1 2-2h2a2 2 0 0 1 2 2v2M4 13h16',
    org: 'M12 4v3M6 20v-4h12v4M9 10h6v3H9zM6 16h12M4 20h4M16 20h4',
    people: 'M8 11a3 3 0 1 0 0-6 3 3 0 0 0 0 6zM2 20c0-3 3-5 6-5s6 2 6 5M17 8a2.5 2.5 0 1 0 0-5M16 15c3 0 6 1.6 6 4.5',
    book: 'M5 5h6a2 2 0 0 1 2 2v13a2 2 0 0 0-2-2H5zM19 5h-6a2 2 0 0 0-2 2v13a2 2 0 0 1 2-2h6z',
    qr: 'M4 4h6v6H4zM14 4h6v6h-6zM4 14h6v6H4zM14 14h2v2h-2zM18 14h2v2h-2zM14 18h2v2h-2zM18 18h2v2h-2z',
    chat: 'M4 5h16v11H9l-5 4z',
    doc: 'M6 3h8l4 4v14H6zM14 3v4h4M9 12h6M9 16h6',
    chart: 'M4 20h16M7 20v-6M12 20V8M17 20v-9',
    search: 'M11 18a7 7 0 1 0 0-14 7 7 0 0 0 0 14zM20 20l-4.2-4.2',
    gear: 'M12 15a3 3 0 1 0 0-6 3 3 0 0 0 0 6zM19.4 15a1.6 1.6 0 0 0 .3 1.8l.1.1a2 2 0 1 1-2.8 2.8l-.1-.1a1.6 1.6 0 0 0-2.7 1.1V21a2 2 0 1 1-4 0v-.1A1.6 1.6 0 0 0 7 19.4a1.6 1.6 0 0 0-1.8.3l-.1.1a2 2 0 1 1-2.8-2.8l.1-.1A1.6 1.6 0 0 0 3 15H3a2 2 0 1 1 0-4h.1A1.6 1.6 0 0 0 4.6 7a1.6 1.6 0 0 0-.3-1.8l-.1-.1a2 2 0 1 1 2.8-2.8l.1.1A1.6 1.6 0 0 0 9 3V3a2 2 0 1 1 4 0v.1a1.6 1.6 0 0 0 2.7 1.1',
    collapse: 'M14 8l-4 4 4 4',
    expand: 'M10 8l4 4-4 4',
  }
  return (
    <svg
      aria-hidden
      viewBox="0 0 24 24"
      className="size-5 shrink-0"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.7"
      strokeLinecap="round"
      strokeLinejoin="round"
    >
      <path d={p[name] ?? p.home} />
    </svg>
  )
}
