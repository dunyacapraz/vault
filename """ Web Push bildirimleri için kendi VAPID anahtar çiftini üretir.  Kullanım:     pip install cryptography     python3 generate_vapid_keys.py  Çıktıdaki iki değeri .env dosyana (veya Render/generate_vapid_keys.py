"""
Web Push bildirimleri için kendi VAPID anahtar çiftini üretir.

Kullanım:
    pip install cryptography
    python3 generate_vapid_keys.py

Çıktıdaki iki değeri .env dosyana (veya Render/host ortam değişkenlerine) ekle:
    VAPID_PUBLIC_KEY=...
    VAPID_PRIVATE_KEY=...

Bu anahtarlar kişiseldir — başkasıyla paylaşma, repoya commit'leme.
"""
import base64
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives import serialization


def b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def main():
    private_key = ec.generate_private_key(ec.SECP256R1())

    # Public key: uncompressed point (0x04 || X || Y), 65 byte — tarayıcıya bu gönderilir
    public_numbers = private_key.public_key().public_numbers()
    x = public_numbers.x.to_bytes(32, "big")
    y = public_numbers.y.to_bytes(32, "big")
    public_raw = b"\x04" + x + y

    # Private key: ham 32 byte skaler — sunucu tarafında imzalama için
    private_value = private_key.private_numbers().private_value
    private_raw = private_value.to_bytes(32, "big")

    print("VAPID_PUBLIC_KEY=" + b64url(public_raw))
    print("VAPID_PRIVATE_KEY=" + b64url(private_raw))
    print()
    print("Bu iki satırı .env dosyana (veya host'unun ortam değişkenlerine) ekle.")


if __name__ == "__main__":
    main()
