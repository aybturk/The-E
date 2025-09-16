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


@dataclass
class PromtResult:
    """Normalized result for downstream usage (e.g., Claid addBackground)."""
    subject: str
    claid_prompt: str
    product_summary: str
    raw_text: str


class PromtMaker:
    """
    Minimal one-shot multimodal pipeline for PromtMaker:
      - Send the image to Gemini 2.0 Flash (Vertex AI).
      - Ask for a strict JSON with {subject, claid_prompt, product_summary}.
      - Return a normalized result ready for Claid addBackground.

    Environment:
      - PROJECT_ID: GCP project id (fallback to gcloud default if not set)
      - VERTEXAI_REGION: Vertex AI location (default: us-central1)
      - GOOGLE_APPLICATION_CREDENTIALS: service-account JSON (local dev)
    """

    def __init__(
        self,
        project_id: Optional[str] = None,
        region: Optional[str] = None,
        model_name: str = "gemini-2.0-flash",
    ):
        self.project_id = project_id or os.getenv("PROJECT_ID") or os.getenv("GOOGLE_CLOUD_PROJECT")
        if not self.project_id:
            raise RuntimeError(
                "PROJECT_ID is not set. Export PROJECT_ID or run `gcloud config set project ...`."
            )
        self.region = region or os.getenv("VERTEXAI_REGION") or "us-central1"

        aiplatform.init(project=self.project_id, location=self.region)
        self.model = GenerativeModel(model_name)

        # Reasonable safety settings that avoid over-blocking
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
        temperature: float = 0.4,
        style_hint: Optional[str] = None
    ) -> PromtResult:
        """
        Analyze the image and craft a Claid-ready background prompt.
        `temperature` controls creativity; `style_hint` nudges stylistic choices.
        """
        img_path = pathlib.Path(image_path).expanduser()
        if not img_path.exists():
            raise FileNotFoundError(f"Image not found: {img_path}")

        mime, _ = mimetypes.guess_type(str(img_path))
        if not mime:
            mime = "image/jpeg"

        img_part = Part.from_data(mime_type=mime, data=img_path.read_bytes())

        system = (
            "You are an expert e-commerce visual prompt engineer. "
            "You receive an input image (product/lifestyle) and must output a STRICT JSON block with "
            "three fields tailored for a background-generation API (e.g., Claid addBackground):\n"
            "1) subject: a concise subject name (e.g., 'matte black wireless earbuds').\n"
            "2) claid_prompt: ONE short action-ready prompt for background generation, "
            "   focusing on style, ambiance, surface, lighting; avoid camera jargon unless obvious; "
            "   no brand names; keep it SFW; ~15–30 words.\n"
            "3) product_summary: 1–2 short sentences summarizing the item/material/color.\n"
            "Output format MUST be pure JSON, no markdown fences, no extra text."
        )

        user_task = (
            "Analyze the product in the image and produce the JSON. "
            "Prefer studio-like phrasing (clean, minimal, soft light, natural shadows) unless the subject clearly suggests a different context. "
            "Avoid hallucinations: if uncertain, say 'unknown' for that part."
        )

        # Optional stylistic guidance for prompt diversification
        if style_hint:
            user_task += f" Style preference: {style_hint.strip()}"

        resp = self.model.generate_content(
            [
                system,
                img_part,
                user_task,
                'Return JSON like: {"subject":"...", "claid_prompt":"...", "product_summary":"..."}',
            ],
            safety_settings=self.safety,
            generation_config={"temperature": float(temperature), "max_output_tokens": 256},
        )

        raw = (resp.text or "").strip()
        data = self._extract_json(raw)

        subject = (data.get("subject") or "unknown").strip()
        claid_prompt = (data.get("claid_prompt") or "").strip()
        product_summary = (data.get("product_summary") or "").strip()

        if not claid_prompt:
            base = subject if subject and subject != "unknown" else "product"
            claid_prompt = (
                f"clean minimal studio background for {base}, soft lighting, natural shadows, "
                f"subtle gradient backdrop, premium look"
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
    if len(sys.argv) < 2:
        print("Usage: python -m TheProd.PromtMaker /path/to/image.(jpg|png)")
        raise SystemExit(1)

    maker = PromtMaker(
        project_id=os.getenv("PROJECT_ID"),
        region=os.getenv("VERTEXAI_REGION") or "us-central1",
        model_name="gemini-2.0-flash",
    )

    result = maker.analyze_and_prompt(sys.argv[1])
    print(json.dumps({
        "subject": result.subject,
        "claid_prompt": result.claid_prompt,
        "product_summary": result.product_summary,
        "raw_text": result.raw_text
    }, ensure_ascii=False, indent=2))