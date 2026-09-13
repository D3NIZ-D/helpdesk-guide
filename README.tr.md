<!-- English: README.md -->

# helpdesk-guide

**IT destek masaları için çevrimdışı çalışan arıza giderme asistanı.** Belirtiyi
kullanıcının söylediği gibi yaz — `monitorum calismiyor` — ve dallanan bir karar
ağacında adım adım ilerle; arıza çözülene ya da işe yarar bir özetle devredilene
kadar.

Her şey yerelde çalışır. Bulut yok, hesap yok, ağ isteği yok. IT desteğe en çok
ihtiyaç duyulan an, zaten ağın çöktüğü andır.

[![CI](https://github.com/OWNER/helpdesk-guide/actions/workflows/ci.yml/badge.svg)](https://github.com/OWNER/helpdesk-guide/actions/workflows/ci.yml)
[![Motor: MIT](https://img.shields.io/badge/motor-MIT-blue.svg)](LICENSE)
[![İçerik: CC BY-SA 4.0](https://img.shields.io/badge/i%C3%A7erik-CC%20BY--SA%204.0-lightgrey.svg)](content/LICENSE)

---

## Hızlı başlangıç

```bash
pipx install helpdesk-guide
helpdesk compile          # gömülü içeriği yerel veritabanına derler
helpdesk serve            # tarayıcıda http://127.0.0.1:8756 açılır
```

Depodan kurmak istersen:

```bash
git clone https://github.com/OWNER/helpdesk-guide && cd helpdesk-guide
pip install -e ".[web,dev]"
helpdesk compile && helpdesk serve
```

Çekirdeğin — `compile`, `lint`, `search`, `run`, `report` — tek bir bağımlılığı
var: PyYAML. Web arayüzü isteğe bağlı bir ek.

### Terminalden, tarayıcısız

```console
$ helpdesk search "monitorum calismiyor"
"monitorum calismiyor"
  normalised : monitorum calismiyor
  stems      : monitor calis
  intent     : NOT_WORKING

  ●●●●○○   61%  DSP-001  Monitörde görüntü yok
          donanim/goruntu · runbook · ✔ doğrulanmış · via alias_key
  ●●○○○○   37%  PWR-001  Bilgisayar hiç açılmıyor
          donanim/guc · runbook · ✔ doğrulanmış · via fts

$ helpdesk run DSP-001
```

---

## Neden var?

L1 teknisyeni aynı yirmi arızayla tekrar tekrar karşılaşır, ama:

- Bilgi kıdemli kişilerin kafasında durur, yazıya dökülmez.
- Yazılı olanlar Word/Excel/Confluence'ta dağınıktır ve **arama çalışmaz**:
  `monitörüm`, `monitörde`, `monitörünüzün` — Türkçe eklemeli bir dil.
- Her teknisyen farklı sırayla ilerler; çözüm süresi tutarsız, adımlar atlanır.
- Çözülemeyen vakalar L2'ye eksik bilgiyle gider.

Bu araç dördüncü sorunu bedavaya çözüyor, ve asıl bu yüzden ilk üçünün
çözülmesine katlanılıyor: **eskalasyon özeti oturum kaydından otomatik üretiliyor.**
Aynı devir notunu elle yazmak her seferinde beş dakika.

---

## Nasıl çalışır?

```
Sunum:      Yerel web arayüzü (127.0.0.1, token korumalı)  ·  CLI
                              │
Uygulama:   FastAPI — arama · oturum · içerik · raporlar
                              │
Çekirdek:   Eşleştirici (BM25 + alias + niyet)
            Karar ağacı yürütücüsü (deterministik)
            Oturum kaydedici + eskalasyon özeti
            Türkçe normalizasyon · içerik derleyici + doğrulayıcı
                              │
Veri:       SQLite (WAL) + FTS5 tam metin indeksi
                              ▲  helpdesk compile
İçerik:     content/runbooks/**.yaml   (açık, CC BY-SA)
            content-local/**           (kuruma özel, asla commit edilmez)
```

İçerik YAML olarak yazılır, SQLite'a derlenir ve çalışma zamanında yalnızca
okunur. YAML insan-okunur ve diff'lenebilir kalır; çalışma zamanı ayrıştırma
maliyeti ödemez ve gerçek bir tam metin indeksi kazanır.

### Tasarım ilkeleri

1. **Önce çevrimdışı, az bağımlılık.** Ağ yokken de çalışmalı.
2. **İçerik koddan bağımsız.** Runbook'lar git'te YAML dosyaları.
3. **Bir düğüm, bir eylem.** "Kabloyu kontrol et ve sürücüyü güncelle" iki adımdır.
4. **Her adımın doğrulaması var.** "Yaptım" kanıt değil; `verify_text` neyin
   değiştiğini sorar.
5. **Deterministik yürütme.** Aynı cevaplar her zaman aynı yolu üretir. LLM alias
   üretmeye yardım edebilir; yürütme yolunda asla yer almaz.
6. **Kullanım içeriği besler.** Nerede takılındığı ölçülür, ölçüm bir yapılacaklar
   listesidir.

---

## Üç içerik katmanı

Her arıza karar ağacı hak etmez. Ağaç **belirsizliği daraltmak** için vardır:
"görüntü yok" altı olası kök nedene işaret eder, sorular beşini eler.
`0x0000007B` ise tek anlamlıdır; onu on iki düğüme sokmak teknisyeni yavaşlatır.

| Katman | Yapı | Hedef adet | Yazma süresi |
|---|---|---|---|
| **`runbook`** | Dallanan karar ağacı | 100–200 | 2–4 saat |
| **`guide`** | Doğrusal kontrol listesi, 3–8 adım | 800–1.500 | 20–40 dk |
| **`reference`** | Belirti → nedenler → çözüm, tek kart | 3.000–4.000 | 5–15 dk |

Depoda 19 karar ağacı, 3 kontrol listesi ve 4 referans kartı var — tasarım
dokümanındaki v1 içerik listesinin tamamı.

Üçü de aynı arama havuzunu ve aynı şemayı paylaşır; yalnız yürütme biçimi
farklıdır. Kartlar `related_runbook` ile ağaçlara bağlanır, böylece 5.000 kayıt
dağınık bir yığın değil, 150 ağacın etrafında örülmüş bir ağ olur.

Fark şurada: 5.000 karar ağacı 5-10 kişi-yıl. 5.000 **kayıt** ise ulaşılabilir,
ve gerçek vaka hacminin %80'i ilk 210 tanesiyle karşılanıyor.

---

## Güven merdiveni

Yirmi kayıtta içerik kalitesi göz önündedir. Beş binde ise tek önemli şey odur —
kayıtların beşte biri yanlışsa bilgi tabanı **hiç olmamasından kötüdür**:
teknisyen yanlış adımı uygular, arızayı büyütür ve bir daha araca güvenmez.

| Seviye | Anlamı | Skor çarpanı |
|---|---|---|
| `dogrulanmis` | Gerçek vakada uygulandı ve çözdü | ×1,00 |
| `incelendi` | Konuyu bilen biri okudu, onayladı | ×0,90 |
| `taslak` | Yazıldı, incelenmedi | ×0,70 |
| `otomatik` | Üretildi/aktarıldı, hiç insan görmedi | ×0,50 |

Bunlar öneri değil, kodda zorunlu:

- `otomatik` bir kayıt **yayınlanamaz** — derleyici reddeder.
- Doğrulanmamış içerikte `risk: yuksek` adım **derleme hatasıdır**. Registry
  silmeyi, BIOS ayarı değiştirmeyi kimsenin okumadığı içerik öneremez.
- `risk: yuksek` olup `rollback_md` yoksa **derleme hatasıdır**.

---

## Türkçe arama

Projenin en zor ve en kritik parçası.

```
"MONİTORUM calismiyor!!"
  │
  ├─ Unicode NFC
  ├─ Türkçe küçültme        İ→i, I→ı   (str.lower() bunu yanlış yapar)
  ├─ Hata kodu çıkarımı     0x0000007B, INACCESSIBLE_BOOT_DEVICE
  ├─ Noktalama temizliği
  ├─ ASCII katlama          ş→s, ğ→g, ı→i, ö→o, ü→u, ç→c
  ├─ Tokenizasyon + stopword
  ├─ Ek soyma               monitorum → monitor,  calismiyor → calis
  ├─ Kavram eşleme          ekran → monitor  (kavram, her eşanlam değil)
  └─ Niyet çıkarımı         NOT_WORKING, NO_DISPLAY
                            ↓
                FTS5:  "monitor"* OR "ekran"* OR "calis"* ...
```

İşin çoğunu iki karar yapıyor:

**Gövdeleme doğru değil, tutarlı.** Sorgu ve alias aynı hattan geçiyor, dolayısıyla
üretilen token gerçek bir Türkçe kök olmasa bile iki taraf aynı yere düşüyor.
`dosya` → `dos` dilbilimsel olarak yanlış ve tamamen zararsız; önemli olan
`dosyası`nın da aynı yere düşmesi.

**Her FTS terimi bir önek sorgusu.** İndeks tarafında kalan ekler yine eşleşiyor;
bu yüzden fazla kesmek güvenli, az kesmek ölümcül. 200 satırlık bir kural
tablosunun tam bir morfolojik çözümleyicinin yerini tutmasını sağlayan şey bu.

Skorlama:

```
temel = 0,40·BM25 + 0,25·alias + 0,15·niyet + 0,10·bağlam + 0,10·geçmiş
skor  = max(temel, alias_tabanı) × doğrulama_ağırlığı
```

`alias_tabanı`, küçük korpusta BM25'in IDF terimi çöktüğü için gerekli: tam bir
alias eşleşmesi orada da kaydı doğrudan açabilsin diye. Katman önceliği formülde
**yok** — yalnız sıralamada eşitliği bozar, çünkü fark yaratacak kadar büyük bir
bonus, gerçek bir alaka farkını devirecek kadar da büyüktür.

| Skor | Davranış |
|---|---|
| ≥ 0,75 | Runbook doğrudan açılır |
| 0,40 – 0,75 | En iyi 5 aday, güven rozetiyle |
| 0,20 – 0,40 | Elle yazılmış netleştirme sorusu |
| < 0,40 | `knowledge_gaps` kaydı |

Son satır en değerlisi: **henüz yazılmamış runbook'ların, onlara ihtiyaç duyan
kişilerce sıralanmış listesi.**

### Ölçüm

```bash
helpdesk eval
# 141 queries · Recall@1 92.9% · Recall@3 100.0%
```

`tests/golden_queries.yaml` gerçek ifadelerden oluşan bir regresyon setidir ve CI
her içerik değişikliğinde koşar. Yeni bir runbook eski bir aramayı bozarsa, üç
hafta sonra telefonda değil, pull request'te görülür.

---

## Komutlar

| Komut | Ne yapar |
|---|---|
| `helpdesk compile` | İçeriği doğrular ve SQLite'a yazar |
| `helpdesk lint` | Hiçbir şey yazmadan tüm kalite kapılarını çalıştırır |
| `helpdesk search "<belirti>"` | Belirtiyi eşleştirir, skorları ve sinyalleri gösterir |
| `helpdesk run DSP-001` | Runbook'u adım adım yürütür |
| `helpdesk run -q "ekran siyah"` | Arar, en iyi eşleşmeyi yürütür |
| `helpdesk tree <kod\|dosya>` | Karar ağacını ASCII olarak çizer |
| `helpdesk new --tier guide` | Yeni içerik dosyası iskeleti üretir |
| `helpdesk eval` | Arama kalitesini altın sorgu setiyle ölçer |
| `helpdesk report` | Bilgi boşlukları, ölü uçlar, bayat içerik, kök nedenler |
| `helpdesk prune` | Saklama süresi dolan telemetriyi siler |
| `helpdesk serve` | Yerel web arayüzü |

---

## Kurumunda kullanmak

Çoğu kurumun runbook'u kendine özeldir ve birçoğu **gizlidir**. Bu yüzden içerik
baştan ikiye ayrılmış:

```
content/          açık, jenerik, CC BY-SA    ← upstream güncellemelerini al
content-local/    sana ait, git'e girmez     ← asla commit edilmez
```

İkisi de derlenir; `content-local/` içindeki aynı `code`'lu kayıt açık olanın
üstüne biner. Depoyu klonla, kendi gizli runbook'larını `content-local/`'e yaz,
upstream güncellemelerini **tek bir çakışma yaşamadan** almaya devam et.

`content-local/` `.gitignore`'un ilk satırı ve bir pre-commit hook onu sahnelemeyi
reddeder — bu, geri alınamayan tek hata olduğu için iki kat koruma var.

---

## Raporlar

Tüm telemetri yereldedir ve **içeriği iyileştirmek** içindir, personeli ölçmek
için değil — "teknisyen başına oturum" raporu bilinçli olarak yoktur.

| Rapor | Sorduğu soru | Aksiyon |
|---|---|---|
| `knowledge_gaps` | Hangi aramalar sonuçsuz kaldı? | O runbook'ları yaz |
| `dead_ends` | Oturumlar nerede terk ediliyor? | O dalı yeniden yaz |
| `escalation_hotspots` | Hangi runbook'lar sık eskale ediliyor? | Dalları derinleştir |
| `skipped_steps` | Hangi adımlar hep atlanıyor? | Gereksizse sil |
| `slow_steps` | Hangi adım uzun sürüyor? | Talimatı netleştir |
| `stale_content` | Gözden geçirme tarihi geçenler | İnceleme ata |
| `root_cause_distribution` | Gerçekte ne bozuluyor? | Sebebi düzelt |

Sonuncusu projenin bedelini iki kez çıkarır. "Son 90 günde 23 vaka
`VIDEO_KABLO_ARIZA`" bir arıza bilgisi değil, **bir satın alma kararıdır**.

---

## Gizlilik ve güvenlik

- **Yalnız loopback.** Sunucu `127.0.0.1`'e bağlanır; `--host` yüksek sesle uyarır.
  Başlatma adresindeki token, yereldeki başka bir sayfanın API'yi sürmesini
  engeller.
- **İçerikte asla kimlik bilgisi yok.** Derleyici parola, API anahtarı, özel
  anahtar, JWT, bağlantı dizesi ve lisans anahtarı tarar ve **derlemeyi durdurur**.
  Runbook'lar parola kasasındaki kaydın adını referans verir.
- **Kişisel veri tutulmaz.** Son kullanıcı adı ve e-postası saklanmaz; serbest
  metin sorguları veritabanına yazılmadan maskelenir.
- **İçerik veridir, kod değildir.** Yalnız `yaml.safe_load`; kenar koşulları
  `eval` ile değil elle yazılmış küçük bir ayrıştırıcıyla; Markdown HTML kapalı
  ve sıkı CSP altında render edilir.
- **Yapılandırılabilir saklama süresi**, varsayılan 365 gün, `helpdesk prune`.

Açık bildirimi için [SECURITY.md](SECURITY.md).

---

## Katkı

Burada içerik katkısı kod katkısından daha değerlidir — motor birkaç bin satır ve
sonlu; içerik hiç bitmez.

- **[CONTRIBUTING-CONTENT.md](CONTRIBUTING-CONTENT.md)** — runbook yazma rehberi.
  Kod yazmıyorsan bile buradan başla. Şablonu kopyala, doldur, PR aç; CI şemayı,
  grafı, sırları ve arama regresyonunu doğrular, sonra **karar ağacını PR
  yorumuna ASCII olarak basar** — inceleyen kişi YAML okumadan mantığı görür.
- **[CONTRIBUTING.md](CONTRIBUTING.md)** — kod katkısı.
- **[docs/DESIGN.tr.md](docs/DESIGN.tr.md)** — tam teknik tasarım dokümanı.
- **[docs/DECISIONS.md](docs/DECISIONS.md)** — uygulamanın tasarımdan ayrıldığı
  yerler ve gerekçeleri.

İlk katkı için iyi fikirler: mevcut bir runbook'a alias ekle (aramayı
iyileştirmenin en ucuz yolu), iyi bildiğin bir hata kodu için referans kartı yaz,
ya da karşılaştığın ama kaydı olmayan bir arıza için `runbook_request` aç.

---

## Lisans

| Bölüm | Lisans |
|---|---|
| Motor (`src/`) | [MIT](LICENSE) |
| İçerik (`content/`) | [CC BY-SA 4.0](content/LICENSE) |

İki lisans bilinçli. Motorun benimsenmesi sürtünmesiz olmalı. Operasyonel bilgi
ise dolaşarak iyileşir; iyileştirmeler ona bel bağlayan herkese açık kalmalı.
