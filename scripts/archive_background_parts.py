#!/usr/bin/env python3
"""Save completed neural background pieces compactly for recovery/remixing."""
import argparse,hashlib,json,subprocess,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from youtube_auto_dub.ffmpeg_bin import ffmpeg_exe

def sha(path):
    h=hashlib.sha256()
    with path.open('rb') as f:
        for data in iter(lambda:f.read(1024*1024),b''):h.update(data)
    return h.hexdigest()

def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--work-dir',type=Path,required=True)
    p.add_argument('--output-dir',type=Path,required=True)
    args=p.parse_args();args.output_dir.mkdir(parents=True,exist_ok=True)
    parts=[]
    for folder in sorted(args.work_dir.glob('piece-*')):
        checkpoint=folder/'done.json';stem=folder/'background.flac'
        if not checkpoint.is_file() or not stem.is_file():continue
        try:r=json.loads(checkpoint.read_text())
        except json.JSONDecodeError:continue
        out=args.output_dir/(folder.name+'.opus');meta=out.with_suffix('.json')
        cached=json.loads(meta.read_text()) if meta.is_file() else None
        if not(out.is_file() and cached and cached['key']==r['key'] and cached['sha256']==sha(out)):
            subprocess.run([ffmpeg_exe(),'-y','-v','error','-i',str(stem),'-c:a','libopus','-b:a','32k','-vbr','off','-application','audio',str(out)],check=True)
            record={'key':r['key'],'sample_rate_before_archive':44100,'file':out.name,'sha256':sha(out),'codec':'Opus stereo 32 kbps CBR','lossless_model_output_retained':False,'model_method':'UVR source-phase masking; no speech-purity guarantee'}
            meta.write_text(json.dumps(record,indent=2)+'\n')
        parts.append(out.name)
    (args.output_dir/'index.json').write_text(json.dumps({'parts':parts,'count':len(parts),'contains_context_overlaps':True},indent=2)+'\n')
    print('Archived background pieces:',len(parts),flush=True)

if __name__=='__main__':main()
