from typing import Optional, Dict, Any, List
import os
import requests
from .keys import Keys


# -----------------------------
# Helpers
# -----------------------------
def _clamp(v: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, v))


def _norm_position(pos: Optional[Dict[str, float]]) -> Dict[str, float]:
    """
    Normalize and clamp position dict to [0..1].
    Defaults to the image center if missing.
    """
    if not isinstance(pos, dict):
        return {"x": 0.5, "y": 0.5}
    x = _clamp(float(pos.get("x", 0.5)), 0.0, 1.0)
    y = _clamp(float(pos.get("y", 0.5)), 0.0, 1.0)
    return {"x": x, "y": y}


def _norm_scale(scale: Optional[float], default_val: float) -> float:
    """
    Normalize and clamp scale to a safe range.
    Lower scale => smaller product in frame (more visible background).
    """
    if scale is None:
        return default_val
    return _clamp(float(scale), 0.40, 0.95)


def _collect_tmp_urls(data: Dict[str, Any]) -> List[str]:
    """
    Normalize Claid response into data['tmp_urls']: List[str]
    """
    urls: List[str] = []
    out = (data or {}).get("output")
    if isinstance(out, dict):
        tu = out.get("tmp_url")
        if isinstance(tu, str) and tu:
            urls.append(tu)
    elif isinstance(out, list):
        for o in out:
            if isinstance(o, dict):
                tu = o.get("tmp_url")
                if isinstance(tu, str) and tu:
                    urls.append(tu)
    data["tmp_urls"] = urls
    return urls


# -----------------------------
# Claid client
# -----------------------------
class ClaidFunc:
    """
    Minimal Claid API wrapper for:
      - Background removal
      - AI background scene creation

    Defaults are tuned for e‑commerce:
    - Smaller object by default (scale ~0.68) so backgrounds are visible
    - Centered placement (x=0.5, y=0.5)
    - Strong decompression; PNG output for transparency
    """

    def __init__(self, api_key: Optional[str] = None, timeout: int = 60):
        self.api_key = api_key or Keys.get_claid_api_key()
        self.base_url = "https://api.claid.ai"
        self.timeout = timeout
        self.session = requests.Session()
        self.session.headers.update({
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
            "Accept": "*/*",
        })

        # Global defaults (override via env without editing code)
        #   THEE_ABG_DEFAULT_SCALE -> default scale for add_background
        #   THEE_ABG_NEGATIVE      -> default negative prompt
        self._default_scale = float(os.getenv("THEE_ABG_DEFAULT_SCALE", "0.68"))
        self._default_negative = os.getenv(
            "THEE_ABG_NEGATIVE",
            "text, logo, watermark, hands, reflections, low quality, artifacts, extra objects"
        )

    # ---------------------------
    # BACKGROUND REMOVAL (URL)
    # ---------------------------
    def remove_background_url(
        self,
        *,
        input_url: str,
        category: str = "products",
        clipping: bool = True,
        color: str = "transparent",
        decompress: str = "strong",
        polish: bool = False,
        output_type: str = "png",
        jpeg_quality: int = 100,
        progressive: bool = True,
    ) -> Dict[str, Any]:
        """
        POST /v1/image/edit with background.remove.
        Returns the raw 'data' dict from Claid; cutout URL at data['output']['tmp_url'].
        """
        if color == "transparent" and output_type == "jpeg":
            raise ValueError("Transparent background requires png/webp/avif output.")

        url = f"{self.base_url}/v1/image/edit"
        operations: Dict[str, Any] = {
            "restorations": {"decompress": decompress, "polish": polish},
            "background": {
                "remove": {"category": category, "clipping": clipping},
                "color": color,
            },
        }
        fmt: Dict[str, Any] = {"type": output_type}
        if output_type == "jpeg":
            fmt.update({"quality": jpeg_quality, "progressive": progressive})

        payload: Dict[str, Any] = {
            "input": input_url,
            "operations": operations,
            "output": {"format": fmt},
        }

        resp = self.session.post(url, json=payload, timeout=self.timeout)
        if resp.status_code >= 400:
            try:
                err = resp.json()
            except Exception:
                err = {"error_message": resp.text}
            raise RuntimeError(f"Claid error {resp.status_code}: {err}")

        return resp.json().get("data", {})

    # ---------------------------
    # AI BACKGROUND (GENERIC)
    # ---------------------------
    def add_background(
        self,
        *,
        object_image_url: str,
        use_autoprompt: bool = True,
        guidelines: Optional[str] = None,
        prompt: Optional[str] = None,
        model: str = "v2",
        aspect_ratio: str = "1:1",
        number_of_images: int = 1,
        preference: str = "optimal",
        negative_prompt: Optional[str] = None,
        placement_type: str = "absolute",
        scale: Optional[float] = None,
        position: Optional[Dict[str, float]] = None,
        output_format: str = "png",
    ) -> Dict[str, Any]:
        """
        POST /v1/scene/create to synthesize a background behind the product cutout.

        Args:
            object_image_url: Cutout image URL (transparent preferred).
            use_autoprompt: Let Claid generate prompt from guidelines.
            guidelines: Guidance text (used when use_autoprompt=True).
            prompt: Explicit prompt (required if use_autoprompt=False).
            model: Claid scene model, e.g., 'v2'.
            aspect_ratio: e.g., '1:1', '9:7', '16:9' etc.
            number_of_images: How many images to generate.
            preference: 'optimal' (balanced) is usually best.
            negative_prompt: Things to avoid (watermarks, text...).
            placement_type: 'absolute' for explicit placement control.
            scale: Object scale (0.40–0.95). Lower => smaller product.
            position: Dict with 'x' and 'y' in [0..1].
            output_format: 'png' recommended.

        Returns:
            Dict containing Claid 'data' plus normalized list at data['tmp_urls'].
        """
        if not use_autoprompt and not prompt:
            raise ValueError("Provide 'prompt' when use_autoprompt=False.")

        # Safe defaults for framing
        scale_val = _norm_scale(scale, self._default_scale)
        pos_val = _norm_position(position)
        neg = negative_prompt or self._default_negative

        # Build prompt section
        if use_autoprompt:
            prompt_block: Dict[str, Any] = {"generate": True}
            if guidelines:
                prompt_block["guidelines"] = guidelines
        else:
            prompt_block = prompt  # plain string

        payload = {
            "object": {
                "image_url": object_image_url,
                "placement_type": placement_type,
                "scale": scale_val,
                "position": pos_val,
            },
            "scene": {
                "model": model,
                "prompt": prompt_block,
                "negative_prompt": neg,
                "aspect_ratio": aspect_ratio,
                "preference": preference,
            },
            "output": {
                "number_of_images": number_of_images,
                "format": output_format,
            },
        }

        url = f"{self.base_url}/v1/scene/create"
        resp = self.session.post(url, json=payload, timeout=self.timeout)
        if resp.status_code >= 400:
            try:
                err = resp.json()
            except Exception:
                err = {"error_message": resp.text}
            raise RuntimeError(f"Claid error {resp.status_code}: {err}")

        data = resp.json().get("data", {}) or {}
        _collect_tmp_urls(data)
        return data


def _sanity_check():
    f = ClaidFunc()
    print("CLAID key loaded:", Keys.mask(f.api_key))


if __name__ == "__main__":
    _sanity_check()