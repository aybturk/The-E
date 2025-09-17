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

IMAGES_DIR  = ROOT / "TheImage" / "pubimg" / "images"   # input folder
OUTPUT_DIR  = ROOT / "TheProd" / "output"               # save results here

# Guidance (kept for possible autoprompt toggles)
GUIDELINES = os.getenv(
    "THEE_GUIDELINES",
    "minimal studio, soft daylight, light wood tabletop, photorealistic"
)

# Allowed Claid aspect ratios (per docs)
ALLOWED_RATIOS = {"5:12","9:16","4:7","7:9","4:5","1:1","9:7","19:13","7:4","16:9","12:5"}

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

# Default behavior (backward compatible): two images, both 1:1
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


def _list_images(folder: pathlib.Path) -> List[pathlib.Path]:
    """List images in mtime order (old→new)."""
    if not folder.exists():
        return []
    exts = {".png", ".jpg", ".jpeg", ".webp"}
    imgs = [p for p in folder.iterdir() if p.is_file() and p.suffix.lower() in exts]
    return sorted(imgs, key=lambda p: p.stat().st_mtime)


def _latest_image_in(folder: pathlib.Path) -> Optional[pathlib.Path]:
    """Pick the most-recent image from a folder."""
    imgs = _list_images(folder)
    return imgs[-1] if imgs else None


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

      image (local) → (ImagePrep.prepare) → prepped file (local)
                    → S3 upload → public URL
                    → Claid remove_background (cutout URL)
                    → Gemini prompt(s) from local prepped file
                    → Claid add_background (explicit prompt), N times
                    → Download finals to output/

    Configurability (call-time overrideable):
      - ratios: list[str] of aspect ratios (e.g., ["1:1","9:7"]). If omitted, defaults to THEE_ASPECTS (or ["1:1","1:1"]).
      - quantity: total number of output images to create (1..5). If omitted, defaults to len(ratios).
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
        quantity: Optional[int] = None,         # total output count (1..5). If None → len(ratios)
    ) -> None:
        self.images_dir = pathlib.Path(images_dir)
        self.output_dir = pathlib.Path(output_dir)
        self.s3 = S3Uploader(bucket_name=s3_bucket, region=s3_region)
        self.claid = ClaidFunc()
        self.prep = ImagePrep()  # can be tuned via env in edit_image.py
        self.guidelines = guidelines

        # validate/prepare ratios
        base_ratios = list(ratios) if ratios else list(DEFAULT_ASPECTS)
        if not base_ratios:
            base_ratios = ["1:1"]
        for a in base_ratios:
            if a not in ALLOWED_RATIOS:
                raise ValueError(f"Unsupported aspect ratio for Claid: {a}")
        self.ratios = base_ratios

        # quantity: default to number of ratios; clamp to 1..5
        if quantity is None:
            quantity = max(1, min(5, len(self.ratios)))
        if not (1 <= int(quantity) <= 5):
            raise ValueError("quantity must be between 1 and 5")
        self.quantity = int(quantity)

        self.default_scale = default_scale
        self.default_y = default_y

    # ---------- Public entrypoints ----------

    def run_auto(
        self,
        image_path: Optional[str] = None,
        *,
        quantity: Optional[int] = None,
        ratios: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        """
        Unified entrypoint:
          - If image_path is provided → process that image.
          - Else → pick the newest image from images_dir.

        You can override quantity/ratios call-time, e.g.:
          PicPre().run_auto("/path.jpg", quantity=3, ratios=["1:1","9:7"])
        """
        if quantity is not None or ratios is not None:
            tmp = PicPre(
                images_dir=self.images_dir,
                output_dir=self.output_dir,
                s3_bucket=S3_BUCKET,
                s3_region=S3_REGION,
                guidelines=self.guidelines,
                ratios=ratios if ratios else self.ratios,
                default_scale=self.default_scale,
                default_y=self.default_y,
                quantity=quantity if quantity is not None else self.quantity,
            )
            return tmp.run_for_image(image_path) if image_path else tmp.run()
        return self.run_for_image(image_path) if image_path else self.run()

    def run(self) -> Dict[str, Any]:
        """Process the latest image from IMAGES_DIR (default behavior)."""
        src = _latest_image_in(self.images_dir)
        if not src:
            raise FileNotFoundError(f"Görsel bulunamadı: {self.images_dir}")
        print(f"🖼  Son görsel: {src.name}")
        return self._pipeline(src)

    def run_for_image(self, image_path: str) -> Dict[str, Any]:
        """Process a specific image given by absolute/relative path."""
        src = pathlib.Path(image_path).expanduser().resolve()
        if not src.exists():
            raise FileNotFoundError(f"Image not found: {src}")
        print(f"🖼  Seçilen görsel: {src.name}")
        return self._pipeline(src)

    # ---------- Discovery helpers (UI/CLI'e faydalı) ----------
    def list_inputs(self, limit: int = 20) -> List[str]:
        """Return up to `limit` image paths in images_dir (old→new)."""
        return [str(p) for p in _list_images(self.images_dir)][-limit:]

    # ---------- Core pipeline ----------
    def _pipeline(self, src: pathlib.Path) -> Dict[str, Any]:
        """Shared core pipeline that both run() and run_for_image() use."""
        self.output_dir.mkdir(parents=True, exist_ok=True)

        # 1) Pre-Claid prepping (resize/denoise/sharpen/format)
        prepped_path, prep_report = self.prep.prepare(src)
        print(f"🧼 Prepped image: {pathlib.Path(prepped_path).name}")

        # 2) Upload to S3 (Claid will consume this public URL)
        s3_url = self._ensure_s3_url(pathlib.Path(prepped_path))
        print(f"☁️  S3 URL: {s3_url}")

        # 3) Generate N diverse Gemini prompts from local prepped image
        prompts_overrides = self._gemini_prompts_from_local(str(prepped_path), count=self.quantity)

        # 4) Remove background (transparent cutout URL)
        cutout_url = self._remove_bg(s3_url)
        print(f"✂️  Cutout URL: {cutout_url}")

        # 5) Create exactly `self.quantity` scenes, cycling through ratios (and prompts)
        urls_by_key = self._make_images(
            cutout_url,
            desired_count=self.quantity,
            ratios=self.ratios,
            prompt_override=prompts_overrides,
        )
        for key, u in urls_by_key.items():
            print(f"⭐ {key}: {u}")

        # 6) Download finals locally
        saved = self._download_many(urls_by_key, basename=_slugify(src.stem))

        return {
            "source_path": str(src),
            "prepped_path": str(prepped_path),
            "prep_report": prep_report.to_dict(),
            "s3_url": s3_url,
            "cutout_url": cutout_url,
            "result_urls": urls_by_key,
            "saved_files": saved,
            "output_dir": str(self.output_dir),
            "quantity": self.quantity,
            "ratios": self.ratios,
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

    def _gemini_prompts_from_local(self, prepped_path: str, count: int = 2) -> List[str]:
        """
        Generate `count` creative prompts via PromtMaker with varying temperatures / style hints.
        Ensures at least `count` prompts for maximum diversity.
        """
        prompts: List[str] = []
        try:
            pm = PromtMaker()

            # spread temperatures around a base range
            temps = [0.6 + 0.1 * i for i in range(max(1, count))]  # 0.6, 0.7, 0.8, ...
            style_hints = [
                "Minimal studio look with natural textures",
                "Premium editorial catalog style, nuanced lighting",
                "Warm lifestyle context, modern kitchen ambiance",
                "Soft daylight with artisan aesthetic, tactile surfaces",
                "Clean modern e-commerce product look, subtle gradient backdrop",
                "Scandinavian minimalism, light wood tabletop, airy feel",
                "Muted tones, linen backdrop, gentle falloff shadows",
            ]

            for i in range(count):
                res = pm.analyze_and_prompt(
                    prepped_path,
                    temperature=temps[i % len(temps)],
                    style_hint=style_hints[i % len(style_hints)]
                )
                p = (res.claid_prompt or "").strip()
                if p:
                    prompts.append(p)

            # Fallbacks if model returns too little
            if not prompts:
                prompts = [
                    "clean minimal studio background, soft lighting, natural shadows, subtle gradient backdrop, premium look"
                ] * count
            elif len(prompts) < count:
                base = prompts[-1]
                prompts += [base] * (count - len(prompts))

            print("🤖 Gemini prompts:")
            for i, p in enumerate(prompts, 1):
                print(f"   {i}. {p}")

        except Exception as e:
            print(f"⚠️ Gemini prompt generation failed: {e}")
            prompts = [
                "clean minimal studio background, soft lighting, natural shadows, subtle gradient backdrop, premium look"
            ] * count

        return prompts

    def _make_images(
        self,
        cutout_url: str,
        *,
        desired_count: int,
        ratios: List[str],
        prompt_override: Optional[object] = None
    ) -> Dict[str, str]:
        """
        Create exactly `desired_count` images, cycling through given `ratios` and `prompt_override`.
        Keys look like: {"1x1_1": url, "1x1_2": url, "9x7_1": url, ...}
        """
        out: Dict[str, str] = {}
        counters: Dict[str, int] = {}
        prompt_idx = 0

        if not ratios:
            ratios = ["1:1"]
        for a in ratios:
            if a not in ALLOWED_RATIOS:
                raise ValueError(f"Unsupported aspect ratio for Claid: {a}")

        for i in range(desired_count):
            aspect = ratios[i % len(ratios)]

            # Choose prompt: if list -> round-robin; if str -> reuse; else use fallback map
            if isinstance(prompt_override, list) and prompt_override:
                prompt = prompt_override[prompt_idx % len(prompt_override)]
                prompt_idx += 1
            elif isinstance(prompt_override, str) and prompt_override:
                prompt = prompt_override
            else:
                prompt = PROMPT_BY_ASPECT.get(aspect) or (
                    "clean studio, soft daylight, subtle realistic shadows, photorealistic"
                )

            print(f"📝 [{i+1}/{desired_count}] Using prompt for {aspect}: {prompt}")

            scene = self.claid.add_background(
                object_image_url=cutout_url,
                use_autoprompt=False,
                prompt=prompt,
                guidelines=self.guidelines,              # ignored since explicit prompt path
                aspect_ratio=aspect,
                number_of_images=1,                      # deterministic naming; loop per image
                preference="optimal",
                output_format="png",
                scale=self.default_scale,
                position={"x": 0.5, "y": self.default_y} if self.default_y is not None else None,
            )

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


# ------------------- CLI -------------------
def main() -> None:
    """
    CLI usage:
      python -m TheProd.PicPre                      -> process latest image (default ratios & quantity)
      python -m TheProd.PicPre /path/to.jpg         -> process the given image
    """
    pp = PicPre()
    if len(sys.argv) > 1:
        result = pp.run_for_image(sys.argv[1])
    else:
        result = pp.run()
    print("\n✅ Tamamlandı.\n")
    print(json.dumps(result, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()