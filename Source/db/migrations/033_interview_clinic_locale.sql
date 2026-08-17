-- 033_interview_clinic_locale.sql
-- Язык клиники для итога интервью.
--
-- Кабинет переведён на узбекский, и менеджер-узбек читал бы русское саммари:
-- вопросы он пишет на своём языке, ответы кандидата ему пересказывают на чужом.
-- Язык берём у того, кто завёл вакансию, — это и есть язык, на котором с ней
-- работают. Не у кандидата: он отвечает как ему удобно, а читает клиника.
--
-- CREATE OR REPLACE тут не сработает: список выходных колонок — часть подписи
-- функции, поэтому сначала DROP.

DROP FUNCTION IF EXISTS product.job_of_interview(uuid);

CREATE FUNCTION product.job_of_interview(p_interview_id uuid)
RETURNS TABLE (
    job_id        uuid,
    title         text,
    specialty     text,
    role_category text,
    experience_min_months integer,
    required_skills text[],
    source_text   text,
    clinic_locale text)
LANGUAGE sql SECURITY DEFINER SET search_path = product, public STABLE AS $$
    SELECT j.id, j.title, j.specialty, j.role_category,
           j.experience_min_months, j.required_skills, j.source_text,
           -- created_by может быть NULL у демо-данных и у вакансий, чей автор
           -- уже удалён: тогда русский, как и было.
           coalesce(u.locale::text, 'ru')
    FROM interviews i
    JOIN applications a ON a.id = i.application_id
    JOIN jobs j ON j.id = a.job_id
    LEFT JOIN users u ON u.id = j.created_by
    WHERE i.id = p_interview_id;
$$;

ALTER FUNCTION product.job_of_interview(uuid) OWNER TO ezgumed;
GRANT EXECUTE ON FUNCTION product.job_of_interview(uuid) TO ishmed_app;

COMMENT ON FUNCTION product.job_of_interview(uuid) IS
  'Вакансия интервью для сборки итога. clinic_locale — язык менеджера, '
  'создавшего вакансию: на нём пишется саммари для клиники.';
