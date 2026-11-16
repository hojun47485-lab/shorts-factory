#!/usr/bin/env python3
"""원소스 멀티유즈: content JSON 하나로 쇼츠 + 카드뉴스를 한 번에 만든다.

    python make.py content/year-end-tax.json            # 둘 다
    python make.py content/year-end-tax.json --only cards
    python make.py content/year-end-tax.json --only shorts

결과물:
    output/<slug>/shorts.mp4
    output/<slug>/cards/card_01.png ...
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

from render_cards import render_cards

ROOT = Path(__file__).resolve().parent

REQUIRED_SCENE_KEYS = ("headline", "narration")


def validate(data: dict, path: Path) -> list[str]:
    errs = []
    if not data.get("title"):
        errs.append("title이 없습니다")
    scenes = data.get("scenes") or []
    if len(scenes) < 2:
        errs.append("scenes는 2개 이상이어야 합니다 (첫 장면=후킹/표지)")
    for i, sc in enumerate(scenes, 1):
        for k in REQUIRED_SCENE_KEYS:
            if not sc.get(k):
                errs.append(f"scenes[{i}]에 {k}가 없습니다")
    return [f"{path}: {e}" for e in errs]


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("content", help="content/<slug>.json")
    ap.add_argument("--only", choices=["shorts", "cards"])
    ap.add_argument("--outroot", default="output")
    args = ap.parse_args()

    path = Path(args.content)
    data = json.loads(path.read_text(encoding="utf-8"))
    errs = validate(data, path)
    if errs:
        print("\n".join(errs), file=sys.stderr)
        return 1

    slug = data.get("slug") or path.stem
    outdir = Path(args.outroot) / slug

    if args.only in (None, "cards"):
        paths = render_cards(data, outdir / "cards")
        print(f"카드뉴스 {len(paths)}장 → {outdir / 'cards'}")

    if args.only in (None, "shorts"):
        out = outdir / "shorts.mp4"
        cmd = [sys.executable, str(ROOT / "render_shorts.py"), "--script", str(path), "--output", str(out)]
        r = subprocess.run(cmd)
        if r.returncode != 0:
            return r.returncode
        print(f"쇼츠 → {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
