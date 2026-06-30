"""
R2 bağlantısını izole test etmek için. .env dosyasının yanına koy ve çalıştır:
    py test_r2_connection.py
"""
import os
import boto3
from botocore.config import Config
from dotenv import load_dotenv

load_dotenv()

R2_ACCOUNT_ID = os.environ.get('R2_ACCOUNT_ID')
R2_ACCESS_KEY_ID = os.environ.get('R2_ACCESS_KEY_ID')
R2_SECRET_ACCESS_KEY = os.environ.get('R2_SECRET_ACCESS_KEY')
R2_BUCKET_NAME = os.environ.get('R2_BUCKET_NAME')
R2_ENDPOINT_URL = os.environ.get('R2_ENDPOINT_URL') or f"https://{R2_ACCOUNT_ID}.r2.cloudflarestorage.com"

print(f"Endpoint: {R2_ENDPOINT_URL}")
print(f"Bucket:   {R2_BUCKET_NAME}")
print("Bağlanılıyor...")

s3 = boto3.client(
    "s3",
    endpoint_url=R2_ENDPOINT_URL,
    aws_access_key_id=R2_ACCESS_KEY_ID,
    aws_secret_access_key=R2_SECRET_ACCESS_KEY,
    config=Config(signature_version="s3v4", region_name="auto"),
)

try:
    resp = s3.list_objects_v2(Bucket=R2_BUCKET_NAME, MaxKeys=5)
    print("\n✅ BAĞLANTI BAŞARILI")
    print(f"Bucket içinde {resp.get('KeyCount', 0)} obje bulundu (ilk 5 gösteriliyor).")
    for obj in resp.get('Contents', []):
        print(" -", obj['Key'])
except Exception as e:
    print("\n❌ BAĞLANTI HATASI:")
    print(type(e).__name__, "-", str(e))
