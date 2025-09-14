# CLAID/keys.py
from __future__ import annotations
import os, json, pathlib
from typing import Optional

# CLAID klasörü içinde secrets dosyası için yol
_SECRETS_FILE = pathlib.Path(__file__).with_name("claid.secrets.json")

class Keys:


    @staticmethod
    def get_claid_api_key() -> str:
        key = os.getenv("CLAID_API_KEY")
        if key and key.strip():
            return key.strip()

        if _SECRETS_FILE.exists():
            try:
                data = json.loads(_SECRETS_FILE.read_text())
                key = (data.get("CLAID_API_KEY") or "").strip()
                if key:
                    return key
            except Exception:
                pass

        raise RuntimeError(
            "CLAID_API_KEY not found. "
            "Set environment variable CLAID_API_KEY or create CLAID/claid.secrets.json"
        )

    @staticmethod
    def save_claid_api_key(value: str) -> None:
        value = (value or "").strip()
        if not value:
            raise ValueError("Empty API key.")
        _SECRETS_FILE.write_text(json.dumps({"CLAID_API_KEY": value}, indent=2))
        try:
            os.chmod(_SECRETS_FILE, 0o600)  # Unix sistemlerde sadece sahip okuyabilsin
        except Exception:
            pass

    @staticmethod
    def mask(key: Optional[str], show: int = 4) -> str:
        if not key:
            return ""
        key = key.strip()
        return ("*" * max(len(key) - show, 0)) + key[-show:]