# gif_utils.py
# 미샵 이미지 -> GIF (포토샵 Save for Web 느낌)
# 핵심: 팔레트 고정(전체 프레임 기반) + 디더링 + optimize=False + 캔버스 통일(옵션)

from __future__ import annotations

from io import BytesIO
from typing import List, Tuple, Optional

from PIL import Image


def _open_from_bytes(b: bytes) -> Image.Image:
    return Image.open(BytesIO(b))


def _composite_on_bg(img: Image.Image, bg_rgb=(255, 255, 255)) -> Image.Image:
    """GIF 팔레트 변환 전, RGBA/투명은 배경 합성해서 안정화"""
    if img.mode in ("RGBA", "LA") or (img.mode == "P" and "transparency" in img.info):
        rgba = img.convert("RGBA")
        bg = Image.new("RGBA", rgba.size, (*bg_rgb, 255))
        return Image.alpha_composite(bg, rgba).convert("RGB")
    return img.convert("RGB")


def _resize_keep_aspect(img: Image.Image, max_width: Optional[int]) -> Image.Image:
    if not max_width:
        return img
    w, h = img.size
    if w <= max_width:
        return img
    new_h = int(round(h * (max_width / w)))
    return img.resize((max_width, new_h), Image.Resampling.LANCZOS)


def _unify_canvas(frames: List[Image.Image], bg_rgb=(255, 255, 255)) -> List[Image.Image]:
    """사이즈가 섞일 때 가장 큰 캔버스로 패딩해서 통일(정렬 흔들림 방지)"""
    if not frames:
        return frames
    max_w = max(im.size[0] for im in frames)
    max_h = max(im.size[1] for im in frames)
    out = []
    for im in frames:
        if im.size == (max_w, max_h):
            out.append(im)
            continue
        canvas = Image.new("RGB", (max_w, max_h), bg_rgb)
        x = (max_w - im.size[0]) // 2
        y = (max_h - im.size[1]) // 2
        canvas.paste(im, (x, y))
        out.append(canvas)
    return out


def _dither_mode(dither: str):
    d = (dither or "").lower()
    if d in ("floyd_steinberg", "floyd", "fs"):
        return Image.Dither.FLOYDSTEINBERG
    if d in ("bayer", "ordered"):
        return Image.Dither.ORDERED
    return Image.Dither.NONE


def _best_quantize_method():
    """
    Pillow가 libimagequant를 지원하면 그게 사진 GIF에 훨씬 유리.
    환경에 없을 수 있으니 안전하게 fallback.
    """
    # Pillow enum 존재 여부/지원 여부가 환경마다 달라서 try로 처리
    try:
        return Image.Quantize.LIBIMAGEQUANT
    except Exception:
        return Image.Quantize.MEDIANCUT


def _build_palette_seed_from_frames(
    frames: List[Image.Image],
    colors: int,
    sample_count: int = 12,
) -> Image.Image:
    """
    ✅ 핵심 개선:
    - 팔레트를 첫 프레임 1장으로 뽑지 말고,
      여러 프레임을 샘플링해서 '팔레트 시드'를 만듦.
    - 이렇게 해야 프레임마다 색/명암이 조금씩 달라도 덜 깨짐.
    """
    if not frames:
        raise ValueError("frames empty")

    colors = 256 if colors > 256 else (2 if colors < 2 else colors)

    # 샘플링: 앞/중간/뒤 골고루
    n = len(frames)
    if n <= sample_count:
        picks = list(range(n))
    else:
        step = max(1, n // sample_count)
        picks = list(range(0, n, step))[:sample_count]

    # 샘플 프레임을 세로로 이어붙인 "팔레트용 큰 이미지" 생성
    w, h = frames[0].size
    stack = Image.new("RGB", (w, h * len(picks)))
    for idx, fi in enumerate(picks):
        stack.paste(frames[fi], (0, idx * h))

    method = _best_quantize_method()
    palette_seed = stack.quantize(
        colors=colors,
        method=method,
        dither=Image.Dither.NONE,
    )
    return palette_seed


def build_gif_from_images(
    files: List[Tuple[str, bytes]],
    delay_sec: float = 1.0,
    loop_forever: bool = True,
    unify_canvas: bool = True,
    max_width: Optional[int] = 450,
    colors: int = 256,
    dither: str = "floyd_steinberg",
) -> Optional[bytes]:
    """
    files: [(filename, bytes), ...]
    return: gif bytes
    """
    if not files:
        return None

    # 1) 로드 + RGB 합성 + 리사이즈
    frames: List[Image.Image] = []
    for _, b in files:
        im = _open_from_bytes(b)
        im.load()
        im = _composite_on_bg(im, bg_rgb=(255, 255, 255))
        im = _resize_keep_aspect(im, max_width=max_width)
        frames.append(im)

    if not frames:
        return None

    # 2) 캔버스 통일(옵션)
    if unify_canvas:
        frames = _unify_canvas(frames, bg_rgb=(255, 255, 255))

    # 3) ✅ 팔레트 "전체 프레임 기반"으로 고정 시드 생성 (여기가 품질 핵심)
    colors = int(colors)
    palette_seed = _build_palette_seed_from_frames(frames, colors=colors, sample_count=12)

    # 4) 모든 프레임을 "같은 팔레트"로 변환 + 디더링(사용자 선택)
    dither_mode = _dither_mode(dither)
    pal_frames: List[Image.Image] = []
    for im in frames:
        q = im.quantize(palette=palette_seed, dither=dither_mode)
        pal_frames.append(q)

    # 5) 저장 (optimize=False가 품질 안정에 중요)
    duration_ms = int(round(float(delay_sec) * 1000))
    out = BytesIO()

    pal_frames[0].save(
        out,
        format="GIF",
        save_all=True,
        append_images=pal_frames[1:],
        duration=duration_ms,
        loop=0 if loop_forever else 1,
        optimize=False,
        disposal=2,
    )
    return out.getvalue()
