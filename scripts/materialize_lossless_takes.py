#!/usr/bin/env python3
"""Restore approved recording samples from immutable Git FLAC archives."""
import argparse,hashlib,json,subprocess,sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from urllib.parse import quote
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from youtube_auto_dub.ffmpeg_bin import ffmpeg_exe
REPO='zaldynbwtany202-dev/DUB-'

def sha(path):
    h=hashlib.sha256()
    with path.open('rb') as f:
        for b in iter(lambda:f.read(1048576),b''):h.update(b)
    return h.hexdigest()

def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--script',type=Path,required=True);args=p.parse_args()
    doc=json.loads(args.script.read_text());cache=ROOT/'.cache/voice-archive-downloads';cache.mkdir(parents=True,exist_ok=True)
    def restore(take):
        out=(args.script.parent/take['audio']).resolve()
        if not out.is_relative_to(ROOT/'.cache'):raise ValueError('Restore only derived cache files')
        current=sha(out) if out.is_file() else None
        if current is None and not take.get('lossless_archive'):
            return  # Pending recording: never substitute another speaker.
        if current is None or current not in {take.get('sha256'),take.get('tool_wav_sha256')}:
            ref=take['lossless_archive'];flac=cache/(ref['sha256']+'.flac')
            if not flac.is_file() or sha(flac)!=ref['sha256']:
                with flac.open('wb') as f:
                    subprocess.run(['gh','api',f"repos/{REPO}/contents/{quote(ref['path'],safe='/')}?ref={ref['commit']}",'-H','Accept: application/vnd.github.raw+json'],stdout=f,check=True)
                if sha(flac)!=ref['sha256']:raise ValueError('Archive checksum mismatch')
            out.parent.mkdir(parents=True,exist_ok=True)
            subprocess.run([ffmpeg_exe(),'-y','-v','error','-i',str(flac),str(out)],check=True)
        take['sha256']=sha(out)
        print('Restored take',take['index'],flush=True)
    with ThreadPoolExecutor(max_workers=3) as pool:list(pool.map(restore,doc['takes']))
    args.script.write_text(json.dumps(doc,ensure_ascii=False,indent=2)+'\n')
if __name__=='__main__':main()
