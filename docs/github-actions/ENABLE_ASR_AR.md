# تشغيل وركفلو التفريغ حتى يتحكم الوكيل

الربط الحالي **لا يستطيع** إنشاء أو تعديل ملفات داخل `.github/workflows`.  
بعد ما الملف يعيش هناك مرة واحدة، الوكيل يتحكم بالتفريغ من الفرع `arena/01a0922c-dub` عبر `.arena/run.json`.

الوركفلو الجاهز: [`arena_asr.yml`](arena_asr.yml)  
ينزّل **faster-whisper medium** على GitHub Actions، يفرّغ `library/0911-1/source.mp4`، ويدفع النتيجة إلى `.arena/transcript.json` و`dubs/0911-1/ASR_MEDIUM.json`.

لا ترفع أوزان، ولا PAT، ولا تدمج الفرع.

---

## 1) صلاحية Actions (كتابة على الفرع)

1. افتح  
   https://github.com/zaldynbwtany202-dev/DUB-/settings/actions
2. **Actions permissions:** Allow all actions and reusable workflows.
3. **Workflow permissions:** Read and write permissions.
4. احفظ **Save**.

من غير «Read and write» الوركفلو يفرّغ ولا يقدر يدفع النص على الفرع.

---

## 2) ضع الوركفلو في مكان التشغيل (مرة واحدة)

افتح هذا الرابط على فرع الجلسة (ينشئ الملف مباشرة):

https://github.com/zaldynbwtany202-dev/DUB-/new/arena/01a0922c-dub/.github/workflows?filename=arena_asr.yml

1. الصق محتوى  
   https://raw.githubusercontent.com/zaldynbwtany202-dev/DUB-/arena/01a0922c-dub/docs/github-actions/arena_asr.yml
2. Commit مباشرة على `arena/01a0922c-dub`.
3. **لا تدمج إلى main.**

بعد أول كوميت، أي دفع لـ `.arena/run.json` على نفس الفرع يشغّل التفريغ. الوكيل هو اللي يدفع الملف.

---

## 3) (اختياري) صلاحية التطبيق نفسه

إذا أردت أن ينشئ الوكيل الوركفلو من غير الخطوة 2:

1. GitHub → Settings → Applications → Installed GitHub Apps → تطبيق Arena.
2. Repository permissions → **Workflows: Read and write**.
3. Save، ثم أعد ربط GitHub من Arena.

---

## 4) بعد ما الملف يظهر في Actions

الوكيل يعمل الباقي:

- يحدّث `.arena/run.json` (`whisper_model: medium`)
- الوركفلو ينزّل Medium على runner GitHub (هناك الشبكة مفتوحة)
- يدفع التفريغ على `arena/01a0922c-dub`
- يسجّل الدبلجة على النص الجديد

مراقبة التشغيل:  
https://github.com/zaldynbwtany202-dev/DUB-/actions
