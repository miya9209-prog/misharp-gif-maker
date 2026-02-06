# gif_utils.py
# 미샵 이미지 -> GIF (포토샵 Save for Web 느낌)
# 핵심: 팔레트 고정(여러 프레임 기반) + 디더링 + optimize=False + 캔버스 통일(옵션)
# ✅ Streamlit Cloud/Pillow 환경에서 quantize ValueError 방지용 fallback 포함

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


def _quantize_safe(im: Image.Image, colors: int) -> Image.Image:
    """
    ✅ 핵심:
    - Streamlit Cloud 환경에서 LIBIMAGEQUANT가 enum은 있어도 실제 빌드가 없으면 ValueError가 남.
    - 그래서 여러 method로 순차 fallback.
    """
    colors = 256 if colors > 256 else (2 if colors < 2 else int(colors))

    methods = []
    # 존재하면 후보에 넣되, 실패 가능성이 있어 try로 안전 처리
    for name in ("LIBIMAGEQUANT", "FASTOCTREE", "MEDIANCUT"):
        m = getattr(Image.Quantize, name, None)
        if m is not None:
            methods.append(m)

    last_err = None
    for m in methods:
        try:
            return im.quantize(colors=colors, method=m, dither=Image.Dither.NONE)
        except Exception as e:
            last_err = e
            continue

    # 그래도 실패하면 method 파라미터 없이 한 번 더(최후의 수단)
    try:
        return im.quantize(colors=colors, dither=Image.Dither.NONE)
    except Exception as e:
        raise RuntimeError(f"quantize failed. last={last_err!r}, final={e!r}") from e


def _build_palette_seed_from_frames(
    frames: List[Image.Image],
    colors: int,
    sample_count: int = 12,
) -> Image.Image:
    """
    ✅ 팔레트를 여러 프레임 기반으로 만들기 (프레임마다 깨짐/깜빡임 감소)
    ✅ quantize ValueError 방지: _quantize_safe 사용
    """
    if not frames:
        raise ValueError("frames empty")

    colors = 256 if colors > 256 else (2 if colors < 2 else int(colors))

    n = len(frames)
    if n <= sample_count:
        picks = list(range(n))
    else:
        step = max(1, n // sample_count)
        picks = list(range(0, n, step))[:sample_count]

    w, h = frames[0].size
    stack = Image.new("RGB", (w, h * len(picks)))
    for idx, fi in enumerate(picks):
        stack.paste(frames[fi], (0, idx * h))

    # ✅ 여기서 안전 quantize
    palette_seed = _quantize_safe(stack, colors=colors)
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
    if not files:
        return None

    frames: List[Image.Image] = []
    for _, b in files:
        im = _open_from_bytes(b)
        im.load()
        im = _composite_on_bg(im, bg_rgb=(255, 255, 255))
        im = _resize_keep_aspect(im, max_width=max_width)
        frames.append(im)

    if not frames:
        return None

    if unify_canvas:
        frames = _unify_canvas(frames, bg_rgb=(255, 255, 255))

    colors = int(colors)
    palette_seed = _build_palette_seed_from_frames(frames, colors=colors, sample_count=12)

    dither_mode = _dither_mode(dither)
    pal_frames: List[Image.Image] = []
    for im in frames:
        q = im.quantize(palette=palette_seed, dither=dither_mode)
        pal_frames.append(q)

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
