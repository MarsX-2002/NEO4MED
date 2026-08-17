"""Узбекские тексты бота (латиница).

ВНИМАНИЕ: перевод сделан мной, носителем языка не проверен. Перед демо
нужна вычитка живым человеком — особенно формулировка согласия, там цена
ошибки не косметическая. Ключи совпадают с ru.py, отсутствующие падают
на русский автоматически.
"""

T: dict[str, str] = {
    # ── Til ───────────────────────────────────────────────────────────────────
    "choose_language": "Выберите язык / Tilni tanlang",
    "language_set": "Til: o‘zbekcha.",

    # ── Rozilik ───────────────────────────────────────────────────────────────
    "consent_ask": (
        "IshMed tibbiyot xodimlariga O‘zbekistondagi klinikalarda ish topishga yordam beradi.\n\n"
        "Davom etish uchun ma’lumotlaringizni qayta ishlashga roziligingiz kerak:\n"
        "• o‘zingiz haqingizda aytganlaringiz — mutaxassislik, tajriba, tuman, jadval, "
        "kutilayotgan maosh;\n"
        "• aloqa ma’lumotlaringiz alohida saqlanadi va siz taklifni qabul qilmaguningizcha "
        "klinikalarga <b>ko‘rsatilmaydi</b>.\n\n"
        "Profilni istalgan vaqtda yashirish yoki o‘chirish mumkin."
    ),
    "consent_accept": "Roziman",
    "consent_details": "Batafsil",
    "consent_details_text": (
        "Ma’lumotlar bilan nima bo‘ladi:\n\n"
        "1. Sizning hikoyangiz profil kartasiga aylanadi. Uni to‘liq ko‘rasiz va "
        "saqlashdan oldin har qanday maydonni tuzatishingiz mumkin.\n"
        "2. Klinika kartani <b>aloqa ma’lumotlarisiz</b> ko‘radi: mutaxassislik, tajriba, "
        "tuman, jadval.\n"
        "3. Klinika taklif yuborishi mumkin. Siz «Qabul qilaman» tugmasini bosmaguningizcha "
        "telefon va username mavjud emas — bu ma’lumotlar bazasi darajasidagi cheklov, "
        "shunchaki va’da emas.\n"
        "4. Rad etish sizning ma’lumotlaringizni oshkor qilmaydi.\n\n"
        "Bu bosqichda barcha malakalar — sizning so‘zlaringizdan, biz ularni "
        "tekshirmadik va «tekshirilgan» deb yozmaymiz."
    ),
    "consent_required": "Rozilik bo‘lmasa, davom etib bo‘lmaydi.",
    "consent_saved": "Rahmat. Rozilik qayd etildi.",

    # ── Asosiy menyu ──────────────────────────────────────────────────────────
    "medic_welcome": (
        "Tayyor. Keyingi tartib shunday: siz vakansiyani tanlaysiz, bot suhbat "
        "o‘tkazadi, klinika javoblaringizni o‘qiydi.\n\n"
        "Ochiq vakansiyalar — /vacancies. Sizning kartangiz — /profile.\n\n"
        "Savollarga <b>ovoz yoki matn bilan</b> javob berish mumkin — qulay bo‘lganicha."
    ),
    "menu_profile": "Mening profilim",
    "menu_invitations": "Takliflar",
    "menu_help": "Yordam",
    "menu_vacancies": "Vakansiyalar",

    # ── Klinikalar uchun ──────────────────────────────────────────────────────
    "clinic_redirect": (
        "Bu bot — tibbiyot xodimlari uchun.\n\n"
        "Agar siz klinika bo‘lsangiz va xodim izlayotgan bo‘lsangiz, kabinet shu manzilda: {url}\n"
        "U yerda vakansiyani matn bilan yaratish yoki ovoz bilan aytib berish mumkin."
    ),
    "i_am_clinic": "Men klinikaman",

    # ── Xizmat xabarlari ──────────────────────────────────────────────────────
    "unknown_message": (
        "Tushunmadim. Men nimalarni bilaman:\n\n"
        "/vacancies — ochiq vakansiyalar\n"
        "/profile — sizning kartangiz\n"
        "/help — barcha buyruqlar"
    ),
    "file_not_supported": (
        "Faylni oldim, lekin rezyumeni hozircha o‘qimaymiz — o‘qigan bo‘lib "
        "ko‘rsatmayman ham.\n\n"
        "O‘zingiz haqingizda suhbatda gapirib bering: /vacancies orqali vakansiyani "
        "tanlang, savollarni tinglash va ovoz bilan javob berish mumkin."
    ),
    "error_generic": (
        "Bizning tomonda xatolik yuz berdi. Yana bir marta urinib ko‘ring — "
        "ma’lumotlar yo‘qolmadi."
    ),
    "help_text": (
        "Buyruqlar:\n"
        "/start — boshidan boshlash\n"
        "/vacancies — ochiq vakansiyalar\n"
        "/profile — profilni ko‘rsatish\n"
        "/language — tilni o‘zgartirish\n"
        "/forget — profil va aloqa ma’lumotini o‘chirish\n"
        "/help — bu yordam"
    ),

    # ── Tibbiyot xodimining kartasi ───────────────────────────────────────────
    "profile_title": "<b>Sizning kartangiz</b>",
    "profile_empty": (
        "Hozircha karta yo‘q — u suhbatdan yig‘iladi.\n\n"
        "Vakansiyani tanlang (/vacancies), savollarga javob bering, va klinika "
        "ko‘radigan ma’lumot shu yerda paydo bo‘ladi."
    ),
    "profile_role": "Kimsiz: {value}",
    "profile_experience": "Tajriba: {value}",
    "profile_experience_unset": "Tajriba: ko‘rsatilmagan",
    "profile_skills": "Ko‘nikmalar: {value}",
    "profile_languages": "Tillar: {value}",
    "profile_salary": "Maosh bo‘yicha kutilma: {value}",
    "profile_contact_yes": (
        "Telefon: ko‘rsatilgan. Klinika uni faqat arizangizni qabul qilgandan keyin ko‘radi."
    ),
    "profile_contact_no": (
        "Telefon: ko‘rsatilmagan. U bo‘lmasa klinika sizga qo‘ng‘iroq qila olmaydi."
    ),
    "profile_stats": "Arizalar: {apps}. O‘tilgan suhbatlar: {done}.",
    "profile_status_draft": (
        "Kartani faqat siz ariza topshirgan klinikalar ko‘radi. "
        "Umumiy qidiruvda siz yo‘qsiz."
    ),
    "profile_status_hidden": "Karta yashirilgan: klinikalar uni ko‘rmaydi.",
    "profile_source_note": (
        "Kartadagi hamma narsa — sizning so‘zlaringizdan, suhbatdan. Biz hech narsani "
        "tekshirmadik va «tekshirilgan» deb yozmaymiz."
    ),

    # ── Aloqa ma’lumoti ───────────────────────────────────────────────────────
    "contact_ask": (
        "Oxirgisi. Agar klinika arizangizni qabul qilsa, sizga qo‘ng‘iroq qilishi kerak.\n\n"
        "Telefon <b>kartadan alohida</b> saqlanadi va klinikaga faqat ariza qabul "
        "qilingandan keyin ochiladi. Har bir ochilish qayd etiladi — kim ko‘rganini "
        "bilib olasiz."
    ),
    "btn_share_contact": "Raqamimni yuborish",
    "btn_contact_later": "Hozir emas",
    "contact_saved": (
        "Telefon saqlandi. Klinika uni faqat arizani qabul qilgandan keyin oladi."
    ),
    "contact_skipped": (
        "Yaxshi. Telefon bo‘lmasa klinika faqat Telegram orqali javob bera oladi — "
        "raqam qo‘shmoqchi bo‘lsangiz, /profile ni ochingiz."
    ),
    "contact_failed": (
        "Raqamni saqlab bo‘lmadi. /profile orqali yana urinib ko‘ring."
    ),
    "contact_needs_profile": (
        "Avval istalgan vakansiya bo‘yicha suhbatdan o‘ting — /vacancies. "
        "Undan keyin telefonni bog‘lash mumkin bo‘ladi."
    ),

    # ── Klinika arizani qabul qildi ───────────────────────────────────────────
    "accepted_notice": (
        "<b>Yaxshi xabar.</b>\n\n"
        "«{clinic}» klinikasi «{job}» vakansiyasiga arizangizni qabul qildi. "
        "Ya\u2019ni javoblaringiz o\u2018qildi va siz bilan tanishmoqchilar."
    ),
    "accepted_needs_contact": (
        "Klinika qo\u2018ng\u2018iroq qilishi uchun raqamingiz kerak. Telefon faqat shu "
        "klinikaga va faqat hozir, sizni chaqirgandan keyin ochiladi."
    ),


    # ── Profilni o‘chirish ────────────────────────────────────────────────────
    "forget_ask": (
        "Profil o‘chirilsinmi?\n\n"
        "Hammasi o‘chadi: karta, ko‘nikmalar, telefon. Klinika allaqachon o‘qigan "
        "suhbat javoblari unda qoladi — ularni qaytarib olishga imkonimiz yo‘q va "
        "bor deb ko‘rsatmaymiz."
    ),
    "btn_forget_yes": "Ha, o‘chirilsin",
    "btn_forget_no": "Bekor qilish",
    "forget_done": (
        "Profil va telefon o‘chirildi. Faol takliflar qaytarib olindi.\n\n"
        "Yana kerak bo‘lsa — /start."
    ),
    "forget_nothing": "O‘chiradigan narsa yo‘q: hozircha profil yo‘q.",
    "forget_cancelled": "Bekor qilindi, hech narsa o‘chirilmadi.",

    # ── Ariza oldidan rozilik ─────────────────────────────────────────────────
    "consent_before_apply": (
        "Ariza topshirishdan oldin roziligingiz kerak — aks holda "
        "aytganlaringizni yozib olishga haqimiz yo‘q."
    ),

    # ══ Vakansiyalar va avto-suhbat ═══════════════════════════════════════════
    # Reja savollarining o‘zi klinika tilida qoladi: ularni menejer yozgan va
    # tasdiqlagan, tasdiqlangan matnni tarjima qilishga haqimiz yo‘q.

    # ── Birliklar va lug‘atlar ────────────────────────────────────────────────
    "currency": "so‘m",
    "salary_unset": "ko‘rsatilmagan",
    # «dan» va «gacha» — qo‘shimchalar, so‘zga qo‘shib yoziladi: «so‘mdan».
    "salary_from": "{value}dan",
    "salary_to": "{value}gacha",
    # O‘zbek tilida son bilan kelgan ot ko‘plik qo‘shimchasini olmaydi:
    # uch shakl ham bir xil, ru.py bilan kalitlar mos bo‘lishi uchun saqlanadi.
    "years_from_one": "yil",
    "years_from_few": "yil",
    "years_from_many": "yil",
    "months_from_one": "oy",
    "months_from_few": "oy",
    "months_from_many": "oy",
    "schedule_full_time": "to‘liq ish kuni",
    "schedule_part_time": "yarim stavka",
    "schedule_shift": "smenali",
    "schedule_night": "tungi",
    "schedule_weekend": "dam olish kunlari",

    # ── Vakansiya kartasi ─────────────────────────────────────────────────────
    "job_clinic": "Klinika: {name}",
    "job_specialty": "Mutaxassislik: {name}",
    "job_experience": "Tajriba: {value}dan",
    "job_salary": "To‘lov: {value}",
    "job_schedule": "Ish jadvali: {value}",
    "job_skills": "Talablar: {value}",
    "job_interview_note": (
        "Botda suhbat: {count} ta {questions_word}, ovoz bilan javob berish mumkin."
    ),
    "questions_one": "savol",
    "questions_few": "savol",
    "questions_many": "savol",

    # ── Tugmalar ──────────────────────────────────────────────────────────────
    "btn_apply": "Ariza topshirish va suhbatdan o‘tish",
    "btn_continue": "Suhbatni davom ettirish",
    "btn_jobs_list": "Vakansiyalar ro‘yxatiga",
    "btn_other_jobs": "Boshqa vakansiyalar",
    "btn_my_applications": "Mening arizalarim",
    "btn_listen": "Savolni tinglash",
    "btn_skip": "Savolni o‘tkazib yuborish",
    "btn_stop": "Tugatish",
    "btn_prev": "Orqaga",
    "btn_more": "Yana",

    # ── Vakansiyalar ro‘yxati ─────────────────────────────────────────────────
    "jobs_header": "Ochiq vakansiyalar ({first}–{last}):",
    "jobs_empty": "Hozircha ochiq vakansiyalar yo‘q. Klinika e’lon qilishi bilan ko‘rsatamiz.",
    "jobs_end": "Vakansiyalar shu bilan tugadi.",
    "job_closed": "Vakansiya yopilgan.",
    "job_gone": "Bu vakansiya endi ochiq emas. Boshqalarini ko‘ring — /vacancies",

    # ── Arizalar ──────────────────────────────────────────────────────────────
    "applications_title": "<b>Sizning arizalaringiz</b>",
    "applications_empty": "Siz hali hech qayerga ariza topshirmagansiz.",
    "application_line": "• {title} — {clinic}\n  ariza {status}, {progress}",
    "app_status_sent": "yuborilgan",
    "app_status_viewed": "ko‘rilgan",
    "app_status_accepted": "qabul qilingan",
    "app_status_declined": "rad etilgan",
    "app_status_withdrawn": "qaytarib olingan",
    "progress_done": "suhbat yakunlangan",
    "progress_running": "suhbat: {total} tadan {answered} ta",
    "progress_none": "suhbat boshlanmagan",
    "applied_toast": "Ariza yuborildi",
    "already_interviewed": (
        "Siz bu vakansiya bo‘yicha suhbatdan allaqachon o‘tgansiz. Uni qaytadan "
        "o‘tib bo‘lmaydi — klinika javoblaringizni ko‘rib turibdi."
    ),

    # ── Suhbat jarayoni ───────────────────────────────────────────────────────
    "interview_intro": (
        "Hozir «{clinic}» klinikasidan {count} ta {questions_word} beraman. "
        "Matn yoki ovozli xabar bilan javob berishingiz mumkin — qulay bo‘lganicha.\n\n"
        "Savollarni bot beradi, qarorni klinika qabul qiladi: javoblaringizni "
        "xodim izlayotgan odam ko‘radi."
    ),
    "question_header": "{total} savoldan {ord}-savol:",
    "resume_question": "Davom etamiz. {total} savoldan {ord}-savol:",
    "resume_follow_up": "Davom etamiz. Oldingi savol bo‘yicha aniqlashtiraman:",
    "no_active_interview": "Tugallanmagan suhbatlar yo‘q.",
    "interview_not_running": "Suhbat ketmayapti",
    "no_pending_question": "Hozir berilgan savol yo‘q",
    "skipped_toast": "O‘tkazib yuborildi",

    # ── Ovoz ──────────────────────────────────────────────────────────────────
    "voice_recording": "Yozib olayapman…",
    "voice_failed": "Ovozga o‘girib bo‘lmadi. Savol yuqorida matn bilan turibdi.",
    "voice_too_long": "Ovozli xabar juda uzun. Qisqaroq yoki matn bilan javob bering.",
    "voice_listening": "Tinglayapman…",
    "voice_not_recognized": (
        "Yozuvni ajrata olmadim. Yana urinib ko‘ring yoki matn bilan yozing."
    ),
    "voice_transcribed": "Yozib oldim: {text}",

    # ── Yakun ─────────────────────────────────────────────────────────────────
    "interview_wrapping": "Rahmat, savollar shu bilan tugadi. Klinika uchun yakun tayyorlayapman…",
    "interview_done": (
        "Suhbat yakunlandi.\n"
        "Javob berilgan savollar: {answered}.\n\n"
        "Klinika javoblaringizni oladi va agar mos kelsangiz bog‘lanadi. "
        "Aloqa ma’lumotlaringiz faqat klinika arizani qabul qilgandan keyin ochiladi."
    ),

    # ══ Sharhlar boti @ishmedsifatbot ═════════════════════════════════════════
    "review_greeting": (
        "<b>{title}</b>\n{clinic}\n\n"
        "Sizga qanday xizmat ko‘rsatilganini baholang. Bu yarim daqiqa vaqt oladi.\n\n"
        "Biz faqat xizmat ko‘rsatish haqida so‘raymiz, hech qachon sog‘liq, tashxis "
        "yoki davolash haqida emas. Sharhni faqat klinika ko‘radi."
    ),
    "review_scale_hint": "1 — yomon · 5 — a’lo",
    # Ro‘yxatdagi til — o‘tish mumkin bo‘lgan til, joriy til emas.
    "review_switch_language": "Русский",
    "review_ask_details": (
        "Rahmat, 5 tadan {rating} ta.\n\n"
        "Batafsil qo‘shmoqchimisiz? <b>Matn yozishingiz</b>, "
        "<b>ovozli xabar qoldirishingiz</b> yoki <b>rasm yuborishingiz</b> mumkin.\n\n"
        "Yoki «Tayyor» tugmasini bosing — baho ham yetarli."
    ),
    "review_btn_done": "Tayyor",
    "review_no_qr": (
        "Assalomu alaykum! Bu bot klinikadagi qabul haqida sharhlarni qabul qiladi.\n\n"
        "Sharh qoldirish uchun kamerani xona yonidagi QR-kodga to‘g‘rilang — "
        "u sizni kerakli xona bilan botga olib keladi."
    ),
    "review_not_found": (
        "Bunday so‘rovnoma topilmadi. Kod eskirgan bo‘lishi mumkin — "
        "qabul stolida aniqlashtiring."
    ),
    "review_closed": "Bu so‘rovnoma yopilgan. Qiziqishingiz uchun rahmat!",
    "review_duplicate": "Bu xona bo‘yicha sharhingiz bugun allaqachon qabul qilingan. Rahmat!",
    "review_save_failed": "Sharhni saqlab bo‘lmadi. Birozdan keyin yana urinib ko‘ring.",
    "review_comment_saved": (
        "Yozib oldik. Yana qo‘shishingiz yoki «Tayyor» tugmasini bosishingiz mumkin."
    ),
    "review_voice_listening": "Yozuvni tinglayapman…",
    "review_voice_transcribed": (
        "Yozib oldik. Biz shunday tushundik:\n\n<i>{text}</i>\n\n"
        "Agar biror narsa noto‘g‘ri bo‘lsa, matn bilan yozing."
    ),
    "review_voice_saved": "Ovozli xabarni yozib oldik. Klinika uni tinglaydi.",
    "review_photo_saved": "Rasm biriktirildi.",
    "review_finished": (
        "Rahmat! «{title}» haqidagi sharhingiz klinikaga yetkazildi.\n\n"
        "U hech qayerda ommaga ko‘rinmaydi."
    ),
    "review_finished_fallback": "qabul",

    # ══ O‘z kartasi va umumiy qidiruv (И4) ════════════════════════════════════
    "profile_offer": (
        "Klinikalar sizni o‘zlari topishini xohlaysizmi?\n\n"
        "Kartani to‘ldiring — yetti qadam, hammasi tugmalar bilan. Klinikalar "
        "mutaxassislik, tajriba, tuman va jadvalni ko‘radi. <b>Ism va telefonni "
        "ko‘rmaydi</b>, taklifni o‘zingiz qabul qilmaguningizcha."
    ),
    "profile_needs_start": "Avval /start ni bosing — tilni bilishimiz va rozilik olishimiz kerak.",
    "profile_role_unset": "Kimsiz: ko‘rsatilmagan",
    "profile_specialty": "Mutaxassislik: {value}",
    "profile_districts": "Tumanlar: {value}",
    "profile_schedule": "Jadval: {value}",
    "profile_pool_yes": (
        "Klinikalar sizni qidiruvda ko‘radi. Takliflar shu yerga, «Takliflar» ga keladi."
    ),
    "profile_pool_no": (
        "Hozircha klinikalar qidiruvida yo‘qsiz. Karta qidiruvga chiqarilmaguncha, "
        "uni faqat o‘zingiz ariza yuborgan klinikalar ko‘radi."
    ),

    # ── Shakl ─────────────────────────────────────────────────────────────────
    "form_intro": (
        "<b>Sizning kartangiz</b>\n\n"
        "Bir nechta savol, javoblari tayyor — tugmalardan tanlang. "
        "Yarmida to‘xtasangiz, shu joyga qaytasiz, hech narsa yo‘qolmaydi."
    ),
    "form_ask_role": "Kasbingiz bo‘yicha kimsiz?",
    "form_ask_specialty": "Mutaxassislikni aniqlashtiring.",
    "form_ask_experience": "Necha yildan beri ishlayapsiz?",
    "form_ask_districts": (
        "Qaysi tumanlarda ishlash qulay? Mos keladiganlarini belgilab, «Tayyor» ni bosing."
    ),
    "form_ask_schedule": "Qaysi jadval sizga mos? Bir nechtasini tanlash mumkin.",
    "form_ask_salary": (
        "Oyiga qaysi summadan boshlab takliflarni ko‘rib chiqasiz?\n\n"
        "Klinika bu raqamni ko‘radi. Ko‘rsatmasangiz, kamroq takliflar ham keladi."
    ),
    "form_saved": "Yozib oldik",
    "form_save_failed": "Saqlanmadi. Yana bir marta urinib ko‘ring.",
    "form_pick_at_least_one": "Kamida bittasini belgilang.",
    "form_pick_field": "Nimani o‘zgartiramiz?",
    "form_ready": (
        "Karta to‘ldirildi. Hozircha qidiruvda emas — «Klinikalarga ko‘rsatish» ni "
        "bosing, sizni topa boshlaydilar."
    ),
    "field_role": "Kasb",
    "field_specialty": "Mutaxassislik",
    "field_experience": "Tajriba",
    "field_districts": "Tumanlar",
    "field_schedule": "Jadval",
    "field_salary": "To‘lov",
    "btn_create_profile": "Profil yaratish",
    "btn_profile_edit": "O‘zgartirish",
    "btn_form_done": "Tayyor",
    "btn_salary_skip": "Ko‘rsatmaslik",
    "exp_less_year": "bir yildan kam",
    "years_one": "yil",
    "years_few": "yil",
    "years_many": "yil",

    # ── Umumiy qidiruv ────────────────────────────────────────────────────────
    "btn_pool_join": "Klinikalarga ko‘rsatish",
    "btn_pool_leave": "Qidiruvdan olish",
    "pool_incomplete": (
        "Qidiruvga tushish uchun yetishmaydi: {fields}.\n\n"
        "To‘liq bo‘lmagan kartani klinikalar filtrlar orqali topmaydi, shuning uchun "
        "ko‘rsatishdan ma’no yo‘q — to‘ldiraylik."
    ),
    "pool_joined_toast": "Qidiruvdasiz",
    "pool_joined": (
        "<b>Tayyor. Klinikalar kartangizni ko‘radi.</b>\n\n"
        "Nima ko‘rinadi: mutaxassislik, tajriba, tumanlar, jadval va to‘lov kutilmasi.\n"
        "Nima ko‘rinmaydi: ism, telefon, Telegram.\n\n"
        "Klinika taklif yuborishi mumkin. Telefon unga faqat siz taklifni qabul "
        "qilganingizdan keyin ochiladi. Kartani qidiruvdan istalgan vaqtda olish "
        "mumkin — /profile."
    ),
    "pool_needs_contact": (
        "Telefon qoldi. U bo‘lmasa, siz taklifni qabul qilganingizdan keyin ham "
        "klinika qo‘ng‘iroq qila olmaydi."
    ),
    "pool_left_toast": "Qidiruvdan oldik",
    "pool_left": (
        "Karta qidiruvdan olindi. Ma’lumotlar saqlanib qoldi — bitta tugma bilan "
        "qaytish mumkin.\n\nHammasini butunlay o‘chirish kerak bo‘lsa — /forget."
    ),

    # ── Takliflar ─────────────────────────────────────────────────────────────
    "invite_notice": (
        "<b>«{clinic}» klinikasi sizni taklif qiladi</b>\n\n"
        "Vakansiya: {job}\n\n"
        "Qiziqsangiz — taklifni qabul qiling, klinikadan bir nechta savol beramiz. "
        "Telefon unga faqat siz qabul qilganingizdan keyin ochiladi."
    ),
    "invitations_title": "<b>Sizning takliflaringiz</b>",
    "invitations_empty": (
        "Hozircha taklif yo‘q. Kartangiz qidiruvda — klinikalar uni ko‘radi.\n\n"
        "Kutish shart emas: vakansiyalarni ko‘rib, o‘zingiz ariza yuboring."
    ),
    "invitations_empty_hidden": (
        "<b>Taklif yo‘q va hozircha bo‘lmaydi.</b>\n\n"
        "Kartangiz qidiruvga chiqarilmagan, shuning uchun klinikalar sizni topmaydi."
    ),
    "invite_card_head": "<b>{clinic}</b>\n{job}",
    "invite_accepted_note": "Taklif qabul qilindi, suhbat o‘tdi.",
    "invite_accepted_go": "Taklif qabul qilindi. Klinika savollariga javob berish qoldi.",
    "invite_job_closed": "Klinika bu vakansiyani yopdi.",
    "btn_invite_accept": "Taklifni qabul qilish",
    "btn_invite_decline": "Qiziqmaydi",
    "invite_accepted": (
        "Qabul qildik. Endi klinika telefoningizni ko‘radi — o‘zi siz bilan "
        "bog‘lanadi.\n\nQuyida vakansiya: savollarga javob bersangiz, menejer "
        "qo‘ng‘iroqdan oldin javoblaringizni o‘qiydi."
    ),
    "invite_declined": (
        "Rad javobini yetkazdik. Bu klinika shu vakansiya bo‘yicha sizni boshqa "
        "bezovta qilmaydi, telefon ham ochilmadi."
    ),
    "invite_already_answered": "Bu taklifga siz allaqachon javob bergansiz.",
    "invite_failed": "Javobni yetkaza olmadik. Yana urinib ko‘ring.",
}
