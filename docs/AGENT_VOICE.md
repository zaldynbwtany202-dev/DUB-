# صوت الوكيل ثم المزامنة

في جلسة أرينا على `DUB-` الصوت لا يُولَّد من Edge أو VoxCPM أو XTTS.

صوت 0911-1 مقفول: **`voice-33`**. الملف: `dubs/0911-1/LOCKED_VOICE.json`. لا يُبدَّل ولا تُعالَج تسجيلاته.

1. الوكيل يختار صوتاً (`add_voice`) ويسجّل كل جملة (`generate_speech`). لهذا الكليب استخدم `voice-33` دائماً.
2. الملفات تُحفظ بجانب ورقة الإشارات، عادة `agent_voice/seg-0000.mp3`.
3. المستودع يمطّ كل مقطع لزمنه الأصلي، يمزج الخلفية إن طُلب، ويركب الصوت على الصورة.

```bash
python scripts/agent_dub.py plan --video library/<name>/source.mp4 \
  --segments segments.json --out cues.json --language ar-EG

# بعد توليد الملفات:
python scripts/agent_dub.py sync --cues cues.json --output dubs/<name>/final-dub.mp4
```
