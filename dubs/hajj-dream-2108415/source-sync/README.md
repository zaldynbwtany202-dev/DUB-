# دبلجة متزامنة مع كلام الأصل — صوت voice-12 المُعتمد

ملف الإخراج: `final-dub-source-sync.mp4` — 676.33 ثانية (11:16)،
الصورة منسوخة بدون إعادة ضغط، والتعليق مصري رجالي بنفس التسجيلات التي وافقت عليها.

هذه النسخة أُصلحت فيها **المزامنة فقط**. لم يُعد توليد أي مقطع صوتي، ولم تُسرَّع أي
كلمة، ولم تُحذف أي جملة: التسجيلات الأصلية استُعيدت من أرشيف FLAC في Git وثبت أنها
مطابقة عيّنة‑بعيّنة، ثم أعيد توزيع **الصمت الذي بينها** على توقيت كلام الأصل.

## ما الذي تغيّر عن النسخة السابقة

كان كل مقطع يوضع في أول نافذته، فيبقى التعليق صامتاً بينما الأصل يتكلم (شكاواك:
فجوات فارغة). الآن يُقسَّم كل تسجيل عند مواضع صمته المقاسة، وتوضع كل جملة عند بداية
الوحدة الكلامية المقابلة في الأصل، ثم تُطوَّل الوقفات أو تُقصَّر داخل الصمت فقط.

| الجزء | نافذة الأصل (ث) | موضع التعليق بعد الإصلاح | الانحراف | جُمل | كلام (ث) | وقفات (ث) | أطول وقفة (ث) |
|---|---|---|---|---|---|---|---|
| 0 | 0.0–63.5 | 0.0–63.5 | +0.00 | 22 | 57.8 | 5.5 | 0.40 |
| 1 | 63.5–148.7 | 63.5–148.7 | +0.00 | 31 | 74.6 | 10.4 | 0.64 |
| 2 | 148.7–248.4 | 148.7–248.4 | +0.00 | 31 | 85.8 | 13.7 | 0.70 |
| 3 | 248.4–333.5 | 248.4–333.5 | -0.00 | 33 | 74.1 | 10.8 | 0.49 |
| 4 | 333.5–411.8 | 333.5–413.1 | +0.00 | 35 | 75.4 | 4.1 | 0.12 |
| 5 | 411.8–504.3 | 413.1–504.3 | +1.34 | 30 | 80.1 | 10.8 | 0.53 |
| 6 | 504.3–593.8 | 504.3–593.8 | +0.00 | 30 | 82.1 | 7.2 | 0.36 |
| 7 | 593.8–676.3 | 593.8–676.2 | +0.00 | 31 | 69.9 | 12.3 | 0.70 |

## الأرقام المقاسة (لا الذوق)

- كلام الأصل: **675.1 ث** من 676.3 ث — الأصل يتكلم بلا توقف تقريباً
  (صمته الكلي 1.28 ث).
- كلام الدبلجة: **599.8 ث**.
- تغطية كلام الأصل: **97.56%** في مسار التعليق الجاف،
  **97.06%** في المكساج المُسلَّم.
- أطول فترة يقول فيها الأصل ولا يقول التعليق: **0.39 ث**
  (كانت 5.7 ث)، وعدد الفجوات ≥ 0.3 ث: **8**.
- سرعة الكلام: **1.000×** — لا إبطاء ولا تسريع. لا اقتطاع كلام (`speech_cut: false`)
  ولا تراكب جملتين (`overlapping_speech: 0`).
- كل قطعة صوتية (243 قطعة) طابقت التسجيل المُعتمد عيّنة‑بعيّنة، وأقصى خطأ زمني في
  موضعها **0.045 مللي‑ثانية** (2 عيّنة).
- الصورة: MD5 لتيار الفيديو مطابق للمصدر تماماً (27145 KiB)،
  وأخطاء فك الترميز 0.
- الجهارة: **-16.02 LUFS** والذروة **-1.28 dBTP**.

## ما لا تدّعيه هذه النسخة

1. **الكلمات**: نص الدبلجة 1457 كلمة مقابل
   1855 كلمة في الأصل (~79%). الأصل يُلقى بسرعة
   2.75 كلمة/ث، وهو أسرع من إلقائنا؛ لذلك بقيت ~16 ثانية
   من زمن الكلام بلا مقابل لفظي (موسيقى الخلفية تملؤها). سدّ هذه الهوة يستلزم نصاً
   أكثف وإعادة تسجيل — وهذا يتطلب صوتاً تعتمده في الجلسة الحالية.
2. **الخلفية**: تقدير منفصل بالـ UVR من صوت المصدر (+12.0 dB مع خفض تلقائي
   أثناء الكلام). قد تحمل بقايا من الراوي الأصلي؛ الفصل الآلي لا يضمن نقاءً تاماً.
3. **الذوق**: النطق واللهجة والانفعال لا تحكم عليها الفحوص الرقمية — تُحكم بسمعك.

## الملفات

- `final-dub-source-sync.mp4` / `.srt` — الفيديو و96 وصلة نصية بتوقيت الجمل نفسه.
- `final-dub-source-sync.mix.json` — وصف المكساج (الخلفية، الخفض، الجهارة، البصمات).
- `render-verification.json` — البوابات الست ونتائجها (`scripts/verify_source_sync_render.py`).
- `../timing-repair/pause-plan.json` — خطة المواضع: كل جملة، موضعها في الفيديو، وموضعها
  في التسجيل الأصلي، مع مقياس السلامة `sample-identical to the approved FLAC archive 22d604d0b32e…; container differs`.
- `../timing-repair/source-analysis/words.json` — توقيتات كلمات الأصل (1855 كلمة) التي بُنيت عليها الخطة.

## كيف تُعيد الإنتاج

```bash
scripts/materialize_lossless_takes.py --script dubs/hajj-dream-2108415/timing-repair/script.json
scripts/sync_takes_to_source_timeline.py \
  --script dubs/hajj-dream-2108415/timing-repair/script.json \
  --source library/hajj-dream-2108415/source.mp4 \
  --narration .cache/hajj-sync-repair/narration.wav \
  --plan-json dubs/hajj-dream-2108415/timing-repair/pause-plan.json \
  --srt dubs/hajj-dream-2108415/timing-repair/final-dub-source-sync.srt
scripts/restore_background_parts.py --source library/hajj-dream-2108415/source.mp4 \
  --parts dubs/hajj-dream-2108415/background/parts --output .cache/hajj-sync-repair/background.wav
scripts/mix_original_background.py --voice-video library/hajj-dream-2108415/source.mp4 \
  --voice-audio .cache/hajj-sync-repair/narration.wav --background .cache/hajj-sync-repair/background.wav \
  --output dubs/hajj-dream-2108415/source-sync/final-dub-source-sync.mp4 \
  --background-gain-db 12.0 --audio-bitrate 160k
scripts/verify_source_sync_render.py --source library/hajj-dream-2108415/source.mp4 \
  --output dubs/hajj-dream-2108415/source-sync/final-dub-source-sync.mp4 \
  --narration .cache/hajj-sync-repair/narration.wav \
  --plan dubs/hajj-dream-2108415/timing-repair/pause-plan.json \
  --script dubs/hajj-dream-2108415/timing-repair/script.json \
  --output-json dubs/hajj-dream-2108415/source-sync/render-verification.json
```

لا استدعاء لأي مزوّد صوت في هذه الخطوات: الأصوات إما معتمدة ومحفوظة، أو لا يوجد إخراج.
