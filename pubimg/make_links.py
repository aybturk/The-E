# pubimg/make_links.py
import os
import hashlib
from typing import List
from pubimg.s3_uploader import S3Uploader
from pubimg.link_store import LinkStore

# ✏️ Burayı sadece gerekirse değiştir
BUCKET = os.environ.get("THE_E_S3_BUCKET", "the-e-assets")
REGION  = os.environ.get("THE_E_AWS_REGION", "eu-north-1")
IMAGES_DIR = os.path.join(os.path.dirname(__file__), "images")
INDEX_PATH = os.path.join(os.path.dirname(__file__), "index.json")

ALLOWED_EXTS = {".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp", ".tiff"}

def sha256_file(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()

def scan_images(root: str) -> List[str]:
    if not os.path.isdir(root):
        print(f"⚠️  images klasörü yok: {root}")
        return []
    files = []
    for name in os.listdir(root):
        p = os.path.join(root, name)
        if not os.path.isfile(p):
            continue
        ext = os.path.splitext(name)[1].lower()
        if ext in ALLOWED_EXTS:
            files.append(p)
        else:
            print(f"↩️  Atlandı (uzantı desteklenmiyor): {name}")
    return sorted(files)

def main() -> None:
    print(f"🔎 Scanning: {IMAGES_DIR}")
    paths = scan_images(IMAGES_DIR)
    if not paths:
        print("😶  Yüklenecek yeni dosya yok.")
        return

    store = LinkStore(INDEX_PATH)
    uploader = S3Uploader(bucket_name=BUCKET, region=REGION)

    uploaded_any = False
    for p in paths:
        digest = sha256_file(p)
        existing = store.get(digest)
        if existing:
            print(f"✅ ZATEN VAR: {os.path.basename(p)} → {existing}")
            continue

        try:
            url = uploader.upload_file(p)
            store.set(digest, p, url)
            print(f"🟢 KAYDEDİLDİ: {os.path.basename(p)} → {url}")
            uploaded_any = True
        except Exception as e:
            print(f"🔴 HATA: {os.path.basename(p)} → {e}")

    if not uploaded_any:
        print("ℹ️  Hepsi daha önce yüklenmişti (index.json’dan bulundu).")
    else:
        print(f"📒 Index güncellendi: {INDEX_PATH}")

if __name__ == "__main__":
    main()