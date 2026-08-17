"""Prepare every image the HTML deck needs, at sane sizes."""

from pathlib import Path

from PIL import Image, ImageChops, ImageFilter

HERE = Path(__file__).parent
SRC = HERE / "assets"
IMG = HERE / "deck" / "img"
IMG.mkdir(parents=True, exist_ok=True)


def save(im: Image.Image, name: str, width: int | None = None):
    if width and im.width > width:
        h = round(im.height * width / im.width)
        im = im.resize((width, h), Image.LANCZOS)
    path = IMG / name
    im.save(path, optimize=True)
    print(f"{name:22s} {im.size}  {path.stat().st_size / 1024:.0f} KB")


def trim_white(im: Image.Image, tol: int = 8) -> Image.Image:
    rgb = im.convert("RGB")
    bg = Image.new("RGB", rgb.size, (255, 255, 255))
    diff = ImageChops.difference(rgb, bg).convert("L").point(lambda p: 255 if p > tol else 0)
    box = diff.getbbox()
    return im.crop(box) if box else im


def black_to_alpha(im: Image.Image, cut: int = 78, erode: int = 1) -> Image.Image:
    """Clear only the black that is connected to the border.

    The app logo is drawn for a dark background, so a plain threshold would also
    punch holes through the badge's own dark navy interior. Flood-fill inwards
    from the edge instead: enclosed darks are never reached.

    The outer glow fades to black with no clean edge, so shave `erode` pixels off
    the result — otherwise the leftover fringe reads as grime on a white slide.
    """
    from collections import deque

    im = im.convert("RGBA")
    w, h = im.size
    px = im.load()

    def dark(x: int, y: int) -> bool:
        return max(px[x, y][:3]) <= cut

    seen = bytearray(w * h)
    q: deque[tuple[int, int]] = deque()

    def seed(x: int, y: int):
        if not seen[y * w + x] and dark(x, y):
            seen[y * w + x] = 1
            q.append((x, y))

    for x in range(w):
        seed(x, 0)
        seed(x, h - 1)
    for y in range(h):
        seed(0, y)
        seed(w - 1, y)

    while q:
        x, y = q.popleft()
        if x:         seed(x - 1, y)
        if x < w - 1: seed(x + 1, y)
        if y:         seed(x, y - 1)
        if y < h - 1: seed(x, y + 1)

    alpha = Image.frombytes("L", (w, h), bytes(0 if s else 255 for s in seen))
    for _ in range(erode):
        alpha = alpha.filter(ImageFilter.MinFilter(3))
    im.putalpha(alpha.filter(ImageFilter.GaussianBlur(0.6)))
    return im


def white_to_alpha(im: Image.Image, cutoff: int = 246) -> Image.Image:
    """Knock out the flat white card behind a logo so it sits on any background."""
    im = im.convert("RGBA")
    px = im.load()
    w, h = im.size
    for y in range(h):
        for x in range(w):
            r, g, b, a = px[x, y]
            if r >= cutoff and g >= cutoff and b >= cutoff:
                px[x, y] = (r, g, b, 0)
    return im


# ── SIH branding, straight out of the official template ───────────────────────
sih = Image.open(SRC / "sih-image2.png").convert("RGBA")
save(white_to_alpha(trim_white(sih)), "sih-logo.png", 1100)

w, h = sih.size
brain = sih.crop((0, 0, int(w * 0.40), h))
save(white_to_alpha(trim_white(brain)), "sih-brain.png", 900)

# ── team logo ─────────────────────────────────────────────────────────────────
team = white_to_alpha(trim_white(Image.open(HERE / "teamlogo.png").convert("RGBA")))
save(team, "team-logo.png", 620)
# Header slots are ~54px tall, where the wordmark is unreadable — use the mark alone.
save(team.crop((0, 0, team.width, int(team.height * 0.90))), "team-mark.png", 480)

# ── app logo ──────────────────────────────────────────────────────────────────
app = black_to_alpha(Image.open(HERE / "applogo.png"))
aw, ah = app.size

# Full lockup, minus the garbled pill under the tagline.
full = app.crop((0, 0, aw, int(ah * 0.855)))
save(full.crop(full.getbbox()), "app-logo.png", 1000)

# Badge alone, for the slide headers and the title hero — the wordmark is
# unreadable at header size and duplicates the typeset name anyway.
mark = app.crop((0, 0, aw, int(ah * 0.652)))
save(mark.crop(mark.getbbox()), "app-mark.png", 620)

# ── product screenshots ───────────────────────────────────────────────────────
save(Image.open(SRC / "ui-dispatch-clean.png"), "ui-dispatch.png", 1500)

# The load plan sits in a narrow slot on slide 3, so crop to the part that
# carries the argument — the LOADABLE / LIFO / CoG badges and the 3D deck —
# and drop the manifest table, which is unreadable at that width anyway.
lp = Image.open(SRC / "ui-loadplan-1.png")
lw, lh = lp.size
save(lp.crop((0, 0, int(lw * 0.596), int(lh * 0.616))), "ui-loadplan.png", 1100)

driver = Image.open(SRC / "ui-driver.png")
dw, dh = driver.size
save(driver.crop((int(dw * 0.100), 0, int(dw * 0.845), int(dh * 0.790))),
     "ui-driver.png", 1200)

