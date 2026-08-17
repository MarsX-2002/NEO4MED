-- 014_applications_and_kb.sql
-- Два дополнения к продуктовой модели:
--   1. отклики: медик сам стучится в опубликованную вакансию;
--   2. база знаний клиники, вакансии и подразделения — рабочая, а не архив:
--      её куски эмбеддятся и попадают в контекст извлечения и объяснения матча.

-- ══ Отклики ═══════════════════════════════════════════════════════════════════
-- Поток теперь двусторонний:
--   исходящий — клиника приглашает, медик принимает  (product.invitations)
--   входящий  — клиника публикует, медик откликается  (product.applications)
--
-- Отклик — это уже добровольное согласие медика: он сам выбрал конкретную
-- вакансию. Вторая половина обоюдности — явное действие клиники «принять».
-- Контакт открывается только после него, ровно как в приглашениях.
DO $$ BEGIN
    CREATE TYPE product.application_status AS ENUM
        ('sent','viewed','accepted','declined','withdrawn');
EXCEPTION WHEN duplicate_object THEN NULL; END $$;

CREATE TABLE IF NOT EXISTS product.applications (
    id           uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    job_id       uuid NOT NULL REFERENCES product.jobs(id) ON DELETE CASCADE,
    candidate_id uuid NOT NULL REFERENCES product.candidate_profiles(id) ON DELETE CASCADE,
    status       product.application_status NOT NULL DEFAULT 'sent',
    message      text,
    applied_at   timestamptz NOT NULL DEFAULT now(),
    viewed_at    timestamptz,
    responded_at timestamptz,
    UNIQUE (job_id, candidate_id),
    CONSTRAINT ck_applications_response_time CHECK (
        (status IN ('sent','viewed')) = (responded_at IS NULL)
    )
);

COMMENT ON TABLE product.applications IS
  'Отклик медика на опубликованную вакансию. Сам факт отклика — согласие медика; '
  'контакт открывается после того, как клиника приняла отклик.';

CREATE INDEX IF NOT EXISTS ix_applications_job ON product.applications (job_id, status);
CREATE INDEX IF NOT EXISTS ix_applications_candidate ON product.applications (candidate_id, status);

-- Разрешённые типы событий журнала пополняются: ограничение было списком.
ALTER TABLE product.consent_events DROP CONSTRAINT IF EXISTS ck_consent_event_type;
ALTER TABLE product.consent_events ADD CONSTRAINT ck_consent_event_type CHECK (
    event_type IN (
        'invite_sent','invite_accepted','invite_declined','invite_withdrawn',
        'application_sent','application_accepted','application_declined',
        'contact_revealed','profile_hidden','profile_deleted','consent_given'
    )
);

ALTER TABLE product.consent_events
    ADD COLUMN IF NOT EXISTS application_id uuid REFERENCES product.applications(id) ON DELETE CASCADE;

-- ══ Раскрытие контакта по отклику ═════════════════════════════════════════════
-- Отдельная функция, а не флаг в существующей: два разных пути к контакту
-- должны читаться в коде как два разных явления, иначе через месяц никто не
-- вспомнит, какой аргумент что значит.
CREATE OR REPLACE FUNCTION product.reveal_application_contact(
    p_application_id uuid,
    p_actor_user_id  bigint
)
RETURNS TABLE (phone text, telegram_username text)
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, product
AS $$
DECLARE
    app       product.applications;
    is_clinic boolean;
    is_medic  boolean;
BEGIN
    SELECT * INTO app FROM product.applications WHERE id = p_application_id;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'отклик не найден' USING ERRCODE = 'no_data_found';
    END IF;

    IF app.status <> 'accepted' THEN
        RAISE EXCEPTION 'контакт закрыт: отклик в статусе %', app.status
            USING ERRCODE = 'insufficient_privilege';
    END IF;

    SELECT EXISTS (
        SELECT 1 FROM product.applications a
        JOIN product.jobs j            ON j.id = a.job_id
        JOIN product.clinic_members cm ON cm.clinic_id = j.clinic_id
        WHERE a.id = p_application_id AND cm.user_id = p_actor_user_id
    ) INTO is_clinic;

    SELECT EXISTS (
        SELECT 1 FROM product.candidate_profiles c
        WHERE c.id = app.candidate_id AND c.user_id = p_actor_user_id
    ) INTO is_medic;

    IF NOT (is_clinic OR is_medic) THEN
        RAISE EXCEPTION 'запрашивающий не участник этого отклика'
            USING ERRCODE = 'insufficient_privilege';
    END IF;

    INSERT INTO product.consent_events (application_id, actor_user_id, event_type, meta)
    VALUES (p_application_id, p_actor_user_id, 'contact_revealed',
            jsonb_build_object('as', CASE WHEN is_clinic THEN 'clinic' ELSE 'medic' END,
                               'via', 'application'));

    RETURN QUERY
        SELECT cc.phone, cc.telegram_username
        FROM product.candidate_contacts cc
        WHERE cc.candidate_id = app.candidate_id;
END $$;

COMMENT ON FUNCTION product.reveal_application_contact IS
  'Контакт по отклику. Требует status=accepted и участия запрашивающего.';

-- forget_candidate должен отзывать и отклики, иначе обещание об удалении
-- профиля выполняется лишь наполовину.
CREATE OR REPLACE FUNCTION product.forget_candidate(p_user_id bigint)
RETURNS void
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, product
AS $$
DECLARE cand uuid;
BEGIN
    SELECT id INTO cand FROM product.candidate_profiles WHERE user_id = p_user_id;
    IF cand IS NULL THEN RETURN; END IF;

    DELETE FROM product.candidate_contacts WHERE candidate_id = cand;
    DELETE FROM product.matches            WHERE candidate_id = cand;

    UPDATE product.invitations SET status = 'withdrawn', responded_at = now()
     WHERE candidate_id = cand AND status = 'sent';
    UPDATE product.applications SET status = 'withdrawn', responded_at = now()
     WHERE candidate_id = cand AND status IN ('sent','viewed');

    UPDATE product.candidate_profiles
       SET status = 'deleted', transcript = NULL, extraction = NULL,
           skills = '{}', credential_claims = '{}', districts = '{}'
     WHERE id = cand;

    INSERT INTO product.consent_events (actor_user_id, event_type)
    VALUES (p_user_id, 'profile_deleted');
END $$;

-- ══ База знаний ═══════════════════════════════════════════════════════════════
-- Рабочая, не архив: содержимое режется на куски, эмбеддится и подмешивается
-- в контекст при извлечении полей вакансии и при объяснении матча. Поэтому
-- документ обязан знать свою область — клиника, подразделение или вакансия.
DO $$ BEGIN
    CREATE TYPE product.kb_scope AS ENUM ('clinic','unit','job');
EXCEPTION WHEN duplicate_object THEN NULL; END $$;

CREATE TABLE IF NOT EXISTS product.kb_documents (
    id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    clinic_id   uuid NOT NULL REFERENCES product.clinics(id) ON DELETE CASCADE,
    scope       product.kb_scope NOT NULL,
    unit_id     uuid REFERENCES product.clinic_units(id) ON DELETE CASCADE,
    job_id      uuid REFERENCES product.jobs(id) ON DELETE CASCADE,
    kind        text NOT NULL DEFAULT 'note',
    title       text NOT NULL,
    content     text NOT NULL,
    content_sha text NOT NULL,
    created_by  bigint REFERENCES product.users(id),
    created_at  timestamptz NOT NULL DEFAULT now(),
    updated_at  timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT ck_kb_kind CHECK (kind IN (
        'about','requirements','interview_questions','template','benefits','note'
    )),
    -- Область и ссылка обязаны соответствовать друг другу, иначе документ
    -- вакансии окажется виден всей клинике или наоборот потеряется.
    CONSTRAINT ck_kb_scope_target CHECK (
        (scope = 'clinic' AND unit_id IS NULL AND job_id IS NULL) OR
        (scope = 'unit'   AND unit_id IS NOT NULL AND job_id IS NULL) OR
        (scope = 'job'    AND job_id IS NOT NULL AND unit_id IS NULL)
    )
);

CREATE INDEX IF NOT EXISTS ix_kb_documents_clinic ON product.kb_documents (clinic_id, scope);
CREATE INDEX IF NOT EXISTS ix_kb_documents_job ON product.kb_documents (job_id) WHERE job_id IS NOT NULL;

CREATE TABLE IF NOT EXISTS product.kb_chunks (
    id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    document_id uuid NOT NULL REFERENCES product.kb_documents(id) ON DELETE CASCADE,
    clinic_id   uuid NOT NULL REFERENCES product.clinics(id) ON DELETE CASCADE,
    chunk_no    integer NOT NULL,
    content     text NOT NULL,
    tokens      integer,
    model       text NOT NULL REFERENCES ai.embedding_models(model),
    embedding   vector(1536) NOT NULL,
    created_at  timestamptz NOT NULL DEFAULT now(),
    UNIQUE (document_id, chunk_no, model)
);

COMMENT ON COLUMN product.kb_chunks.clinic_id IS
  'Дублируется из документа намеренно: RLS должен отсекать чужой тенант без '
  'джойна, иначе политика на векторном поиске станет дорогой.';

CREATE INDEX IF NOT EXISTS ix_kb_chunks_hnsw
    ON product.kb_chunks USING hnsw (embedding vector_cosine_ops)
    WITH (m = 16, ef_construction = 64);

-- ══ Триггеры ══════════════════════════════════════════════════════════════════
DROP TRIGGER IF EXISTS tr_kb_documents_touch ON product.kb_documents;
CREATE TRIGGER tr_kb_documents_touch BEFORE UPDATE ON product.kb_documents
    FOR EACH ROW EXECUTE FUNCTION core.tg_touch_updated_at();

-- ══ Владелец, RLS, права ══════════════════════════════════════════════════════
ALTER TABLE product.applications  OWNER TO ezgumed;
ALTER TABLE product.kb_documents  OWNER TO ezgumed;
ALTER TABLE product.kb_chunks     OWNER TO ezgumed;

ALTER TABLE product.applications  ENABLE ROW LEVEL SECURITY;
ALTER TABLE product.kb_documents  ENABLE ROW LEVEL SECURITY;
ALTER TABLE product.kb_chunks     ENABLE ROW LEVEL SECURITY;

-- Отклик виден клинике по своей вакансии и медику по своему профилю.
CREATE POLICY p_applications_both_sides ON product.applications
    USING (
        EXISTS (SELECT 1 FROM product.jobs j
                 WHERE j.id = applications.job_id AND j.clinic_id = product.current_clinic_id())
        OR EXISTS (SELECT 1 FROM product.candidate_profiles c
                    WHERE c.id = applications.candidate_id AND c.user_id = product.current_user_id())
    )
    WITH CHECK (
        EXISTS (SELECT 1 FROM product.jobs j
                 WHERE j.id = applications.job_id AND j.clinic_id = product.current_clinic_id())
        OR EXISTS (SELECT 1 FROM product.candidate_profiles c
                    WHERE c.id = applications.candidate_id AND c.user_id = product.current_user_id())
    );

CREATE POLICY p_kb_documents_tenant ON product.kb_documents
    USING (clinic_id = product.current_clinic_id())
    WITH CHECK (clinic_id = product.current_clinic_id());

CREATE POLICY p_kb_chunks_tenant ON product.kb_chunks
    USING (clinic_id = product.current_clinic_id())
    WITH CHECK (clinic_id = product.current_clinic_id());

GRANT SELECT, INSERT, UPDATE ON product.applications, product.kb_documents TO ishmed_app;
GRANT SELECT, INSERT, UPDATE, DELETE ON product.kb_chunks TO ishmed_app;
GRANT EXECUTE ON FUNCTION product.reveal_application_contact(uuid, bigint) TO ishmed_app;
