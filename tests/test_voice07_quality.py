import numpy as np
import pytest
import soundfile as sf

from youtube_auto_dub.agent_tts import fit_take
from scripts.plan_scene_cues import build_take
from scripts import mix_original_background as mixer


def make_tone(tmp_path):
    rate=24000
    data=(.25*np.sin(2*np.pi*440*np.arange(rate)/rate)).astype(np.float32)
    p=tmp_path/'voice.wav';sf.write(p,data,rate)
    return p


def test_natural_policy_never_slows_short_takes(tmp_path):
    p=make_tone(tmp_path)
    audio,report=fit_take({'start':0,'end':3,'audio':str(p),'text':'اختبار'},tmp_path,0,min_tempo=1,max_tempo=1.08,sample_rate=48000)
    assert report['tempo']==1
    assert len(audio)/48000==pytest.approx(1,abs=.01)
    assert not report['speech_truncated']


def test_pronunciation_policy_rejects_large_speedups(tmp_path):
    p=make_tone(tmp_path)
    with pytest.raises(ValueError,match='shorten/re-record'):
        fit_take({'start':0,'end':.8,'audio':str(p),'text':'اختبار'},tmp_path,0,min_tempo=1,max_tempo=1.08)


def test_scene_merge_keeps_all_text_under_gentle_tempo_cap():
    t={'index':0,'start':0,'end':4,'text':'أهلا بيك. تعال هنا.','audio':'voice.wav','sha256':'same','scene_cuts':[{'source_time':.9,'text_starts':'تعال'}]}
    a={'audio_sha256':'same','duration':3,'words':[{'word':'أهلا','start':.1,'end':.4},{'word':'بيك','start':.5,'end':.8},{'word':'تعال','start':1.4,'end':1.7},{'word':'هنا','start':1.8,'end':2.2}],'silences':[{'start':.9,'end':1.3}]}
    segments,report=build_take(t,a,max_tempo=1.06)
    assert len(segments)==1 and segments[0]['text']==t['text']
    assert report['merged_boundaries']==[.9]


def test_missing_lossless_master_never_falls_back_to_video_audio(tmp_path):
    video=tmp_path/'picture.mp4';video.write_bytes(b'fixture')
    background=tmp_path/'background.flac';background.write_bytes(b'fixture')
    with pytest.raises(FileNotFoundError):
        mixer.mix(video,background,tmp_path/'result.mp4',tmp_path/'work',voice_audio=tmp_path/'missing.wav')
    assert not (tmp_path/'result.mp4').exists()
