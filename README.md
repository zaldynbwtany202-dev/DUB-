# DUB- 🎙️ أداة دبلجة احترافية بالذكاء الاصطناعي

**🌐 صفحة الاستخدام:** بعد تفعيل GitHub Pages ← `https://<اسمك>.github.io/DUB-/` — ارفع فيديو من المتصفح، اختر لغة/محرك/مشاعر، وابدأ.



أداة تدبلج أي فيديو إلى لغة أخرى **بأصوات مستنسخة من المتحدثين الأصليين**، مع مزامنة دقيقة على نوافذ حركة الشفاه، والحفاظ على خلفية الفيديو الأصلية (موسيقى / جمهور / مؤثرات).

**المستودع هو الأداة والمنفّذ معًا** — ثلاث طرق للتشغيل، اختر ما يناسبك:

| الطريقة | بدون تثبيت أي شيء | كيف |
|---|---|---|
| ☁️ **GitHub Actions** | ✅ | من صفحة المستودع: **Actions → Dub a video → Run workflow** — الصق رابط الفيديو، اختر اللغة، ونزّل النتيجة من Artifacts |
| 🐳 **Docker** | تحتاج Docker فقط | `make docker-dub VIDEO=clip.mp4 TGT=ar` |
| 💻 **محلي** | — | `make setup` ثم `make dub VIDEO=clip.mp4 TGT=ar` |

**English summary below ↓**

---

## خط المعالجة

```
فيديو أصلي
   │
   ├─ 1. استخراج الصوت .................. ffmpeg
   ├─ 2. فصل الحوار عن الخلفية ......... demucs (أو تخفيض الصوت الأصلي)
   ├─ 3. تفريغ نصي بتوقيتات الكلمات .... faster-whisper
   ├─ 4. عينة صوتية نقية لكل متحدث ..... تُقصّ تلقائيًا من مسار الحوار
   ├─ 5. ترجمة الجمل ................... google / argos / ملف بشري
   ├─ 6. استنساخ الأصوات + توليد ....... XTTS (محلي) أو ElevenLabs / Minimax (سحابي)
   ├─ 7. مزامنة احترافية ............... مجدول ذكي (انظر أدناه)
   └─ 8. دمج + معايرة جهارة + تركيب .... ffmpeg (loudnorm -16 LUFS)
```

## خوارزمية المزامنة (سرّ الجودة)

كل جملة في الأصل لها **نافذة شفاه** (متى يتحرك فم المتحدث) يليها **فاصل صامت** قبل الجملة التالية. المجدول لكل جملة مدبلجة:

1. يضعها في توقيت بدايتها الأصلي بالضبط (تطابق حركة الشفاه عند الدخول).
2. إن كانت أطول من نافذتها، يسرّعها بـ `atempo` حتى حد أقصى طبيعي (افتراضي **1.25×** فقط — فوقه يتشوه الصوت).
3. يسمح لها بأن تمتد داخل الفاصل الصامت التالي (الشفاه لا تظهر حينها).
4. إن لم تكفِ كل هذه الحيل، يعيد توليد الجملة من المحرك **بسرعة تحدث أعلى** (حتى 1.8×) ثم يعيد القياس — بدل تمديد مفرط يفسد النبرة.
5. يمنع أي تداخل بين جملة والتي تليها.

## ١. التشغيل من صفحة الويب (الأسهل)

بعد تفعيل GitHub Pages (Settings → Pages → Source = **GitHub Actions**)، افتح:

```
https://<username>.github.io/DUB-/
```

- ارفع فيديو (حتى ~1GB يذهب لـ Catbox مؤقت) أو الصق رابطًا مباشرًا
- اختر اللغة، المحرك (xtts مجاني، بلا مفاتيح)، المشاعر
- فعّل **"فيديو طويل"** إن كان أكثر من 3 دقائق — يُقسَّم إلى أجزاء متوازية ثم يُدمج
- الصفحة تُطلق الـ workflow، تتابع التقدم لحظيًا، وتنزّل النتيجة تلقائيًا

## ٢. التشغيل من GitHub Actions مباشرة

1. تبويب **Actions** ← اختر **Dub a video** (قصير) أو **Dub long video (parallel chunks)** (طويل) ← **Run workflow**.
2. الصق **رابط فيديو مباشر**، اختر لغة المصدر والهدف والمحرك والمشاعر.
3. بعد دقائق، نزّل الفيديو المدبلج من قسم **Artifacts** في صفحة التشغيل.

المحرك الافتراضي `xtts` يعمل مجانًا على الـ runner. للمحركات السحابية أضف secret في **Settings → Secrets → Actions**: `FAL_KEY` (لـ Minimax) أو `ELEVENLABS_API_KEY` (لـ ElevenLabs).

وworkflow ثانٍ (`ci.yml`) يفحص الكود ويشغّل الاختبارات تلقائيًا عند كل push — أي خطأ يظهر فورًا بعلامة حمراء على المستودع.

## المفاتيح (API Keys)

> ⚠️ **لا تضع أي مفتاح داخل المستودع أبدًا** — المفاتيح المنشورة في GitHub تُلتقط آليًا خلال دقائق وتُسرَق حصتها. الطريقة الصحيحة الوحيدة هي المتغيرات البيئية / GitHub Secrets.

| المفتاح | للمحرك | من أين تحصل عليه |
|---|---|---|
| `ELEVENLABS_API_KEY` | `elevenlabs` | [elevenlabs.io](https://elevenlabs.io) ← Profile ← API Keys |
| `FAL_KEY` | `minimax` | [fal.ai/dashboard/keys](https://fal.ai/dashboard/keys) |
| — | `xtts` | **لا يحتاج أي مفتاح** — يعمل محليًا مجانًا |

**للتشغيل المحلي:** انسخ `.env.example` إلى `.env` واملأه، ثم `set -a; source .env; set +a` قبل `dub run`.

**للتشغيل على GitHub Actions:** `bash scripts/set_secrets.sh` يرفع مفاتيحك من `.env` إلى Secrets المستودع بأمر واحد (أو يدويًا: Settings ← Secrets and variables ← Actions).

## ٣. التشغيل بـ Docker

```bash
docker build -t dub-forge .
docker run --rm -v "$PWD":/work dub-forge \
    run /work/clip.mp4 --src en --tgt ar --out /work/dubbed_ar.mp4
```

## ٤. التثبيت المحلي

```bash
# متطلب نظامي وحيد: ffmpeg
sudo apt-get install -y ffmpeg      # Ubuntu/Debian
# brew install ffmpeg               # macOS

git clone https://github.com/zaldynbwtany202-dev/DUB-.git
cd DUB-

# التثبيت الكامل بالمحرك المحلي (موصى به — مجاني ويعمل دون إنترنت بعد تنزيل النماذج)
pip install -e '.[whisper,demucs,xtts,google]'
```

## الاستخدام التفصيلي

```bash
# دبلجة إنجليزي → عربي باستنساخ أصوات محلي
dub run clip.mp4 --src en --tgt ar --engine xtts --out dubbed_ar.mp4

# بمحرك سحابي
export FAL_KEY=...                   # Minimax
dub run clip.mp4 --src en --tgt ar --engine minimax

export ELEVENLABS_API_KEY=...        # ElevenLabs
dub run clip.mp4 --src en --tgt ar --engine elevenlabs

# بترجمة بشرية جاهزة بدل الترجمة الآلية
dub run clip.mp4 --src en --tgt ar --translate-backend file --translations-file my_lines.json
```

أهم الخيارات:

| الخيار | الافتراضي | الوظيفة |
|---|---|---|
| `--engine` | `xtts` | محرك الصوت: `xtts` محلي · `elevenlabs` · `minimax` |
| `--max-stretch` | `1.25` | أقصى تسريع للمقطع قبل إعادة التوليد بسرعة أعلى |
| `--bg-volume` | `0.5` | مستوى الخلفية الأصلية تحت صوت الدبلجة |
| `--no-separate` | — | تخطي فصل الخلفية (أسلوب تعليق صوتي فوق الأصل المخفّض) |
| `--speaker-map` | تناوب تلقائي | تعيين المتحدثين يدويًا: `'0:S2,1:S1'` |
| `--whisper-model` | `small` | حجم نموذج التفريغ (`large-v3` أدق وأبطأ) |

كل مرحلة تُحفظ في `workdir/project.json` — إذا توقفت الأداة لأي سبب، أعد نفس الأمر وستكمل من حيث توقفت.

## مقارنة المحركات

| المحرك | استنساخ الصوت | العربية | يعمل محليًا | المتطلبات |
|---|---|---|---|---|
| **xtts** (Coqui XTTS v2) | ✅ من 10 ثوانٍ | ✅ ضمن 17 لغة | ✅ | `pip install TTS` (GPU يُفضَّل) |
| **minimax** (Speech 2.8 HD) | ✅ | ✅ جيدة بلمسة لكنة | ❌ | `FAL_KEY` + `pip install fal-client` |
| **elevenlabs** | ✅ Instant Clone | ✅ ممتازة | ❌ | `ELEVENLABS_API_KEY` |

## الاختبارات

```bash
pip install -e '.[dev]'
pytest tests/
```

## الموديلات

القائمة الكاملة بكل موديل ودوره وطريقة تحميله في [MODELS.md](MODELS.md) — باختصار: faster-whisper للتفريغ، demucs للفصل، XTTS v2 محليًا للاستنساخ (أو Minimax / ElevenLabs سحابيًا)، وffmpeg للدمج. كلها تُنزَّل تلقائيًا عند أول تشغيل؛ الاستنساخ يُبنى لحظيًا من عينات تقصّها الأداة بنفسها.

## البنية

```
dub/
├── cli.py          # واجهة سطر الأوامر
├── pipeline.py     # تنسيق المراحل مع حفظ الحالة والاستئناف
├── transcribe.py   # تفريغ بتوقيتات الكلمات + تجميع الجمل
├── separate.py     # فصل الحوار عن الخلفية (demucs)
├── translate.py    # الترجمة (google / argos / ملف بشري)
├── sync.py         # مجدول المزامنة الاحترافي
├── mix.py          # دمج المقاطع مع الخلفية وتركيب الفيديو
└── tts/            # محركات الصوت القابلة للتبديل
    ├── xtts.py     # محلي (Coqui)
    ├── elevenlabs.py
    └── minimax.py
```

## حدود معروفة

- تعيين المتحدثين الافتراضي تناوبي (مناسب للحوارات الثنائية)؛ للمقاطع متعددة المتحدثين استخدم `--speaker-map`.
- الاستنساخ عبر اللغات (صوت إنجليزي ينطق العربية) يحمل لمسة لكنة أجنبية — طبيعي في كل محركات اليوم.
- يلزم **ffmpeg** مثبتًا على النظام.

## الرخصة

MIT — استخدمها كما تشاء.

---

# English

**DUB-** is a CLI that dubs any video into another language with **voices cloned from the original speakers**, precise lip-window sync, and the original background (music/crowd/SFX) preserved.

Pipeline: ffmpeg extract → demucs stem separation → faster-whisper word timestamps → per-speaker reference samples → translation (google/argos/manual file) → voice-clone TTS (local **XTTS v2**, or cloud **ElevenLabs** / **Minimax**) → a dialogue-aware scheduler (≤1.25× atempo, gap-aware overflow, faster re-synthesis fallback) → loudness-normalized mix (−16 LUFS) → mux.

```bash
pip install -e '.[whisper,demucs,xtts,google]'   # needs ffmpeg on the system
dub run clip.mp4 --src en --tgt ar --engine xtts --out dubbed.mp4
```

State is cached per stage in the workdir — reruns resume where they stopped. See the Arabic section above for the full option reference. MIT licensed.
