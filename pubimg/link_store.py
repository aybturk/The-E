# pubimg/link_store.py
import json
import os
from typing import Dict, Optional

class LinkStore:
    """
    index.json içinde: { file_hash: { "path": <str>, "url": <str> } }
    """
    def __init__(self, index_path: str):
        self.index_path = index_path
        self._data: Dict[str, Dict[str, str]] = {}
        self.load()

    def load(self) -> None:
        try:
            with open(self.index_path, "r", encoding="utf-8") as f:
                self._data = json.load(f)
        except FileNotFoundError:
            self._data = {}

    def save(self) -> None:
        os.makedirs(os.path.dirname(self.index_path), exist_ok=True)
        tmp = self.index_path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(self._data, f, ensure_ascii=False, indent=2)
        os.replace(tmp, self.index_path)

    def get(self, file_hash: str) -> Optional[str]:
        row = self._data.get(file_hash)
        return row["url"] if row else None

    def set(self, file_hash: str, path: str, url: str) -> None:
        self._data[file_hash] = {"path": path, "url": url}
        self.save()