from __future__ import annotations
import os
import tempfile
import subprocess
from typing import Optional

import imageio_ffmpeg


def build_gif_from_video_ffmpeg(
    video_bytes: bytes,
    fps: int = 12,
    max_width: Optional[int] = 720,
    loop_forever: bool = True,
    start_sec: Optional[float] = None,
    end_sec: Optional[float] = None,
    colors: int = 128,
    dither: str = "floyd",  # "floyd" or "none"
) -> bytes:
    """
    동영상 -> GIF (고퀄/저용량)
    핵심: palettegen + paletteuse, 그리고 팔레트 색상수 제한(colors), fps, width 조절

    - fps: 낮출수록 용량 대폭 감소 (권장 8~15)
    - max_width: 가로폭 낮출수록 용량 대폭 감소 (권장 540~720)
    - colors: 64/96/128/256 (낮출수록 용량 감소, 96~128이 실무 밸런스)
    - dither: floyd가 품질 좋지만 약간 용량↑, none은 더 가벼움
    """

    ffmpeg = imageio_ffmpeg.get_ffmpeg_exe()

    with tempfile.TemporaryDirectory() as td:
        in_path = os.path.join(td, "input_video.mp4")
        pal_path = os.path.join(td, "palette.png")
        out_path = os.path.join(td, "output.gif")

        with open(in_path, "wb") as f:
            f.write(video_bytes)

        # 구간 옵션
        ss = []
        if start_sec is not None and start_sec >= 0:
            ss = ["-ss", str(start_sec)]

        dur = []
        if end_sec is not None and start_sec is not None and end_sec > start_sec:
            dur = ["-t", str(end_sec - start_sec)]
        elif end_sec is not None and start_sec is None and end_sec > 0:
            dur = ["-to", str(end_sec)]

        # 스케일 필터
        if max_width:
            scale_filter = f"scale={max_width}:-1:flags=lanczos"
        else:
            scale_filter = "scale=iw:ih:flags=lanczos"

        # 디더
        dither_mode = "floyd_steinberg" if dither == "floyd" else "none"

        # ✅ palettegen: max_colors로 색상 제한 (용량 절감 핵심)
        # stats_mode=full: 안정적인 팔레트
        cmd_palette = [
            ffmpeg,
            *ss,
            "-i", in_path,
            *dur,
            "-vf", f"fps={fps},{scale_filter},palettegen=stats_mode=full:max_colors={colors}",
            "-y",
            pal_path
        ]

        # ✅ paletteuse: 디더링 적용 + 알파 처리 향상
        loop_flag = 0 if loop_forever else 1
        cmd_gif = [
            ffmpeg,
            *ss,
            "-i", in_path,
            *dur,
            "-i", pal_path,
            "-lavfi",
            f"fps={fps},{scale_filter}[x];[x][1:v]paletteuse=dither={dither_mode}:diff_mode=rectangle",
            "-loop", str(loop_flag),
            "-y",
            out_path
        ]

        subprocess.run(cmd_palette, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        subprocess.run(cmd_gif, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)

        with open(out_path, "rb") as f:
            return f.read()
