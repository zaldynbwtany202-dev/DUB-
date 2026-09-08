#!/usr/bin/env python3
"""Publish exact recording samples before replacing worktree copies with refs.

Archive commits stay in the current branch's reachable history. Temporary
FLAC copies are removed only after the remote object was verified.
"""
import argparse,hashlib,json,subprocess,sys
from pathlib import Path
from urllib.parse import quote
import numpy as np
import soundfile as sf
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from youtube_auto_dub.ffmpeg_bin import ffmpeg_exe
BRANCH='arena/01a07c92-dub';REPO='zaldynbwtany202-dev/DUB-'

def digest(p):
    h=hashlib.sha256()
    with p.open('rb') as f:
        for b in iter(lambda:f.read(1048576),b''):h.update(b)
    return h.hexdigest()

def git(*args):
    return subprocess.check_output(['git',*args],cwd=ROOT,text=True).strip()

def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--script',type=Path,required=True);p.add_argument('--raw-dir',type=Path,required=True);args=p.parse_args()
    assert git('branch','--show-current')==BRANCH
    if git('diff','--cached','--name-only'):raise RuntimeError('Preserve unrelated staging before checkpointing')
    doc=json.loads(args.script.read_text());folder=args.script.parent/'_lossless_archive';folder.mkdir(exist_ok=True);pending=[]
    for t in doc['takes']:
        raw=args.raw_dir/t.get('raw_file',f"seg-{t['index']:04d}.wav")
        if not raw.is_file():continue
        raw_sha=digest(raw);text_sha=hashlib.sha256(t['text'].encode()).hexdigest()
        if t.get('lossless_archive') and raw_sha in {t.get('sha256'),t.get('tool_wav_sha256')}:
            if t.get('spoken_text_sha256',text_sha)!=text_sha:raise ValueError('Text changed without new speech')
            continue
        flac=folder/f"seg-{t['index']:04d}.flac"
        if flac.exists():raise RuntimeError(f'Inspect unfinished archive {flac}')
        subprocess.run([ffmpeg_exe(),'-y','-v','error','-i',str(raw),'-c:a','flac','-compression_level','8',str(flac)],check=True)
        a,sr=sf.read(raw,dtype='float64');b,other=sf.read(flac,dtype='float64')
        if sr!=other or not np.array_equal(a,b):raise ValueError('Archive is not sample-identical')
        pending.append((t,raw_sha,text_sha,flac,len(a)/sr))
    if not pending:print('No new recordings to archive');return
    files=[str(row[3].resolve().relative_to(ROOT)) for row in pending]
    git('add','--',*files);git('commit','-m','Checkpoint lossless approved narration recordings','--only','--',*files)
    commit=git('rev-parse','HEAD');subprocess.run(['git','push','origin',BRANCH],cwd=ROOT,check=True)
    for t,raw_sha,text_sha,flac,duration in pending:
        path=str(flac.resolve().relative_to(ROOT))
        remote=json.loads(subprocess.check_output(['gh','api',f"repos/{REPO}/contents/{quote(path,safe='/')}?ref={commit}"],text=True))
        blob=git('rev-parse',f'{commit}:{path}')
        if remote['sha']!=blob or remote['size']!=flac.stat().st_size:raise ValueError('Remote verification failed')
        t.update(status='generated',duration=duration,sha256=raw_sha,tool_wav_sha256=raw_sha,spoken_text_sha256=text_sha,lossless_archive={'commit':commit,'path':path,'git_blob_sha':blob,'sha256':digest(flac),'bytes':flac.stat().st_size,'sample_identical_to_tool_wav':True})
    git('rm','--',*files)
    args.script.write_text(json.dumps(doc,ensure_ascii=False,indent=2)+'\n')
    script=str(args.script.resolve().relative_to(ROOT));git('add','--',script)
    git('commit','-m','Reference verified recording archives without duplicate working audio','--only','--',script,*files)
    subprocess.run(['git','push','origin',BRANCH],cwd=ROOT,check=True)
    print('Verified archive:',commit,'takes',[row[0]['index'] for row in pending])
if __name__=='__main__':main()
