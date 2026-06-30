"""
Mevcut yerel uploads/ klasöründeki dosyaları R2'ye taşımak için tek seferlik script.

Kullanım:
    1. .env dosyasını doldur (R2 bilgileri)
    2. Eski projenin uploads/ klasörünü bu script'in yanına kopyala
    3. python3 migrate_to_r2.py
"""
import os
import mimetypes
import boto3
from botocore.config import Config
from dotenv import load_dotenv

load_dotenv()

R2_ACCOUNT_ID = os.environ.get('R2_ACCOUNT_ID')
R2_ACCESS_KEY_ID = os.environ.get('R2_ACCESS_KEY_ID')
R2_SECRET_ACCESS_KEY = os.environ.get('R2_SECRET_ACCESS_KEY')
R2_BUCKET_NAME = os.environ.get('R2_BUCKET_NAME')
R2_ENDPOINT_URL = os.environ.get('R2_ENDPOINT_URL') or f"https://{R2_ACCOUNT_ID}.r2.cloudflarestorage.com"

UPLOAD_FOLDER = os.path.join(os.path.dirname(__file__), 'uploads')

s3 = boto3.client(
    "s3",
    endpoint_url=R2_ENDPOINT_URL,
    aws_access_key_id=R2_ACCESS_KEY_ID,
    aws_secret_access_key=R2_SECRET_ACCESS_KEY,
    config=Config(signature_version="s3v4", region_name="auto"),
)


def main():
    if not os.path.isdir(UPLOAD_FOLDER):
        print(f"'{UPLOAD_FOLDER}' bulunamadı. Eski uploads/ klasörünü buraya kopyaladığından emin ol.")
        return

    count = 0
    for trip_id in os.listdir(UPLOAD_FOLDER):
        trip_path = os.path.join(UPLOAD_FOLDER, trip_id)
        if not os.path.isdir(trip_path):
            continue
        for filename in os.listdir(trip_path):
            file_path = os.path.join(trip_path, filename)
            if not os.path.isfile(file_path):
                continue
            key = f"{trip_id}/{filename}"
            content_type = mimetypes.guess_type(filename)[0] or 'application/octet-stream'
            with open(file_path, 'rb') as f:
                s3.upload_fileobj(f, R2_BUCKET_NAME, key, ExtraArgs={"ContentType": content_type})
            print(f"Yüklendi: {key}")
            count += 1

    print(f"\nTamamlandı. {count} dosya R2'ye yüklendi.")


if __name__ == '__main__':
    main()
