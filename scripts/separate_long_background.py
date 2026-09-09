#!/usr/bin/env python3
"""Bounded-memory UVR separation with resumable contextual pieces."""
from __future__ import annotations
import argparse,json,math,subprocess,sys,time
from pathlib import Path
import numpy as np
import soundfile as sf

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from youtube_auto_dub.neural_background import digest,separate_background
from youtube_auto_dub.ffmpeg_bin import ffmpeg_exe


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--source',type=Path,required=True)
    p.add_argument('--output',type=Path,required=True)
    p.add_argument('--work-dir',type=Path,required=True)
    p.add_argument('--progress',type=Path,required=True)
    p.add_argument('--chunk-seconds',type=float,default=60)
    p.add_argument('--context-seconds',type=float,default=4)
    p.add_argument('--mask-power',type=float,default=1.35)
    args=p.parse_args()
    if args.chunk_seconds<20 or not 1<=args.context_seconds<=10:p.error('Invalid context/window')
    args.work_dir.mkdir(parents=True,exist_ok=True);args.progress.parent.mkdir(parents=True,exist_ok=True)
    sr=44100;wav=args.work_dir/'source.wav';source_sha=digest(args.source)
    subprocess.run([ffmpeg_exe(),'-y','-v','error','-i',str(args.source),'-vn','-ac','2','-ar',str(sr),'-c:a','pcm_s24le',str(wav)],check=True)
    total=sf.info(wav).frames;duration=total/sr;count=math.ceil(duration/args.chunk_seconds)
    pieces=[];started=time.monotonic()
    with sf.SoundFile(wav) as inp:
        for i in range(count):
            a=round(i*args.chunk_seconds*sr);b=min(total,round((i+1)*args.chunk_seconds*sr))
            begin=max(0,a-round(args.context_seconds*sr));end=min(total,b+round(args.context_seconds*sr))
            folder=args.work_dir/f'piece-{i:03d}';folder.mkdir(exist_ok=True)
            clip=folder/'source.wav';stem=folder/'background.flac';checkpoint=folder/'done.json'
            key={'source_sha256':source_sha,'begin':begin,'end':end,'power':args.mask_power,'version':1}
            if not(stem.is_file() and checkpoint.is_file() and json.loads(checkpoint.read_text()).get('key')==key):
                inp.seek(begin);sf.write(clip,inp.read(end-begin,dtype='float32'),sr,subtype='PCM_24')
                print(f'BACKGROUND {i+1}/{count}: {a/sr:.1f}–{b/sr:.1f}',flush=True)
                report=separate_background(clip,stem,folder/'work',power=args.mask_power)
                checkpoint.write_text(json.dumps({'key':key,'report':report},indent=2)+'\n')
            pieces.append({'start':a,'end':b,'begin':begin,'stem':str(stem)})
            args.progress.write_text(json.dumps({'status':'separating','completed_pieces':i+1,'total_pieces':count,'processed_until':b/sr,'source_sha256':source_sha},indent=2)+'\n')
            print(f'SAVED BACKGROUND {i+1}/{count}',flush=True)
    args.output.parent.mkdir(parents=True,exist_ok=True)
    with sf.SoundFile(args.output,'w',samplerate=sr,channels=2,subtype='PCM_24') as out:
        for i,piece in enumerate(pieces):
            length=piece['end']-piece['start']
            with sf.SoundFile(piece['stem']) as f:
                f.seek(piece['start']-piece['begin']);audio=f.read(length,dtype='float32')
            if i:
                n=min(round(.4*sr),len(audio));prev=pieces[i-1]
                with sf.SoundFile(prev['stem']) as f:
                    f.seek(piece['start']-prev['begin']);old=f.read(n,dtype='float32')
                if len(old)!=n:raise ValueError('Missing overlap')
                w=np.linspace(0,1,n,dtype=np.float32)[:,None];audio[:n]=old*(1-w)+audio[:n]*w
            if len(audio)!=length or not np.isfinite(audio).all():raise ValueError('Invalid piece')
            out.write(audio)
    assert sf.info(args.output).frames==total
    result={'status':'separated_needs_audit','source':str(args.source),'source_sha256':source_sha,'output':str(args.output),'output_sha256':digest(args.output),'duration':duration,'sample_rate':sr,'samples':total,'pieces':count,'context_seconds':args.context_seconds,'crossfade_seconds':.4,'mask_power':args.mask_power,'speech_purity_guarantee':False,'elapsed_seconds':round(time.monotonic()-started,3)}
    args.progress.write_text(json.dumps(result,indent=2)+'\n')
    args.output.with_suffix('.json').write_text(json.dumps(result,indent=2)+'\n')
    print('BACKGROUND COMPLETE',flush=True)

if __name__=='__main__':main()
