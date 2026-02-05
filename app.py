# app.py
# Streamlit: "이미지(프레임) → FFmpeg → GIF" 엔진 통째 교체 버전
# - UI는 최소/안정형
# - 배포(특히 Streamlit Cloud)에서 안 깨지도록 /tmp 사용 + ffmpeg 존재 체크 + 로그 노출
# - 업로드: 여러 이미지 선택(권장) 또는 zip(프레임 폴더) 둘 다 지원
#
# ✅ 배포 필수:
# 1) requirements.txt: streamlit, pillow
# 2) packages.txt: ffmpeg  (Streamlit Cloud가 apt로 설치)
#
# frames 파일명 정렬은 기본 "이름순" (0001.png, 0002.png ... 권장)

import os
import re
import io
import zipfile
import tempfile
import shutil
import subprocess
from pathlib import Path
from typing import List, Tuple

import streamlit as st
from PIL import Image

st.set_page_config(page_title="MISHARP GIF Engine (FFmpeg)", layout="wide")

# ----------------------------
# Helpers
# ----------------------------
def natural_key(s: str):
    # 1,2,10 순서 문제 방지용
    return [int(t) if t.isdigit() else t.lower() for t in re.split(r"(\d+)", s)]

def ensure_clean_dir(dir_path: Path):
    if dir_path.exists():
        for p in dir_path.glob("*"):
            try:
                if p.is_file():
                    p.unlink()
                else:
                    shutil.rmtree(p)
            except Exception:
                pass
    dir_path.mkdir(parents=True, exist_ok=True)

def find_ffmpeg() -> str | None:
    return shutil.which("ffmpeg")

def save_uploaded_images_to_frames(files, frames_dir: Path) -> List[Path]:
    # 업로드된 이미지들을 frames/%04d.png 로 저장
    # - 원본 확장자 상관없이 png로 통일 (팔레트/알파 안정)
    # - 이름순 정렬
    files_sorted = sorted(files, key=lambda f: natural_key(f.name))
    out_paths = []
    for idx, uf in enumerate(files_sorted, start=1):
        try:
            img = Image.open(uf).convert("RGBA")
        except Exception as e:
            raise RuntimeError(f"이미지 열기 실패: {uf.name} / {e}")

        out_path = frames_dir / f"{idx:04d}.png"
        img.save(out_path, format="PNG")
        out_paths.append(out_path)
    return out_paths

def extract_zip_to_frames(zip_file, frames_dir: Path) -> List[Path]:
    # zip 내부에서 이미지 파일만 찾아 이름순으로 frames/%04d.png 로 저장
    with zipfile.ZipFile(zip_file) as z:
        names = [n for n in z.namelist() if not n.endswith("/")]

        img_names = []
        for n in names:
            ext = Path(n).suffix.lower()
            if ext in [".png", ".jpg", ".jpeg", ".webp"]:
                img_names.append(n)

        if not img_names:
            raise RuntimeError("ZIP 안에서 이미지 파일을 찾지 못했습니다. (png/jpg/webp)")

        img_names = sorted(img_names, key=natural_key)

        out_paths = []
        for idx, name in enumerate(img_names, start=1):
            data = z.read(name)
            img = Image.open(io.BytesIO(data)).convert("RGBA")
            out_path = frames_dir / f"{idx:04d}.png"
            img.save(out_path, format="PNG")
            out_paths.append(out_path)
        return out_paths

def run_cmd(cmd: List[str]) -> Tuple[int, str]:
    # stdout+stderr 합쳐서 반환
    p = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    return p.returncode, p.stdout

def make_gif_ffmpeg(
    frames_dir: Path,
    out_gif: Path,
    preset: str,
    width: int,
    height_mode: str,
    fps: int,
    colors: int,
    dither: str,
    pad_square: bool,
    pad_color: str,
) -> Tuple[bytes, str]:
    ffmpeg = find_ffmpeg()
    if not ffmpeg:
        raise RuntimeError("서버에 ffmpeg가 없습니다. (packages.txt에 ffmpeg 추가 필요)")

    palette_path = frames_dir.parent / "palette.png"

    # Scale & optional pad
    if pad_square:
        # 900x900 같은 정사각 패딩 (상세페이지에서 프레임 흔들림 방지)
        # pad_color: white/black/transparent 등 ffmpeg color syntax
        base_vf = (
            f"scale={width}:{width}:force_original_aspect_ratio=decrease:flags=lanczos,"
            f"pad={width}:{width}:(ow-iw)/2:(oh-ih)/2:color={pad_color},"
            f"format=rgba"
        )
    else:
        # 일반 가로 고정
        if height_mode == "auto":
            base_vf = f"scale={width}:-1:flags=lanczos,format=rgba"
        else:
            # height_mode == "even": GIF에서 홀수 높이 이슈 줄이기(안전)
            base_vf = f"scale={width}:-2:flags=lanczos,format=rgba"

    # Preset overrides (요청하신 2프리셋: 포토샵급 / 용량 우선)
    if preset == "포토샵급(퀄리티)":
        # 퀄리티 우선: fps 12 / colors 256 / sierra2_4a
        fps = fps or 12
        colors = colors or 256
        dither = dither or "sierra2_4a"
    elif preset == "용량우선(가벼움)":
        # 용량 우선: fps 8 / colors 128 / bayer
        fps = fps or 8
        colors = colors or 128
        dither = dither or "bayer"
    else:
        # 커스텀
        fps = fps or 12
        colors = colors or 256
        dither = dither or "sierra2_4a"

    # ----------------------------
    # 1) palettegen
    # ----------------------------
    # reserve_transparent=1: 투명 배경 프레임에 유리
    cmd_palette = [
        ffmpeg, "-y",
        "-framerate", str(fps),
        "-i", str(frames_dir / "%04d.png"),
        "-vf", f"{base_vf},palettegen=max_colors={colors}:reserve_transparent=1:stats_mode=diff",
        str(palette_path),
    ]
    rc1, log1 = run_cmd(cmd_palette)
    if rc1 != 0 or not palette_path.exists():
        raise RuntimeError(f"palettegen 실패\n\n{log1}")

    # ----------------------------
    # 2) paletteuse
    # ----------------------------
    # dither: none | bayer | sierra2_4a
    # bayer_scale는 용량/디테일 밸런스 조절용 (bayer에서만 의미있음)
    if dither == "bayer":
        paletteuse = "paletteuse=dither=bayer:bayer_scale=5:alpha_threshold=128"
    elif dither == "none":
        paletteuse = "paletteuse=dither=none:alpha_threshold=128"
    else:
        paletteuse = "paletteuse=dither=sierra2_4a:alpha_threshold=128"

    cmd_gif = [
        ffmpeg, "-y",
        "-framerate", str(fps),
        "-i", str(frames_dir / "%04d.png"),
        "-i", str(palette_path),
        "-filter_complex",
        f"{base_vf}[x];[x][1:v]{paletteuse}",
        "-loop", "0",
        str(out_gif),
    ]
    rc2, log2 = run_cmd(cmd_gif)
    if rc2 != 0 or not out_gif.exists():
        raise RuntimeError(f"gif 생성 실패\n\n{log2}")

    data = out_gif.read_bytes()
    combined_log = (
        "=== palettegen ===\n" + log1 +
        "\n\n=== paletteuse ===\n" + log2
    )
    return data, combined_log


# ----------------------------
# UI
# ----------------------------
st.title("MISHARP GIF Engine (FFmpeg) — 통째 교체 버전")

with st.expander("✅ 배포 체크(여기서 바로 원인 확인)", expanded=True):
    st.write("ffmpeg 경로:", find_ffmpeg())
    st.write("현재 작업 경로(cwd):", os.getcwd())
    st.write("임시폴더(tmp):", tempfile.gettempdir())
    if not find_ffmpeg():
        st.error("ffmpeg가 없습니다. Streamlit Cloud라면 packages.txt에 ffmpeg를 넣어야 합니다.")

colA, colB = st.columns([1, 1], gap="large")

with colA:
    st.subheader("1) 입력(프레임 이미지)")
    mode = st.radio("업로드 방식", ["여러 이미지 업로드", "ZIP 업로드"], horizontal=True)

    uploaded_images = None
    uploaded_zip = None

    if mode == "여러 이미지 업로드":
        uploaded_images = st.file_uploader(
            "프레임 이미지 여러 장을 선택하세요 (권장: 0001.png, 0002.png ...)",
            type=["png", "jpg", "jpeg", "webp"],
            accept_multiple_files=True
        )
    else:
        uploaded_zip = st.file_uploader("프레임 이미지가 들어있는 ZIP 업로드", type=["zip"])

    st.caption("TIP: 프레임 수가 많을수록 용량이 커집니다. 상세페이지용이면 24~48프레임 정도를 권장합니다.")

with colB:
    st.subheader("2) 프리셋/옵션")
    preset = st.selectbox("프리셋", ["포토샵급(퀄리티)", "용량우선(가벼움)", "커스텀"])

    # 기본값은 preset에서 override 되지만, 커스텀용으로 노출
    fps = st.number_input("FPS", min_value=4, max_value=30, value=12, step=1)
    width = st.number_input("가로폭(px)", min_value=300, max_value=2000, value=900, step=10)
    colors = st.selectbox("Colors(팔레트)", [256, 192, 160, 128, 96, 64], index=0)
    dither = st.selectbox("Dither", ["sierra2_4a", "bayer", "none"], index=0)

    pad_square = st.checkbox("정사각 패딩(프레임 흔들림 방지)", value=False)
    pad_color = st.selectbox("패딩 색상", ["white", "black", "transparent"], index=0)
    height_mode = st.selectbox("높이 처리", ["auto", "even(-2)"], index=0)
    height_mode = "auto" if height_mode == "auto" else "even"

st.divider()

generate = st.button("🎬 GIF 생성하기", type="primary", use_container_width=True)

if generate:
    try:
        if mode == "여러 이미지 업로드":
            if not uploaded_images or len(uploaded_images) < 2:
                st.warning("이미지를 최소 2장 이상 업로드해주세요.")
                st.stop()
        else:
            if not uploaded_zip:
                st.warning("ZIP 파일을 업로드해주세요.")
                st.stop()

        # 임시 작업폴더 (배포 안정성 핵심)
        workdir = Path(tempfile.mkdtemp(prefix="misharp_gif_"))
        frames_dir = workdir / "frames"
        ensure_clean_dir(frames_dir)

        if mode == "여러 이미지 업로드":
            frame_paths = save_uploaded_images_to_frames(uploaded_images, frames_dir)
        else:
            frame_paths = extract_zip_to_frames(uploaded_zip, frames_dir)

        st.success(f"프레임 {len(frame_paths)}장 준비 완료 ✅")
        st.write("첫 프레임 미리보기:")
        st.image(str(frame_paths[0]), use_container_width=True)

        out_gif = workdir / "output.gif"

        with st.spinner("FFmpeg로 GIF 생성 중..."):
            gif_bytes, logs = make_gif_ffmpeg(
                frames_dir=frames_dir,
                out_gif=out_gif,
                preset=preset,
                width=int(width),
                height_mode=height_mode,
                fps=int(fps),
                colors=int(colors),
                dither=dither,
                pad_square=pad_square,
                pad_color=pad_color,
            )

        st.success("GIF 생성 완료 ✅")
        st.image(gif_bytes)
        st.download_button(
            "⬇️ GIF 다운로드",
            data=gif_bytes,
            file_name="misharp.gif",
            mime="image/gif",
            use_container_width=True
        )

        with st.expander("로그(배포에서 깨질 때 여기 보세요)"):
            st.code(logs)

        # 작업폴더는 남겨두면 용량 쌓일 수 있어서 즉시 정리
        try:
            shutil.rmtree(workdir, ignore_errors=True)
        except Exception:
            pass

    except Exception as e:
        st.error(str(e))
        st.info("배포에서 실패하면 대부분 ffmpeg 미설치 / 경로 문제입니다. 위 '배포 체크'에서 ffmpeg 경로가 None인지 먼저 확인하세요.")

