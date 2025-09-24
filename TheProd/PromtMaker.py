# TheProd/PromtMaker.py
from __future__ import annotations

import json
import os
import pathlib
import re
import mimetypes
from dataclasses import dataclass
from typing import Any, Dict, Optional

from google.cloud import aiplatform
from vertexai.generative_models import GenerativeModel, Part, SafetySetting, HarmCategory

# ---- SABİTLER (senin GCP projen) ----
# GCP Console ekranındaki Project ID:
GCP_PROJECT_ID = "melodic-splicer-449022-g3"
# Vertex AI konumu (istemezsen böyle kalsın):
GCP_VERTEX_REGION = "us-central1"
# İsteğe bağlı: modeli de sabitleyebiliriz
GEMINI_MODEL_NAME = "gemini-2.0-flash"


def _ensure_env_vars() -> None:
    """
    Ortam değişkenleri set değilse kod içinde set et.
    Güvenlik sebebiyle normalde .env / gcloud önerilir; burada senin isteğinle sabitliyoruz.
    """
    os.environ.setdefault("PROJECT_ID", GCP_PROJECT_ID)
    os.environ.setdefault("GOOGLE_CLOUD_PROJECT", GCP_PROJECT_ID)
    os.environ.setdefault("VERTEXAI_REGION", GCP_VERTEX_REGION)


@dataclass
class PromtResult:
    """Normalized result for downstream usage (e.g., Claid addBackground)."""
    subject: str
    claid_prompt: str
    product_summary: str
    raw_text: str


class PromtMaker:
    """
    Creative multimodal prompt maker for background generation:
      - Send the image to Gemini 2.0 Flash (Vertex AI).
      - Ask for a strict JSON with {subject, claid_prompt, product_summary}.
      - Return a normalized result ready for Claid addBackground.

    Environment (bu sınıfta default’lanır):
      - PROJECT_ID / GOOGLE_CLOUD_PROJECT: GCP project id
      - VERTEXAI_REGION: Vertex AI location
      - GOOGLE_APPLICATION_CREDENTIALS: (gerekirse) service-account JSON path
    """

    def __init__(
        self,
        project_id: Optional[str] = None,
        region: Optional[str] = None,
        model_name: str = GEMINI_MODEL_NAME,
    ):
        _ensure_env_vars()

        self.project_id = project_id or os.getenv("PROJECT_ID") or os.getenv("GOOGLE_CLOUD_PROJECT")
        if not self.project_id:
            raise RuntimeError("PROJECT_ID is not set and fallback failed.")

        self.region = region or os.getenv("VERTEXAI_REGION") or GCP_VERTEX_REGION

        # Vertex AI init
        aiplatform.init(project=self.project_id, location=self.region)
        self.model = GenerativeModel(model_name)

        # Reasonable safety settings
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

    # ------------------------ Public API ------------------------

    def analyze_and_prompt(
        self,
        image_path: str,
        *,
        temperature: float = 0.7,
        style_hint: Optional[str] = None,
        prompt_words: int = 60,
    ) -> PromtResult:
        """
        Analyze the image and craft a Claid-ready background prompt.
        `temperature` controls creativity; `style_hint` nudges stylistic choices.
        `prompt_words` targets the length of the background description (default ~60 words).
        """
        img_path = pathlib.Path(image_path).expanduser()
        if not img_path.exists():
            raise FileNotFoundError(f"Image not found: {img_path}")

        mime, _ = mimetypes.guess_type(str(img_path))
        if not mime:
            mime = "image/jpeg"

        img_part = Part.from_data(mime_type=mime, data=img_path.read_bytes())

        system = (
            "You are an expert e‑commerce visual prompt engineer. "
            "You receive a product image and must output a STRICT JSON object tailored for a background generation API (e.g., Claid addBackground). "
            "Fields: \n"
            "1) subject: concise item name (e.g., 'matte black wireless earbuds').\n"
            "2) claid_prompt: ONE vivid, cohesive scene description of ~" + str(prompt_words) + " words (±15). "
            "   Describe: environment/context, surface/material under the product, lighting mood & direction, shadows/reflections, depth of field, color palette/harmony, and overall style (studio/editorial/lifestyle). "
            "   Be imaginative but truthful to the image; no brand names or logos; no camera jargon unless clearly implied; SFW. Avoid clutter, busy patterns, extreme props, text overlays, or watermark‑like elements.\n"
            "3) product_summary: 1–2 concise sentences summarizing item/material/color.\n"
            "Output MUST be pure JSON (no markdown fences, no extra prose)."
        )

        user_task = (
            "Analyze the product and compose the JSON. "
            "For claid_prompt, write a single paragraph that reads like a creative art director note. "
            "Lead with the scene (space/ambience), then the surface, then lighting & shadows, then palette and finishing touches. "
            "Prefer natural language over technical terms. Be specific about textures (e.g., travertine, light oak, linen, matte ceramic) and light quality (soft daylight, window side‑light, gentle falloff). "
            "Keep it tasteful and production‑ready."
        )
        if style_hint:
            user_task += " Strong style preference: " + style_hint.strip()
        user_task += " Compose around " + str(prompt_words) + " words."

        resp = self.model.generate_content(
            [
                system,
                img_part,
                user_task,
                'Return JSON like: {"subject":"...", "claid_prompt":"...", "product_summary":"..."}',
            ],
            safety_settings=self.safety,
            generation_config={
                "temperature": float(temperature),
                "top_p": 0.9,
                "top_k": 40,
                "max_output_tokens": 512,
            },
        )

        raw = (resp.text or "").strip()
        data = self._extract_json(raw)

        subject = (data.get("subject") or "unknown").strip()
        claid_prompt = (data.get("claid_prompt") or "").strip()
        product_summary = (data.get("product_summary") or "").strip()

        if not claid_prompt:
            base = subject if subject and subject != "unknown" else "product"
            claid_prompt = (
                f"elegant studio scene for {base}; light oak tabletop with gentle texture; "
                f"soft daylight from the left creating realistic shadows and a calm gradient backdrop; "
                f"subtle reflection; cohesive neutral palette with one warm accent; uncluttered, premium, photorealistic"
            )

        return PromtResult(
            subject=subject,
            claid_prompt=claid_prompt,
            product_summary=product_summary,
            raw_text=raw,
        )

    # ------------------------ Utils ------------------------

    def _extract_json(self, text: str) -> Dict[str, Any]:
        """Extract a JSON object from model text, stripping optional code fences."""
        cleaned = re.sub(r"^```(?:json)?\s*|\s*```$", "", text.strip(), flags=re.IGNORECASE | re.MULTILINE)
        try:
            return json.loads(cleaned)
        except Exception:
            m = re.search(r"\{.*\}", cleaned, flags=re.DOTALL)
            if not m:
                return {}
            try:
                return json.loads(m.group(0))
            except Exception:
                return {}


# ------------------------ CLI ------------------------

if __name__ == "__main__":
    import sys
    _ensure_env_vars()

    if len(sys.argv) < 2:
        print("Usage: python -m TheProd.PromtMaker /path/to/image.(jpg|png)")
        raise SystemExit(1)

    maker = PromtMaker(
        project_id=GCP_PROJECT_ID,
        region=GCP_VERTEX_REGION,
        model_name=GEMINI_MODEL_NAME,
    )
    result = maker.analyze_and_prompt(sys.argv[1])
    print(json.dumps({
        "subject": result.subject,
        "claid_prompt": result.claid_prompt,
        "product_summary": result.product_summary,
        "raw_text": result.raw_text
    }, ensure_ascii=False, indent=2))