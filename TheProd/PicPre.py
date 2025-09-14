# TheProd/PicPre.py

from __future__ import annotations
import os
import sys
import io
import json
import time
import shutil
import hashlib
import pathlib
from datetime import datetime
from typing import List, Dict, Optional
import requests

# === Proje kökünü sys.path'e ekle (…/THE E) ===
ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

# === Yerel modüller (paket isimleri klasör yapına göre) ===
from TheImage.pubimg.s3_uploader import S3Uploader
from TheImage.Claid.Claid_func import ClaidFunc   # Claid_func içindeki import .keys olmalı

# =========================
#  Yapılandırma (override için env)
# =========================
S3_BUCKET   = os.getenv("THEE_S3_BUCKET",   "the-e-assets")
S3_REGION   = os.getenv("THEE_S3_REGION",   "eu-north-1")

IMAGES_DIR  = ROOT / "TheImage" / "pubimg" / "images"   # izleyeceğimiz yerel klasör
OUTPUT_DIR  = ROOT / "TheProd" / "output"               # indirilecek final dosyalar

# Claid varsayılanları
SAFE_9x7_SCALE = float(os.getenv("THEE_SAFE9x7_SCALE", "0.82"))
SAFE_9x7_Y     = float(os.getenv("THEE_SAFE9x7_Y",     "0.52"))

GUIDELINES = "minimal studio, soft daylight, light wood tabletop, photorealistic"

# 1:1 varyant promptları
PROMPTS_1x1: List[str] = [
    "clean minimal studio, soft daylight, light wooden surface, shallow depth of field, photorealistic product photo",
    "lifestyle scene, cozy interior, natural daylight near window, light wood table, soft shadows, photorealistic",
    "bright editorial look, seamless paper backdrop, gentle gradient light, soft shadow, photorealistic"
]

HTTP_TIMEOUT = 90


def _slugify(name: str) -> str:
    base = name.strip().replace(" ", "-")
    allowed = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-._"
    cleaned = "".join(ch if ch in allowed else "-" for ch in base)
    return cleaned[:120]


def _latest_image_in(folder: pathlib.Path) -> Optional[pathlib.Path]:
    if not folder.exists():
        return None
    exts = {".png", ".jpg", ".jpeg", ".webp"}
    imgs = [p for p in folder.iterdir() if p.is_file() and p.suffix.lower() in exts]
    if not imgs:
        return None
    return max(imgs, key=lambda p: p.stat().st_mtime)


def _download(url: str, dest: pathlib.Path) -> pathlib.Path:
    dest.parent.mkdir(parents=True, exist_ok=True)
    with requests.get(url, stream=True, timeout=HTTP_TIMEOUT) as r:
        r.raise_for_status()
        with open(dest, "wb") as f:
            shutil.copyfileobj(r.raw, f)
    return dest


class PicPre:
    """
    En güncel yerel görseli alır -> S3'e yükler (public URL) ->
    Claid ile arka planı kaldırır -> 9:7 master + 3 adet 1:1 varyant üretir ->
    Sonuç görsellerini TheProd/output içine indirir.
    """

    def __init__(
        self,
        images_dir: pathlib.Path = IMAGES_DIR,
        output_dir: pathlib.Path = OUTPUT_DIR,
        s3_bucket: str = S3_BUCKET,
        s3_region: str = S3_REGION,
        safe_scale: float = SAFE_9x7_SCALE,
        safe_y: float = SAFE_9x7_Y,
        guidelines: str = GUIDELINES,
        prompts_1x1: Optional[List[str]] = None,
    ) -> None:
        self.images_dir = pathlib.Path(images_dir)
        self.output_dir = pathlib.Path(output_dir)
        self.s3 = S3Uploader(bucket_name=s3_bucket, region=s3_region)
        self.claid = ClaidFunc()
        self.safe_scale = float(safe_scale)
        self.safe_y = float(safe_y)
        self.guidelines = guidelines
        self.prompts_1x1 = list(prompts_1x1) if prompts_1x1 else PROMPTS_1x1

    def run(self) -> Dict[str, object]:
        self.output_dir.mkdir(parents=True, exist_ok=True)

        src = _latest_image_in(self.images_dir)
        if not src:
            raise FileNotFoundError(f"Görsel bulunamadı: {self.images_dir}")
        print(f"🖼  Son görsel: {src.name}")

        s3_url = self._ensure_s3_url(src)
        print(f"☁️  S3 URL: {s3_url}")

        cutout_url = self._remove_bg(s3_url)
        print(f"✂️  Cutout URL: {cutout_url}")

        master_url = self._make_master_9x7(cutout_url)
        print(f"⭐ Master 9:7: {master_url}")

        variant_urls = self._make_variants_1x1(cutout_url)
        for i, u in enumerate(variant_urls, 1):
            print(f"🔁 Varyant {i}: {u}")

        saved = self._download_all(
            master_url=master_url,
            variants=variant_urls,
            basename=_slugify(src.stem),
        )

        return {
            "source_path": str(src),
            "s3_url": s3_url,
            "cutout_url": cutout_url,
            "master_url": master_url,
            "variant_urls": variant_urls,
            "saved_files": saved,
            "output_dir": str(self.output_dir),
        }

    def _ensure_s3_url(self, local_path: pathlib.Path) -> str:
        return self.s3.upload_file(str(local_path))

    def _remove_bg(self, input_url: str) -> str:
        data = self.claid.remove_background_url(
            input_url=input_url,
            category="products",
            clipping=True,
            color="transparent",
            output_type="png",
            decompress="strong",
            polish=False,
        )
        out = (data or {}).get("output", {})
        url = out.get("tmp_url")
        if not url:
            raise RuntimeError(f"Claid remove_background_url başarısız: {data}")
        return url

    def _make_master_9x7(self, cutout_url: str) -> str:
        scene = self.claid.add_background_safe_9x7(
            object_image_url=cutout_url,
            scale=self.safe_scale,
            y=self.safe_y,
            guidelines=self.guidelines,
        )
        urls = scene.get("tmp_urls") or []
        if not urls:
            raise RuntimeError(f"Claid 9x7 sahne başarısız: {scene}")
        return urls[0]

    def _make_variants_1x1(self, cutout_url: str) -> List[str]:
        out: List[str] = []
        for prompt in self.prompts_1x1:
            scene = self.claid.add_background(
                object_image_url=cutout_url,
                use_autoprompt=False,
                prompt=prompt,
                guidelines=self.guidelines,
                aspect_ratio="1:1",
                number_of_images=1,
                preference="optimal",
                output_format="png",
            )
            urls = scene.get("tmp_urls") or []
            if urls:
                out.append(urls[0])
        return out

    def _download_all(self, master_url: str, variants: List[str], basename: str) -> Dict[str, str]:
        ts = datetime.utcnow().strftime("%Y%m%d-%H%M%S")
        saved: Dict[str, str] = {}

        master_name = f"{basename}__master-9x7__{ts}.png"
        master_path = self.output_dir / master_name
        _download(master_url, master_path)
        saved["master"] = str(master_path)

        for i, url in enumerate(variants, 1):
            name = f"{basename}__v{i}-1x1__{ts}.png"
            path = self.output_dir / name
            _download(url, path)
            saved[f"variant_{i}"] = str(path)

        return saved


def main() -> None:
    pp = PicPre()
    result = pp.run()
    print("\n✅ Tamamlandı.\n")
    print(json.dumps(result, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()