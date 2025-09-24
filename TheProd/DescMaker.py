# TheProd/DescMaker.py
from __future__ import annotations

import json
import os
import pathlib
import re
import mimetypes
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from google.cloud import aiplatform
from vertexai.generative_models import GenerativeModel, Part, SafetySetting, HarmCategory

# ==== SABİT PROJE AYARLARI (şimdilik gömülü) ====
GCP_PROJECT_ID = "melodic-splicer-449022-g3"   # <-- kendi Project ID'in
GCP_VERTEX_REGION = "us-central1"              # <-- Vertex AI bölgesi
GEMINI_MODEL_NAME = "gemini-2.0-flash"         # <-- model adı

# SDK ihtiyaç duyarsa ortam değişkenlerini de set edelim
os.environ["PROJECT_ID"] = GCP_PROJECT_ID
os.environ["GOOGLE_CLOUD_PROJECT"] = GCP_PROJECT_ID
os.environ["VERTEXAI_REGION"] = GCP_VERTEX_REGION

# Çıktı klasörü
ROOT = pathlib.Path(__file__).resolve().parents[1]
DESC_OUT_DIR = ROOT / "TheProd" / "output" / "descriptions"


@dataclass
class DescResult:
    title: str
    description: str
    raw_text: str
    images: List[str]
    out_json: str
    out_md: str
    hints_used: Optional[str] = None


class DescMaker:
    """
    1–4 görsel için Etsy'ye uygun Title + Description üretir.
    Opsiyonel 'hints' (anahtar kelimeler/özellikler) verilebilir ve metne zarifçe dahil edilir.
    Başka sınıflardan çağırılabilsin diye sade bir API sağlar.
    """

    def __init__(self) -> None:
        aiplatform.init(project=GCP_PROJECT_ID, location=GCP_VERTEX_REGION)
        self.model = GenerativeModel(GEMINI_MODEL_NAME)
        # --- Safety (ANAHTAR ARGÜMANLARLA) ---
        self.safety = [
            SafetySetting(
                category=HarmCategory.HARM_CATEGORY_DANGEROUS_CONTENT,
                threshold=SafetySetting.HarmBlockThreshold.BLOCK_MEDIUM_AND_ABOVE,
            ),
            SafetySetting(
                category=HarmCategory.HARM_CATEGORY_HARASSMENT,
                threshold=SafetySetting.HarmBlockThreshold.BLOCK_MEDIUM_AND_ABOVE,
            ),
            SafetySetting(
                category=HarmCategory.HARM_CATEGORY_HATE_SPEECH,
                threshold=SafetySetting.HarmBlockThreshold.BLOCK_MEDIUM_AND_ABOVE,
            ),
            SafetySetting(
                category=HarmCategory.HARM_CATEGORY_SEXUALLY_EXPLICIT,
                threshold=SafetySetting.HarmBlockThreshold.BLOCK_MEDIUM_AND_ABOVE,
            ),
        ]

    # ---- Public API: başka sınıflardan da çağır ----
    def generate_for_images(self, image_paths: List[str], *, hints: Optional[str] = None) -> DescResult:
        """
        image_paths: 1–4 adet yerel görsel yolu (aynı ürüne ait).
        hints: Opsiyonel ürün anahtar kelimeleri/özellikleri (örn: "plate 23 cm, matte white, ceramic").
        Dönüş: metinler ve kaydedilen dosya yolları (DescResult).
        """
        if not image_paths:
            raise ValueError("At least one image required")
        if len(image_paths) > 4:
            raise ValueError("Maximum 4 images allowed")

        parts = []
        norm_paths: List[str] = []
        for p in image_paths:
            path = pathlib.Path(p).expanduser().resolve()
            if not path.exists():
                raise FileNotFoundError(f"Image not found: {path}")
            mime, _ = mimetypes.guess_type(str(path))
            mime = mime or "image/jpeg"
            parts.append(Part.from_data(mime_type=mime, data=path.read_bytes()))
            norm_paths.append(str(path))

        system = (
            "You are an elite Etsy SEO copywriter and visual merchandiser. "
            "You will receive 1–4 images of the SAME product (different angles). "
            "Return STRICT JSON only with keys: {\"title\":\"...\",\"description\":\"...\"}."
        )

        guidelines = (
            "Title (US English): 110–140 characters, highly readable, optimized for Etsy/eBay SEO. "
            "Include product type, key material/finish, color/tone, style, and primary use. "
            "Front‑load important keywords, but keep it human and natural. No brand names.\n"
            "Description: 3 short but vivid paragraphs — (1) compelling lifestyle/aesthetic hook + main use case, "
            "(2) specific details: materials, dimensions, variations, unique craftsmanship, "
            "(3) care, packaging, shipping, and a persuasive closing call‑to‑action.\n"
            "Use energetic, sensory language (textures, moods, occasions). "
            "Think like a top‑tier Etsy/eBay seller: highlight versatility, gifting potential, décor value. "
            "Never fabricate facts that clearly contradict the images. If uncertain, phrase cautiously (e.g., 'approximately', 'designed for'). "
            "No pricing or guarantees. End with an inviting CTA."
        )

        extra = ""
        if hints and hints.strip():
            extra = (
                "Additional, optional product facts/keywords provided by the seller (use them naturally if relevant):\n"
                f"{hints.strip()}\n"
                "Incorporate these details succinctly—prefer title and the first paragraph. "
                "Do not over-stuff; keep copy human and tasteful."
            )

        prompt = "Analyze the product images and craft the Etsy listing copy in English."

        content_parts = [system, *parts, guidelines]
        if extra:
            content_parts.append(extra)
        content_parts += [prompt, 'Return JSON as: {"title":"...","description":"..."}']

        resp = self.model.generate_content(
            content_parts,
            safety_settings=self.safety,
            generation_config={
                "temperature": 0.7,
                "top_p": 0.9,
                "top_k": 40,
                "max_output_tokens": 900,
            },
        )

        raw = (resp.text or "").strip()
        data = self._extract_json(raw)

        title = (data.get("title") or "Untitled Product").strip()
        description = (data.get("description") or
                       "A thoughtfully designed piece with a clean, minimalist aesthetic. "
                       "Crafted with durable materials and finished for everyday use. "
                       "Shipped with care. Questions? Send a message.").strip()

        ts = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
        base = f"etsy_desc_{ts}"
        DESC_OUT_DIR.mkdir(parents=True, exist_ok=True)

        out_json = DESC_OUT_DIR / f"{base}.json"
        out_md = DESC_OUT_DIR / f"{base}.md"

        payload = {
            "title": title,
            "description": description,
            "images": norm_paths,
            "model": GEMINI_MODEL_NAME,
            "project_id": GCP_PROJECT_ID,
            "region": GCP_VERTEX_REGION,
            "hints_used": hints.strip() if hints and hints.strip() else None,
            "raw_text": raw,
            "created_at_utc": ts,
        }
        out_json.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
        out_md.write_text(
            f"# Etsy Listing Copy\n\n**Title**\n\n{title}\n\n"
            f"**Description**\n\n{description}\n",
            encoding="utf-8"
        )

        return DescResult(
            title=title,
            description=description,
            raw_text=raw,
            images=norm_paths,
            out_json=str(out_json),
            out_md=str(out_md),
            hints_used=payload["hints_used"],
        )

    # ---- Fonksiyonel arayüz (başka modüller için pratik) ----
    def generate_listing_copy(self, image_paths: List[str], *, hints: Optional[str] = None) -> Dict[str, Any]:
        r = self.generate_for_images(image_paths, hints=hints)
        return {
            "title": r.title,
            "description": r.description,
            "images": r.images,
            "out_json": r.out_json,
            "out_md": r.out_md,
            "hints_used": r.hints_used,
        }

    # ---- JSON ayıklama ----
    def _extract_json(self, text: str) -> Dict[str, Any]:
        cleaned = re.sub(r"^```(?:json)?\s*|\s*```$", "", text.strip(), flags=re.MULTILINE)
        try:
            return json.loads(cleaned)
        except Exception:
            m = re.search(r"\{.*\}", cleaned, flags=re.DOTALL)
            return json.loads(m.group(0)) if m else {}


# ---- CLI ----
if __name__ == "__main__":
    import sys
    # Basit --hints=... argüman desteği
    hints_arg = None
    img_args: List[str] = []
    for a in sys.argv[1:]:
        if a.startswith("--hints="):
            hints_arg = a.split("=", 1)[1]
        else:
            img_args.append(a)

    if not img_args:
        print("Usage: python -m TheProd.DescMaker [--hints='plate 23 cm, matte white'] img1.jpg [img2.jpg ...]")
        raise SystemExit(1)

    dm = DescMaker()
    res = dm.generate_for_images(img_args, hints=hints_arg)
    print(json.dumps({
        "title": res.title,
        "description": res.description,
        "out_json": res.out_json,
        "out_md": res.out_md,
        "hints_used": res.hints_used,
    }, indent=2, ensure_ascii=False))