# TheProd/PicPre.py
from __future__ import annotations

import os
import sys
import json
import shutil
import pathlib
from datetime import datetime, timezone
from typing import List, Dict, Optional, Any

import requests

# --- Project root on sys.path (…/The E) ---
ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

# --- Local modules ---
from TheImage.pubimg.s3_uploader import S3Uploader
from TheImage.pubimg.edit_image import ImagePrep
from TheImage.Claid.Claid_func import ClaidFunc
from TheProd.PromtMaker import PromtMaker  # Gemini-based prompt generator

# =========================
# Settings (override via env)
# =========================
S3_BUCKET   = os.getenv("THEE_S3_BUCKET",   "the-e-assets")
S3_REGION   = os.getenv("THEE_S3_REGION",   "eu-north-1")

IMAGES_DIR  = ROOT / "TheImage" / "pubimg" / "images"   # input folder to watch/pick
OUTPUT_DIR  = ROOT / "TheProd" / "output"               # where we save generated results

# Guidance (used only when Claid autoprompt is enabled, we keep for future toggles)
GUIDELINES = os.getenv(
    "THEE_GUIDELINES",
    "minimal studio, soft daylight, light wood tabletop, photorealistic"
)

# Aspect → default fallback prompt (if Gemini fails)
PROMPT_BY_ASPECT: Dict[str, str] = {
    "1:1": (
        "editorial look, seamless paper backdrop, gentle gradient light, "
        "soft shadow, photorealistic product photo"
    ),
    "9:7": (
        "clean minimal studio, soft daylight, light wooden tabletop, "
        "subtle realistic shadows, photorealistic product photo"
    ),
}

# Default: generate two images, both 1:1
DEFAULT_ASPECTS_ENV = os.getenv("THEE_ASPECTS", "1:1,1:1")
DEFAULT_ASPECTS: List[str] = [a.strip() for a in DEFAULT_ASPECTS_ENV.split(",") if a.strip()]

HTTP_TIMEOUT = 90


# =========================
# Small utilities
# =========================
def _slugify(name: str) -> str:
    """Make a file-system friendly base name."""
    base = name.strip().replace(" ", "-")
    allowed = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-._"
    cleaned = "".join(ch if ch in allowed else "-" for ch in base)
    return cleaned[:120]


def _latest_image_in(folder: pathlib.Path) -> Optional[pathlib.Path]:
    """Pick the most-recent image from a folder."""
    if not folder.exists():
        return None
    exts = {".png", ".jpg", ".jpeg", ".webp"}
    imgs = [p for p in folder.iterdir() if p.is_file() and p.suffix.lower() in exts]
    if not imgs:
        return None
    return max(imgs, key=lambda p: p.stat().st_mtime)


def _download(url: str, dest: pathlib.Path) -> pathlib.Path:
    """Download a URL to a local path."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    with requests.get(url, stream=True, timeout=HTTP_TIMEOUT) as r:
        r.raise_for_status()
        with open(dest, "wb") as f:
            shutil.copyfileobj(r.raw, f)
    return dest


# =========================
# PicPre pipeline
# =========================
class PicPre:
    """
    End-to-end pipeline:

      images/ → (ImagePrep.prepare) → prepped file (local)
               → S3 upload → public URL
               → Claid remove_background (cutout URL)
               → Gemini prompt(s) from local prepped file
               → Claid add_background (explicit prompt), for each aspect
               → Download finals to output/

    Configurability:
      - ratios: list of aspect ratios (e.g., ["1:1","9:7"]). Default from THEE_ASPECTS or ["1:1","1:1"]
      - default_scale: smaller => product appears smaller; default 0.72
      - default_y: None => centered; or 0..1 to nudge vertical placement
    """

    def __init__(
        self,
        images_dir: pathlib.Path = IMAGES_DIR,
        output_dir: pathlib.Path = OUTPUT_DIR,
        s3_bucket: str = S3_BUCKET,
        s3_region: str = S3_REGION,
        guidelines: str = GUIDELINES,
        ratios: Optional[List[str]] = None,     # e.g. ["1:1","1:1"] or ["9:7","1:1"]
        default_scale: Optional[float] = 0.72,  # smaller product by default
        default_y: Optional[float] = None,      # None => center (Claid_func normalizes)
    ) -> None:
        self.images_dir = pathlib.Path(images_dir)
        self.output_dir = pathlib.Path(output_dir)
        self.s3 = S3Uploader(bucket_name=s3_bucket, region=s3_region)
        self.claid = ClaidFunc()
        self.prep = ImagePrep()  # can be tuned via env in edit_image.py
        self.guidelines = guidelines
        self.ratios = list(ratios) if ratios else list(DEFAULT_ASPECTS)
        self.default_scale = default_scale
        self.default_y = default_y

    def run(self) -> Dict[str, Any]:
        """Run full pipeline for the latest image in IMAGES_DIR."""
        self.output_dir.mkdir(parents=True, exist_ok=True)

        # 1) Pick latest image
        src = _latest_image_in(self.images_dir)
        if not src:
            raise FileNotFoundError(f"Görsel bulunamadı: {self.images_dir}")
        print(f"🖼  Son görsel: {src.name}")

        # 2) Pre-Claid prepping (resize/denoise/sharpen/format)
        prepped_path, prep_report = self.prep.prepare(src)
        print(f"🧼 Prepped image: {pathlib.Path(prepped_path).name}")

        # 3) Upload to S3 (Claid will consume this public URL)
        s3_url = self._ensure_s3_url(pathlib.Path(prepped_path))
        print(f"☁️  S3 URL: {s3_url}")

        # 3.5) Generate two diverse Gemini prompts from local prepped image
        prompts_overrides = self._gemini_prompts_from_local(str(prepped_path))

        # 4) Remove background (transparent cutout URL)
        cutout_url = self._remove_bg(s3_url)
        print(f"✂️  Cutout URL: {cutout_url}")

        # 5) Create scenes for requested aspect ratios (round-robin prompts)
        urls_by_aspect = self._make_images(cutout_url, self.ratios, prompt_override=prompts_overrides)
        for key, u in urls_by_aspect.items():
            print(f"⭐ {key}: {u}")

        # 6) Download finals locally
        saved = self._download_many(urls_by_aspect, basename=_slugify(src.stem))

        return {
            "source_path": str(src),
            "prepped_path": str(prepped_path),
            "prep_report": prep_report.to_dict(),
            "s3_url": s3_url,
            "cutout_url": cutout_url,
            "result_urls": urls_by_aspect,
            "saved_files": saved,
            "output_dir": str(self.output_dir),
        }

    # ---------- Internals ----------

    def _ensure_s3_url(self, local_path: pathlib.Path) -> str:
        """Upload local path to S3 and return public URL."""
        return self.s3.upload_file(str(local_path))

    def _remove_bg(self, input_url: str) -> str:
        """Call Claid remove_background (URL) and return cutout tmp_url."""
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
            raise RuntimeError(f"Claid remove_background_url failed: {data}")
        return url

    def _gemini_prompts_from_local(self, prepped_path: str) -> List[str]:
        """Generate two creative prompts via PromtMaker with different temperatures."""
        prompts: List[str] = []
        try:
            pm = PromtMaker()

            # Variant A: more imaginative / lifestyle-leaning
            res_a = pm.analyze_and_prompt(
                prepped_path,
                temperature=0.9,
                style_hint="Be bold and imaginative. Prefer tasteful lifestyle context if appropriate."
            )
            # Variant B: premium editorial studio look
            res_b = pm.analyze_and_prompt(
                prepped_path,
                temperature=0.7,
                style_hint="Premium editorial studio look, nuanced lighting variations, high-end presentation."
            )
            for r in (res_a, res_b):
                p = (r.claid_prompt or "").strip()
                if p:
                    prompts.append(p)

            # Fallback logic
            if not prompts:
                prompts = [
                    "clean minimal studio background, soft lighting, natural shadows, subtle gradient backdrop, premium look"
                ]
            elif len(prompts) == 1:
                prompts.append("clean minimal studio background, soft lighting, natural shadows, subtle gradient backdrop, premium look")

            print("🤖 Gemini prompts:")
            for i, p in enumerate(prompts, 1):
                print(f"   {i}. {p}")

        except Exception as e:
            print(f"⚠️ Gemini prompt generation failed: {e}")
            prompts = [
                "clean minimal studio background, soft lighting, natural shadows, subtle gradient backdrop, premium look",
                "editorial studio on seamless backdrop, soft gradient light, premium catalog look"
            ]
        return prompts

    def _make_images(
        self,
        cutout_url: str,
        ratios: List[str],
        prompt_override: Optional[object] = None
    ) -> Dict[str, str]:
        """
        Build a dict like: {"1x1_1": url, "1x1_2": url, "9x7_1": url, ...}
        Keys are deterministic for saving: {basename}__{key}__{ts}.png
        """
        out: Dict[str, str] = {}
        counters: Dict[str, int] = {}

        for aspect in ratios:
            aspect = aspect.strip()
            # Allowed enumerations per Claid doc:
            if aspect not in {"5:12","9:16","4:7","7:9","4:5","1:1","9:7","19:13","7:4","16:9","12:5"}:
                raise ValueError(f"Unsupported aspect ratio for Claid: {aspect}")

            # Choose prompt: if list -> round-robin; if str -> reuse; else use fallback map
            if isinstance(prompt_override, list) and prompt_override:
                idx = counters.get("__prompt_idx__", 0)
                prompt = prompt_override[idx % len(prompt_override)]
                counters["__prompt_idx__"] = idx + 1
            elif isinstance(prompt_override, str) and prompt_override:
                prompt = prompt_override
            else:
                prompt = PROMPT_BY_ASPECT.get(aspect) or (
                    "clean studio, soft daylight, subtle realistic shadows, photorealistic"
                )
            print(f"📝 Using prompt for {aspect}: {prompt}")

            # Explicit prompt path: autoprompt=False + prompt=...
            scene = self.claid.add_background(
                object_image_url=cutout_url,
                use_autoprompt=False,
                prompt=prompt,
                guidelines=self.guidelines,              # ignored since explicit prompt path
                aspect_ratio=aspect,
                number_of_images=1,
                preference="optimal",
                output_format="png",
                scale=self.default_scale,                # can be None to use Claid default
                position={"x": 0.5, "y": self.default_y} if self.default_y is not None else None,
            )
            print(f"📨 Claid scene request done for {aspect}")

            urls = scene.get("tmp_urls") or []
            if not urls:
                raise RuntimeError(f"Scene failed for aspect {aspect}: {scene}")

            counters[aspect] = counters.get(aspect, 0) + 1
            key = f"{aspect.replace(':','x')}_{counters[aspect]}"
            out[key] = urls[0]

        return out

    def _download_many(self, urls_by_aspect: Dict[str, str], basename: str) -> Dict[str, str]:
        """Download all generated images to OUTPUT_DIR with deterministic names."""
        ts = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
        saved: Dict[str, str] = {}

        for key, url in urls_by_aspect.items():
            name = f"{basename}__{key}__{ts}.png"
            path = self.output_dir / name
            _download(url, path)
            saved[key] = str(path)

        return saved


def main() -> None:
    pp = PicPre()
    result = pp.run()
    print("\n✅ Tamamlandı.\n")
    print(json.dumps(result, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()