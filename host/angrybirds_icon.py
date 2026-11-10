"""Build a local Angry Birds icon from the user's original sprite, never tracked."""
from pathlib import Path
import sys


def build(repo: Path, source: Path, output: Path) -> None:
    from PIL import Image, ImageDraw
    # Use the pinned port's atlas reader; do not duplicate its unlicensed parser.
    sys.path.insert(0, str(repo / 'tools'))
    from convert_assets import read_sheet, load_pvr
    from .app_icons import assign_app_id, encode_icon_bin

    texture, sprites = read_sheet(source / 'INGAME.dat')
    red = next(sprite for sprite in sprites if sprite['name'] == 'BIRD_RED')
    path = source / texture
    atlas = load_pvr(str(path)) if path.suffix.lower() == '.pvr' else Image.open(path).convert('RGBA')
    x, y, w, h = (red[key] for key in ('x', 'y', 'w', 'h'))
    if not (0 <= x < x + w <= atlas.width and 0 <= y < y + h <= atlas.height):
        raise ValueError('BIRD_RED sprite lies outside the atlas')
    bird = atlas.crop((x, y, x + w, y + h))
    bird = Image.frombytes('RGBa', bird.size, bird.tobytes()).convert('RGBA')
    bird.thumbnail((72, 72), Image.Resampling.LANCZOS)
    tile = Image.new('RGBA', (112, 112))
    ImageDraw.Draw(tile).ellipse((12, 6, 100, 95), fill='#2980b9', outline='#174663', width=1)
    tile.alpha_composite(bird, (56 - bird.width // 2, 51 - bird.height // 2))
    png = output.with_suffix('.png')
    tile.save(png)
    output.write_bytes(encode_icon_bin(png, assign_app_id('org.nanoapps.angrybirds')))


if __name__ == '__main__':
    build(*(Path(arg) for arg in sys.argv[1:]))
