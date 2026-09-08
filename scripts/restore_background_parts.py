#!/usr/bin/env python3
"""Recover a saved original-background estimate with source-aligned crossfades."""
import argparse,hashlib,json,subprocess,sys
from pathlib import Path
import numpy as np
import soundfile as sf
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from youtube_auto_dub.ffmpeg_bin import ffmpeg_exe

def sha(p):
    h=hashlib.sha256()
    with p.open('rb') as f:
        for data in iter(lambda:f.read(1048576),b''):h.update(data)
    return h.hexdigest()

def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--source',type=Path,required=True);p.add_argument('--parts',type=Path,required=True);p.add_argument('--output',type=Path,required=True);args=p.parse_args()
    expected=sha(args.source);index=json.loads((args.parts/'index.json').read_text());rows=[]
    for name in index['parts']:
        file=args.parts/name;m=json.loads(file.with_suffix('.json').read_text())
        if sha(file)!=m['sha256'] or m['key']['source_sha256']!=expected:raise ValueError('Wrong background piece')
        rows.append((file,m))
    if not rows:raise ValueError('No background parts')
    sr=44100;total=max(m['key']['end'] for _,m in rows);previous=None;previous_begin=0
    args.output.parent.mkdir(parents=True,exist_ok=True)
    with sf.SoundFile(args.output,'w',samplerate=sr,channels=2,subtype='PCM_24') as output:
        for i,(file,m) in enumerate(rows):
            begin=m['key']['begin'];length=m['key']['end']-begin
            data=subprocess.check_output([ffmpeg_exe(),'-v','error','-i',str(file),'-ac','2','-ar',str(sr),'-f','f32le','-'])
            audio=np.frombuffer(data,dtype=np.float32).reshape(-1,2)
            if abs(len(audio)-length)>sr*.05:raise ValueError('Background duration mismatch')
            audio=np.pad(audio,((0,max(0,length-len(audio))),(0,0)))[:length]
            a=i*60*sr;b=min(total,(i+1)*60*sr);core=audio[a-begin:b-begin].copy()
            if previous is not None:
                n=min(round(sr*.4),len(core));other=previous[a-previous_begin:a-previous_begin+n]
                if len(other)!=n:raise ValueError('Missing overlap')
                weight=np.linspace(0,1,n,dtype=np.float32)[:,None];core[:n]=other*(1-weight)+core[:n]*weight
            if len(core)!=b-a:raise ValueError('Missing core samples')
            output.write(core);previous=audio;previous_begin=begin
    assert sf.info(args.output).frames==total
    print('Background restored:',total/sr,'seconds; no separation rerun.')
if __name__=='__main__':main()
