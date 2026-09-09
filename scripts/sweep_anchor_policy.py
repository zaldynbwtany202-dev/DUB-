#!/usr/bin/env python3
"""Sweep the placement policy and report what each setting costs the ear.

Two things a viewer notices, measured on the same audio with the same detectors:

* silence the dub leaves while the original is still talking (audible holes), and
* how far each dub clause starts from the moment the original said that content (phase).

Moving silence toward the anchors improves the second and pays for it in the first; the
sweep prints the frontier so the shipped setting is a chosen point on it, not a guess.

Only the narration master is written (into ``--work-dir``); the render itself is produced by
the normal mix step with the settings picked here.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT))

from compare_quiet_holes import quiet_holes  # noqa: E402
from verify_source_sync_render import decode, vad  # noqa: E402

SR = 44100


def run(script: Path, source: Path, extra: list[str], work: Path) -> dict:
    master, plan = work / "master.wav", work / "plan.json"
    command = [sys.executable, str(ROOT / "scripts" / "sync_takes_to_source_timeline.py"),
               "--script", str(script), "--source", str(source), "--max-uncovered", "2000",
               "--narration", str(master), "--plan-json", str(plan)] + extra
    done = subprocess.run(command, capture_output=True, text=True)
    if done.returncode:
        raise RuntimeError(f"{extra} failed: {done.stdout[-400:]}{done.stderr[-400:]}")
    document = json.loads(plan.read_text(encoding="utf-8"))
    return {"master": str(master), "plan": str(plan),
            "phase": document["sentence_timing_error"],
            "planned_uncovered": document["uncovered_source_speech_seconds"]}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--script", type=Path, required=True)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--work-dir", type=Path, default=Path(".cache/anchor-sweep"))
    parser.add_argument("--hole-caps", default="0.25,0.32,0.4,0.5,0.62,0.75",
                        help="comma-separated --anchor-hole values to try, plus the --no-anchor baseline")
    parser.add_argument("--output-json", type=Path)
    args = parser.parse_args()
    args.work_dir.mkdir(parents=True, exist_ok=True)
    spans = vad(decode(args.source, SR), SR)
    rows = []
    for label, extra in ([("window_fill", ["--no-anchor"])] +
                         [(f"anchor_hole_{hole}", ["--anchor-hole", hole])
                          for hole in args.hole_caps.split(",") if hole]):
        work = args.work_dir / label
        work.mkdir(parents=True, exist_ok=True)
        result = run(args.script, args.source, extra, work)
        holes = quiet_holes(Path(result["master"]), spans)
        rows.append({"setting": label, "extra_args": extra,
                     "phase_mean_seconds": result["phase"].get("mean_seconds"),
                     "phase_median_seconds": result["phase"].get("median_seconds"),
                     "phase_p90_seconds": result["phase"].get("p90_seconds"),
                     "phase_max_seconds": result["phase"].get("max_seconds"),
                     "anchored_rows": result["phase"].get("anchored", 0),
                     "hole_count": holes["count"], "hole_total_seconds": holes["total_seconds"],
                     "hole_max_seconds": holes["max_seconds"], "hole_p90_seconds": holes["p90_seconds"],
                     "holes_over_0_7s": holes["over_0_7_seconds"],
                     "master": result["master"], "plan": result["plan"]})
        print(json.dumps(rows[-1], ensure_ascii=False))
    document = {"script": str(args.script), "source": str(args.source),
                "reading": ("phase improves as the hole cap grows; audible silence grows with it. "
                            "Choose the largest cap whose hole total is acceptable, because the "
                            "alternative - moving speech - is not allowed"),
                "best_hole_setting": min(rows, key=lambda row: row["hole_total_seconds"])["setting"],
                "best_phase_setting": min(rows, key=lambda row: row["phase_mean_seconds"] or 9e9)["setting"],
                "rows": rows}
    if args.output_json:
        args.output_json.parent.mkdir(parents=True, exist_ok=True)
        args.output_json.write_text(json.dumps(document, ensure_ascii=False, indent=2) + "\n",
                                    encoding="utf-8")
    print(json.dumps({key: value for key, value in document.items() if key != "rows"},
                     ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
