from PIL import Image
import io

def build_gif_from_images(
    files,
    delay_sec=1.0,
    loop_forever=True,
    unify_canvas=False,
    max_width=None,
):
    """
    ✅ 이미지→GIF 고화질 우선 버전(첫 버전 퀄리티)
    - 색상 축소/디더링/최적화(quantize)로 인한 깨짐 방지
    - RGBA 유지
    - optimize=False (중요)
    """
    images = []

    for name, data in files:
        img = Image.open(io.BytesIO(data)).convert("RGBA")

        # 가로폭 제한(선택)
        if max_width:
            w, h = img.size
            if w > max_width:
                ratio = max_width / w
                img = img.resize((int(w * ratio), int(h * ratio)), Image.LANCZOS)

        images.append(img)

    if not images:
        return b""

    # 캔버스 통일(선택)
    if unify_canvas:
        max_w = max(img.width for img in images)
        max_h = max(img.height for img in images)
        unified = []
        for img in images:
            canvas = Image.new("RGBA", (max_w, max_h), (255, 255, 255, 0))
            x = (max_w - img.width) // 2
            y = (max_h - img.height) // 2
            canvas.paste(img, (x, y), img)
            unified.append(canvas)
        images = unified

    buf = io.BytesIO()

    images[0].save(
        buf,
        format="GIF",
        save_all=True,
        append_images=images[1:],
        duration=int(delay_sec * 1000),
        loop=0 if loop_forever else 1,
        disposal=2,
        optimize=False,  # ✅ 깨짐 방지 핵심
    )

    buf.seek(0)
    return buf.getvalue()
