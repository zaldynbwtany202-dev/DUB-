#!/usr/bin/env python3
"""Checkpoint original speech words for timing repair; never generate narration."""
import argparse,hashlib,json,math,subprocess,sys,time,wave
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from scripts.transcribe_video import cpp_words,sha256_file
from youtube_auto_dub.ffmpeg_bin import ffmpeg_exe


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--source',type=Path,required=True);p.add_argument('--out',type=Path,required=True);p.add_argument('--work',type=Path,required=True);p.add_argument('--seconds',type=float,default=90);args=p.parse_args()
    args.out.mkdir(parents=True,exist_ok=True);args.work.mkdir(parents=True,exist_ok=True)
    src_sha=sha256_file(args.source);model=ROOT/'.cache/models/whisper-ggml/ggml-small.bin';cli=ROOT/'.cache/tools/whisper.cpp/build/bin/whisper-cli'
    model_sha=sha256_file(model);wav=args.work/'original-16k.wav'
    subprocess.run([ffmpeg_exe(),'-y','-v','error','-i',str(args.source),'-vn','-ac','1','-ar','16000','-c:a','pcm_s16le',str(wav)],check=True)
    started=time.monotonic();parts=[]
    with wave.open(str(wav),'rb') as inp:
        sr=inp.getframerate();duration=inp.getnframes()/sr;count=math.ceil(duration/args.seconds)
        for i in range(count):
            start=i*args.seconds;end=min(duration,start+args.seconds);left=max(0,start-3);right=min(duration,end+3)
            key={'source_sha256':src_sha,'model_sha256':model_sha,'start':start,'end':end,'context_seconds':3}
            checkpoint=args.out/f'part-{i:03d}.json'
            saved=json.loads(checkpoint.read_text()) if checkpoint.is_file() else None
            if saved and saved.get('signature')==key:part=saved
            else:
                path=args.work/f'part-{i:03d}.wav';inp.setpos(round(left*sr))
                with wave.open(str(path),'wb') as out:
                    out.setnchannels(1);out.setsampwidth(2);out.setframerate(sr);out.writeframes(inp.readframes(round((right-left)*sr)))
                prefix=args.work/f'part-{i:03d}'
                print(f'ORIGINAL ASR {i+1}/{count}: {start:.1f}–{end:.1f}',flush=True)
                with prefix.with_suffix('.log').open('w') as log:
                    subprocess.run([str(cli),'-m',str(model),'-f',str(path),'-l','ar','-t','2','-ng','-dtw','small','-bs','5','-mc','0','-sow','-ml','140','-ojf','-of',str(prefix)],stdout=log,stderr=log,check=True)
                native=json.loads(prefix.with_suffix('.json').read_text(encoding='utf-8',errors='surrogateescape'));words=[]
                for row in native['transcription']:
                    for w in cpp_words(row.get('tokens',[])):
                        points=sorted(t+left for t in w['dtw_points'] if math.isfinite(t))
                        if not points:continue
                        anchor=points[len(points)//2]
                        if not start<=anchor<end:continue
                        words.append({'word':w['word'],'start':round(max(start,points[0]-.05),3),'end':round(min(end,points[-1]+.10),3),'anchor':round(anchor,3),'probability':w['probability']})
                words.sort(key=lambda w:w['anchor'])
                part={'signature':key,'language':native['result']['language'],'words':words,'asr_review_required':True}
                checkpoint.write_text(json.dumps(part,ensure_ascii=False,separators=(',',':'))+'\n')
            parts.append(part);words=[w for part in parts for w in part['words']]
            for n,w in enumerate(words):w['id']=n
            report={'status':'complete_draft' if i+1==count else 'partial','source':str(args.source),'source_sha256':src_sha,'duration':duration,'completed_parts':i+1,'total_parts':count,'transcribed_until':end,'word_count':len(words),'timing_method':'Whisper DTW token envelopes; speech activity must be checked independently','words':words,'elapsed_seconds':round(time.monotonic()-started,3)}
            (args.out/'words.json').write_text(json.dumps(report,ensure_ascii=False,separators=(',',':'))+'\n')
            (args.out/'original.txt').write_text(' '.join(w['word'] for w in words)+'\n')
            print(f'SAVED ORIGINAL {i+1}/{count}: {len(words)} words',flush=True)
    print('ORIGINAL TRANSCRIPTION COMPLETE',flush=True)

if __name__=='__main__':main()
