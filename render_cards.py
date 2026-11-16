#!/usr/bin/env python3
"""content JSON으로 인스타그램용 카드뉴스(1080x1350 PNG)를 만든다.

카드 구성:
    1장   표지      title + 첫 장면 headline
    2..n  본문      scenes[1:] 각각 (tag, headline, body 불릿)
    마지막 마무리    cta (없으면 생략)

사용법:
    python render_cards.py --content content/year-end-tax.json --outdir output/year-end-tax/cards
"""
from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from render_shorts import ACCENTS, font, gradient_background, soft_shadow

CW, CH = 1080, 1350
MARGIN = 96
TEXT = (255, 255, 255)
MUTED = (178, 184, 210)
INK = (12, 14, 26)


# --------------------------------------------------------------------------- #
# 텍스트 유틸 (픽셀 폭 기준 줄바꿈)
# --------------------------------------------------------------------------- #
def text_w(draw: ImageDraw.ImageDraw, s: str, fnt: ImageFont.FreeTypeFont) -> int:
    b = draw.textbbox((0, 0), s, font=fnt)
    return b[2] - b[0]


UNITS = {"원", "명", "개", "년", "회", "세", "%", "만", "억"}
NBSP = "\u00a0"


def _glue_units(para: str) -> list[str]:
    """'100만 원'처럼 숫자 뒤 한 글자 단위는 앞 어절에 붙여 같은 줄에 둔다."""
    words: list[str] = []
    for w in (t for t in para.split(" ") if t):
        if words and w.rstrip(".,)") in UNITS and words[-1][-1:].isdigit() is False and \
                any(c.isdigit() for c in words[-1]):
            words[-1] += NBSP + w
        else:
            words.append(w)
    return words


def _greedy(draw, words: list[str], fnt, max_w: int) -> list[str]:
    lines, cur = [], ""
    for word in words:
        cand = f"{cur} {word}".strip()
        if text_w(draw, cand, fnt) <= max_w:
            cur = cand
            continue
        if cur:
            lines.append(cur)
        while text_w(draw, word, fnt) > max_w:  # 한 어절이 너무 길면 글자 단위로
            k = len(word)
            while k > 1 and text_w(draw, word[:k], fnt) > max_w:
                k -= 1
            lines.append(word[:k])
            word = word[k:]
        cur = word
    lines.append(cur)
    return lines


def wrap_px(draw, text: str, fnt, max_w: int) -> list[str]:
    """명시적 줄바꿈(\\n)을 지키면서 픽셀 폭에 맞춰 어절 단위로 줄바꿈.
    여러 줄이 되면 줄 수는 유지한 채 폭을 줄여 줄 길이를 고르게 맞춘다."""
    out: list[str] = []
    for para in text.split("\n"):
        words = _glue_units(para)
        lines = _greedy(draw, words, fnt, max_w)
        if len(lines) > 1:
            lo, hi = max_w // 3, max_w
            while hi - lo > 4:
                mid = (lo + hi) // 2
                if len(_greedy(draw, words, fnt, mid)) == len(lines):
                    hi = mid
                else:
                    lo = mid
            lines = _greedy(draw, words, fnt, hi)
        out.extend(ln.replace(NBSP, " ") for ln in lines)
    return out


def line_h(fnt) -> int:
    a, d = fnt.getmetrics()
    return a + d


def draw_lines(draw, x, y, lines, fnt, fill, gap, align="left", box_w=0) -> int:
    lh = line_h(fnt)
    for ln in lines:
        lx = x
        if align == "center":
            lx = x + (box_w - text_w(draw, ln, fnt)) / 2
        draw.text((lx, y), ln, font=fnt, fill=fill)
        y += lh + gap
    return y - gap


def body_items(scene: dict) -> list[str]:
    """body가 없으면 나레이션을 문장 단위로 쪼개 쓴다."""
    if scene.get("body"):
        return list(scene["body"])
    return [s.strip() for s in re.split(r"(?<=[.?!])\s+", scene.get("narration", "")) if s.strip()]


# --------------------------------------------------------------------------- #
# 공통 프레임
# --------------------------------------------------------------------------- #
def base_canvas(accent, seed: int) -> Image.Image:
    bg = gradient_background(accent, seed, size=(CW, CH)).convert("RGBA")
    grid = Image.new("RGBA", (CW, CH), (0, 0, 0, 0))
    gd = ImageDraw.Draw(grid)
    for x in range(0, CW, 90):
        gd.line([(x, 0), (x, CH)], fill=(255, 255, 255, 7))
    for y in range(0, CH, 90):
        gd.line([(0, y), (CW, y)], fill=(255, 255, 255, 7))
    return Image.alpha_composite(bg, grid)


def header_footer(img: Image.Image, series: str, page: int, total: int, accent) -> None:
    d = ImageDraw.Draw(img)
    sf = font(34)
    d.rounded_rectangle((MARGIN, 92, MARGIN + 54, 100), 4, fill=(*accent, 255))
    d.text((MARGIN, 120), series, font=sf, fill=MUTED)
    pg = f"{page:02d} / {total:02d}"
    d.text((CW - MARGIN - text_w(d, pg, sf), 120), pg, font=sf, fill=MUTED)

    # 하단 진행 점
    gap, y = 30, CH - 86
    x0 = CW / 2 - gap * (total - 1) / 2
    for i in range(total):
        r = 8 if i == page - 1 else 5
        col = (*accent, 255) if i == page - 1 else (255, 255, 255, 70)
        cx = x0 + i * gap
        d.ellipse((cx - r, y - r, cx + r, y + r), fill=col)


def tag_pill(d: ImageDraw.ImageDraw, x: int, y: int, tag: str, accent) -> int:
    tf = font(36, heavy=True)
    b = d.textbbox((0, 0), tag, font=tf)
    px, py = 28, 14
    box = (x, y, x + (b[2] - b[0]) + 2 * px, y + (b[3] - b[1]) + 2 * py)
    d.rounded_rectangle(box, (box[3] - box[1]) // 2, fill=(*accent, 255))
    d.text((x + px - b[0], y + py - b[1]), tag, font=tf, fill=INK)
    return box[3]


# --------------------------------------------------------------------------- #
# 카드 종류별
# --------------------------------------------------------------------------- #
def cover_card(title: str, subtitle: str, series: str, total: int, accent) -> Image.Image:
    img = base_canvas(accent, seed=100)
    header_footer(img, series, 1, total, accent)
    d = ImageDraw.Draw(img)
    max_w = CW - 2 * MARGIN

    tf = font(104, heavy=True)
    t_lines = wrap_px(d, title, tf, max_w)
    sf = font(52)
    s_lines = wrap_px(d, subtitle.replace("\n", " "), sf, max_w - 60) if subtitle else []

    t_h = len(t_lines) * line_h(tf) + (len(t_lines) - 1) * 20
    s_h = len(s_lines) * line_h(sf) + max(0, len(s_lines) - 1) * 14
    block = t_h + (90 + s_h if s_lines else 0)
    y = (CH - block) // 2 - 10

    y = draw_lines(d, MARGIN, y, t_lines, tf, TEXT, 20)
    if s_lines:
        y += 46
        d.rounded_rectangle((MARGIN, y, MARGIN + 120, y + 10), 5, fill=(*accent, 255))
        y += 44
        draw_lines(d, MARGIN, y, s_lines, sf, MUTED, 14)

    hint_f = font(36)
    hint = "옆으로 넘겨보세요  →"
    d.text((CW - MARGIN - text_w(d, hint, hint_f), CH - 190), hint, font=hint_f, fill=(*accent, 255))
    return img


def point_card(scene: dict, series: str, page: int, total: int, accent) -> Image.Image:
    img = base_canvas(accent, seed=page)
    header_footer(img, series, page, total, accent)
    d = ImageDraw.Draw(img)
    max_w = CW - 2 * MARGIN

    y = 250
    if scene.get("tag"):
        y = tag_pill(d, MARGIN, y, scene["tag"], accent) + 40
    hf = font(92, heavy=True)
    y = draw_lines(d, MARGIN, y, wrap_px(d, scene["headline"], hf, max_w), hf, TEXT, 16)
    y += 70

    # 불릿 박스: 남은 공간에 맞춰 글자 크기를 줄여 가며 배치
    items = body_items(scene)
    bottom = CH - 150
    for size in (50, 46, 42, 38, 34):
        bf = font(size)
        laid = [wrap_px(d, it, bf, max_w - 120) for it in items]
        pad, gap = 34, 24
        heights = [len(ls) * line_h(bf) + (len(ls) - 1) * 10 + 2 * pad for ls in laid]
        if y + sum(heights) + gap * (len(heights) - 1) <= bottom:
            break

    for ls, h in zip(laid, heights):
        box = (MARGIN, y, CW - MARGIN, y + h)
        img.alpha_composite(soft_shadow((CW, CH), box, 28, 16, 110))
        panel = Image.new("RGBA", (CW, CH), (0, 0, 0, 0))
        pd = ImageDraw.Draw(panel)
        pd.rounded_rectangle(box, 28, fill=(22, 24, 44, 210), outline=(255, 255, 255, 30), width=2)
        img.alpha_composite(panel)
        d = ImageDraw.Draw(img)
        cy = y + pad + line_h(bf) // 2
        d.ellipse((MARGIN + 38, cy - 9, MARGIN + 56, cy + 9), fill=(*accent, 255))
        draw_lines(d, MARGIN + 84, y + pad, ls, bf, TEXT, 10)
        y += h + gap
    return img


def cta_card(cta: str, series: str, page: int, total: int, accent) -> Image.Image:
    img = base_canvas(accent, seed=200)
    header_footer(img, series, page, total, accent)
    d = ImageDraw.Draw(img)
    max_w = CW - 2 * MARGIN
    cf = font(84, heavy=True)
    lines = wrap_px(d, cta, cf, max_w)
    h = len(lines) * line_h(cf) + (len(lines) - 1) * 18
    y = (CH - h) // 2 - 40
    draw_lines(d, MARGIN, y, lines, cf, TEXT, 18, align="center", box_w=max_w)
    icons = "저장  ·  공유  ·  팔로우"
    f2 = font(40)
    d.text(((CW - text_w(d, icons, f2)) / 2, y + h + 70), icons, font=f2, fill=(*accent, 255))
    return img


# --------------------------------------------------------------------------- #
def render_cards(data: dict, outdir: Path) -> list[Path]:
    title = data["title"]
    series = data.get("series") or title.replace("\n", " ")
    scenes = data["scenes"]
    cta = data.get("cta")
    total = 1 + len(scenes) - 1 + (1 if cta else 0)

    outdir.mkdir(parents=True, exist_ok=True)
    for old in outdir.glob("card_*.png"):
        old.unlink()

    cards = [cover_card(title, scenes[0]["headline"], series, total, ACCENTS[0])]
    for i, sc in enumerate(scenes[1:], start=1):
        accent = tuple(sc.get("accent", ACCENTS[i % len(ACCENTS)]))
        cards.append(point_card(sc, series, i + 1, total, accent))
    if cta:
        cards.append(cta_card(cta, series, total, total, ACCENTS[0]))

    paths = []
    for i, c in enumerate(cards, start=1):
        p = outdir / f"card_{i:02d}.png"
        c.convert("RGB").save(p, optimize=True)
        paths.append(p)
    return paths


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--content", required=True)
    ap.add_argument("--outdir", required=True)
    args = ap.parse_args()
    data = json.loads(Path(args.content).read_text(encoding="utf-8"))
    for p in render_cards(data, Path(args.outdir)):
        print(p)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
