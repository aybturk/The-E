# pubimg/s3_uploader.py
import os
import mimetypes
import boto3
from datetime import datetime

class S3Uploader:
    def __init__(self, bucket_name: str, region: str = "eu-north-1"):
        self.bucket = bucket_name
        self.region = region
        self.s3 = boto3.client("s3", region_name=region)

    def _guess_content_type(self, file_path: str) -> str:
        ctype, _ = mimetypes.guess_type(file_path)
        return ctype or "application/octet-stream"

    def upload_file(self, file_path: str) -> str:
        base = os.path.basename(file_path)
        # tarih bazlı key (boşlukları dash yapalım)
        slug = base.replace(" ", "-")
        today = datetime.utcnow().strftime("%Y/%m/%d")
        key = f"uploads/{today}/{slug}"

        ctype = self._guess_content_type(file_path)

        print(f"⬆️  Uploading: {file_path}  →  s3://{self.bucket}/{key}  ({ctype})")
        self.s3.upload_file(
            file_path,
            self.bucket,
            key,
            ExtraArgs={"ContentType": ctype}
        )

        url = f"https://{self.bucket}.s3.{self.region}.amazonaws.com/{key}"
        print(f"✅ Uploaded URL: {url}")
        return url