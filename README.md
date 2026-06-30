# Vault — Ortak Tatil & Anı Arşivi

Arkadaş grubunuz için özel, şifre korumalı fotoğraf/video arşivi. Her üye kendi tatil/anı albümünü oluşturup içine fotoğraf-video ekleyebilir.

## Kurulum

```bash
pip install flask
python3 app.py
```

Tarayıcıda `http://127.0.0.1:5000` adresini aç.

## Arkadaşları ekleme / düzenleme

`app.py` dosyasının başındaki `USERS` sözlüğünü düzenle:

```python
USERS = {
    "admin":  {"password": "1234",       "name": "Admin",  "avatar_color": "#0f172a"},
    "ayse":   {"password": "ayse2026",   "name": "Ayşe",   "avatar_color": "#be185d"},
    "mehmet": {"password": "mehmet2026", "name": "Mehmet", "avatar_color": "#1d4ed8"},
    "zeynep": {"password": "zeynep2026", "name": "Zeynep", "avatar_color": "#15803d"},
}
```

Yeni bir arkadaş için bu listeye bir satır ekleyip kullanıcı adı/şifreyi kendisiyle paylaşman yeterli. `avatar_color` o kişinin baş harfinin görüneceği rozetin rengi (hex kod).

## Neler eklendi

- **Yaklaşan etkinlik geri sayımı** — giriş ekranında en yakın etkinlik canlı gün:saat:dakika:saniye geri sayımıyla görünüyor (örn. "3 gün sonra Tekirdağ Buluşması"). Dashboard'dan "Etkinlik Ekle" ile herkes yeni etkinlik açabiliyor, sadece ekleyen kişi silebiliyor.
- **Çoklu üye girişi** — her arkadaş kendi kullanıcı adı/şifresiyle giriş yapıyor.
- **Tatil/Anı albümleri** — herkes "+ Yeni Tatil Ekle" ile başlık, tarih ve açıklamayla yeni bir albüm açabiliyor.
- **Çoklu dosya yükleme** — sürükle-bırak veya tıkla, birden fazla fotoğraf/video aynı anda yüklenebiliyor; yükleme ilerleme çubuğuyla gösteriliyor.
- **Polaroid galeri** — fotoğraflar hafif eğik, hover'da düzelen kartlarla anı panosu gibi görünüyor.
- **Lightbox görüntüleyici** — bir anıya tıklayınca tam ekran açılıyor, ok tuşlarıyla/okçuklarla gezinebiliyorsun, indirebiliyorsun.
- **Kim ekledi rozeti** — her anının altında onu ekleyen kişinin baş harfi/rengi görünüyor.
- **Silme yetkisi** — bir anıyı sadece ekleyen kişi veya albümü oluşturan silebilir; albümün tamamını sadece oluşturan silebilir (yanlışlıkla silmeyi engellemek için onay penceresi var).
- **Toast bildirimler** — `alert()` yerine sayfanın sağ üstünde yumuşak geçişli bildirimler (sayfa yenilense bile mesaj kayboluyor).
- **Animasyonlar** — sayfa açılışında kartların sırayla belirmesi, modal pencerelerin yumuşak açılması, buton basma efektleri, yükleme sırasında shimmer/spinner efektleri.
- **Korumalı dosya erişimi** — `/uploads/...` linkleri sadece giriş yapmış kullanıcılar tarafından görülebiliyor.

## Bilinmesi gerekenler / öneriler

- Şu an veriler `data/trips.json` dosyasında, dosyalar da `uploads/<albüm_id>/` klasöründe saklanıyor — küçük bir arkadaş grubu için yeterli ama düzenli **yedek alman** (örn. zip'leyip Drive'a atman) iyi olur.
- Şifreler düz metin olarak kodda duruyor; sadece güvendiğiniz kapalı bir grup için uygun. Daha geniş/genel kullanım için kayıt sistemi + şifre hash'leme önerilir.
- Geliştirmeye devam etmek istersen aşağıdaki eklentiler doğal bir sonraki adım olur:
  - Her anıya **yorum / emoji tepkisi** ekleme
  - Albüme **harita üzerinde konum** işaretleme
  - **"Bugün ne oldu"** tarzı geçmiş yıllardan anı hatırlatması
  - Albümü **zip olarak toplu indirme**
  - **Karanlık mod**
  - Mobilden direkt fotoğraf çekip yükleme (kamera erişimi)

## Çalıştırmadan önce

`app.secret_key` değerini kendi rastgele bir değerle değiştirmen, gerçek arkadaşlarınla paylaşmadan önce iyi bir güvenlik pratiği olur.
