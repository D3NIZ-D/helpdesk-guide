# Runbook yazma rehberi

> English summary at the bottom. This guide is primarily in Turkish because
> the core content is Turkish; contributions in English are equally welcome
> and follow the same rules.

**Bu projede kod değil içerik darboğazdır.** Motor birkaç bin satır ve bir gün
biter. İçerik hiç bitmez. Kod yazmasan da katkı verebilirsin — bu dosya tam
olarak bunun için var.

---

## En hızlı başlangıç

```bash
git clone https://github.com/OWNER/helpdesk-guide && cd helpdesk-guide
pip install -e ".[dev]"

helpdesk new --tier guide --code NET-020   # iskelet üretir
# dosyayı doldur
helpdesk lint                              # kontrol et
helpdesk compile --allow-drafts            # veritabanına yaz
helpdesk run NET-020                       # kendi yazdığını yürü
```

Son adımı atlama. Yazdığın ağacı bir kez baştan sona yürümek, okuyarak
göremeyeceğin üç eksiği ilk üç dakikada gösterir.

---

## Önce doğru katmanı seç

En sık yapılan hata her şeyi karar ağacı yapmaktır. Karar ağacı **belirsizliği
daraltmak** için vardır. Belirsizlik yoksa ağaç sadece yavaşlatır.

| Sana ait durum | Katman | Süre |
|---|---|---|
| "Bu belirti 4-5 farklı sebepten olabilir, sorularla eliyorum" | `runbook` | 2-4 saat |
| "Sırayla şu 5 şeyi denerim, biri tutar" | `guide` | 20-40 dk |
| "Bu hata kodunun tek bir anlamı var" | `reference` | 5-15 dk |

Hızlı test: **iki farklı cevabın iki farklı yola çıkması gerekiyor mu?**
Gerekmiyorsa `guide` yaz. Bir `guide` sonradan `runbook`'a terfi edebilir;
gereksiz yere ağaç yazmak ise iki saatini geri getirmez.

> 20 kayıttan 5.000'e giden yolun büyük kısmı `guide` ve `reference`'tır.
> Vakaların %80'i 60 runbook + 150 guide ile karşılanıyor (tasarım §22.12).

---

## Alias yazmak — en yüksek getirili iş

Arama motorunun yarısı burada. Beş dakikada yazacağın beş alias, herhangi bir
morfoloji iyileştirmesinden daha çok işe yarar.

**Kural: kullanıcının ağzından yaz, kendi ağzından değil.**

```yaml
aliases:
  # ✅ kullanıcı böyle söyler
  - { term: "monitör çalışmıyor",  kind: belirti, weight: 1.0 }
  - { term: "ekran siyah",         kind: belirti, weight: 0.9 }
  - { term: "no signal",           kind: hata_kodu, weight: 1.2 }

  # ❌ teknisyen böyle söyler, kullanıcı asla
  - { term: "video sinyal yolu arızası" }
```

Şunları ekle:

* **Yanlış kelimeyi.** Kullanıcı "kasa" derken monitörü kastediyor olabilir.
* **İngilizce terimi.** "no signal", "printer offline", "blue screen".
* **Hata kodunu**, `kind: hata_kodu` ve `weight: 1.2`+ ile. Hata kodu
  belirsizlik taşımaz; eşleşirse kayıt doğrudan açılır.

Şunları ekleme:

* **Türkçe karaktersiz kopyasını.** Sistem "monitör" ve "monitor" ifadelerini
  zaten aynı anahtara indiriyor; kopya alias `alias.duplicate` uyarısı verir.
* **Başka bir kaydın alias'ını.** Aynı alias iki kayıtta tam eşleşirse derleme
  **durur** — arama ikisi arasında seçim yapamaz.

Katman ne olursa olsun en az 3 alias gerekir; iyi bir kayıtta 6-10 vardır.

---

## Düğüm yazma kuralları

### Bir düğüm = bir eylem

```yaml
# ❌ iki iş, tek düğüm — teknisyen hangisinin işe yaradığını bilemez
- title: "Kabloyu kontrol et ve sürücüyü güncelle"

# ✅ iki düğüm
- title: "Video kablosunu çıkar, tak ve çapraz test et"
- title: "Grafik sürücüsünü yeniden kur"
```

### Her `talimat` düğümünde `verify_text` olmalı

"Yaptım" bir kanıt değildir. Neyin değişeceğini sor:

```yaml
- key: N20
  type: talimat
  title: "Güç zincirini kontrol et"
  body_md: |
    1. Güç kablosunu iki uçtan da çıkar, tekrar tak.
    2. Uzatma kullanılıyorsa doğrudan duvar prizine bağla.
  verify_text: "Güç LED'i yandı mı?"      # ← zorunlu
```

### Riskli adımlarda geri alma zorunlu

`risk: yuksek` yazıp `rollback_md` yazmazsan **derleme hata verir**. Bu bir
öneri değil, kapı.

```yaml
  risk: yuksek
  requires_admin: true
  requires_user_downtime: true
  rollback_md: |
    Sürücü sorun çıkarırsa: Aygıt Yöneticisi → Özellikler → Sürücü →
    Sürücüyü Geri Al. Seçenek pasifse Güvenli Mod'da önceki sürümü kur.
```

### Her dal bitmeli

Her yol ya `cozum` ya `eskalasyon` ile kapanır. En az bir `eskalasyon`
düğümü olmalı — her arıza yerinde çözülmez, ve teknisyeni ağacın sonunda
boşlukta bırakmak en kötü sonuçtur.

`cozum` düğümlerinde `root_cause` zorunlu, `UPPER_SNAKE_CASE`. Raporlamanın
tamamı buna dayanıyor: "son 90 günde 23 vaka `VIDEO_KABLO_ARIZA`" cümlesi bir
satın alma kararına dönüşüyor. Mevcut kodları önce
`helpdesk report root_cause_distribution` ile kontrol et, yenisini uydurmadan
önce var olanı kullan.

### En uzun yol ≤ 7 adım

Telefondaki teknisyen yedinci adımdan sonra sabrını kaybeder. Daha uzun
oluyorsa ya ağaç iki kayda bölünmeli ya da bazı adımlar birleşmeli.

---

## Asla yazılmayacak şeyler

Bunlar derlemeyi **durdurur**, uyarı vermez:

* Parolalar, lisans anahtarları, API token'ları, VPN ön-paylaşımlı anahtarları
* Özel anahtarlar, JWT'ler, kimlik bilgisi içeren bağlantı dizeleri

Kimlik bilgisi gerekiyorsa **kasa kaydının adını** yaz:

```yaml
body_md: |
  Yerel yönetici hesabıyla giriş yap.
  Kimlik bilgisi: parola kasasında **"Local-Admin-Workstations"** kaydı.
```

Yanlış pozitif olduğundan eminsen satır sonuna `# helpdesk:allow-secret`
ekleyebilirsin — ama önce iki kez düşün.

Ekran görüntülerinde kurum logosu, gerçek kullanıcı adı veya gerçek bir
sunucu adı olmamalı.

---

## Kurumuna özel içerik: `content-local/`

Kendi VPN'inin, kendi sunucu adlarının, kendi süreçlerinin runbook'u
buraya yazılır:

```
content/          herkese açık, CC BY-SA     ← upstream'den güncelleme al
content-local/    sana ait, git'e girmez     ← asla commit edilmez
```

Aynı `code` ile `content-local/` içine yazdığın kayıt, açık olanın **üstüne
biner**. Böylece upstream güncellemelerini çakışmasız alırsın.

`content-local/` `.gitignore`'un ilk satırı ve bir pre-commit hook onu
sahnelemeyi reddeder. Bu geri alınamayan tek hata olduğu için iki kat koruma
var.

---

## Lisans — kopyalama, bağlantı ver

`content/` altındaki her şey **CC BY-SA 4.0**. Katkı verirken bunu kabul
etmiş olursun.

| Kaynak | Ne yapabilirsin |
|---|---|
| Arch Wiki, Ubuntu Docs (CC BY-SA) | **Uyarlayabilirsin** — atıf ver, aynı lisansla paylaş |
| Microsoft Learn, HP/Dell/Lenovo destek | **Kopyalayamazsın.** Bağlantı ver + 2-3 satır kendi cümlenle özetle |
| Hata kodu listeleri | Kod ve kısa açıklama olgudur; **çözüm metni senin yazın olmalı** |
| Stack Overflow, forumlar | Güvenilirliği düşük — `verification: taslak` olarak girer |

`reference` kartının doğru biçimi şudur: **kısa kendi özetin + otoriter
kaynağa bağlantı**. Hem yasal, hem bakımı ucuz (kaynak güncellenince senin
metnin bayatlamaz), hem dürüst.

```yaml
fix_summary: |
  BIOS'ta SATA modunu eski değerine döndür; bu kodun en sık sebebi budur.
source:
  name: "Microsoft Learn — Bug check code reference"
  url: "https://learn.microsoft.com/..."
  note: "Bağlantı verildi, metin kopyalanmadı."
```

---

## Doğrulama merdiveni

Her kaydın bir `verification` seviyesi var ve motor buna göre davranır:

| Seviye | Anlamı | Skor çarpanı |
|---|---|---|
| `dogrulanmis` | Gerçek bir vakada uygulandı ve çözdü | ×1.00 |
| `incelendi` | Konuyu bilen biri okudu, onayladı | ×0.90 |
| `taslak` | Yazıldı, incelenmedi | ×0.70 |
| `otomatik` | Üretildi/aktarıldı, hiç insan görmedi | ×0.50 |

**Kendi yazdığın kaydı `dogrulanmis` yapma.** O seviye "ben bu adımları
gerçek bir arızada uyguladım ve sorun çözüldü" demektir. Yeni yazılmış
içerik `taslak` olarak girer; biri inceleyince `incelendi` olur.

`otomatik` kayıtlar yayınlanamaz ve `risk: yuksek` adım içeremez — derleyici
ikisini de hata sayar.

---

## "Bitti" tanımı

Bir kayıt ancak bunların hepsi sağlanınca `status: yayinda` olur:

- [ ] En az 3 gerçekçi alias (iyi bir kayıtta 6-10)
- [ ] Çözüme ulaşan en uzun yol ≤ 7 adım
- [ ] Her `talimat` düğümünde `verify_text` dolu
- [ ] Her dal `cozum` veya `eskalasyon` ile bitiyor
- [ ] En az bir `eskalasyon` düğümü var
- [ ] `risk: yuksek` adımlarda `rollback_md` dolu
- [ ] Her `cozum` düğümünde `root_cause` var
- [ ] Parola, anahtar, iç sunucu adı yok (`helpdesk lint` geçti)
- [ ] `tests/golden_queries.yaml` içine en az 3 sorgu eklendi, `helpdesk eval` geçiyor
- [ ] `review_period_days` ayarlandı
- [ ] **Yazan kişi dışında biri ağacı baştan sona yürüdü**

---

## Yazım disiplini

Bu bölüm tavsiye değil, projenin en büyük riskine karşı alınmış önlem:
*içerik yazılmaz, proje boş kalır.*

* **Günde 1 kayıt.** Tek oturuşta 20 tane yazmaya çalışmak, 20'sinin de yarım
  kalması demektir.
* **Gerçek vakadan yaz.** Hafızadan yazılan runbook eksik olur. Çözdüğün
  arızayı çözerken not al, akşam kayda çevir.
* **Önce iskelet, sonra gövde.** Düğümleri ve dalları çıkar, `body_md`'leri
  ikinci turda doldur.
* **Kendi yazdığını kullan.** Bir sonraki gerçek vakada kendi ağacını takip et.

---

## Pull request açarken

CI şunları otomatik kontrol eder:

1. Şema ve enum değerleri
2. Graf bütünlüğü — çıkmaz sokak, ulaşılamaz düğüm, döngü, derinlik
3. Sır taraması
4. Altın sorgu seti regresyonu — yeni kayıt eski aramaları bozuyor mu
5. **Karar ağacını ASCII olarak PR yorumuna basar**, böylece inceleyen kişi
   YAML okumadan mantığı görebilir

İnsan incelemesi teknik doğruluğa ve yukarıdaki listeye bakar.

---

## English summary

Content matters more than code here. Copy a template from
`content/_templates/`, fill it in, run `helpdesk lint && helpdesk compile`,
then **walk your own tree** with `helpdesk run <CODE>` before opening a PR.

Key rules:

* **Pick the right tier.** Only write a decision tree when different answers
  genuinely lead to different paths; otherwise write a `guide` (checklist) or
  a `reference` card. Most content should not be a tree.
* **Aliases are the highest-value work.** Write how a *user* describes the
  fault, not how a technician would. Include the English term and the error
  code. Do not add a diacritic-free duplicate — the normaliser handles that.
* **Every instruction needs `verify_text`.** "I did it" is not evidence.
* **Every branch must end** in a resolution or an escalation, and there must
  be at least one escalation.
* **High risk requires `rollback_md`** — enforced, not suggested.
* **Never include credentials.** Reference a vault entry by name. The
  compiler stops the build otherwise.
* **Cite, do not copy.** Write your own two sentences and link the source.
* **Do not mark your own new content `dogrulanmis`/`verified`** — that level
  means it was applied to a real case and worked.

Enum values may be written in Turkish or English (`type: soru` or
`type: question`); they are stored canonically in English. Prose stays in
whatever language the file is written in.
