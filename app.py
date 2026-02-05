# app.py
# MISHARP GIF Engine (FFmpeg)
# - 이미지 프레임 → GIF
# - 동영상 → GIF
# - 가로 그리드 미리보기 + ← → 순서변경
# - 시간 조절:
#   1) FPS
#   2) 총 재생시간(초)
#   3) 프레임 간격(초) 0.5초부터 0.5초 단위 (최대 10초)
# - 무한반복 ON/OFF

import os
import io
import shutil
import tempfile
import subprocess
from dataclasses import dataclass
from typing import List, Optional
from fractions import Fraction

import streamlit as st
from PIL import Image


# -----------------------------
# 기본 설정
# -----------------------------
st.set_page_config(page_title="MISHARP GIF Engine", layout="wide")
st.title("MISHARP GIF Engine (FFmpeg)")
st.caption("이미지 프레임 또는 동영상을 GIF로 변환합니다. (FFmpeg 필요)")

def _which(cmd: str) -> Optional[str]:
    from shutil import which
    return which(cmd)

if not _which("ffmpeg"):
    st.error("FFmpeg가 설치되어 있지 않습니다. 서버/PC에 ffmpeg를 설치한 뒤 다시 실행해 주세요.")
    st.stop()


# -----------------------------
# 데이터 구조 / 세션
# -----------------------------
@dataclass
class FrameItem:
    name: str
    bytes: bytes

def ensure_state():
    if "frames" not in st.session_state:
        st.session_state.frames = []  # List[FrameItem]
    if "video_file" not in st.session_state:
        st.session_state.video_file = None  # {"name":str,"bytes":bytes}

ensure_state()


# -----------------------------
# 유틸: ffmpeg 실행
# -----------------------------
def run_cmd(cmd: List[str]) -> str:
    p = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    if p.returncode != 0:
        raise RuntimeError((p.stderr or "")[-4000:])
    return p.stdout or ""


# -----------------------------
# 유틸: 리스트 순서 이동
# -----------------------------
def move_item(lst, i: int, direction: int):
    j = i + direction
    if 0 <= j < len(lst):
        lst[i], lst[j] = lst[j], lst[i]


# -----------------------------
# UI: 가로 그리드 미리보기 + 순서변경
# -----------------------------
def render_frame_grid(frames: List[FrameItem], cols: int = 5, thumb_w: int = 170):
    st.markdown("#### 프레임 미리보기 (순서 변경)")
    st.caption("썸네일 아래 ← → 버튼으로 순서를 바꾸고, 🗑️로 삭제할 수 있어요.")
    st.write(f"현재 프레임: **{len(frames)}장**")

    if not frames:
        st.info("프레임 이미지를 업로드해 주세요.")
        return

    rows = (len(frames) + cols - 1) // cols
    idx = 0
    for _ in range(rows):
        grid = st.columns(cols, gap="small")
        for c in range(cols):
            if idx >= len(frames):
                break
            item = frames[idx]
            with grid[c]:
                try:
                    img = Image.open(io.BytesIO(item.bytes)).convert("RGB")
                    st.image(img, width=thumb_w)
                except Exception:
                    st.write("미리보기 불가")

                st.caption(f"{idx+1:03d}")
                st.write(item.name)

                b1, b2, b3 = st.columns([1, 1, 1])
                with b1:
                    if st.button("←", key=f"left_{idx}", disabled=(idx == 0)):
                        move_item(st.session_state.frames, idx, -1)
                        st.rerun()
                with b2:
                    if st.button("→", key=f"right_{idx}", disabled=(idx == len(frames) - 1)):
                        move_item(st.session_state.frames, idx, +1)
                        st.rerun()
                with b3:
                    if st.button("🗑️", key=f"del_{idx}"):
                        st.session_state.frames.pop(idx)
                        st.rerun()
            idx += 1


# -----------------------------
# 시간 계산
# -----------------------------
def calc_fps_by_duration(frame_count: int, duration_sec: float) -> int:
    if frame_count <= 0:
        return 12
    fps = int(round(frame_count / max(0.1, duration_sec)))
    return max(1, min(60, fps))

def fps_str_from_interval(interval_sec: float) -> str:
    # interval -> framerate (can be fractional, e.g. 2/3)
    frac = Fraction(1, 1) / Fraction(str(interval_sec))
    frac = frac.limit_denominator(1000)
    if frac.denominator == 1:
        return str(frac.numerator)
    return f"{frac.numerator}/{frac.denominator}"

def fps_float_from_interval(interval_sec: float) -> float:
    frac = Fraction(1, 1) / Fraction(str(interval_sec))
    return float(frac)


# -----------------------------
# GIF 생성: 프레임 이미지 → GIF (팔레트 방식)
# -----------------------------
def make_gif_from_frames(
    frames: List[FrameItem],
    out_path: str,
    width: int,
    framerate_str: str,
    colors: int,
    dither: str,
    pad_square: bool,
    pad_color: str,
    loop_infinite: bool,
):
    tmp = tempfile.mkdtemp()
    try:
        # 현재 순서 그대로 저장
        first_ext = ".png"
        for i, it in enumerate(frames):
            ext = os.path.splitext(it.name)[1].lower()
            if ext not in [".png", ".jpg", ".jpeg", ".webp"]:
                ext = ".png"
            if i == 0:
                first_ext = ext
            fp = os.path.join(tmp, f"{i:04d}{ext}")
            with open(fp, "wb") as f:
                f.write(it.bytes)

        pattern = os.path.join(tmp, f"%04d{first_ext}")
        palette = os.path.join(tmp, "palette.png")

        scale = f"scale={width}:-1:flags=lanczos"
        if pad_square:
            vf1 = f"{scale},pad={width}:{width}:(ow-iw)/2:(oh-ih)/2:color={pad_color}"
        else:
            vf1 = scale

        loop_value = "0" if loop_infinite else "-1"

        # 팔레트 생성
        run_cmd([
            "ffmpeg", "-y",
            "-framerate", framerate_str,
            "-i", pattern,
            "-vf", f"{vf1},palettegen=max_colors={colors}",
            palette
        ])

        # GIF 생성 + 반복 설정
        run_cmd([
            "ffmpeg", "-y",
            "-framerate", framerate_str,
            "-i", pattern,
            "-i", palette,
            "-lavfi", f"{vf1} [x]; [x][1:v] paletteuse=dither={dither}",
            "-loop", loop_value,
            out_path
        ])

    finally:
        shutil.rmtree(tmp, ignore_errors=True)


# -----------------------------
# GIF 생성: 동영상 → GIF (팔레트 방식)
# -----------------------------
def make_gif_from_video(
    video_bytes: bytes,
    out_path: str,
    width: int,
    fps: int,
    colors: int,
    dither: str,
    pad_square: bool,
    pad_color: str,
    start_sec: float,
    duration_sec: float,  # 0이면 끝까지
    loop_infinite: bool,
):
    tmp = tempfile.mkdtemp()
    try:
        in_path = os.path.join(tmp, "input_video")
        with open(in_path, "wb") as f:
            f.write(video_bytes)

        palette = os.path.join(tmp, "palette.png")

        scale = f"scale={width}:-1:flags=lanczos"
        if pad_square:
            vf_base = f"fps={fps},{scale},pad={width}:{width}:(ow-iw)/2:(oh-ih)/2:color={pad_color}"
        else:
            vf_base = f"fps={fps},{scale}"

        ss_args = ["-ss", f"{start_sec:.3f}"] if start_sec and start_sec > 0 else []
        t_args = ["-t", f"{duration_sec:.3f}"] if duration_sec and duration_sec > 0 else []

        loop_value = "0" if loop_infinite else "-1"

        # 팔레트 생성
        run_cmd([
            "ffmpeg", "-y",
            *ss_args,
            "-i", in_path,
            *t_args,
            "-vf", f"{vf_base},palettegen=max_colors={colors}",
            palette
        ])

        # GIF 생성 + 반복 설정
        run_cmd([
            "ffmpeg", "-y",
            *ss_args,
            "-i", in_path,
            *t_args,
            "-i", palette,
            "-lavfi", f"{vf_base} [x]; [x][1:v] paletteuse=dither={dither}",
            "-loop", loop_value,
            out_path
        ])

    finally:
        shutil.rmtree(tmp, ignore_errors=True)


# -----------------------------
# 레이아웃
# -----------------------------
left, right = st.columns([1.25, 1.0], gap="large")

with left:
    st.markdown("### 1) 입력")

    input_type = st.radio("입력 타입", ["이미지(프레임)", "동영상"], horizontal=True)

    if input_type == "이미지(프레임)":
        uploads = st.file_uploader(
            "프레임 이미지 업로드 (PNG/JPG/WEBP) — 여러 장 선택",
            type=["png", "jpg", "jpeg", "webp"],
            accept_multiple_files=True
        )

        c1, c2 = st.columns([1, 1])
        with c1:
            if st.button("업로드 추가", use_container_width=True):
                if uploads:
                    for f in uploads:
                        st.session_state.frames.append(FrameItem(name=f.name, bytes=f.getvalue()))
        with c2:
            if st.button("프레임 비우기", use_container_width=True):
                st.session_state.frames = []

        render_frame_grid(st.session_state.frames, cols=5, thumb_w=170)

    else:
        v = st.file_uploader(
            "동영상 업로드 (mp4/mov/webm/m4v)",
            type=["mp4", "mov", "webm", "m4v"],
            accept_multiple_files=False
        )
        if v:
            st.session_state.video_file = {"name": v.name, "bytes": v.getvalue()}
            st.success(f"업로드됨: {v.name}")

        c1, c2 = st.columns([1, 1])
        with c1:
            if st.button("동영상 비우기", use_container_width=True):
                st.session_state.video_file = None

        if st.session_state.video_file:
            st.info("동영상은 오른쪽 옵션에서 구간/속도를 설정한 뒤 GIF로 변환합니다.")


with right:
    st.markdown("### 2) 옵션")

    preset = st.selectbox("프리셋", ["포토(고퀄리티)", "웹(가볍게)", "상세페이지(안정)"], index=0)

    if preset == "포토(고퀄리티)":
        default_fps = 12
        default_width = 900
        default_colors = 256
        default_dither = "sierra2_4a"
    elif preset == "웹(가볍게)":
        default_fps = 10
        default_width = 800
        default_colors = 128
        default_dither = "floyd_steinberg"
    else:
        default_fps = 12
        default_width = 900
        default_colors = 256
        default_dither = "sierra2_4a"

    # ✅ 무한반복만 있으면 됨
    loop_infinite = st.checkbox("무한 반복", value=True)

    st.divider()

    time_mode = st.radio(
        "시간 조절",
        ["FPS로 조절", "총 재생시간(초)로 조절", "프레임 간격(초)로 조절"],
        horizontal=True
    )

    fps = st.number_input("FPS", min_value=1, max_value=60, value=int(default_fps), step=1)
    duration_sec = st.number_input("총 재생시간(초)", min_value=0.5, max_value=30.0, value=2.0, step=0.1)

    # ✅ 간격초수: 0.5~10.0, 0.5 단위
    frame_interval = st.slider(
        "이미지 사이 간격(초)",
        min_value=0.5,
        max_value=10.0,
        value=0.5,
        step=0.5,
        help="모든 프레임에 동일하게 적용됩니다."
    )

    # 프레임 입력일 때 framerate 결정
    eff_fps_str = str(int(fps))
    eff_fps_int = int(fps)

    if input_type == "이미지(프레임)":
        if time_mode == "총 재생시간(초)로 조절" and len(st.session_state.frames) > 0:
            eff_fps_int = calc_fps_by_duration(len(st.session_state.frames), float(duration_sec))
            eff_fps_str = str(eff_fps_int)
            st.caption(f"계산된 FPS: **{eff_fps_int}** (프레임 {len(st.session_state.frames)}장 기준)")
        elif time_mode == "프레임 간격(초)로 조절":
            eff_fps_str = fps_str_from_interval(float(frame_interval))
            st.caption(f"프레임 간격 {frame_interval:.1f}s → framerate: **{eff_fps_str}**")
        else:
            eff_fps_str = str(int(fps))
    else:
        # 동영상은 정수 fps가 안정적
        eff_fps_int = int(fps)

    st.divider()

    width = st.number_input("가로폭(px)", min_value=300, max_value=1600, value=int(default_width), step=10)
    colors = st.selectbox("Colors(팔레트)", [64, 128, 256], index=[64, 128, 256].index(default_colors))
    dither = st.selectbox("Dither", ["sierra2_4a", "floyd_steinberg", "bayer"], index=["sierra2_4a", "floyd_steinberg", "bayer"].index(default_dither))

    st.divider()

    pad_square = st.checkbox("정사각 패딩(흔들림 방지)", value=False)
    pad_color = st.selectbox("패딩 색상", ["white", "black", "#f6f6f6"], index=0)

    st.divider()

    start_sec = 0.0
    vid_duration = 0.0
    if input_type == "동영상":
        start_sec = st.number_input("시작(초)", min_value=0.0, max_value=9999.0, value=0.0, step=0.1)

        if time_mode == "총 재생시간(초)로 조절":
            vid_duration = float(duration_sec)
            st.caption("동영상은 ‘총 재생시간’만큼 잘라서 GIF로 만듭니다.")
        else:
            vid_duration = st.number_input("변환 길이(초) (0이면 끝까지)", min_value=0.0, max_value=9999.0, value=0.0, step=0.1)


# -----------------------------
# 생성
# -----------------------------
st.divider()
st.markdown("### 3) 생성")

def build_output_path(prefix: str = "misharp") -> str:
    return os.path.join(tempfile.gettempdir(), f"{prefix}_output.gif")

if st.button("🎞️ GIF 생성하기", use_container_width=True):
    out_gif = build_output_path("misharp")
    try:
        if input_type == "이미지(프레임)":
            if len(st.session_state.frames) < 2:
                st.error("프레임 이미지는 2장 이상 필요합니다.")
            else:
                make_gif_from_frames(
                    frames=st.session_state.frames,
                    out_path=out_gif,
                    width=int(width),
                    framerate_str=eff_fps_str,
                    colors=int(colors),
                    dither=str(dither),
                    pad_square=bool(pad_square),
                    pad_color=str(pad_color),
                    loop_infinite=bool(loop_infinite),
                )
                st.success("GIF 생성 완료")
                st.image(out_gif)
                with open(out_gif, "rb") as f:
                    st.download_button("다운로드", f, file_name="misharp.gif", use_container_width=True)

        else:
            if not st.session_state.video_file:
                st.error("동영상을 업로드해 주세요.")
            else:
                make_gif_from_video(
                    video_bytes=st.session_state.video_file["bytes"],
                    out_path=out_gif,
                    width=int(width),
                    fps=int(eff_fps_int),
                    colors=int(colors),
                    dither=str(dither),
                    pad_square=bool(pad_square),
                    pad_color=str(pad_color),
                    start_sec=float(start_sec),
                    duration_sec=float(vid_duration),
                    loop_infinite=bool(loop_infinite),
                )
                st.success("동영상 → GIF 변환 완료")
                st.image(out_gif)
                with open(out_gif, "rb") as f:
                    st.download_button("다운로드", f, file_name="misharp_video.gif", use_container_width=True)

    except Exception as e:
        st.error(f"실패: {e}")
