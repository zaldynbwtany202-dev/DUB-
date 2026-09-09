#!/usr/bin/env python3
"""Archive existing agent MP3 takes compactly; never synthesize speech."""
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
    p.add_argument('--script',type=Path,required=True)
    p.add_argument('--raw-dir',type=Path,required=True)
    args=p.parse_args()
    doc=json.loads(args.script.read_text())
    ready=[]
    for take in doc['takes']:
        raw=args.raw_dir/f"seg-{take['index']:04d}.mp3"
        out=args.script.parent/take['audio']
        if not raw.is_file():
            if out.is_file() and take.get('sha256')==sha(out):ready.append(take['index'])
            continue
        original_hash=sha(raw)
        if not (out.is_file() and take.get('tool_output_sha256')==original_hash and take.get('sha256')==sha(out)):
            out.parent.mkdir(parents=True,exist_ok=True)
            subprocess.run([ffmpeg_exe(),'-y','-v','error','-i',str(raw),'-ac','1','-c:a','libopus','-b:a','40k','-vbr','off','-application','audio',str(out)],check=True)
            take.update(status='generated',sha256=sha(out),tool_output_sha256=original_hash,storage_codec='Opus mono 40 kbps CBR; encoded from the agent tool output')
        ready.append(take['index'])
    doc['status']='all_takes_generated' if len(ready)==len(doc['takes']) else 'recording'
    args.script.write_text(json.dumps(doc,ensure_ascii=False,indent=2)+'\n')
    state_path=args.script.parent/'state.json';state=json.loads(state_path.read_text())
    state.update(status='building_voice_and_background',generated_take_indices=ready,pending_take_indices=[t['index'] for t in doc['takes'] if t['index'] not in ready])
    state_path.write_text(json.dumps(state,ensure_ascii=False,indent=2)+'\n')
    print('Archived:',ready,flush=True)

if __name__=='__main__':main()
