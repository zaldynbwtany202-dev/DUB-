#!/usr/bin/env python3
"""Place reviewed scene markers at measured pauses in complete agent takes."""
from __future__ import annotations
import argparse,difflib,json,os,re,sys
from pathlib import Path
import numpy as np
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from scripts.transcribe_video import sha256_file

def normal(text):
    return re.sub(r'[^\u0621-\u064aA-Za-z0-9]','',text.translate(str.maketrans('أإآىة','ااايه')))

def boundary_time(text,position,alignment):
    ref=normal(text);point=len(normal(text[:position]));obs='';times=[]
    for w in alignment['words']:
        word=normal(w['word']);obs+=word
        if word:times.extend(np.linspace(w['start'],w['end'],len(word)).tolist())
    if not obs:return None,0.0
    matcher=difflib.SequenceMatcher(None,ref,obs,autojunk=False)
    left=(0,0);right=(len(ref),len(obs)-1)
    for b in matcher.get_matching_blocks():
        if b.size and b.a<=point<b.a+b.size:return times[min(b.b+point-b.a,len(times)-1)],matcher.ratio()
        if b.size and b.a+b.size<=point:left=(b.a+b.size,b.b+b.size)
        if b.size and b.a>=point:right=(b.a,b.b);break
    if min(abs(point-left[0]),abs(right[0]-point))>40:return None,matcher.ratio()
    ratio=(point-left[0])/max(1,right[0]-left[0]);index=round(left[1]+ratio*(right[1]-left[1]))
    return times[min(max(0,index),len(times)-1)],matcher.ratio()

def choose_pause(estimate,pauses,duration):
    candidates=[]
    for p in pauses:
        a,b=p['start'],p['end'];middle=(a+b)/2
        if b-a>=.12 and a>=.15 and b<=duration-.15 and estimate-1<=middle<=estimate+.35:
            candidates.append((abs(b-estimate)+.035/(b-a),middle,p))
    return min(candidates,key=lambda x:x[0])[1:] if candidates else (None,None)

def build_take(take,alignment):
    if alignment['audio_sha256']!=take['sha256']:raise ValueError('Alignment does not match audio')
    points=[{'source':take['start'],'audio':0.0,'position':0}];audit=[]
    for cut in take.get('scene_cuts',[]):
        pos=take['text'].index(cut['text_starts']);estimate,ratio=boundary_time(take['text'],pos,alignment)
        at,pause=choose_pause(estimate,alignment['silences'],alignment['duration']) if estimate is not None else (None,None)
        accepted=at is not None and ratio>=.65 and at>points[-1]['audio']+.15 and cut['source_time']>points[-1]['source']
        audit.append({**cut,'estimate':estimate,'character_match_ratio':ratio,'pause':pause,'accepted':accepted})
        if accepted:points.append({'source':cut['source_time'],'audio':at,'position':pos})
    points.append({'source':take['end'],'audio':alignment['duration'],'position':len(take['text'])})
    merged=[]
    while len(points)>2:
        rates=[(b['audio']-a['audio'])/max(.01,b['source']-a['source']-.08) for a,b in zip(points,points[1:])]
        bad=next((i for i,x in enumerate(rates) if x>1.5),None)
        if bad is None:break
        options=([bad] if bad>0 else [])+([bad+1] if bad+1<len(points)-1 else [])
        def penalty(k):
            a,b=points[k-1],points[k+1]
            return abs((b['audio']-a['audio'])/(b['source']-a['source'])-1)
        merged.append(points.pop(min(options,key=penalty))['source'])
    segments=[]
    for i,(a,b) in enumerate(zip(points,points[1:])):
        s={'start':a['source'],'end':b['source'],'text':take['text'][a['position']:b['position']].strip(),'audio':take['audio'],'audio_start':round(a['audio'],6),'take_index':take['index'],'speaker':'NARRATOR'}
        if i<len(points)-2:s['audio_end']=round(b['audio'],6)
        segments.append(s)
    if ' '.join(s['text'] for s in segments).split()!=take['text'].split():raise ValueError('Script text changed')
    return segments,{'take_index':take['index'],'cuts':audit,'merged_boundaries':merged,'segments':len(segments)}

def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--script',type=Path,required=True);p.add_argument('--video',type=Path,required=True);p.add_argument('--output',type=Path,required=True)
    args=p.parse_args();script=json.loads(args.script.read_text());segments=[];audit=[]
    for take in sorted(script['takes'],key=lambda t:t['index']):
        alignment=json.loads((args.script.parent/'voice-alignment'/f"take-{take['index']:04d}.json").read_text())
        xs,report=build_take(take,alignment);segments.extend(xs);audit.append(report)
    for i,s in enumerate(segments):s['index']=i
    result={'video':os.path.relpath(args.video.resolve(),args.output.parent.resolve()),'source_sha256':sha256_file(args.video),'voice_backend':'agent','voice_id':script['voice_id'],'language':script['language'],'timing':'Reviewed scene windows and measured recording pauses; not lip animation','segments':segments}
    args.output.write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n')
    args.output.with_name('scene-alignment-audit.json').write_text(json.dumps({'takes':audit,'script_preserved':True,'cuts_only_in_measured_silence':True},ensure_ascii=False,indent=2)+'\n')
    print('Scene cues:',len(segments),flush=True)

if __name__=='__main__':main()
