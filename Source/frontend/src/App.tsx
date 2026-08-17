import { Navigate, Route, Routes } from 'react-router-dom'
import { useQuery } from '@tanstack/react-query'
import { ApiError, auth, isManager, type Me } from './lib/api'
import { I18nProvider, useT } from './lib/i18n'
import { AppShell } from './components/AppShell'
import Login from './pages/Login'
import Jobs from './pages/Jobs'
import JobDetail from './pages/JobDetail'
import Structure from './pages/Structure'
import Employees from './pages/Employees'
import Reviews from './pages/Reviews'
import QrCodes from './pages/QrCodes'
import Matching from './pages/Matching'
import Courses from './pages/Courses'
import CourseResults from './pages/CourseResults'
import MyCourses from './pages/portal/MyCourses'
import CourseRunner from './pages/portal/CourseRunner'
import MyReviews from './pages/portal/MyReviews'
import { Knowledge, Overview, Settings } from './pages/stubs'

export default function App() {
  // Единственный источник правды о том, вошли мы или нет — ответ сервера.
  // Локально ничего не храним: токен в httponly cookie, JS его не видит.
  const { data: me, isPending, error } = useQuery<Me>({
    queryKey: ['me'],
    queryFn: auth.me,
    retry: (_count, err) => !(err instanceof ApiError && err.status === 401),
    staleTime: 60_000,
  })

  if (isPending) {
    return (
      <I18nProvider>
        <Booting />
      </I18nProvider>
    )
  }

  const unauthorized = error instanceof ApiError && error.status === 401
  if (unauthorized || !me) {
    // Провайдер нужен и здесь: страница входа тоже переводится, а язык на ней
    // берётся из localStorage — сервер о госте ещё ничего не знает.
    return (
      <I18nProvider>
        <Routes>
          <Route path="/login" element={<Login />} />
          <Route path="*" element={<Navigate to="/login" replace />} />
        </Routes>
      </I18nProvider>
    )
  }

  return (
    <I18nProvider initial={me.locale}>
      <AppRoutes me={me} />
    </I18nProvider>
  )
}

/** Две разные карты маршрутов, а не общая с проверками внутри.
 *
 *  Сотруднику кабинет не «скрыт» — его маршрутов у него просто нет, и любой
 *  адрес уводит в портал. Настоящее разграничение всё равно на сервере: RLS не
 *  отдаст чужие данные, а менеджерские роуты отвечают 403. Здесь мы только не
 *  показываем человеку двенадцать пунктов, из которых работают два.
 */
function AppRoutes({ me }: { me: Me }) {
  if (!isManager(me)) {
    return (
      <Routes>
        <Route path="/login" element={<Navigate to="/my/courses" replace />} />
        <Route element={<AppShell me={me} />}>
          <Route path="my/courses" element={<MyCourses />} />
          <Route path="my/courses/:courseId" element={<CourseRunner />} />
          <Route path="my/reviews" element={<MyReviews />} />
          <Route path="*" element={<Navigate to="/my/courses" replace />} />
        </Route>
      </Routes>
    )
  }

  return (
    <Routes>
      <Route path="/login" element={<Navigate to="/" replace />} />
      <Route element={<AppShell me={me} />}>
        <Route index element={<Overview />} />
        {/* Штат */}
        <Route path="structure" element={<Structure />} />
        <Route path="employees" element={<Employees />} />
        <Route path="reviews" element={<Reviews />} />
        <Route path="qr" element={<QrCodes />} />
        {/* Обучение */}
        <Route path="courses" element={<Courses />} />
        <Route path="knowledge" element={<Knowledge />} />
        <Route path="results" element={<CourseResults />} />
        {/* Найм */}
        <Route path="jobs" element={<Jobs />} />
        <Route path="jobs/:jobId" element={<JobDetail />} />
        <Route path="candidates" element={<Matching />} />
        <Route path="settings" element={<Settings />} />
        <Route path="*" element={<Navigate to="/" replace />} />
      </Route>
    </Routes>
  )
}

function Booting() {
  const t = useT()
  return (
    <div className="grid min-h-screen place-items-center">
      <span
        aria-label={t('common.loading')}
        className="size-6 animate-spin rounded-full border-2 border-line border-t-accent"
      />
    </div>
  )
}
