#!/usr/bin/env python
"""把截图缩到能被读取的尺寸。

多图请求里每张图的每条边都必须 ≤2000px，外接屏截图基本都超，超了整个请求被拒。

    uv run python scripts/shrink.py ~/Desktop/截图.png

输出 <原名>_small.png，长边 1400。也可以什么都不做——直接把原图路径告诉我，我自己缩。
"""

from __future__ import annotations

import sys
from pathlib import Path

MAX_EDGE = 1400


def shrink(src: Path, max_edge: int = MAX_EDGE) -> Path:
    from PIL import Image

    with Image.open(src) as im:
        im = im.convert("RGB")
        before = im.size
        im.thumbnail((max_edge, max_edge), Image.LANCZOS)
        out = src.with_name(f"{src.stem}_small.png")
        im.save(out)
    print(f"{before[0]}x{before[1]} → {im.width}x{im.height}  {out}")
    return out


if __name__ == "__main__":
    if len(sys.argv) < 2:
        raise SystemExit(__doc__)
    for arg in sys.argv[1:]:
        shrink(Path(arg).expanduser())
