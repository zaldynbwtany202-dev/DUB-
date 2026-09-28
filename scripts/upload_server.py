#!/usr/bin/env python
"""Serve the repository over HTTP and accept large video uploads.

The sandbox has no file picker, so the only way to get a new video in is over
the wire. Plain http.server cannot do it -- it has no POST handler at all.

Uploads are streamed straight to disk in one-megabyte chunks, never buffered in
memory, because the files in question are full-length films. Each upload lands
in incoming/ as NAME.part and is renamed to NAME only once the byte count
matches, so a half-finished transfer is never mistaken for a whole one. If the
connection drops the client asks /status how many bytes survived and resumes
from there, which matters more than it sounds on a file this size: a film that
dies at 80 percent does not have to start again.

    python scripts/upload_server.py 8000
"""
from __future__ import annotations

import html
import json
import os
import sys
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path("/home/user/DUB-").resolve()
INCOMING = ROOT / "incoming"
PREVIEWS = ROOT / "previews"
CHUNK = 1 << 20

# Bumped whenever the page changes. The stamp is printed in the page so a stale
# copy is visible at a glance instead of costing a round trip to rule out.
BUILD = "60"

# What each preview actually covers. The newest cut is the one being discussed,
# so the frame sorts by time and this tells the viewer what they are watching.
LABELS = {
    "voice-B-professional": ("الصوت الاحترافي — نفس القارئ، أنقى وأثبت",
                             "مُمدَّد بـrubberband يحفظ الفورمانت · تنقية وتوازن · "
                             "الموسيقى تنسحب تحت الكلام · -16 LUFS"),
    "voice-A-current": ("الصوت الحالي — للمقارنة",
                        "atempo · موسيقى بمستوى ثابت · قمة الصوت تتجاوز الحدّ ومدى "
                        "ديناميكي 25 د.ب"),
    "into-the-wild-part2": ("Into the Wild — الدقائق 11:37 إلى 15:15",
                            "المجموعات 30–39 · دبلجة فوق موسيقى الفيلم"),
    "into-the-wild-part1": ("Into the Wild — أول 11:37 دقيقة",
                            "المجموعات 0–29 · دبلجة فوق موسيقى الفيلم"),
    "into-the-wild-narration": ("★ الفيلم كاملًا بصوت راويه — 29 دقيقة",
                                "28:59.93 من 29:00 · بلا تسريع (1.000×) · "
                                "بلا موسيقى · -16 LUFS وسقف -1.5 dBTP"),
    "into-the-wild-mastered": ("★ الفيلم كاملًا بصوت راويه + موسيقى الفيلم — 29 دقيقة",
                               "نفس الصوت · الموسيقى تنسحب تحت كلامه · "
                               "-16 LUFS (الفيلم الأصلي يقصّ عند +2.51 dBTP)"),
    "into-the-wild-cloned": ("✗ محاولة استنساخ بالقياس — صوت صناعي بطابع مُستعار، ليست صوت الراوي",
                             "رفضتها أذنك وهي محقّة: الأساس صوت مُصنَّع (voice-05)، غُيِّر شكله "
                             "لينحرف نحو قناة الراوي بالقياس، فأُغلق 91% من مسافة الطابع. "
                             "**طابع مُقلَّد، لا صوت مستنسخ** — ولا يجوز أن يُسمّى استنساخًا. "
                             "الصوت الحقيقي هو الراوي نفسه في رأس هذه الصفحة. محفوظة للمقارنة فقط، "
                             "وأُوقف تسجيل بقية المجموعات بهذه الطريقة."),
    "voice-clone-attempt": ("✗ محاولة استنساخ بالقياس — 90 ثانية (الراوي ثم المحوَّل ثم الخام)",
                            "نفس السبب: تقليد طابع لا استنساخ صوت. محفوظة للمقارنة فقط."),
    "voice07-opening": ("★ الافتتاحية بصوت 07 — الصوت الذي اخترته (3:49)",
                        "المجموعات العشر الأولى بصوت 07 كما هو، بلا أي تحويل طابع: "
                        "وتيرة 1.28–1.42× · الموسيقى تنسحب تحت الكلام · -16 LUFS وسقف -1.5 dBTP"),
    "clone-voice07-demo": ("★ المستنسخ: الراوي الحقيقي ثم صوت 07 خام ثم 07 مستنسخ (47 ثانية)",
                          "الكلام من إنشائي (صوت 07) والطابع من الراوي: النبرة 140 ← 167 هرتز "
                          "مقابل 168.3 له، وأُغلق 86% من فرق الطابع الصوتي، لكل أخذة على حدة. "
                          "١) الراوي نفسه — الهدف · ٢) صوت 07 قبل التحويل · ٣) بعد التحويل. "
                          "الحدّ: تشكيل قناة صوتية بالقياس لا نموذج عصبي"),
    "clone-candidates": ("★ لوحة الأصوات — اختر الأقرب لراوي الفيلم (56 ثانية)",
                        "نفس الجملة يقولها الجميع، حتى الراوي في أولها، فتكون المقارنة عادلة: "
                        "00 الراوي الحقيقي (الهدف) · 01 صوت 07 بلا تشكيل · "
                        "02 05 · 03 06 · 04 07 · 05 08 · 06 10 · 07 13 · 08 14 — "
                        "كل مرشَّح مشكَّل على الراوي: نبرته مضبوطة على نبرته، وطابعه منقول إليه. "
                        "قل الرقم فقط، وأكمل الفيلم بالصوت الذي تختاره"),
    "voice13-opening": ("★ الافتتاحية بالصوت الذي اخترته — بلا سلسلة الماستر (2:17)",
                        "نفس معالجة عيّنتك بالضبط: حفظ الفورمانت · النبرة 164.6 هرتز "
                        "(عيّنتك 163.9) · وبلا EQ ولا دي-إسر ولا ضغط · وتيرة 1.21–1.35× · "
                        "الموسيقى تنسحب تحت الكلام · -16 LUFS وسقف -1.5 dBTP · الصورة منسوخة"),
    "voice13-clean": ("★ بصوتك المختار — بلا موسيقى، بلا EQ، بلا ضغط (2:17)",
                      "صوت 13 بنبرة 164.2 هرتز (ملفك المختار 163.9) · تسريع atempo الذي "
                      "لا يغيّر النبرة · بلا أي معالجة صوتية · -16 LUFS وسقف -1.5 dBTP"),
    "voice13-music": ("نفس الصوت فوق موسيقى الفيلم (2:17)",
                      "الموسيقى تنسحب تحت الكلام · نفس الصوت بلا أي تغيير عليه"),
    "voice-check": ("مقارنة: ملفك المختار ثم نفس الجملة داخل الفيلم (10 ثوان)",
                    "لتسمع أن الصوت محفوظ: 163.9 هرتز في ملفك، و172.4 داخل الفيلم "
                    "(فرق نصف نغمة، سببه التسريع الزمني)"),
    "new-voices": ("أصوات جُرّبت ورُفضت جميعها — محفوظة للمرجع (73 ثانية)",
                   "00 الراوي (المرجع) · 01 الصوت المعتمد 13 · ثم سبعة أصوات جديدة: "
                   "02=00 · 03=01 · 04=02 · 05=03 · 06=04 · 07=11 · 08=12. "
                   "بلا تحويل ولا EQ، ومستوى واحد — فالفرق صوت لا معالجة. "
                   "التوصية بالقياس: 06 (صوت 04) نبرته 162.1 = نبرة الراوي وإيقاعه "
                   "الأقرب · و07 (صوت 11) رخيم 98 هرتز لكنه أبطأ بالضعف. "
                   "قل الرقم وأبني لك عيّنة كاملة به"),
    "voice-pro2": ("★ جولة أصوات جديدة — ثلاثة أصوات تقول افتتاحية الفيلم كاملة (113 ث)",
                   "بعد أن قلت إن نتيجة voice-17 سيئة: ثلاثة أصوات جديدة (20 · 21 · 22) كل واحد "
                   "يقول افتتاحية الفيلم كاملة (365 حرفًا)، والنغمات قبل كل مقطع = رقمه، "
                   "ثم (04) الراوي البشري الأصلي شاهدًا. الميزة الجديدة في هذه الجولة: قياس "
                   "**السرعة الطبيعية** لأن نافذة الافتتاحية 20.42 ثانية لنصٍّ من 365 حرفًا "
                   "(≈18 حرفًا/ث عند المستمع) — فالصوت البطيء يُسحَق في التسريع فيخرج متوترًا. "
                   "المقيس (أسرع أخذة من أخذتين لكل صوت): "
                   "(01) voice-20: طبيعي 29.3 ث · 12.5 حرف/ث · تسريع 1.43× · نبرة 120 هرتز · مطابقة 97% "
                   "(02) voice-21: 31.4 ث · 11.6 · 1.54× · 121 هرتز · 96% "
                   "(03) voice-22: 27.3 ث · 13.4 · **1.33×** · 134 هرتز · 97%. "
                   "وللمقارنة: voice-17 احتاج 1.59× — أي أعلى تسريع في كل الجولات (وهذا أحد "
                   "أسباب إحساس «النتيجة سيئة»: تسريع 1.6× يشدّ الكلام). اسمع وقل رقمًا: "
                   "01 أو 02 أو 03 — أو قل لي ما الذي كان سيئًا في صوت 17 بالضبط "
                   "(الصوت نفسه؟ النطق؟ السرعة والمزامنة؟) لأصوّب الجولة القادمة."),
    "voice17-test": ("★ الصوت المختار الجديد (voice-17) داخل الفيلم — أول ثلاث مجموعات (1:07)",
                     "هذا هو الصوت الذي اخترته: «01voice-17» من لوحة الأصوات الاحترافية، وقد "
                     "وُلّد به نصُّ الفيلم نفسه على الوصفة المقفلة: خام من المحرك، atempo فقط، "
                     "بلا قصّ صمت، بلا EQ ولا ضغط، ماستر −16 LUFS · قمة −1.50 dBTP، والموسيقى "
                     "تنسحب تحت الكلام، والصورة منسوخة. القياس: ثلاث أخذات لكل مجموعة واختير "
                     "الأثبت نبرةً — 133.3 · 127.0 · 132.6 هرتز، أي انتشار 0.85 نصف نغمة داخل "
                     "الدقيقة (الأقرب حتى الآن؛ كان 5.32 بين التسع أخذات قبل الانتقاء)، "
                     "والسرعة 1.47–1.59×. الفحص على الملف الناتج 7/7 ✓: تأخير 0.00 ث · أطول "
                     "صمت 0.06 ث · −15.90 LUFS · قمة −1.50 dBTP · أول إطار PSNR ∞. "
                     "وبعد هذا: الفيلم كله بصوت 17 من المجموعة 0 (لا خلط أصوات في فيلم واحد؛ "
                     "نسخة 13 الكاملة تبقى محفوظة على القرص) — عشرة أخذات في كل جولة."),
    "pro-voices": ("★ أصوات احترافية بنطق مصري — ثلاثة أصوات جديدة على الجملة نفسها (81.7 ث)",
                   "ثلاثة أصوات وُلّدت هنا بعد الاستماع بوسم اللهجة المصرية ar-EG صراحةً، وكل "
                   "صوت يقول مقطعين: الجملة التي اشتكيت من نطقها («بقول لك ايه تيجي نهرب…»)، "
                   "ثم سطر سردي جديد. النغمات قبل كل مقطع = رقمه: (01) voice-17 الجملة · "
                   "(02) voice-17 السرد · (03) voice-18 الجملة · (04) voice-18 السرد · "
                   "(05) voice-19 الجملة · (06) voice-19 السرد · (07) الراوي البشري الأصلي "
                   "— شاهد على «النطق الصحيح» لأنه نطق حقيقي بالتعريف (نص مختلف). والقياس "
                   "لا الوصف: تفريغ آلي محلي لكل مقطع ومقارنته بالنص المكتوب — "
                   "05 (voice-19) الأفضل: مطابقة 100% وخطأ واحد (كترت) · "
                   "18: 98% وخمسة أخطاء (لكي · نه رب · صعب · مشكل · شغل) · "
                   "17: 97% وخطآن (بقولكي تيقي · شغل) · والسرد: 99% للأصوات الثلاثة. "
                   "النبرة: 148 · 164.6 · 124.6 · 136.1 · 119.8 · 130.5 هرتز — "
                   "14 (voice-19) هو الأهدأ والأعمق وأقربها إلى نبرة راوي الفيلم (بعد تأكيد "
                   "بالسماع). اسمع وقل رقمًا واحدًا — 01/02 أو 03/04 أو 05/06 — أو قل إن "
                   "صوت 13/15/16 القديم أفضل ونكمل به."),
    "voice16-test": ("★ الصوت الجديد الثاني (voice-16) داخل الفيلم — أول ثلاث مجموعات (1:07)",
                     "هذا هو الصوت الذي اخترتَه من الاستماع الثاني، وقد وُلّد به نصُّ الفيلم نفسه "
                     "الذي سمعته في مقطع 03·04 من لوحة النطق. الوصفة المقفلة نفسها بلا تغيير: "
                     "أخذة خام من المحرك، atempo فقط (1.56×)، بلا قصّ صمت، بلا EQ ولا ضغط، "
                     "ماستر −16 LUFS · قمة −1.50 dBTP، والموسيقى تنسحب تحت الكلام، والصورة "
                     "منسوخة (-c:v copy). قياس الأخذات: 117.6 · 132.6 · 123.1 هرتز — ولأن المجموعة "
                     "الثانية خرجت مرتفعة (+2.7 نصف نغمة) وُلّدت لها ثلاث أخذات بديلة "
                     "(144.1 الأصلية · 132.6 · 137.1 · 162.2) واختيرت الأهدأ، فصار الانتشار "
                     "2.08 نصف نغمة فقط. الفحص الأخير على الملف الناتج 7/7 ✓: تأخير 0.00 ث · "
                     "أطول صمت 0.06 ث · أقصى سرعة 1.56× · −15.90 LUFS · قمة −1.50 dBTP · أول "
                     "إطار PSNR ∞. اسمعه، ثم اسمع 03 (voice-15 على الدقيقة نفسها) و04 (صوت 13 في "
                     "الفيلم كله): أيّ الثلاثة نكمل به الفيلم؟"),
    "pronunciation-ab3": ("★ النطق: أربع مقاطع، ثلاثة أصوات — أيّها مقبول؟ (74.9 ث)",
                          "الجملة واحدة والنغمات قبل كل مقطع = رقمه: (01) voice-13 — الصوت "
                          "الذي قبلتَ نطقه سابقًا · (02) voice-15 — الصوت الذي اخترته من "
                          "الاستماع · (03) voice-16 — الصوت الذي اخترته الآن من الاستماع الثاني "
                          "· (04) voice-16 بقاموس النطق المصري الذي بنيته. قياس التفريغ الآلي "
                          "على الأربعة كلها يرى أخطاءً (المحرك يقرأ العربية بمنطق فصحوي لا "
                          "مصري، وهو ما لا يُصلَح بكتابة النص)، ولذلك الحكم لك: اسمع الأربعة "
                          "وقل رقمًا واحدًا — ومن يخرج الأول نمضي به، وإن كان 01 فلنرجع لصوت 13 "
                          "ونكمل الفيلم به مع نظام ثبات النبرة الذي بنيناه."),
    "pronunciation-ab2": ("★ النطق: خمسة مقاطع قصيرة — أيّها نطقه مقبول؟ (83.8 ث)",
                          "الجملة واحدة والنص شبه واحد، والفرق الصوت وطريقة كتابة النص. النغمات "
                          "قبل المقطع = رقمه: (01) voice-13 بنفس الجملة — نطق الصوت الذي قبلتَه "
                          "سابقًا · (02) voice-15 بقاموس النطق المصري الذي بنيته · (03) نفس النص "
                          "مع وسم اللغة ar-EG صراحةً · (04) voice-15 في ثلاثين ثانية من نص "
                          "الفيلم بلا أي تشكيل أو تعديل · (05) تشكيل آلي جاهز لكي تسمع الفرق "
                          "السيئ. والقياس يقول شيئًا مهمًا: تفريغ المحرك رأى في مقطع 13 "
                          "«باولكي تجي نهرب» وفي مقطع 15 «بأقول لك إيه» — أي أن الخطأ موجود في "
                          "الاثنين، والمحرك عربيته غير مصرية بطبعها. اسمع: هل 01 (صوت 13) "
                          "نطقه مقبول؟ وهل 04 أفضل من غيره؟ وبعدها نختار صوتًا جديدًا بنطق أنظف."),
    "pronunciation-test": ("★ النطق — أربع طرق على الجملة نفسها بصوت voice-15 (50.6 ث)",
                          "الجملة واحدة، والصوت واحد (voice-15 الذي اخترتَه)، والفرق طريقة "
                          "كتابة النص قبل التوليد. النغمات قبل كل مقطع = رقمه: نغمة = 01 "
                          "ونغمتان = 02 وهكذا. 01 النص كما هو (ما سمعته وقلت إن نطقه سئ) · "
                          "02 مُشكَّل بقاموس نطق مصري بنيته (بَقول · إيه · تِيجي · نِهْرَب · "
                          "بِكَلِّمَك · مِش · إنْتَ · بِتُحِبَّها · كَمَّلْتش) مع فواصل نحوية "
                          "(فاصلة ونقطة) لأن المحرك يستعملها في الوقفات · 03 مثل 02 لكن "
                          "القاف تُكتب همزة في الأفعال المصرية (بَأُول) لأن نطقها الحقيقي "
                          "همزة حنجرية لا قافًا فصحى · 04 تشكيل آلي من مكتبة عربية جاهزة "
                          "(Mishkal) — للمقارنة: تشكّل كالفصحى (بُقولٌ لَك · مَشَّ · اُنْتُ) "
                          "فهي الأسوأ لهجويًا. القاموس محفوظ في config/egy-pronunciation.json "
                          "ويُطبَّق قبل كل توليد. اسمع وقل الرقم الذي نطقه صحيح."),
    "voice15-test": ("★ الصوت الجديد (voice-15) داخل الفيلم — أول ثلاث مجموعات (1:07)",
                      "تولّد هنا بمحرك الصوت الذي اخترته من الاستماع (نغمة/نغمتان) وليس من "
                      "مكتبتك. سمعته في الصحرا: أول 66 ثانية من الفيلم بالصوت الجديد على "
                      "الوصفة المقفلة نفسها — أخذة خام، atempo فقط، بلا قصّ صمت، ماستر "
                      "−16 LUFS · −1.5 dBTP، وصورة منسوخة. القياس على أخذاته الثلاث: "
                      "نبرته 92.6–108.1 هرتز (أعمق من راوي المكتبة 206 هرتز، وأعمق من 13)، "
                      "وانتشار النبرة بين أخذاته 2.67 نصف نغمة فقط مقابل 6.44 للمحرك 13 — "
                      "أي أهدأ بكثير. والتسليم اجتاز الفحص 7/7: تأخير 0.00 ث · أقصى سرعة "
                      "1.55× · −16.00 LUFS · قمة −1.50 dBTP · أول إطار مطابق ∞. "
                      "ثلاث مجموعات فقط الآن: الفيلم كله يُبنى به إذا قلت تمّ."),
    "voice15-sample": ("★ الصوت الجديد — عيّنتان خامتان (61.5 ث)",
                       "مقطعان من نص الفيلم نفسه بصوت voice-15: افتتاحية الفيلم، ثم مشهد "
                       "البحر والخطر. بلا أي معالجة: لا EQ ولا ضغط ولا تسريع ولا تغيير نبرة "
                       "— تسوية مستوى فقط. نبرة المقطع 95.9 هرتز (مدى 78–130). "
                       "وللمقارنة الصريحة: 13 كان 137.2 هرتز وراوي مكتبتك 206 هرتز."),
    "cloned-voices": ("★ عيّنات مستنسخة من الأصوات البشرية عندك — اختر صوت الدبلجة "
                     "(98.7 ث · 8 أصوات · كلها تقول نفس السطر)",
                       "ثمانية أصوات: محتواها نفسه (سطر واحد من الفيلم بصوت المحرك 13) لكن كل عيّنة "
                       "لابسة طابع راوٍ بشري مختلف من مكتبتك. النغمات قبل المقطع = رقمه: نغمة = 01، "
                       "نغمتان = 02 … ثماني نغمات = 08. الترتيب: 01 راوي الفيلم الأصلي · 02 راوي "
                       "محاضرة «الدكتور» (الصوت الذي أجَزته، مرجعه من بنكه المعتمد) · 03 «حلم الحج» "
                       "· 04 عرض استوديو · 05 الاختبار القصير · 06 راوي 0911 · 07 مخاطبة مباشرة · "
                       "08 حقائق الحيوانات. القياس: النبرة نُقلت إلى نبرة المرجع (إزاحة بين -2.0 و "
                       "+5.4 نصف نغمة، بحدّ ±6 حتى لا يتحوّل الصوت إلى شخص آخر)، والطابع أُغلق بنسبة "
                       "56–77% من فرق الطابع (سيبسترال 40). وصراحةً: هذه ليست شبكة استنساخ عصبية — "
                       "كل موفّريها محجوبون عني (كل المواقع ترد 000) — بل التحويل الكلاسيكي: كلمات "
                       "المحرك + نبرة الراوي وطابعه. وتسجيلاتك البشرية لم تُمَسّ إطلاقًا: قُرِئت مرجعًا "
                       "فقط. اسمع وقل أي رقم تريد أن تكون الدبلجة به. "
),
    "human-voices-2": ("🎧 عيّنات بشرية جديدة — طبيعية وفيها تفاعل، بلهجة مصرية "
                       "(90.8 ث · 8 أصوات · اسمع هذا أولًا)",
                       "ثمانية أصوات بشرية من مكتبتك، أُعيد اختيار المقاطع منها بالقياس لا بالأذن: كل مقطع "
                       "لازم فيه تناوب بين جملتين أو أكثر، ويمرّ على تفريغ صوتي محلي يُعدّ فيه كلمات اللهجة "
                       "المصرية. النغمات قبل المقطع = رقمه (نغمة = 01، نغمتان = 02). الترتيب: "
                       "01 راوي الفيلم الأصلي نفسه (للمقارنة) · 02 راوي محاضرة «الدكتور» — الصوت الذي أجَزته "
                       "03 راوي «حلم الحج» (أعلى مؤشر مصري 10.7%) · 04 عرض استوديو عربي · 05 مقطع الاختبار "
                       "القصير · 06 راوي 0911 · 07 مقطع مخاطبة مباشر · 08 حقائق عن الحيوانات. "
                       "بلا أي معالجة: لا EQ ولا ضغط ولا تسريع ولا تغيير نبرة — تسوية مستوى فقط. "
),
    "human-voices": ("★ عيّنات بشرية احترافية — ثمانية أصوات حقيقية من مكتبتك (92 ثانية)",
                     "قبل كل عيّنة نغمات قصيرة بعددها: واحدة للعيّنة 1، اثنتان للعيّنة 2، وهكذا. "
                     "01 راوي فيلم Into the Wild · 02 راوي ملخص «الدكتور بيقتل طالبه» · "
                     "03 راوي ملخص Into the Wild (0911) · 04 دبلجة كرتون السندباد العربية · "
                     "05 راوي قصة «حلم الحج» · 06 راوي «المرأة التي تدرك أنها ميتة» · "
                     "07 عرض ProStudio العربي · 08 مقطع الاختبار القصير. "
                     "كلها بمستوى واحد وبلا أي معالجة: لا EQ ولا ضغط ولا تسريع ولا تغيير نبرة. "
                     "الشبكة الخارجية مسدودة بالكامل عندي (كل المواقع ترد 000) فالعيّنات مأخوذة من ملفاتك أنت."),
    "voice-steadiest-ab": ("★ قبل/بعد: نبرة الصوت صارت أثبت (46 ثانية)",
                           "نغمة واحدة = قبل، نغمتان = بعد. نفس المجموعات ونفس الكلمات "
                           "ونفس الصوت الخام — الفرق فقط أي أخذة اختُرت. "
                           "القياس: متوسط القفزة بين المجموعات 2.02 ← 0.80 نصف نغمة، "
                           "وأقصى قفزة 4.00 ← 2.10. بلا أي إزاحة نبرة ولا فلتر — "
                           "اختيار لا معالجة."),
    "voice13-created": ("★ الدبلجة من أول الفيلم — 0 → 45 بصوتك المقفل "
                       "(17:28 · الانتقالات صارت أسلس)",
                       "46 مجموعة من 78 (17:28) — والصوت نفسه بلا أي معالجة: أخذة خام + atempo فقط. "
                       "شكوتك عن انتقالات الراوي غير السلسة كانت صحيحة ومقيسة: أسوأ انتقال بين "
                       "مجموعتين كان 4.00 نصف نغمة (26→27) و3.98 (43→44). ولّدت عشر مرشحات للأخذة "
                       "على طرفي هذين الانتقالين (26 · 27 · 43 · 44 · 40) وأعدت اختيار السلسلة الأقل "
                       "تنقلًا على المدى كله فتبدّلت أربع أخذات: 27 · 40 · 43 · 44 — وصار متوسط "
                       "القفزة 1.34 ← 1.00 وأقصاها 4.00 ← 3.65. والاختيار لا المعالجة: الأصل محفوظ. "
                       "والبوابة مرّت على الملف 7/7: تأخير 0.00 ث · سرعة 1.796× · −16.00 LUFS · "
                       "قمة −1.60 dBTP · أول إطار ∞. وأمانةً: الانتقال 38→39 (3.65) ما زال الأعلى، "
                       "ومرشحاته في الجولة القادمة. "
),
    "voice13-synced": ("★ صوتك النهائي — جُرّب كل بديل ورُفض، وبقي هذا (2:17)",
                       "هذا هو الصوت الذي أجزه: صوت 13 كما يخرج من المحرك، بلا تحويل طابع "
                       "وبلا EQ وبلا ضغط، والفارق الوحيد ضغط زمني atempo ليقع الكلام في وقته. "
                       "الوصفة مقفلة في config/voice-lock.json وينفّذها أمر واحد "
                       "(scripts/dub_locked_voice.py) على أي فيلم جديد — بلا إعادة قرار. "
                       "وأُغلق البحث عن بديل: سُمعت الأصوات الخمسة عشر كلها، والباقي "
                       "سبعة سُمعت الآن ولم تعجب — فالصوت 13 هو الصوت، نهائيًا."),
    "voice13-natural": ("★ صوتك بلا أي ضغط زمني — بسرعته الطبيعية (98 ثانية)",
                       "صوت 13 كما خرج من المحرك حرفيًا: لا تسريع، لا قصّ صمت، لا EQ، "
                       "لا ضغط، لا إزاحة. لهذا يحتاج الفيلم إلى ضغط 1.4× ليدخل في وقته — "
                       "وهذه الصفحة تعرض الفرق. المتأخر 11 و21 ثانية لأن الكلام بطبيعته أطول."),
    "ab-pipeline": ("نفس الكلمات أربع طرق — أيها صوتك؟ (45 ثانية)",
                    "00 كما خرج من المحرك · 01 مضغوط 1.4× بـatempo · 02 مضغوط 1.4× "
                    "بـrubberband · 03 كما كان الفيلم يخرجه (قصّ صمت + ضغط). "
                    "كلها بمستوى واحد وبلا EQ ولا ضغط، والقياس: تغيّر الطابع عن الأصل "
                    "1.18 · 1.56 · 1.49 dB/بن — أي أن الضغط والقصّ يغيّران الصوت فعلًا."),
    "approved-voice": ("★ الصوت الذي اخترته — كما هو، بلا أي معالجة (5 ثوان)",
                       "هذا هو الملف الذي أعجبك حرفيًا: صوت 13 بنبرة 163.9 هرتز، "
                       "لا تسريع ولا EQ ولا ضغط. المرجع الذي نقيس عليه كل شيء بعده."),
    "stretch-test": ("اختر نوع التسريع — أي نسخة تبقى صوتك؟ (24 ثانية)",
                     "الفيلم يحتاج تسريع 1.3× ليقع الكلام في وقته، وملفك لم يمرّ به. "
                     "00 ملفك بلا تسريع (163.9 هرتز) · 01 السلسلة الحالية في الفيلم "
                     "(ترفع النبرة إلى 173.6 — هي المشتبه) · 02 روبِرباند مبسّط (167.0) · "
                     "03 atempo (163.9 بلا أي تغيير) · 04 مبسّط جدًا (167.0). "
                     "قل الرقم وأعيد بناء الافتتاحية به"),
    "ab-mastering": ("سبب الخراب: سلسلة الماستر — عيّنتك قبلها وبعدها (17 ثانية)",
                     "عيّنتك نفسها ثلاث مرات: 01 كما سمعتها · 02 بعد السلسلة القديمة "
                     "(رفع +2.5 د.ب عند 3.2 كيلو + دي-إسر + ضغط 3:1) — هذا هو المشوّش · "
                     "03 بلا أي سلسلة. القياس: الطاقة فوق 6 كيلو تضاعفت 0.072 ← 0.132."),
    "pitch-choices": ("★ النبرة — اختر المستوى الأقرب لراوي الفيلم (38 ثانية)",
                      "سبب صوت السنجاب: مقياسي كان مضاعفًا — الراوي 95 هرتز لا 168، "
                      "وكل تشكيل سابق رفع الصوت إلى نحو 165–180. "
                      "الترتيب: 00 الراوي (~95) · 01 صوت 13 كما هو (~165) · "
                      "02 نازل 3 (~154) · 03 نازل 6 (~137) · 04 نازل 9 (~121) · "
                      "05 نازل 12 (~108) — نفس الجملة ونفس الصوت، والفرق نبرة فقط. "
                      "قل الرقم وأكمل الفيلم به"),
    "voice-samples": ("★ عيّنات مقترحة — اختر الأقرب لراوي الفيلم (29 ثانية)",
                      "نفس الجملة يقولها الجميع، والراوي الحقيقي في أولها ليكون الهدف في أذنك: "
                      "00 الراوي (الهدف) · 01 صوت 07 · 02 صوت 10 · 03 صوت 13 · 04 صوت 14 — "
                      "كلها مشكَّلة على الراوي بنفس الطريقة: النبرة 165.8–171.6 مقابل هدفه 168.3. "
                      "التوصية: 01 (07) لأنه الذي أشرت إليه بنفسك · و03 (13) الأقرب نبرةً. "
                      "قل الرقم فقط وأكمل الفيلم به"),
    "clone-original-voice": ("★ استنساخ صوت الراوي الأصلي من الفيديو — (13 ثانية)",
                             "١) جملته الحقيقية «بقول لك ايه تيجي نهرب» للمقارنة · "
                             "٢–٦) خمس جمل لم يقلها، بصوته هو من تسجيلاته: «انت عايز تعيش لوحدك» · "
                             "«احنا عايزين نهرب من التوقعات» · «السعاده مش في الفلوس» · "
                             "«الطبيعه مش مكان الانسان» · «الانسان محتاج الناس عشان يعيش» — "
                             "بلا صوت صناعي وبلا تحويل طابع. "
                             "مفرداته: 2439 كلمة في 5701 موضعًا. الحدّ بالأرقام: التغطية 100% "
                             "في كلامه عن العزلة والطبيعه، و31% في نص غريب (مثال: خبر نقل) — "
                             "فالكلمة التي لم يقلها لا ينطقها، والنبرة مسطّحة كلمة كلمة"),
    "ident-voices": ("أصوات هذه المحادثة مرقَّمة — الجملة نفسها (54 ثانية)",
                     "00 الراوي · 01 voice-05 · 02 voice-06 · 03 voice-07 · "
                     "04 voice-08 · 05 voice-09 · 06 voice-10 · 07 voice-13 · 08 voice-14 — "
                     "أرسل الرقم فقط وأكمل الفيلم بذلك الصوت"),
    "narrator-A": ("راوي الفيلم نفسه — بلا موسيقى (دقيقتان)",
                   "صوت الراوي الأصلي معزولًا · بلا تسريع ولا تشكيل · -16 LUFS"),
    "narrator-B": ("راوي الفيلم نفسه — فوق موسيقى الفيلم (دقيقتان)",
                   "الموسيقى تنسحب تحت كلامه · -16 LUFS وسقف -1.5 dBTP "
                   "(الفيلم الأصلي يقصّ عند +2.51)"),
    "voice-final-two": ("الصوتان المرشَّحان في شكلهما النهائي (67 ثانية)",
                        "الترتيب: راوي الفيلم نفسه · ثم 13 (+0.45 نصف نغمة) · "
                        "ثم 14 (-1.39) — كلاهما بعد قصّ الصمت والماستر"),
    "into-the-wild-fixed": ("Into the Wild — الافتتاحية بعد الإصلاح (2:17)",
                           "قصّ الصمت: وتيرة 1.27–1.43× بدل 1.52–1.62× · "
                           "rubberband يحفظ الفورمانت · موسيقى تنسحب تحت الكلام · "
                           "-16 LUFS وسقف -1.5 dBTP"),
    "voice-compare-2": ("المقارنة الثانية — 09 ثم 10",
                        "09 الأعمق (103.9 هرتز) والأسرع · 10 قريب من الصوت الحالي"),
    "audition-voice-09": ("المرشَّح 09 — النص نفسه",
                          "الأعمق: 103.9 هرتز · 10.21 حرف/ث"),
    "audition-voice-10": ("المرشَّح 10 — النص نفسه",
                          "125.0 هرتز — قريب من الصوت الحالي (122.1)"),
    "audition-voice-07": ("المتسابق 07 — النص نفسه",
                          "أسرع الثلاثة قراءةً · 10.43 حرف/ث"),
    "audition-voice-08": ("المتسابق 08 — النص نفسه",
                          "أعمق الثلاثة · 9.96 حرف/ث"),
    "audition-voice-05": ("الصوت الحالي 05 — النص نفسه",
                          "للمقارنة · 9.63 حرف/ث"),
    "voice-compare": ("مقارنة الأصوات الثلاثة — النص نفسه",
                      "05 الحالي · ثم 07 · ثم 08 · بينها صمت قصير"),
    "into-the-wild-pilot": ("Into the Wild — العيّنة الأولى (55 ثانية)",
                            "مقياس المزامنة: وسيط الفرق 0.15 ث · 93% داخل 0.5 ث"),
    "sindbad-6min": ("السندباد — ست دقائق",
                     "الفيلم السابق، لتقارن الأسلوب"),
}


def safe_name(name):
    """Keep the name inside incoming/ and survive odd characters."""
    name = os.path.basename(name.replace("\\", "/").strip())
    name = name.lstrip(".") or "upload"
    INCOMING.mkdir(parents=True, exist_ok=True)
    return INCOMING / name


def _human(n):
    for unit in ("بايت", "ك.بايت", "م.بايت", "ج.بايت"):
        if n < 1024 or unit == "ج.بايت":
            return f"{n:.0f} {unit}" if unit == "بايت" else f"{n:.1f} {unit}"
        n /= 1024.0


def render_previews():
    """Players for the finished cuts, so the preview frame shows the work and
    not only the door. Reads the directory on every request: a new cut appears
    on refresh instead of needing a restart.

    Newest first, because the newest cut is the one being discussed, and the
    filename of a take is not what it is. LABELS says what each cut actually
    covers so the frame needs no explanation beside it.
    """
    try:
        # Explicit order, newest cut first. Sorting by modification time looked
        # right and was not: restoring the repository rewrites every file at the
        # same instant, so the order came out arbitrary. A cut that matters gets
        # a place in this list; anything else follows, newest first among itself.
        rank = {stem: i for i, stem in enumerate([
            "voice-pro2", "voice17-test", "pro-voices", "voice16-test", "pronunciation-ab3", "voice15-test", "voice13-created",
            "into-the-wild-narration", "into-the-wild-mastered",
            "ident-voices", "voice07-opening",
            "narrator-A", "narrator-B", "voice-final-two", "into-the-wild-fixed",
            "voice-compare-2", "audition-voice-09", "audition-voice-10",
            "voice-compare", "audition-voice-07", "audition-voice-08",
            "audition-voice-05", "voice-B-professional", "voice-A-current",
            "into-the-wild-part2", "into-the-wild-part1", "into-the-wild-pilot",
            # Attempts that the ear rejected go last on purpose: the page leads
            # with the narrator's own voice, not with an imitation of it.
            "into-the-wild-cloned", "voice-clone-attempt",
            "sindbad-6min"])}
        # The page was emptied on the user's instruction: it carries what the
        # work is about right now and nothing else. Everything else stays on disk
        # and in the repository history -- and comes back by adding one name here.
        # The only thing on the page: the accepted voice, kept for whatever
        # film comes next. The audition the ear rejected is off the page but
        # stays on disk (previews/new-voices.mp3) and in the repository.
        # What the work is about right now: the created voice of the film, and
        # the run he already accepted. The human samples stay on disk and come
        # back with one word in this set.
        PAGE_ALLOW = {"voice-pro2", "voice17-test", "pro-voices", "voice16-test", "pronunciation-ab3", "voice15-test", "voice13-created"}
        cuts = sorted([c for c in list(PREVIEWS.glob("*.mp4")) + list(PREVIEWS.glob("*.mp3"))
                       if c.stem in PAGE_ALLOW],
                      key=lambda p: (rank.get(p.stem, len(rank)), -p.stat().st_mtime))
    except OSError:
        cuts = []
    if not cuts:
        return ('<div class="prev"><h2>العيّنات الجاهزة</h2>'
                '<p class="m">الصفحة فارغة بطلبك. العيّنات القديمة كلها محفوظة في المستودع، '
                'ولا تظهر هنا إلا ما نعمل عليه الآن.</p></div>')
    cards = []
    for number, c in enumerate(cuts, 1):
        url = "/previews/" + urllib.parse.quote(c.name)
        title, note = LABELS.get(c.stem, (c.stem, "دبلجة عربية بصوت واحد فوق موسيقى الفيلم"))
        # An audition is audio and a cut is video; the page shows whichever it is
        # rather than forcing every sample to carry a picture it does not need.
        player = ('<audio controls preload="metadata" src="{}"></audio>'.format(url)
                  if c.suffix == ".mp3" else
                  '<video controls preload="metadata" src="{}"></video>'.format(url))
        cards.append(
            '<div class="card">'
            f'<p class="t"><span class="num">{number}</span> {html.escape(title)}</p>'
            f'{player}'
            f'<p class="m">{html.escape(note)} · {_human(c.stat().st_size)}</p>'
            # No "open in a new window" link: previews only play inside Arena's
            # frame, so a raw file URL opens a "Preview Unavailable" page. The
            # player above is the way in, and it always works.
            '<p class="m">شغّله من المشغّل هنا مباشرة.</p>'
            '</div>')
    return ('<div class="prev"><h2>العيّنات الجاهزة</h2>' + "".join(cards) + '</div>')


def page():
    return (PAGE.replace("<!--PREVIEWS-->", render_previews())
                .replace("<!--BUILD-->", BUILD))


PAGE = """<!doctype html><html dir="rtl" lang="ar">
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>رفع فيديو للدبلجة</title>
<style>
 body{font-family:system-ui,Segoe UI,Tahoma,sans-serif;background:#14161a;color:#e8eaed;
      margin:0;padding:28px;line-height:1.7}
 .wrap{max-width:760px;margin:0 auto}
 h1{font-size:21px;margin:0 0 6px}
 p.sub{color:#9aa0a6;margin:0 0 22px;font-size:14px}
 #drop{border:2px dashed #4a4f57;border-radius:14px;padding:44px 20px;text-align:center;
       cursor:pointer;transition:.15s;background:#1b1e24}
 #drop.hot{border-color:#5b8cff;background:#1f2634}
 #drop b{display:block;font-size:17px;margin-bottom:8px}
 #drop span{color:#9aa0a6;font-size:13px}
 #file{display:none}
 .row{margin-top:18px;display:none}
 .name{font-size:14px;margin-bottom:8px;word-break:break-all}
 .bar{height:12px;background:#2a2e36;border-radius:7px;overflow:hidden}
 .fill{height:100%;width:0;background:linear-gradient(90deg,#3b6cff,#5b8cff);transition:width .2s}
 .meta{display:flex;justify-content:space-between;font-size:12px;color:#9aa0a6;margin-top:7px}
 .ok{color:#4ade80;font-size:14px;margin-top:16px;display:none;line-height:1.8}
 .err{color:#f87171;font-size:13px;margin-top:12px}
 .done{color:#9aa0a6;font-size:13px;margin-top:26px;border-top:1px solid #2a2e36;padding-top:16px}
 .done li{margin:4px 0}
 .build{color:#5f646b;font-size:11px;margin-top:22px;text-align:center}
 .probe{margin-top:20px;font-size:12px;color:#9aa0a6}
 .probe.good{color:#4ade80}
 .probe.bad{color:#f87171}
 .prev{margin-top:30px;border-top:1px solid #2a2e36;padding-top:20px}
 .prev h2{font-size:16px;margin:0 0 14px;color:#e8eaed}
 .card{background:#1b1e24;border:1px solid #2a2e36;border-radius:12px;padding:12px;margin-bottom:16px}
 .num{display:inline-block;min-width:26px;height:26px;line-height:26px;text-align:center;
      background:#5b8cff;color:#0d1014;border-radius:50%;font-weight:700;font-size:14px;margin-inline-end:8px}
 .card video{width:100%;border-radius:8px;display:block;background:#000}
 .card .t{font-size:14px;font-weight:600;margin:0 0 4px}
 .card .m{font-size:12px;color:#9aa0a6;margin:6px 0 0}
 .card a{color:#5b8cff;font-size:12px;text-decoration:none;margin-inline-end:14px}
</style>
<div class="wrap">
 <h1>رفع فيديو للدبلجة</h1>
 <p class="sub">اسحب الملف هنا أو اضغط للاختيار. يُرفع على دفعات ويستأنف تلقائيًا إن انقطع.</p>
 <div id="drop"><b>اسحب الفيديو هنا</b><span>أو اضغط لاختيار ملف — mp4 / mkv / mov / webm / mp3 / wav</span></div>
 <input type="file" id="file">
 <div class="row" id="row">
   <div class="name" id="nm"></div>
   <div class="bar"><div class="fill" id="fill"></div></div>
   <div class="meta"><span id="pct">0%</span><span id="spd"></span></div>
 </div>
 <div id="probe" class="probe">أتحقق من الاتصال…</div>
 <div class="ok" id="ok"></div>
 <div class="err" id="err"></div>
 <div class="done" id="done"></div>
 <p class="build">إصدار الصفحة <!--BUILD--></p>
 <!--PREVIEWS-->
</div>
<script>
const drop=document.getElementById('drop'),file=document.getElementById('file');
const row=document.getElementById('row'),fill=document.getElementById('fill');
const pct=document.getElementById('pct'),spd=document.getElementById('spd');
const nm=document.getElementById('nm'),ok=document.getElementById('ok'),err=document.getElementById('err');
let busy=false;

drop.onclick=()=>file.click();
drop.ondragover=e=>{e.preventDefault();drop.classList.add('hot')};
drop.ondragleave=()=>drop.classList.remove('hot');
drop.ondrop=e=>{e.preventDefault();drop.classList.remove('hot');if(e.dataTransfer.files[0])send(e.dataTransfer.files[0])};
file.onchange=()=>{if(file.files[0])send(file.files[0])};

function human(b){const u=['B','KB','MB','GB'];let i=0;while(b>=1024&&i<3){b/=1024;i++}return b.toFixed(1)+' '+u[i]}

function status(name){return fetch('/status?name='+encodeURIComponent(name)).then(r=>r.json())}

// One request per chunk, not one request for the film.
//
// Sending the whole file in a single POST looked fine against a local server
// and stalled against the preview proxy: the page showed one percent and then
// nothing, and the server never saw a request at all. Whatever sits in front of
// the sandbox will not carry an arbitrary body, so nothing larger than a couple
// of megabytes goes out per request, and a chunk that times out is retried from
// the last byte the server confirms it holds -- which also means an interrupted
// upload resumes instead of restarting. If a chunk keeps failing the size is
// halved, down to a quarter of a megabyte, so the client finds a size the
// connection accepts instead of dying at a fixed guess.
const CHUNK_MAX=2097152, CHUNK_MIN=262144;
let chunk=CHUNK_MAX;

function send(f){
  if(busy)return; busy=true;
  ok.style.display='none'; err.textContent=''; row.style.display='block'; nm.textContent=f.name;
  let off=0, fails=0, tries=0, t0=Date.now();
  const setBar=n=>{
    const p=Math.min(100,n/f.size*100);
    fill.style.width=p+'%'; pct.textContent=p.toFixed(1)+'%';
    const sec=(Date.now()-t0)/1000;
    if(sec>0.5&&n>0){const r=n/sec;
      spd.textContent=human(r)+'/ث · بقي '+Math.max(0,Math.round((f.size-n)/r))+'ث'}
  };
  setBar(0);
  status(f.name).then(s=>{
    off=s.done?0:s.bytes;
    if(off>0){const r=confirm('يوجد '+human(off)+' مرفوع مسبقًا. استئناف من حيث توقف؟'); if(!r)off=0}
    setBar(off); pump();
  }).catch(()=>pump());

  function pump(){
    if(off>=f.size) return verify();
    const from=off, to=Math.min(off+chunk,f.size);
    const xhr=new XMLHttpRequest();
    xhr.open('POST','/upload?name='+encodeURIComponent(f.name)+
                    '&offset='+from+'&total='+f.size);
    xhr.setRequestHeader('Content-Type','application/octet-stream');
    xhr.timeout=180000;
    xhr.upload.onprogress=e=>setBar(from+e.loaded);
    xhr.onload=()=>{
      if(xhr.status===200){off=to; fails=0; setBar(off); pump()}
      else if(xhr.status===413){
        // The proxy refused the body size outright. No point retrying the
        // same size -- go straight to the smallest chunk that can work.
        if(chunk<=CHUNK_MIN) return fail('الوسيط يرفض حتى أصغر حجم. أرفق الفيديو في المحادثة.');
        chunk=CHUNK_MIN; retry(from);
      }
      else {tries++; if(tries>40)return fail('رفض الخادم الجزء ('+xhr.status+').');
            retry(from)}
    };
    xhr.onerror=()=>{tries++; if(tries>40)return fail('انقطع الاتصال بعد محاولات كثيرة.'); retry(from)};
    xhr.ontimeout=()=>{tries++; if(tries>40)return fail('تجمّد الرفع. أعد تحميل الصفحة أو أرفق الفيديو في المحادثة.');
                       retry(from)};
    xhr.send(f.slice(from,to));
  }

  function retry(from){
    fails++;
    if(fails%3===0&&chunk>CHUNK_MIN) chunk=Math.max(CHUNK_MIN,Math.floor(chunk/2));
    status(f.name).then(s=>{off=Math.max(0,s.bytes); setBar(off); setTimeout(pump,500)})
      .catch(()=>{off=from; setTimeout(pump,900)});
  }

  function verify(){
    status(f.name).then(s=>{
      if(s.done){
        fill.style.width='100%'; pct.textContent='100%'; ok.style.display='block';
        ok.innerHTML='✓ اكتمل الرفع: <b>'+f.name+'</b> ('+human(s.size)+')<br>'+
          'قل لي «ابدأ الدبلجة» وسأعالجه.';
        busy=false; list();
      } else { off=Math.max(0,s.bytes); setBar(off);
               if(++tries>40)return fail('لم يكتمل الرفع.'); setTimeout(pump,600) }
    }).catch(()=>fail('تعذّر التأكد من الاكتمال.'));
  }
  function fail(m){err.textContent='✗ '+m; busy=false}
}


function list(){
  fetch('/files').then(r=>r.json()).then(d=>{
    if(!d.files.length){done.innerHTML='';return}
    done.innerHTML='<b>الملفات في الاستقبال:</b><ul>'+
      d.files.map(x=>'<li>'+x.name+' — '+human(x.size)+'</li>').join('')+'</ul>';
  }).catch(()=>{})
}
list();

// Can this page reach the server with a body at all?
//
// The point is to answer that in the page instead of in a conversation. The
// first attempt at uploading a film stalled at one percent with nothing in the
// server log, which took a round trip to establish; a 32 KB body that succeeds
// or fails on load says the same thing in a second.
const BUILD='<!--BUILD-->';
const probe=document.getElementById('probe');
(function preflight(){
  const body=new Uint8Array(32768), name='_probe.bin';
  const xhr=new XMLHttpRequest();
  xhr.open('POST','/upload?name='+name+'&offset=0&total='+body.length);
  xhr.setRequestHeader('Content-Type','application/octet-stream');
  xhr.timeout=20000;
  const done=(ok,txt)=>{probe.textContent=txt; probe.className='probe '+(ok?'good':'bad')};
  xhr.onload=()=>{
    fetch('/delete?name='+name,{method:'POST'}).catch(()=>{});
    if(xhr.status===200) done(true,'الاتصال جاهز: الرفع يعمل ✓ · إصدار '+BUILD);
    else done(false,'الرفع لا يعمل عبر هذه الصفحة (رمز '+xhr.status+') — أرفق الفيديو في المحادثة.');
  };
  xhr.onerror=()=>done(false,'الرفع لا يعمل عبر هذه الصفحة — أرفق الفيديو في المحادثة.');
  xhr.ontimeout=()=>done(false,'الرفع يتجمّد — أرفق الفيديو في المحادثة.');
  xhr.send(body);
})();

</script>
</html>
"""


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *a):
        pass

    def log_request(self, code="-", size="-"):
        # Kept on: when an upload stalls the first question is whether the
        # request ever arrived, and this answers it without another round trip.
        rng = self.headers.get("Range")
        sys.stderr.write("[http] %s %s%s -> %s\n" % (
            self.command, self.path, " " + rng if rng else "", code))

    def do_HEAD(self):
        """Answer liveness checks with the same headers a GET would send.

        Plain http.server refuses HEAD with 501, and a proxy that probes the
        preview with HEAD reads that as a dead app rather than as a method the
        server does not implement. _send already omits the body for HEAD, so
        answering is just a matter of routing it like a GET.
        """
        self.do_GET()

    def _send(self, code, body=b"", ctype="text/plain; charset=utf-8",
              body_only_headers: bool = False):
        if isinstance(body, str):
            body = body.encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        # The page is code, and a cached copy of it did real damage once: the
        # browser kept running the version that sent a film as one request, so
        # every upload died at the proxy while the server log stayed silent and
        # the fix looked like it had not worked. A page that must not be stale
        # has to say so in its headers, not hope.
        self.send_header("Cache-Control", "no-store, no-cache, must-revalidate")
        self.send_header("Pragma", "no-cache")
        self.send_header("Expires", "0")
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _safe(self, name):
        return safe_name(name)

    def do_GET(self):
        u = urllib.parse.urlparse(self.path)
        q = urllib.parse.parse_qs(u.query)
        if u.path in ("/upload", "/", "/index.html"):
            # The preview frame opens at '/', so the door has to be there too.
            return self._send(200, page(), "text/html; charset=utf-8")
        if u.path == "/status":
            p = self._safe(q.get("name", [""])[0])
            part = p.with_suffix(p.suffix + ".part") if p.suffix else Path(str(p) + ".part")
            return self._send(200, json.dumps(
                {"bytes": part.stat().st_size if part.exists() else 0,
                 "size": p.stat().st_size if p.exists() else 0,
                 "done": p.exists()}), "application/json")
        if u.path == "/files":
            INCOMING.mkdir(parents=True, exist_ok=True)
            fs = sorted([{"name": f.name, "size": f.stat().st_size}
                         for f in INCOMING.iterdir()
                         if f.is_file() and not f.name.endswith(".part")],
                        key=lambda x: -x["size"])
            return self._send(200, json.dumps({"files": fs}), "application/json")
        # static
        rel = urllib.parse.unquote(u.path).lstrip("/")
        tgt = (ROOT / rel).resolve() if rel else ROOT / "index.html"
        if not str(tgt).startswith(str(ROOT)) or not tgt.is_file():
            return self._send(404, "غير موجود")
        ctype = "video/mp4" if tgt.suffix in (".mp4", ".m4v") else (
            "audio/mpeg" if tgt.suffix == ".mp3" else (
                "audio/wav" if tgt.suffix == ".wav" else "application/octet-stream"))
        size = tgt.stat().st_size
        rng = self.headers.get("Range")
        code, start, end = 200, 0, size - 1
        if rng and rng.startswith("bytes="):
            a, _, b = rng[6:].partition("-")
            start = int(a) if a else 0
            end = int(b) if b else size - 1
            code = 206
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(end - start + 1))
        self.send_header("Accept-Ranges", "bytes")
        if code == 206:
            self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        if self.command == "HEAD":
            return
        with open(tgt, "rb") as fh:
            fh.seek(start)
            left = end - start + 1
            while left > 0:
                buf = fh.read(min(CHUNK, left))
                if not buf:
                    break
                self.wfile.write(buf)
                left -= len(buf)

    def do_POST(self):
        u = urllib.parse.urlparse(self.path)
        q = urllib.parse.parse_qs(u.query)
        if u.path == "/delete":
            tgt = self._safe(q.get("name", [""])[0])
            for f in (tgt, Path(str(tgt) + ".part")):
                if f.is_file() and INCOMING in f.resolve().parents:
                    f.unlink()
            return self._send(200, "ok")
        if u.path != "/upload":
            return self._send(404, "غير موجود")
        name = q.get("name", [""])[0]
        if not name:
            return self._send(400, "لا اسم")
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            return self._send(400, "طول غير صالح")
        try:
            offset = max(0, int(q.get("offset", ["0"])[0]))
        except ValueError:
            offset = 0
        # Optional total. Without it a request is assumed to carry the whole
        # remainder of the file, so a client that sends fixed-size chunks would
        # have each chunk renamed as if it were the finished upload. With it,
        # completion is a byte count rather than a guess.
        try:
            total = max(0, int(q["total"][0])) if "total" in q else None
        except ValueError:
            total = None
        final = self._safe(name)
        part = final.with_suffix(final.suffix + ".part") if final.suffix else Path(str(final) + ".part")
        INCOMING.mkdir(parents=True, exist_ok=True)
        # Honouring an offset is only possible when the bytes are actually
        # there. Opening wb and seeking would silently fill the gap with
        # zeroes -- the file would reach the right size and be renamed as
        # complete while its head was garbage. Refuse instead: 409 carries the
        # real byte count so the client can restart from it.
        have = part.stat().st_size if part.exists() else 0
        if offset and have < offset:
            return self._send(409, json.dumps(
                {"error": "لا بيانات جزئية عند هذه الإزاحة",
                 "bytes": have}), "application/json")
        mode = "r+b" if offset else "wb"
        if mode == "r+b":
            with open(part, "r+b") as fh:
                fh.truncate(offset)
        written = 0
        try:
            with open(part, mode) as fh:
                # Reopening starts at zero. Without this the resumed bytes
                # overwrite the head of the partial file instead of following
                # it, and the upload finishes at the length of the last chunk
                # rather than of the whole file.
                if offset:
                    fh.seek(offset)
                while written < length:
                    buf = self.rfile.read(min(CHUNK, length - written))
                    if not buf:
                        break
                    fh.write(buf)
                    written += len(buf)
        except (BrokenPipeError, ConnectionResetError):
            sys.stderr.write(f"[upload] انقطع {name} عند {offset + written}\n")
            return
        if written < length:
            sys.stderr.write(f"[upload] ناقص {name}: {written}/{length}\n")
            return self._send(500, "رفع ناقص")
        done_at = offset + written
        if total is not None and done_at < total:
            sys.stderr.write(f"[upload] تقدّم {name}: {done_at}/{total}\n")
            return self._send(200, json.dumps({"bytes": done_at, "complete": False}),
                              "application/json")
        if total is not None and done_at > total:
            with open(part, "r+b") as fh:
                fh.truncate(total)
        os.replace(part, final)
        sys.stderr.write(f"[upload] ✓ {final.name} ({final.stat().st_size} بايت)\n")
        self._send(200, "ok")


def main():
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8000
    INCOMING.mkdir(parents=True, exist_ok=True)
    srv = ThreadingHTTPServer(("0.0.0.0", port), Handler)
    print(f"  باب الرفع مفتوح على المنفذ {port}", flush=True)
    srv.serve_forever()


if __name__ == "__main__":
    main()
