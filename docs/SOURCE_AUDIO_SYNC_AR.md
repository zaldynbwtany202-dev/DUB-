# مزامنة ساوند الأصل — بلا إبطاء وبلا دمج

المطلوب هو **الصوت**: كل كلام في الأصل يقابله كلام مدبلج في نفس اللحظة الزمنية للساوند. ليست مزامنة شفاه ولا ضغط صورة.

## السياسة (`config/dub-policy.json`)

- الفرع `arena/01a0922c-dub` لا يُدمج أبداً.
- `min_tempo = 1.0` — ممنوع إبطاء الجملة لملء النافذة.
- `max_tempo = 1.08` — تسريع خفيف فقط؛ الأطول يُعاد نصّه.
- التغطية تُقاس على نوافذ كلام الأصل، لا على مقارنة المزيج قبل/بعد.

## التشغيل

```bash
python scripts/source_sync_plan.py \
  --segments library/<slug>/source.segments.json \
  --video library/<slug>/source.mp4 \
  --out dubs/<slug>/cues.json \
  --voice-id voice-17

# سجّل كل سطر بـ generate_speech ثم:
python scripts/agent_dub.py sync --cues dubs/<slug>/cues.json --output dubs/<slug>/final.mp4
```

ميزانية الكلمات ≈ 1.7 كلمة/ث داخل نافذة الأصل. إذا التسجيل أطول: اختصر. إذا أقصر: زِد معنى، لا تبطئ.

```bash
python scripts/never_merge.py main   # يخرج 1 على فرع الجلسة
```
