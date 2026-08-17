-- 025_dental_specialties.sql
-- Стоматологические специальности.
--
-- Справочник specialties собирался под импортированный каталог с ishapi.mehnat.uz,
-- где стоматология почти не встречалась: там госучреждения с медсёстрами и
-- терапевтами. Демо-клиника у нас стоматологическая, и первая вакансия по замыслу
-- «нужен стоматолог» — без этих кодов её просто нельзя создать, внешний ключ
-- jobs.specialty -> specialties.code не даст.
--
-- Отдельную категорию «стоматолог» не вводим: по роли это врач, и правила
-- матчинга по role_category остаются рабочими. Различает специальность.

INSERT INTO product.specialties (code, name_ru, name_uz, role_category) VALUES
    ('dentist_therapist',   'Стоматолог-терапевт',       'Stomatolog-terapevt',      'doctor'),
    ('dentist_surgeon',     'Стоматолог-хирург',         'Stomatolog-jarroh',        'doctor'),
    ('dentist_orthopedist', 'Стоматолог-ортопед',        'Stomatolog-ortoped',       'doctor'),
    ('orthodontist',        'Ортодонт',                  'Ortodont',                 'doctor'),
    ('pediatric_dentist',   'Детский стоматолог',        'Bolalar stomatologi',      'doctor'),
    ('endodontist',         'Эндодонтист',               'Endodontist',              'doctor'),
    ('periodontist',        'Пародонтолог',              'Parodontolog',             'doctor'),
    ('implantologist',      'Имплантолог',               'Implantolog',              'doctor'),
    ('dental_assistant',    'Ассистент стоматолога',     'Stomatolog assistenti',    'nurse'),
    ('dental_hygienist',    'Гигиенист стоматологический', 'Stomatologik gigienist', 'nurse')
ON CONFLICT (code) DO NOTHING;

COMMENT ON TABLE product.specialties IS
  'Закрытый справочник специальностей. Пополняется миграцией, а не приложением: '
  'по нему считается матчинг, и свободный ввод развалил бы сравнение кандидатов.';
