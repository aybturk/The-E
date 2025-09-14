from typing import Optional, Dict, Any
import requests
from .keys import Keys

class ClaidFunc:
    """Minimal Claid API wrapper for BG remove + AI background."""

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

    # ---------------------------
    # REMOVE BACKGROUND (URL)
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
        progressive: bool = True
    ) -> Dict[str, Any]:
        """POST /v1/image/edit with background.remove."""
        if color == "transparent" and output_type == "jpeg":
            raise ValueError("Transparent requires png/webp/avif.")

        url = f"{self.base_url}/v1/image/edit"
        operations: Dict[str, Any] = {
            "restorations": {"decompress": decompress, "polish": polish},
            "background": {
                "remove": {"category": category, "clipping": clipping},
                "color": color
            }
        }
        fmt: Dict[str, Any] = {"type": output_type}
        if output_type == "jpeg":
            fmt.update({"quality": jpeg_quality, "progressive": progressive})

        payload: Dict[str, Any] = {
            "input": input_url,
            "operations": operations,
            "output": {"format": fmt}
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
    # AI BACKGROUND (GENERIC)  -> /v1/scene/create
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
        scale: float = 0.85,
        position: Optional[Dict[str, float]] = None,
        output_format: str = "png"
    ) -> Dict[str, Any]:
        """POST /v1/scene/create."""
        if position is None:
            position = {"x": 0.5, "y": 0.5}

        if use_autoprompt:
            p: Dict[str, Any] = {"generate": True}
            if guidelines:
                p["guidelines"] = guidelines
        else:
            if not prompt:
                raise ValueError("Provide prompt when use_autoprompt=False.")
            p = prompt

        payload = {
            "object": {
                "image_url": object_image_url,
                "placement_type": placement_type,
                "scale": scale,
                "position": position
            },
            "scene": {
                "model": model,
                "prompt": p,
                "negative_prompt": negative_prompt or "text, logo, watermark, low quality",
                "aspect_ratio": aspect_ratio,
                "preference": preference
            },
            "output": {
                "number_of_images": number_of_images,
                "format": output_format
            }
        }

        url = f"{self.base_url}/v1/scene/create"
        resp = self.session.post(url, json=payload, timeout=self.timeout)
        if resp.status_code >= 400:
            try:
                err = resp.json()
            except Exception:
                err = {"error_message": resp.text}
            raise RuntimeError(f"Claid error {resp.status_code}: {err}")

        data = resp.json().get("data", {})
        out = data.get("output")

        # normalize -> data["tmp_urls"] list
        if isinstance(out, dict):
            data["tmp_urls"] = [out.get("tmp_url")]
        elif isinstance(out, list):
            data["tmp_urls"] = [o.get("tmp_url") for o in out if isinstance(o, dict)]
        else:
            data["tmp_urls"] = []

        return data

    # ---------------------------
    # SAFE 9:7 PRESET (defaults: y=0.52, scale=0.82)
    # ---------------------------
    def add_background_safe_9x7(
        self,
        *,
        object_image_url: str,
        use_autoprompt: bool = True,
        guidelines: str = ("minimal studio, soft daylight, light wood tabletop, "
                           "subtle realistic shadows, photorealistic product photo"),
        prompt: Optional[str] = None,
        scale: float = 0.82,
        y: float = 0.52,
        negative_prompt: str = ("text, logo, watermark, hands, reflections, "
                                "low quality, extra objects"),
    ) -> Dict[str, Any]:
        """9:7 aspect, conservative framing; y can be overridden at call site."""
        return self.add_background(
            object_image_url=object_image_url,
            use_autoprompt=use_autoprompt,
            guidelines=guidelines if use_autoprompt else None,
            prompt=None if use_autoprompt else (prompt or
                "clean minimal studio on light wood tabletop, soft daylight, "
                "subtle realistic shadows, photorealistic"),
            model="v2",
            aspect_ratio="9:7",
            number_of_images=1,
            preference="optimal",
            negative_prompt=negative_prompt,
            placement_type="absolute",
            scale=scale,
            position={"x": 0.5, "y": y},
            output_format="png",
        )


def _sanity_check():
    f = ClaidFunc()
    print("CLAID key loaded:", Keys.mask(f.api_key))


if __name__ == "__main__":
    _sanity_check()