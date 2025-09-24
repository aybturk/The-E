# TheProd/ProductBuilder.py
from __future__ import annotations

import json
import shutil
import pathlib
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Union

# --- Proje kökünü (THE E/) sys.path'e ekle ---
ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# --- Sabit klasörler ---
PRODUCTS_DIR = ROOT / "Products"

# --- Dahili modüller ---
from TheProd.PicPre import PicPre, ALLOWED_RATIOS
from TheProd.DescMaker import DescMaker


def _slugify(name: str) -> str:
    allowed = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-._"
    base = name.strip().replace(" ", "-")
    cleaned = "".join(ch if ch in allowed else "-" for ch in base)
    return cleaned[:120] or "product"


@dataclass
class BuiltProduct:
    title: str
    description: str
    provider_link: Optional[str]
    product_dir: str
    images_generated: List[str]
    images_source: List[str]
    metadata_json: str
    desc_json: Optional[str] = None
    desc_md: Optional[str] = None


class ProductBuilder:
    """
    Çok görselli tek-tık ürün klasörü oluşturucu.
    """

    def __init__(self, products_dir: pathlib.Path = PRODUCTS_DIR) -> None:
        self.products_dir = pathlib.Path(products_dir)
        self.products_dir.mkdir(parents=True, exist_ok=True)

    def build(
        self,
        image_paths: List[str],
        *,
        qty: Union[int, Dict[str, int]] = 2,
        ratios: Optional[List[str]] = None,
        hints: Optional[str] = None,
        provider_link: Optional[str] = None,
    ) -> BuiltProduct:
        if not image_paths:
            raise ValueError("En az bir görsel gerekli.")
        if ratios:
            for r in ratios:
                if r not in ALLOWED_RATIOS:
                    raise ValueError(f"Geçersiz aspect ratio: {r}")

        # 1) Title + Description
        desc = DescMaker().generate_for_images(image_paths, hints=hints)
        title = desc.title.strip()
        description = desc.description.strip()

        # 2) Ürün klasör yapısı
        ts = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
        folder_name = f"{_slugify(title)}__{ts}"
        product_dir = self.products_dir / folder_name
        src_dir = product_dir / "images" / "source"
        gen_dir = product_dir / "images" / "generated"
        src_dir.mkdir(parents=True, exist_ok=True)
        gen_dir.mkdir(parents=True, exist_ok=True)

        # Orijinalleri ürün klasörüne kopyala
        normalized_sources: List[str] = []
        for p in image_paths:
            pth = pathlib.Path(p).expanduser().resolve()
            if not pth.exists():
                raise FileNotFoundError(f"Görsel bulunamadı: {pth}")
            dst = src_dir / pth.name
            if pth != dst:
                shutil.copy2(pth, dst)
            normalized_sources.append(str(dst))

        # qty sözlüğünden bu görsel için değer çek
        def _qty_for(img_path: str) -> int:
            if isinstance(qty, int):
                return max(1, min(5, int(qty)))
            bname = pathlib.Path(img_path).name
            if img_path in qty:
                return max(1, min(5, int(qty[img_path])))
            if bname in qty:
                return max(1, min(5, int(qty[bname])))
            return 2

        # 3) PicPre ile sahne üret
        pic = PicPre()
        generated_paths: List[str] = []
        for p in normalized_sources:
            this_qty = _qty_for(p)
            res = pic.run_auto(p, quantity=int(this_qty), ratios=ratios if ratios else None)
            for local_path in res.get("saved_files", {}).values():
                lp = pathlib.Path(local_path)
                if lp.exists():
                    dst = gen_dir / lp.name
                    if lp != dst:
                        shutil.copy2(lp, dst)
                    generated_paths.append(str(dst))

        # 4) Metadata
        meta = {
            "title": title,
            "description": description,
            "provider_link": provider_link,
            "created_at_utc": ts,
            "product_dir": str(product_dir),
            "images": {
                "source": normalized_sources,
                "generated": generated_paths,
            },
            "desc_files": {
                "json": desc.out_json,
                "md": desc.out_md,
            },
            "shared": False,
        }
        meta_path = product_dir / "product.json"
        meta_path.write_text(json.dumps(meta, indent=2, ensure_ascii=False), encoding="utf-8")

        return BuiltProduct(
            title=title,
            description=description,
            provider_link=provider_link,
            product_dir=str(product_dir),
            images_generated=generated_paths,
            images_source=normalized_sources,
            metadata_json=str(meta_path),
            desc_json=desc.out_json,
            desc_md=desc.out_md,
        )