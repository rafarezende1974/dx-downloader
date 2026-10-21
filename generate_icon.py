# Gera assets/icon.ico + assets/icon.png (ícone próprio do DX Downloader:
# seta de download branca sobre fundo escuro, com bandeja vermelha — relacionado à função, sem logo DX Labs)
from pathlib import Path
from PIL import Image, ImageDraw

OUT = Path(__file__).parent / "assets"
OUT.mkdir(parents=True, exist_ok=True)

S = 1024
img = Image.new("RGBA", (S, S), (0, 0, 0, 0))
d = ImageDraw.Draw(img)

# fundo: quadrado arredondado escuro
d.rounded_rectangle([28, 28, S - 28, S - 28], radius=200, fill=(11, 14, 19, 255))

# seta de download (haste + ponta), branca
cx = S // 2
d.rectangle([cx - 95, 170, cx + 95, 560], fill=(232, 236, 241, 255))
d.polygon([(cx - 265, 500), (cx + 265, 500), (cx, 790)], fill=(232, 236, 241, 255))

# bandeja vermelha embaixo
d.rounded_rectangle([190, 800, S - 190, 880], radius=40, fill=(230, 57, 70, 255))

img.save(OUT / "icon.png")
img.save(OUT / "icon.ico", sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])
print("icon.png / icon.ico gerados em", OUT)
