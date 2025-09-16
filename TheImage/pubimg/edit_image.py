# TheImage/pubimg/edit_image.py
from __future__ import annotations

import os
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Optional, Tuple, Dict, Any

from PIL import Image, ImageFilter, ImageEnhance


# ----------- Varsayılanlar -----------
# E-ticaret için makul aralıklar
MIN_LONG_EDGE = int(os.getenv("THEE_PREP_MIN_LONG", "1800"))   # min uzun kenar (was 1400)
MAX_LONG_EDGE = int(os.getenv("THEE_PREP_MAX_LONG", "3200"))   # max uzun kenar (was 2800)
TARGET_LONG_EDGE = int(os.getenv("THEE_PREP_TARGET_LONG", "2200"))  # ideal uzun kenar hedefi
MIN_FILESIZE_KB_OK = int(os.getenv("THEE_PREP_MIN_KB", "140")) # düşük kalite eşiği (was 120)
DEFAULT_JPEG_QUALITY = int(os.getenv("THEE_PREP_JPEG_Q", "94")) # was 92

# Çıktı nereye?
ROOT = Path(__file__).resolve().parents[2]  # .../The E
DEFAULT_OUTDIR = ROOT / "TheProd" / "prep"
DEFAULT_OUTDIR.mkdir(parents=True, exist_ok=True)


@dataclass
class PrepReport:
    source_path: str
    output_path: str
    format_in: str
    format_out: str
    width_in: int
    height_in: int
    width_out: int
    height_out: int
    file_kb_in: int
    file_kb_out: int
    upscaled: bool
    downscaled: bool
    sharpened: bool
    denoised: bool
    converted: bool
    notes: str

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


class ImagePrep:
    """
    Claid'e gitmeden önce görseli toparlar:
    - Analiz
    - Min/max/target uzun kenara göre yeniden boyutlandırma (LANCZOS, çok-kademeli upscale)
    - Hafif denoise + unsharp mask
    - Şeffaflık yoksa JPEG'e çevirme (quality varsayılan 94, optimize)
    - TheProd/prep/ içine kaydetme
    """

    def __init__(
        self,
        min_long_edge: int = MIN_LONG_EDGE,
        max_long_edge: int = MAX_LONG_EDGE,
        target_long_edge: int = TARGET_LONG_EDGE,
        min_filesize_kb_ok: int = MIN_FILESIZE_KB_OK,
        out_dir: Path = DEFAULT_OUTDIR,
        jpeg_quality: int = DEFAULT_JPEG_QUALITY,
    ):
        self.min_long = int(min_long_edge)
        self.max_long = int(max_long_edge)
        self.target_long = int(target_long_edge)
        self.min_kb = int(min_filesize_kb_ok)
        self.out_dir = Path(out_dir)
        self.out_dir.mkdir(parents=True, exist_ok=True)
        self.jpeg_quality = int(jpeg_quality)

    # ---------- Public API ----------

    def _resize_lanczos(self, img: Image.Image, new_size: tuple[int, int]) -> Image.Image:
        # Single place to call LANCZOS to keep pillow import usage consistent
        return img.resize(new_size, Image.Resampling.LANCZOS)

    def prepare(self, image_path: str | Path) -> Tuple[str, PrepReport]:
        """
        Görseli hazırlar ve (new_path, report) döner.
        new_path, Claid'e/ S3'e gönderilecek dosyadır.
        """
        src = Path(image_path).expanduser().resolve()
        if not src.exists():
            raise FileNotFoundError(src)

        # 1) Analiz
        meta = self._analyze(src)

        # 2) Görseli yükle
        img = Image.open(src)
        img.load()  # lazy load'ı tamamla

        # 3) Boyutlandırma kararı (iterative upscale to target)
        w, h = img.size
        long_edge = max(w, h)
        upscaled = downscaled = False

        # hedef: en az min_long, ideal olarak target_long; üst sınır max_long
        target = max(self.min_long, self.target_long)

        if long_edge < target:
            # Çok küçük görseller için 1.4x adımlarla yumuşak yükseltme,
            # sonra tam hedefe "snap".
            curr_w, curr_h = w, h
            while max(curr_w, curr_h) * 1.4 < target:
                curr_w = int(curr_w * 1.4)
                curr_h = int(curr_h * 1.4)
                img = self._resize_lanczos(img, (curr_w, curr_h))
                # küçük bir median filtre upscale adım sonrası artefaktları alır
                img = img.filter(ImageFilter.MedianFilter(size=3))
            # son hassas adım: tam hedefe oturt
            scale = target / max(img.size)
            new_size = (int(img.size[0] * scale), int(img.size[1] * scale))
            img = self._resize_lanczos(img, new_size)
            upscaled = True

        elif long_edge > self.max_long:
            scale = self.max_long / long_edge
            new_size = (int(w * scale), int(h * scale))
            img = self._resize_lanczos(img, new_size)
            downscaled = True

        # 4) Mild denoise + unsharp
        denoised = False
        if upscaled or downscaled:
            img = img.filter(ImageFilter.MedianFilter(size=3))
            denoised = True

        # Unsharp mask (slightly stronger but still natural)
        img = img.filter(ImageFilter.UnsharpMask(radius=1.5, percent=140, threshold=2))
        sharpened = True

        # 5) Format kararı (alfa varsa PNG kalsın)
        has_alpha = self._has_alpha(img)
        out_ext = ".png" if has_alpha else ".jpg"
        out_format = "PNG" if has_alpha else "JPEG"

        # 6) Biraz contrast/clarity (çok hafif, yapay görünmesin)
        if not has_alpha:
            enhancer = ImageEnhance.Contrast(img)
            img = enhancer.enhance(1.05)  # +5% micro-contrast
            enhancer = ImageEnhance.Sharpness(img)
            img = enhancer.enhance(1.06)  # +6% clarity

        # 7) Kaydet
        out_name = src.stem + "__prep" + out_ext
        out_path = (self.out_dir / out_name).resolve()

        save_kwargs: Dict[str, Any] = {}
        if out_format == "JPEG":
            img = img.convert("RGB")  # güvenli
            save_kwargs.update(dict(quality=self.jpeg_quality, optimize=True))
        else:
            # PNG tarafında optimize otomatik/ sınırlı
            save_kwargs.update(dict(optimize=True))

        img.save(out_path, out_format, **save_kwargs)

        # 8) Rapor
        out_w, out_h = img.size
        report = PrepReport(
            source_path=str(src),
            output_path=str(out_path),
            format_in=meta["format"],
            format_out=out_format,
            width_in=meta["width"],
            height_in=meta["height"],
            width_out=out_w,
            height_out=out_h,
            file_kb_in=meta["file_kb"],
            file_kb_out=self._filesize_kb(out_path),
            upscaled=upscaled,
            downscaled=downscaled,
            sharpened=sharpened,
            denoised=denoised,
            converted=(out_format != meta["format"]),
            notes=self._note(meta),
        )

        return str(out_path), report

    # ---------- Helpers ----------

    def _analyze(self, p: Path) -> Dict[str, Any]:
        with Image.open(p) as im:
            w, h = im.size
            fmt = (im.format or "").upper()
            has_alpha = self._has_alpha(im)
        return {
            "path": str(p),
            "width": w,
            "height": h,
            "long_edge": max(w, h),
            "format": fmt or p.suffix.replace(".", "").upper(),
            "file_kb": self._filesize_kb(p),
            "has_alpha": has_alpha,
        }

    @staticmethod
    def _filesize_kb(p: Path) -> int:
        try:
            return max(1, int(round(p.stat().st_size / 1024)))
        except Exception:
            return 0

    @staticmethod
    def _has_alpha(img: Image.Image) -> bool:
        mode = (img.mode or "").upper()
        return "A" in mode or mode in {"RGBA", "LA", "P"}

    def _note(self, meta: Dict[str, Any]) -> str:
        hints = []
        if meta["file_kb"] < self.min_kb:
            hints.append(f"low-kb({meta['file_kb']}< {self.min_kb})")
        if meta["long_edge"] < self.min_long:
            hints.append(f"short-edge({meta['long_edge']}< {self.min_long})")
        return ", ".join(hints) or "ok"


# ---- CLI kullanım (opsiyonel) ----
if __name__ == "__main__":
    import json
    import sys

    if len(sys.argv) < 2:
        print("Kullanım: python -m TheImage.pubimg.edit_image <image_path>")
        sys.exit(2)

    inp = Path(sys.argv[1])
    prep = ImagePrep()
    out, rep = prep.prepare(inp)
    print("✅ Hazır:", out)
    print(json.dumps(rep.to_dict(), indent=2, ensure_ascii=False))