# مراجعة النطق واللهجة والتفريغ العربي

## 1. التفريغ مسودة ASR وليس نصًا معتمدًا

```bash
python3 scripts/transcribe_video.py --source path/to/take.mp3 \
  --backend faster-whisper --language ar --dialect ar-EG \
  --output-dir work/PROJECT/qa/asr-run-01
```

`--dialect` تلميح للمراجع فقط؛ Whisper لا يكتشف اللهجة هنا. يحتفظ النظام بالتوقيت/الثقة غير الصالحين على هيئة `null` وتحذير، ولا يخترع توقيتًا بتوزيع الكلمات على المقطع. ويمنع الكتابة فوق ناتج تفريغ مختلف.

## 2. إعداد قائمة الاستماع

```bash
python3 scripts/review_arabic_take.py --text-file work/PROJECT/text/g004.txt \
  --audio work/PROJECT/takes/g004.mp3 \
  --asr-json work/PROJECT/qa/asr-run-01/source.segments.json \
  --dialect ar-EG --out work/PROJECT/qa/checklist-g004.json
```

تُقارن بصمة SHA-256 للأخذة بمصدر تقرير ASR. تعرض القائمة الكلمات متعددة القراءة واقتراحات القاموس، لكنها لا تغيّر النص ولا تصادق على النطق. قاموس `ar-EG` لا يعني أن الصوت أصبح مصريًا؛ إذا لم يتوفر ملف موثّق للهجة المطلوبة، يظهر ذلك صراحةً بدل تطبيق قواعد لهجة أخرى.

## 3. تسجيل قرار المستمع وربطه بالأخذة

بعد الاستماع إلى التسجيل المحدد ومراجعة كل معيار:

```bash
python3 scripts/record_arabic_review.py \
  --checklist work/PROJECT/qa/checklist-g004.json \
  --audio work/PROJECT/takes/g004.mp3 \
  --text-file work/PROJECT/text/g004.txt \
  --reviewer 'معرّف المراجع' \
  --pronunciation pass --dialect pass --naturalness pass --timing pass \
  --attest-listened --out work/PROJECT/qa/listener-g004.json
```

أي معيار `needs_work` ينتج `needs_revision` ويتطلب ملاحظات. التقرير غير قابل للاستبدال، ومربوط ببصمات التسجيل والنص وقائمة المراجعة. قبل إصدار أو نشر هذه الأخذة افحص النسخة نفسها:

```bash
python3 scripts/check_arabic_review.py \
  --review work/PROJECT/qa/listener-g004.json \
  --checklist work/PROJECT/qa/checklist-g004.json \
  --audio work/PROJECT/takes/g004.mp3 \
  --text-file work/PROJECT/text/g004.txt
```

تغيير أي بايت في الصوت أو النص أو قائمة المراجعة يبطل الاعتماد. هذا يسجل **إفادة المراجع** ولا يستطيع البرنامج إثبات أن إنسانًا استمع؛ لا يُقبل تقرير من دون الإفادة الصريحة.

لفيلم متعدد المجموعات، أنشئ manifest يعدد كل مجموعة/أخذه مطلوبة **والماستر الصوتي النهائي**؛ المسارات أدناه نسبية إلى مكان manifest:

```json
{
  "schema_version": 1,
  "project": "اسم المشروع",
  "final_asset_id": "final-mix",
  "expected_assets": ["g001", "final-mix"],
  "assets": [
    {"asset_id":"g001","review":"g001-listener.json","checklist":"g001-checklist.json","audio":"../takes/g001.wav","text_file":"../text/g001.txt"},
    {"asset_id":"final-mix","review":"final-listener.json","checklist":"final-checklist.json","audio":"final-mix.wav","text_file":"../text/full-script.txt"}
  ]
}
```

```bash
python3 scripts/check_dub_release.py --manifest qa/release.json
```

يفشل الفحص إذا نُقص عنصر متوقع أو تغيرت بصمة أي عنصر بعد استماعه. أدرج ملف الماستر بعد المزج تحديدًا، لا الأخذات الخام فقط.

## 4. قياس التفريغ على نص بشري موثّق

جهّز JSONL بمرجع مصحح من التسجيل نفسه، ومراجعه، وبصمة التسجيل:

```json
{"recording_id":"eg-001","audio_sha256":"<64 lowercase hex>","dialect":"ar-EG","reference":"النص الذي راجعه إنسان","reference_verified":true,"reference_reviewer":"reviewer-1","hypothesis":"ناتج ASR"}
```

ثم شغّل:

```bash
python3 scripts/evaluate_arabic_asr.py \
  --input qa/human-verified-arabic.jsonl --out qa/asr-metrics.json
```

يحسب WER/CER إجمالًا ولكل لهجة. يجب أن يكون المرجع مرتبطًا بتسجيل ومصححًا بشريًا. الأرقام تقيس **النص فقط**: توحيد الحركات والألف يعني أن «عَلَم» و«عِلْم» قد يظهران تطابقًا كتابيًا كاملًا. لا تقيّم القياسات النطق أو اللهجة أو طبيعية الأداء، ولا تعمل بوابة اعتماد تلقائية.

## 5. مزامنة كلمة بكلمة بقياس مرجعي مستقل

لا تعتبر حدود المقاطع أو توقيت ASR وحدها إثباتًا للمزامنة. تقرير `word_timing_report` يحتفظ بالتوقيتات التي رصدها ASR ولا يصنع حدودًا مثالية خطية. من دون مرجع زمني بشري للأخذة العربية نفسها تكون النتيجة `sync_verified=false`.

للمقارنة، أنشئ JSON يحتوي كلمات التسجيل الفعلية وتوقيتها المحلي، ومرجعًا راجعه مستمع على **التسجيل العربي نفسه** (لا تقابل كلمات لغتين ترجمتين كلمة بكلمة):

```json
{
  "speech_start": 10.0,
  "speech_end": 12.0,
  "tolerance_seconds": 0.15,
  "observed_words": [{"word":"كلمة","start":0.02,"end":0.40}],
  "reference": {
    "reference_verified": true,
    "reviewer":"reviewer-1",
    "words":[{"word":"كلمة","start":0.00,"end":0.39}]
  }
}
```

```bash
python3 scripts/evaluate_word_alignment.py \
  --input qa/alignment-input.json --out qa/alignment-report.json
```

يحسب متوسط وأقصى خطأ لحدود كل كلمة، ويرجع نجاحًا فقط عند تطابق الكلمات وتوفر إفادة المرجع البشري ضمن العتبة. هذا يقيس حدود التسجيل العربي؛ لا يحكم وحده على ملاءمة الترجمة للمشهد أو مزامنة الشفاه.

## 6. تثبيت الصوت وتتبّع المحرك

بنك الاستوديو يحتفظ بالأخذات القديمة، لكن مفاتيح v2 تحفظ الحركات والحروف وعلامات الترقيم وهوية الصوت؛ مفاتيح v1 القديمة لا تستخدم إلا بعد مطابقة النص المحفوظ والصوت. يُرفض المسار الذي يحاول الخروج من مجلد بنك الصوت.

عند تعطل Edge TTS لا يتحول النظام تلقائيًا إلى eSpeak. الافتراضي فشل آمن؛ لا تسمح بـ`allow_local_fallback=True` إلا بقرار صريح، لأن eSpeak صوت مختلف. يعيد مسار النطق اسم المحرك ويكتب `<audio>.voice.json` مع معرّف الصوت وبصمتي النص والصوت والمحرك الذي أنشأ الأخذة.

## 7. إضافة ملف لهجة

للهجات غير المصرية، أضف `config/pronunciation/<locale-lowercase>.json`، مثل `ar-dz.json`، وفق `config/pronunciation/README.md`. يجب أن يحمل الملف رمز لهجة مطابقًا ومراجعًا ومصادر موثّقة. كل قراءة تظهر اقتراحًا فقط؛ لا تُطبّق تلقائيًا. لا توجد بيانات لهجة جزائرية معتمدة مضمّنة في هذا التغيير، لذلك يعلن النظام عدم توفر الملف بدل الادعاء بأنه أتقن الدارجة.

لا يُولّد صوت باتريك جين ولا يُستأنف إنتاجه ضمن هذه الأدوات؛ قرار الإيقاف قائم إلى أن تطلب خلاف ذلك صراحةً.
