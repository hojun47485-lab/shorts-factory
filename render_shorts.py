#!/usr/bin/env python3
"""API 키 없이 쇼츠(1080x1920, 30fps) 영상을 만드는 렌더러.

- 대본: script.json (title, scenes[{tag, headline, narration}])
- 음성: edge-tts (기본 ko-KR-InJoonNeural), 단어 단위 타이밍으로 자막 싱크
- 화면: Pillow로 다크 그라데이션 카드 배경 / 상단 제목 박스 / 하단 자막을 그리고
        MoviePy로 합성해 MP4로 출력

사용법:
    python render_shorts.py --script script.json --output output/sample.mp4
"""
from __future__ import annotations

import argparse
import asyncio
import json
import math
import os
import ssl
import sys
from dataclasses import dataclass, field
from pathlib import Path

import edge_tts
import edge_tts.communicate
import numpy as np
from moviepy import AudioFileClip, CompositeAudioClip, VideoClip
from PIL import Image, ImageDraw, ImageFilter, ImageFont

W, H = 1080, 1920
FPS = 30
def _find_font(candidates: list[str]) -> str | None:
    """OS별 후보 경로 중 처음 존재하는 폰트. 환경변수로 직접 지정할 수도 있다."""
    win = os.environ.get("WINDIR", r"C:\Windows")
    local = os.environ.get("LOCALAPPDATA", "")
    for c in candidates:
        c = c.format(win=win, local=local, home=str(Path.home()))
        if c and Path(c).is_file():
            return c
    return None


_NANUM_DIRS = [
    "/usr/share/fonts/truetype/nanum/",                    # Linux (fonts-nanum)
    "{win}/Fonts/",                                        # Windows (모든 사용자 설치)
    "{local}/Microsoft/Windows/Fonts/",                    # Windows (현재 사용자 설치)
    "/Library/Fonts/", "{home}/Library/Fonts/",            # macOS
]
FONT_BOLD = os.environ.get("SHORTS_FONT_BOLD") or _find_font(
    [d + "NanumGothicBold.ttf" for d in _NANUM_DIRS]
    + ["{win}/Fonts/malgunbd.ttf",                          # Windows 기본: 맑은 고딕 Bold
       "/System/Library/Fonts/AppleSDGothicNeo.ttc"]        # macOS 기본
) or "/usr/share/fonts/truetype/nanum/NanumGothicBold.ttf"
FONT_HEAVY = os.environ.get("SHORTS_FONT_HEAVY") or _find_font(
    [d + "NanumGothicExtraBold.ttf" for d in _NANUM_DIRS]
) or FONT_BOLD

SCENE_PAD = 0.45      # 장면 끝에 붙는 여백(초)
INTRO_ANIM = 0.45     # 헤드라인 카드 등장 애니메이션 길이(초)
SUB_MAX_CHARS = 22    # 자막 한 덩어리 최대 글자 수
SUB_LINE_CHARS = 13   # 자막 한 줄 최대 글자 수

# 장면별 포인트 색 (순환)
ACCENTS = [
    (255, 196, 61),   # amber
    (94, 234, 212),   # teal
    (129, 160, 255),  # periwinkle
    (255, 128, 160),  # pink
    (163, 230, 53),   # lime
]


# --------------------------------------------------------------------------- #
# TTS
# --------------------------------------------------------------------------- #
def _patch_edge_tts_ssl() -> None:
    """edge-tts는 certifi 번들을 고정으로 쓰므로, 사내/클라우드 프록시처럼
    SSL_CERT_FILE로 별도 CA를 지정한 환경에서는 그 번들을 쓰도록 바꾼다.
    (검증은 그대로 유지)"""
    cafile = os.environ.get("SSL_CERT_FILE")
    if cafile and Path(cafile).is_file():
        edge_tts.communicate._SSL_CTX = ssl.create_default_context(cafile=cafile)


@dataclass
class Word:
    text: str
    start: float
    end: float


@dataclass
class Narration:
    audio_path: Path
    duration: float
    words: list[Word] = field(default_factory=list)


async def _tts(text: str, voice: str, rate: str, out: Path) -> list[Word]:
    com = edge_tts.Communicate(text, voice, rate=rate, boundary="WordBoundary")
    words: list[Word] = []
    with out.open("wb") as f:
        async for chunk in com.stream():
            if chunk["type"] == "audio":
                f.write(chunk["data"])
            elif chunk["type"] == "WordBoundary":
                s = chunk["offset"] / 1e7
                words.append(Word(chunk["text"], s, s + chunk["duration"] / 1e7))
    return words


def synthesize(text: str, voice: str, rate: str, out: Path) -> Narration:
    words = asyncio.run(_tts(text, voice, rate, out))
    with AudioFileClip(str(out)) as a:
        duration = a.duration
    return Narration(out, duration, words)


# --------------------------------------------------------------------------- #
# 자막 분할
# --------------------------------------------------------------------------- #
@dataclass
class Cue:
    text: str
    start: float
    end: float


def _attach_punctuation(narration: str, words: list[Word]) -> list[str]:
    """WordBoundary 텍스트에는 문장부호가 빠져 있으므로 원문에서 다시 찾아 붙인다."""
    out, cursor = [], 0
    spans = []
    for w in words:
        idx = narration.find(w.text, cursor)
        if idx == -1 or idx - cursor > 12:
            spans.append(None)
        else:
            spans.append(idx)
            cursor = idx + len(w.text)
    for i, w in enumerate(words):
        if spans[i] is None:
            out.append(w.text)
            continue
        nxt = next((spans[j] for j in range(i + 1, len(words)) if spans[j] is not None),
                   len(narration))
        out.append(narration[spans[i]:nxt].strip() or w.text)
    return out


def _split_sentence(idx: list[int], texts: list[str]) -> list[list[int]]:
    """단어 인덱스 목록을 SUB_MAX_CHARS 이하 덩어리로 나누는 DP.
    덩어리 수가 적고 길이가 고르며, 쉼표/어절 경계에서 끊기는 쪽을 선호한다."""
    n = len(idx)

    def length(a: int, b: int) -> int:
        return len(" ".join(texts[idx[k]] for k in range(a, b)))

    best = [math.inf] * (n + 1)
    prev = [0] * (n + 1)
    best[0] = 0.0
    for b in range(1, n + 1):
        for a in range(b):
            ln = length(a, b)
            if ln > SUB_MAX_CHARS and b - a > 1:
                continue
            cost = 40.0                      # 덩어리 하나당 기본 비용
            cost += (SUB_MAX_CHARS * 0.75 - ln) ** 2 * 0.15 if ln < SUB_MAX_CHARS * 0.75 else 0
            if b < n and texts[idx[b - 1]][-1] not in ",": # 쉼표가 아닌 곳에서 끊으면 감점
                cost += 25.0
            if b < n and (any(ch.isdigit() for ch in texts[idx[b - 1]]) or len(texts[idx[b]]) <= 1):
                cost += 60.0                 # "8천만 / 원"처럼 숫자·단위가 갈라지는 것 방지
            if best[a] + cost < best[b]:
                best[b], prev[b] = best[a] + cost, a
    out, b = [], n
    while b > 0:
        a = prev[b]
        out.append(idx[a:b])
        b = a
    return out[::-1]


def build_cues(narration: str, n: Narration) -> list[Cue]:
    if not n.words:  # 타이밍 정보가 없으면 글자 수 비율로 균등 분배
        chunks, cur = [], ""
        for tok in narration.split():
            if cur and len(cur) + 1 + len(tok) > SUB_MAX_CHARS:
                chunks.append(cur)
                cur = tok
            else:
                cur = f"{cur} {tok}".strip()
        if cur:
            chunks.append(cur)
        total = sum(len(c) for c in chunks)
        t, cues = 0.0, []
        for c in chunks:
            d = n.duration * len(c) / total
            cues.append(Cue(c, t, t + d))
            t += d
        return cues

    texts = _attach_punctuation(narration, n.words)
    # 문장 단위로 나눈 뒤, 긴 문장은 쉼표 위치를 우선해 길이가 고른 덩어리로 쪼갠다.
    sentences, cur = [], []
    for i, t in enumerate(texts):
        cur.append(i)
        if t[-1] in ".?!":
            sentences.append(cur)
            cur = []
    if cur:
        sentences.append(cur)
    groups = [g for sent in sentences for g in _split_sentence(sent, texts)]

    cues = []
    for gi, g in enumerate(groups):
        start = n.words[g[0]].start
        end = n.words[groups[gi + 1][0]].start if gi + 1 < len(groups) else n.duration + SCENE_PAD
        cues.append(Cue(" ".join(texts[j] for j in g), start, end))
    cues[0].start = 0.0
    return cues


def wrap_text(text: str, max_chars: int) -> list[str]:
    """띄어쓰기 기준 줄바꿈. 줄 길이가 고르게 되도록 균형을 맞춘다."""
    if len(text) <= max_chars:
        return [text]
    toks = text.split()
    best, best_score = [text], math.inf
    for k in range(1, len(toks)):
        a, b = " ".join(toks[:k]), " ".join(toks[k:])
        score = max(len(a), len(b)) + abs(len(a) - len(b)) * 0.3
        if score < best_score:
            best, best_score = [a, b], score
    return best


# --------------------------------------------------------------------------- #
# 그래픽
# --------------------------------------------------------------------------- #
def font(size: int, heavy: bool = False) -> ImageFont.FreeTypeFont:
    path = FONT_HEAVY if heavy and Path(FONT_HEAVY).exists() else FONT_BOLD
    return ImageFont.truetype(path, size)


def gradient_background(accent: tuple[int, int, int], seed: int,
                        size: tuple[int, int] = (W, H)) -> Image.Image:
    W, H = size  # noqa: N806 - 쇼츠/카드뉴스 공용
    yy, xx = np.mgrid[0:H, 0:W].astype(np.float32)
    t = yy / H
    top = np.array([14, 16, 32], np.float32)
    bottom = np.array([6, 7, 14], np.float32)
    img = top * (1 - t[..., None]) + bottom * t[..., None]

    # 포인트 색의 은은한 광원 2개
    acc = np.array(accent, np.float32)
    for cx, cy, r, s in [(0.85, 0.18, 0.75, 0.28), (0.1, 0.78, 0.65, 0.16)]:
        d = np.sqrt(((xx - cx * W) / W) ** 2 + ((yy - cy * H) / W) ** 2) / r
        img += acc[None, None, :] * (np.clip(1 - d, 0, 1) ** 2.2)[..., None] * s
    # 보라빛 보조광
    d = np.sqrt(((xx - 0.3 * W) / W) ** 2 + ((yy - 0.42 * H) / W) ** 2) / 0.9
    img += np.array([70, 40, 140], np.float32) * (np.clip(1 - d, 0, 1) ** 2)[..., None] * 0.35

    # 밴딩 방지용 미세 노이즈
    rng = np.random.default_rng(seed)
    img += rng.normal(0, 1.6, img.shape).astype(np.float32)
    return Image.fromarray(np.clip(img, 0, 255).astype(np.uint8))


def draw_center_text(draw: ImageDraw.ImageDraw, cx: int, y: int, lines: list[str],
                     fnt: ImageFont.FreeTypeFont, fill, spacing: int,
                     stroke: int = 0, stroke_fill=None) -> int:
    for line in lines:
        bbox = draw.textbbox((0, 0), line, font=fnt, stroke_width=stroke)
        w, h = bbox[2] - bbox[0], bbox[3] - bbox[1]
        draw.text((cx - w / 2 - bbox[0], y - bbox[1]), line, font=fnt, fill=fill,
                  stroke_width=stroke, stroke_fill=stroke_fill)
        y += h + spacing
    return y


def text_block_height(draw, lines, fnt, spacing, stroke=0) -> int:
    hs = [draw.textbbox((0, 0), ln, font=fnt, stroke_width=stroke) for ln in lines]
    return sum(b[3] - b[1] for b in hs) + spacing * (len(lines) - 1)


def soft_shadow(size, box, radius, blur, alpha) -> Image.Image:
    sh = Image.new("RGBA", size, (0, 0, 0, 0))
    ImageDraw.Draw(sh).rounded_rectangle(box, radius, fill=(0, 0, 0, alpha))
    return sh.filter(ImageFilter.GaussianBlur(blur))


def render_static_layer(title: str, accent, seed: int) -> Image.Image:
    """배경 + 상단 제목 박스 (장면 내내 고정)."""
    bg = gradient_background(accent, seed).convert("RGBA")

    # 은은한 격자 패턴
    grid = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    gd = ImageDraw.Draw(grid)
    for x in range(0, W, 90):
        gd.line([(x, 0), (x, H)], fill=(255, 255, 255, 7))
    for y in range(0, H, 90):
        gd.line([(0, y), (W, y)], fill=(255, 255, 255, 7))
    bg = Image.alpha_composite(bg, grid)

    # 상단 제목 박스
    d = ImageDraw.Draw(bg)
    lines = title.split("\n")
    tf = font(64, heavy=True)
    th = text_block_height(d, lines, tf, 22)
    box = (70, 150, W - 70, 150 + th + 110)
    bg = Image.alpha_composite(bg, soft_shadow((W, H), box, 36, 28, 150))
    panel = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    pd = ImageDraw.Draw(panel)
    pd.rounded_rectangle(box, 36, fill=(255, 255, 255, 18), outline=(*accent, 210), width=4)
    pd.rounded_rectangle((box[0] + 60, box[1] - 4, box[0] + 220, box[1] + 4), 4, fill=(*accent, 255))
    bg = Image.alpha_composite(bg, panel)
    d = ImageDraw.Draw(bg)
    draw_center_text(d, W // 2, box[1] + 55, lines, tf, (255, 255, 255), 22)
    return bg


def render_card(tag: str, headline: str, accent, index: int, total: int) -> Image.Image:
    """화면 중앙의 핵심 키워드 카드 (투명 배경 RGBA, 애니메이션 대상)."""
    layer = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    d = ImageDraw.Draw(layer)
    lines = headline.split("\n")
    hf = font(132, heavy=True)
    hh = text_block_height(d, lines, hf, 34)
    card_h = hh + 300
    top = 770 - card_h // 2 + 120
    box = (90, top, W - 90, top + card_h)

    layer = Image.alpha_composite(layer, soft_shadow((W, H), box, 48, 40, 170))
    card = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    cd = ImageDraw.Draw(card)
    cd.rounded_rectangle(box, 48, fill=(22, 24, 44, 215), outline=(255, 255, 255, 34), width=2)
    # 카드 왼쪽 포인트 바
    cd.rounded_rectangle((box[0], box[1] + 60, box[0] + 10, box[3] - 60), 5, fill=(*accent, 255))
    layer = Image.alpha_composite(layer, card)
    d = ImageDraw.Draw(layer)

    # 태그 pill
    tgf = font(40, heavy=True)
    tb = d.textbbox((0, 0), tag, font=tgf)
    tw, tht = tb[2] - tb[0], tb[3] - tb[1]
    px, py = 34, 18
    pill = (W // 2 - tw // 2 - px, box[1] + 60, W // 2 + tw // 2 + px, box[1] + 60 + tht + 2 * py)
    d.rounded_rectangle(pill, (pill[3] - pill[1]) // 2, fill=(*accent, 255))
    d.text((W // 2 - tw / 2 - tb[0], pill[1] + py - tb[1]), tag, font=tgf, fill=(12, 14, 26))

    # 헤드라인
    y = pill[3] + 60
    draw_center_text(d, W // 2, y, lines, hf, (255, 255, 255), 34)

    # 페이지 점
    dots_y = box[3] - 50
    gap = 34
    x0 = W // 2 - gap * (total - 1) / 2
    for i in range(total):
        r = 9 if i == index else 6
        col = (*accent, 255) if i == index else (255, 255, 255, 70)
        cx = x0 + i * gap
        d.ellipse((cx - r, dots_y - r, cx + r, dots_y + r), fill=col)
    return layer


def render_subtitle(text: str, accent) -> tuple[np.ndarray, int, int]:
    """하단 자막 이미지. (RGBA 배열, x, y) 반환 — 필요한 영역만 합성하기 위함."""
    sf = font(70)
    lines = wrap_text(text, SUB_LINE_CHARS)
    tmp = ImageDraw.Draw(Image.new("RGBA", (1, 1)))
    stroke = 6
    th = text_block_height(tmp, lines, sf, 26, stroke)
    tw = max(tmp.textbbox((0, 0), ln, font=sf, stroke_width=stroke)[2] for ln in lines)
    pad_x, pad_y = 56, 44
    bw, bh = min(W - 80, tw + 2 * pad_x), th + 2 * pad_y
    img = Image.new("RGBA", (bw + 60, bh + 60), (0, 0, 0, 0))
    box = (30, 30, 30 + bw, 30 + bh)
    img = Image.alpha_composite(img, soft_shadow(img.size, box, 34, 18, 140))
    d = ImageDraw.Draw(img)
    d.rounded_rectangle(box, 34, fill=(0, 0, 0, 150))
    d.rounded_rectangle((box[0] + bw // 2 - 50, box[3] - 8, box[0] + bw // 2 + 50, box[3] - 2), 3,
                        fill=(*accent, 230))
    draw_center_text(d, img.width // 2, box[1] + pad_y, lines, sf, (255, 255, 255), 26,
                     stroke=stroke, stroke_fill=(0, 0, 0))
    x = (W - img.width) // 2
    y = 1560 - img.height // 2
    return np.asarray(img), x, y


# --------------------------------------------------------------------------- #
# 합성
# --------------------------------------------------------------------------- #
def blend(dst: np.ndarray, src_rgba: np.ndarray, x: int, y: int, alpha: float = 1.0) -> None:
    h, w = src_rgba.shape[:2]
    x0, y0 = max(x, 0), max(y, 0)
    x1, y1 = min(x + w, W), min(y + h, H)
    if x0 >= x1 or y0 >= y1:
        return
    s = src_rgba[y0 - y:y1 - y, x0 - x:x1 - x].astype(np.float32)
    a = s[..., 3:4] / 255.0 * alpha
    region = dst[y0:y1, x0:x1].astype(np.float32)
    dst[y0:y1, x0:x1] = (s[..., :3] * a + region * (1 - a)).astype(np.uint8)


def crop_to_content(rgba: Image.Image) -> tuple[np.ndarray, int, int]:
    bbox = rgba.getbbox() or (0, 0, 1, 1)
    return np.asarray(rgba.crop(bbox)), bbox[0], bbox[1]


def ease_out(t: float) -> float:
    return 1 - (1 - t) ** 3


@dataclass
class SceneRender:
    start: float
    duration: float
    base: np.ndarray
    card: tuple[np.ndarray, int, int]
    cues: list[tuple[Cue, tuple[np.ndarray, int, int]]]
    accent: tuple[int, int, int]


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--script", default="script.json")
    ap.add_argument("--output", default="output/sample.mp4")
    ap.add_argument("--voice", help="edge-tts 음성 (기본: script.json의 voice 또는 ko-KR-InJoonNeural)")
    ap.add_argument("--rate", help='말하기 속도, 예: "+10%%"')
    args = ap.parse_args()

    for f in (FONT_BOLD,):
        if not Path(f).exists():
            sys.exit(f"폰트가 없습니다: {f}\n"
                     "  Linux: sudo apt-get install -y fonts-nanum\n"
                     "  Windows/macOS: 나눔고딕을 설치하거나 SHORTS_FONT_BOLD 환경변수로 .ttf 경로를 지정하세요")

    data = json.loads(Path(args.script).read_text(encoding="utf-8"))
    voice = args.voice or data.get("voice", "ko-KR-InJoonNeural")
    rate = args.rate or data.get("rate", "+0%")
    title = data.get("title", "")
    scenes = data["scenes"]

    out = Path(args.output)
    work = out.parent / f".{out.stem}_work"
    work.mkdir(parents=True, exist_ok=True)
    _patch_edge_tts_ssl()

    renders: list[SceneRender] = []
    audio_clips = []
    t = 0.0
    for i, sc in enumerate(scenes):
        accent = tuple(sc.get("accent", ACCENTS[i % len(ACCENTS)]))
        print(f"[{i + 1}/{len(scenes)}] TTS: {sc['headline'].replace(chr(10), ' ')}")
        nar = synthesize(sc["narration"], voice, rate, work / f"scene{i + 1:02d}.mp3")
        dur = nar.duration + SCENE_PAD
        cues = build_cues(sc["narration"], nar)

        base = np.array(render_static_layer(title, accent, seed=i).convert("RGB"))
        card = crop_to_content(render_card(sc.get("tag", f"#{i + 1}"), sc["headline"], accent, i, len(scenes)))
        cue_imgs = [(c, render_subtitle(c.text, accent)) for c in cues]
        renders.append(SceneRender(t, dur, base, card, cue_imgs, accent))
        audio_clips.append(AudioFileClip(str(nar.audio_path)).with_start(t))
        print(f"      {dur:5.2f}s, 자막 {len(cues)}개")
        t += dur
    total = t

    def frame(tt: float) -> np.ndarray:
        idx = next((k for k, r in enumerate(renders) if tt < r.start + r.duration), len(renders) - 1)
        r = renders[idx]
        lt = tt - r.start
        f = r.base.copy()

        # 카드: 아래에서 위로 살짝 올라오며 페이드인
        p = ease_out(min(1.0, lt / INTRO_ANIM))
        img, x, y = r.card
        blend(f, img, x, y + int((1 - p) * 60), alpha=p)

        # 자막
        for cue, (simg, sx, sy) in r.cues:
            if cue.start <= lt < cue.end:
                sp = min(1.0, (lt - cue.start) / 0.12)
                blend(f, simg, sx, sy + int((1 - sp) * 14), alpha=sp)
                break

        # 하단 전체 진행 바
        bar_y, bar_h = H - 36, 8
        f[bar_y:bar_y + bar_h, 60:W - 60] = (f[bar_y:bar_y + bar_h, 60:W - 60] * 0.6 + 40).astype(np.uint8)
        filled = 60 + int((W - 120) * min(1.0, tt / total))
        f[bar_y:bar_y + bar_h, 60:filled] = r.accent
        return f

    video = VideoClip(frame_function=frame, duration=total)
    video = video.with_audio(CompositeAudioClip(audio_clips).with_duration(total))
    out.parent.mkdir(parents=True, exist_ok=True)
    video.write_videofile(
        str(out), fps=FPS, codec="libx264", audio_codec="aac", audio_bitrate="160k",
        preset="medium", ffmpeg_params=["-pix_fmt", "yuv420p", "-crf", "20", "-movflags", "+faststart"],
        threads=os.cpu_count() or 2, logger="bar",
    )
    print(f"완료: {out}  ({total:.1f}s)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
