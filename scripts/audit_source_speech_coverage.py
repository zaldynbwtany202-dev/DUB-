#!/usr/bin/env python3
"""Compare ORIGINAL speech activity with dubbed speech, not only mix timing.

This is a measurable timing gate, not a word/meaning or human-quality verdict.
Both masks use the same VAD. Small boundary differences receive tolerance.
"""
from __future__ import annotations
import argparse,json,math,sys
from pathlib import Path
import numpy as np
from scipy.ndimage import maximum_filter1d
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))


def spans(mask,step=.01,min_seconds=.3):
    edges=np.diff(np.r_[False,mask,False].astype(np.int8))
    return [{'start':round(a*step,3),'end':round(b*step,3),'duration':round((b-a)*step,3)} for a,b in zip(np.flatnonzero(edges==1),np.flatnonzero(edges==-1)) if (b-a)*step>=min_seconds]


def speech_mask(intervals,duration,sr=16000,step=.01):
    mask=np.zeros(math.ceil(duration/step),dtype=bool)
    for interval in intervals:
        a=max(0,math.floor(interval['start']/sr/step));b=min(len(mask),math.ceil(interval['end']/sr/step))
        mask[a:b]=True
    return mask


def compare(source,dubbed,step=.01,tolerance=.15,min_gap=.3):
    size=max(len(source),len(dubbed));source=np.pad(source,(0,size-len(source)));dubbed=np.pad(dubbed,(0,size-len(dubbed)))
    width=2*round(tolerance/step)+1
    tolerant=maximum_filter1d(dubbed,size=width,mode='constant')
    missing=source & ~tolerant
    gaps=spans(missing,step,min_gap)
    total=float(source.sum()*step);uncovered=float(missing.sum()*step)
    return {'source_speech_seconds':round(total,3),'dubbed_speech_seconds':round(float(dubbed.sum()*step),3),'uncovered_source_speech_seconds':round(uncovered,3),'source_speech_coverage':round(1-uncovered/total,6) if total else 1,'max_uncovered_gap':max((s['duration'] for s in gaps),default=0),'gaps':gaps,'boundary_tolerance_seconds':tolerance,'minimum_reported_gap_seconds':min_gap}


def main():
    from faster_whisper.audio import decode_audio
    from faster_whisper.vad import get_speech_timestamps,VadOptions
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--source',type=Path,required=True);p.add_argument('--dubbed',type=Path,required=True);p.add_argument('--output',type=Path,required=True);args=p.parse_args()
    opts=VadOptions(threshold=.5,min_speech_duration_ms=80,min_silence_duration_ms=120,speech_pad_ms=40,max_speech_duration_s=30)
    masks=[];intervals=[];durations=[]
    for path in [args.source,args.dubbed]:
        audio=decode_audio(str(path),sampling_rate=16000);duration=len(audio)/16000
        detected=get_speech_timestamps(audio,opts,sampling_rate=16000)
        masks.append(speech_mask(detected,duration));intervals.append(detected);durations.append(duration)
        print('Measured speech:',path,len(detected),'spans',flush=True)
    report=compare(*masks)
    report.update(source=str(args.source),dubbed=str(args.dubbed),source_duration=durations[0],dubbed_duration=durations[1],method='Silero VAD, 10 ms masks, same settings on original and dub',word_content_verified=False,source_intervals_samples=intervals[0],dubbed_intervals_samples=intervals[1],sample_rate=16000)
    args.output.parent.mkdir(parents=True,exist_ok=True);args.output.write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n')
    print(json.dumps({k:v for k,v in report.items() if k not in ['gaps','source_intervals_samples','dubbed_intervals_samples']},ensure_ascii=False,indent=2))
    print('Longest gaps:',sorted(report['gaps'],key=lambda x:x['duration'],reverse=True)[:15])

if __name__=='__main__':main()
