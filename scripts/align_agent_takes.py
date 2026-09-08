#!/usr/bin/env python3
"""Measure word anchors and real silence in existing recordings; no TTS."""
from __future__ import annotations
import argparse,json,subprocess,sys
from pathlib import Path
import numpy as np
import soundfile as sf
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from scripts.transcribe_video import cpp_words,sha256_file
from youtube_auto_dub.ffmpeg_bin import ffmpeg_exe


def silence_ranges(audio,sr):
    frame=max(1,round(sr*.01));padded=np.pad(audio,(0,(-len(audio))%frame))
    rms=np.sqrt(np.mean(padded.reshape(-1,frame)**2,axis=1))
    quiet=rms<=max(1e-5,float(np.max(np.abs(audio)))*10**(-45/20))
    edges=np.diff(np.r_[False,quiet,False].astype(int))
    return [{'start':a*frame/sr,'end':min(len(audio),b*frame)/sr}
            for a,b in zip(np.flatnonzero(edges==1),np.flatnonzero(edges==-1)) if (b-a)*frame/sr>=.1]


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--script',type=Path,required=True)
    p.add_argument('--work-dir',type=Path,required=True)
    args=p.parse_args()
    cfg=json.loads((ROOT/'config/whisper-tiny-alignment.json').read_text())
    model=ROOT/'.cache/models/whisper-tiny-align/model.bin';model.parent.mkdir(parents=True,exist_ok=True)
    if not model.is_file() or sha256_file(model)!=cfg['sha256']:
        tmp=model.with_suffix('.download')
        with tmp.open('wb') as out:
            subprocess.run(['gh','api',f"repos/{cfg['repository']}/contents/{cfg['path']}?ref={cfg['revision']}",'-H','Accept: application/vnd.github.raw+json'],stdout=out,check=True)
        if tmp.stat().st_size!=cfg['bytes'] or sha256_file(tmp)!=cfg['sha256']:raise ValueError('Bad alignment model')
        tmp.replace(model)
    cli=ROOT/'.cache/tools/whisper.cpp/build/bin/whisper-cli'
    args.work_dir.mkdir(parents=True,exist_ok=True)
    outdir=args.script.parent/'voice-alignment';outdir.mkdir(exist_ok=True)
    for take in json.loads(args.script.read_text())['takes']:
        audio=args.script.parent/take['audio']
        if not audio.is_file():continue
        key=sha256_file(audio);dest=outdir/f"take-{take['index']:04d}.json"
        if dest.is_file():
            saved=json.loads(dest.read_text())
            if saved.get('audio_sha256')==key and saved.get('model_sha256')==cfg['sha256']:
                print(f"Cached alignment {take['index']}",flush=True);continue
        prefix=args.work_dir/f"take-{take['index']:04d}";wav=prefix.with_suffix('.wav')
        subprocess.run([ffmpeg_exe(),'-y','-v','error','-i',str(audio),'-ar','16000','-ac','1',str(wav)],check=True)
        print(f"ALIGN TAKE {take['index']}",flush=True)
        with prefix.with_suffix('.log').open('w') as log:
            subprocess.run([str(cli),'-m',str(model),'-f',str(wav),'-l','ar','-t','1','-ng','-dtw','tiny','-bs','1','-bo','1','-mc','0','-ojf','-of',str(prefix),'-sow','-ml','140'],stdout=log,stderr=log,check=True)
        native=json.loads(prefix.with_suffix('.json').read_text(encoding='utf-8',errors='surrogateescape'))
        samples,sr=sf.read(wav,dtype='float32');words=[]
        for row in native.get('transcription',[]):
            for w in cpp_words(row.get('tokens',[]),multilingual=True):
                times=[t for t in w['dtw_points'] if 0<=t<=len(samples)/sr]
                if times:words.append({'word':w['word'],'start':min(times),'end':max(times)+.08,'probability':w['probability']})
        result={'audio_sha256':key,'model_sha256':cfg['sha256'],'method':'Approximate tiny-q5_1 DTW; diagnostic text is not the script','duration':len(samples)/sr,'words':words,'silences':silence_ranges(samples,sr)}
        dest.write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n')
        print(f"ALIGNED TAKE {take['index']}",flush=True)
    print('ALL AVAILABLE TAKES ALIGNED',flush=True)

if __name__=='__main__':main()
