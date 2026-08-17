# Pitch sources

Эти источники используются только для проверяемых утверждений pitch-сайта, выступления и методологии. Claude не должен использовать этот файл как инженерный scope.

## Локальные данные

- `../../medical_vacancies_uzbekistan.csv` — снимок 3 600 исходных уникальных записей; продуктовый ETL принимает 3 595 записей с обязательными ключевыми полями.
- `../../export_medical_vacancies.py` — происхождение и логика выгрузки.
- `../../finalize_medical_csv.py` — финальная сборка данных.

Ограничения:

- поле откликов учитывает только источник;
- часть записей не имеет региона или даты окончания;
- встречаются ложные медицинские совпадения;
- встречаются ошибочные единицы зарплаты;
- публичные контакты не должны демонстрироваться как собственная candidate database.

## Рынок и проблема

- WHO, Uzbekistan country profile 2024–2025: https://www.who.int/about/accountability/results/who-results-report-2024-2025-eob/country-profile/2025/uzbekistan
- Clinics.uz, вакансии частных клиник: https://clinics.uz/vacancies-in-private-medical-centers
- hh.uz, вакансии врачей: https://hh.uz/vacancies/vrach
- Jobster ATS: https://ats.jobster.uz/

## Международный ориентир

- HealthTal product: https://healthtal.com/
- HealthTal pricing: https://healthtal.com/pricing/

## Персональные данные

- Государственная информация о персональных данных и регистрации баз: https://gov.uz/en/advice/61/document/2116
- Регистрация базы персональных данных: https://my.gov.uz/ru/service/1135

## Правило использования

Никакая цифра из research не переносится в pitch deck без повторной проверки первичного или авторитетного источника.
