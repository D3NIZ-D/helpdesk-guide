> **Not:** Bu, projenin yazılmasından önce hazırlanan özgün teknik tasarım
> dokümanıdır ve tarihsel kayıt olarak korunuyor. Uygulama bu tasarımı
> izliyor; bilinçli olarak ayrıldığı yerler ve gerekçeleri
> [DECISIONS.md](DECISIONS.md) içinde.
>
> *This is the original design document, written before any code, kept as a
> historical record. Where the implementation deliberately departs from it,
> the reasons are in [DECISIONS.md](DECISIONS.md).*

---

# IT Destek Troubleshooting Asistanı — Teknik Tasarım Dokümanı

**Kod adı:** `helpdesk-guide`
**Sürüm:** Tasarım v1.0
**Durum:** Uygulama öncesi tasarım (implementasyon henüz başlamadı)

---

## 1. Amaç ve Kapsam

### 1.1 Problem

L1 IT destek personeli aynı arızalarla tekrar tekrar karşılaşır ama:

- Bilgi kıdemli kişilerin kafasında durur, yazılı değildir.
- Yazılı olanlar Word/Excel/Confluence'ta dağınıktır, arama Türkçe morfolojisi yüzünden çalışmaz.
- Her teknisyen farklı sırayla ilerler → tutarsız çözüm süresi, atlanan adımlar.
- Çözülemeyen vakalar L2'ye eksik bilgiyle eskale edilir.

### 1.2 Çözüm

Kullanıcının doğal dille yazdığı belirtiyi ("Monitörüm çalışmıyor") ilgili **runbook**'a eşleyen, ardından teknisyeni **adım adım, dallanan bir karar ağacında** yürüten, tamamen **yerelde (offline)** çalışan bir uygulama.

### 1.3 Kapsam dahilinde (v1)

- Türkçe (ve İngilizce terim karışık) serbest metin arama
- Belirti → runbook eşleştirme, güven skoru, belirsizlikte netleştirme sorusu
- Dallanan karar ağacı yürütücüsü (soru / talimat / ölçüm / çözüm / eskalasyon düğümleri)
- Oturum kaydı: hangi yoldan gidildi, ne kadar sürdü, çözüldü mü
- Eskalasyon için otomatik vaka özeti üretimi (ticket'a yapıştırılabilir)
- İçerik editörü: runbook yazma/düzenleme, versiyonlama
- Raporlama: çözüm oranı, en sık arızalar, "bulunamayan aramalar" (bilgi boşluğu)

### 1.4 Kapsam dışı (v1)

- Bulut senkronizasyonu, çok şubeli merkezi sunucu
- Ticket sisteminden otomatik vaka çekme (v2 — entegrasyon adaptörü)
- Uzaktan cihaz üzerinde otomatik komut çalıştırma (v2 — bkz. §12.4)
- Son kullanıcı self-servis portalı (v2)

---

## 2. Kullanıcılar ve Senaryolar

| Persona | İhtiyaç | Kritik metrik |
|---|---|---|
| **L1 Teknisyen** (birincil) | Telefondayken hızlı, doğru sıra | Adıma ulaşma süresi < 10 sn |
| **Yeni başlayan / stajyer** | Neyi neden yaptığını öğrenmek | İlk temasta çözüm oranı |
| **İçerik editörü** (kıdemli/L2) | Bilgiyi yazıya dökmek, güncellemek | Runbook güncelleme süresi |
| **Ekip yöneticisi** | Nerede tıkanıyoruz? | Çözülemeyen arama oranı |

### 2.1 Ana kullanım senaryosu (happy path)

1. Kullanıcı arar: "monitorum calismiyor" (Türkçe karaktersiz yazılmış)
2. Sistem normalize eder → `DSP-001 · Monitörde görüntü yok` — güven %91
3. Doğrudan runbook açılır, ilk düğüm: "Monitörün güç LED'i yanıyor mu?"
4. Teknisyen "Hayır" → güç zinciri koluna dallanır
5. 4 adım sonra: "Priz değiştirildikten sonra LED yandı" → **Çözüm: R-DSP-003 (Hatalı priz/uzatma)**
6. Sistem oturumu kaydeder, ticket özeti üretir, kullanıcı kopyalar.

### 2.2 Kenar senaryolar

- **Belirsiz sorgu:** "bilgisayar açılmıyor" → 3 aday runbook (POST yok / OS boot / ekran yok) → netleştirme sorusu: "Kasadan ses/ışık geliyor mu?"
- **Sonuç yok:** hiçbir eşleşme yok → sorgu `knowledge_gaps` tablosuna kaydedilir, editöre rapor edilir.
- **Çıkmaz sokak:** ağacın sonuna gelindi, sorun sürüyor → eskalasyon düğümü, özet üretimi.

---

## 3. Tasarım İlkeleri

1. **Offline-first, sıfır bağımlılık.** İnternet yokken de, ağ çökmüşken de çalışmalı — IT destek zaten en çok o anda lazım.
2. **İçerik koddan bağımsız.** Runbook'lar YAML dosyaları; Git'te versiyonlanır, uygulama bunları derleyip veritabanına yazar.
3. **Her adım tek bir eylem.** Bir düğüm = bir soru veya bir işlem. "Kabloyu kontrol et ve sürücüyü güncelle" iki düğümdür.
4. **Her adımın doğrulaması vardır.** "Yaptım" yetmez; "LED yandı mı?" diye sorulur.
5. **Deterministik yürütme.** Aynı cevaplar her zaman aynı yolu üretir. LLM opsiyonel bir yardımcıdır, motor değildir.
6. **Kullanım, içeriği besler.** Hangi adımda takılındığı ölçülür; içerik ona göre düzeltilir.

---

## 4. Sistem Mimarisi

```
┌──────────────────────────────────────────────────────────────┐
│  SUNUM KATMANI                                               │
│  Yerel Web UI (127.0.0.1) · CLI · (v2: Elektron/Tauri kabuk) │
└───────────────────────────┬──────────────────────────────────┘
                            │ HTTP/JSON (yalnız loopback)
┌───────────────────────────┴──────────────────────────────────┐
│  UYGULAMA KATMANI  (FastAPI)                                 │
│  ┌────────────┐ ┌──────────────┐ ┌──────────┐ ┌───────────┐  │
│  │ Search API │ │ Session API  │ │ Admin API│ │ Report API│  │
│  └─────┬──────┘ └──────┬───────┘ └────┬─────┘ └─────┬─────┘  │
└────────┼───────────────┼──────────────┼─────────────┼────────┘
┌────────┴───────────────┴──────────────┴─────────────┴────────┐
│  ÇEKİRDEK (domain)                                           │
│  ┌──────────────────┐  ┌────────────────┐  ┌──────────────┐  │
│  │ Eşleştirme Motoru│  │ Karar Ağacı    │  │ Oturum       │  │
│  │ (§8)             │  │ Yürütücüsü (§9)│  │ Kaydedici    │  │
│  └──────────────────┘  └────────────────┘  └──────────────┘  │
│  ┌──────────────────┐  ┌────────────────┐                    │
│  │ Türkçe NLP       │  │ İçerik Derleyici│                   │
│  │ Normalizasyon    │  │ + Doğrulayıcı   │                   │
│  └──────────────────┘  └────────────────┘                    │
└───────────────────────────┬──────────────────────────────────┘
┌───────────────────────────┴──────────────────────────────────┐
│  VERİ KATMANI                                                │
│  SQLite (WAL)  +  FTS5 tam metin indeksi  +  medya klasörü   │
└──────────────────────────────────────────────────────────────┘
            ▲
            │ derleme (build)
┌───────────┴──────────────────────────────────────────────────┐
│  İÇERİK DEPOSU (Git)                                         │
│  content/runbooks/*.yaml · content/lexicon/*.yaml · media/   │
└──────────────────────────────────────────────────────────────┘
```

**Akış:** İçerik YAML olarak yazılır → `compile` komutu doğrular ve SQLite'a yazar → uygulama sadece SQLite okur. Böylece çalışma zamanında YAML ayrıştırma maliyeti yoktur, içerik ise insan-okunur ve diff'lenebilir kalır.

---

## 5. Alan Modeli (Domain Model)

| Kavram | Tanım |
|---|---|
| **Kategori** | Hiyerarşik sınıflandırma: `donanim/goruntu`, `ag/vpn`, `yazilim/office` |
| **Belirti (Symptom)** | Kullanıcının tarif ettiği durum. Aramanın hedefi. |
| **Eşanlam (Alias)** | Belirtinin alternatif ifadeleri: "ekran siyah", "no signal", "görüntü gelmiyor" |
| **Runbook** | Bir belirtiyi çözmek için düğümlerden oluşan yönlü graf. Versiyonludur. |
| **Düğüm (Node)** | Grafın tek adımı. Tipi vardır (§9.1). |
| **Kenar (Edge)** | Bir düğümden diğerine geçiş; bir cevap etiketine bağlıdır. |
| **Çözüm (Resolution)** | Terminal düğüm. Kök neden kodu + kapanış notu içerir. |
| **Eskalasyon** | Terminal düğüm. L2/tedarikçiye devir + özet şablonu. |
| **Oturum (Session)** | Tek bir arıza vakasının baştan sona kaydı. |
| **Sözlük (Lexicon)** | Cihaz terimleri, arıza fiilleri, stopword'ler, eşanlamlar. |
| **Varlık (Asset)** *(ops.)* | Cihaz envanteri; bağlamı daraltmak için (model, OS, dock var mı). |

**Önemli tasarım kararı:** Belirti ile runbook 1:1 değil, **N:1**. Beş farklı ifade ("monitör çalışmıyor", "ekran açılmıyor"...) aynı runbook'a düşer. Tersine, bir belirti kapsama göre farklı runbook'lara ayrışabilir (Windows / macOS).

---

## 6. Veri Şeması (SQLite)

```sql
PRAGMA journal_mode = WAL;
PRAGMA foreign_keys = ON;

-- ── İçerik ────────────────────────────────────────────────
CREATE TABLE categories (
  id        INTEGER PRIMARY KEY,
  code      TEXT NOT NULL UNIQUE,          -- 'donanim/goruntu'
  name      TEXT NOT NULL,
  parent_id INTEGER REFERENCES categories(id)
);

CREATE TABLE runbooks (
  id           INTEGER PRIMARY KEY,
  code         TEXT NOT NULL UNIQUE,       -- 'DSP-001'
  title        TEXT NOT NULL,
  summary      TEXT,
  category_id  INTEGER NOT NULL REFERENCES categories(id),
  severity     TEXT CHECK(severity IN ('dusuk','orta','yuksek','kritik')),
  os_scope     TEXT,                       -- JSON: ["windows","macos"]
  asset_scope  TEXT,                       -- JSON: ["laptop","masaustu"]
  entry_node   TEXT NOT NULL,              -- 'N10'
  version      INTEGER NOT NULL DEFAULT 1,
  status       TEXT CHECK(status IN ('taslak','yayinda','arsiv')),
  author       TEXT,
  reviewed_at  TEXT,
  review_due   TEXT,                       -- içerik bayatlama kontrolü
  content_hash TEXT NOT NULL,              -- kaynak YAML SHA-256
  updated_at   TEXT NOT NULL
);

CREATE TABLE aliases (
  id          INTEGER PRIMARY KEY,
  runbook_id  INTEGER NOT NULL REFERENCES runbooks(id) ON DELETE CASCADE,
  term        TEXT NOT NULL,               -- ham hali
  term_norm   TEXT NOT NULL,               -- normalize edilmiş (§8.1)
  weight      REAL NOT NULL DEFAULT 1.0,
  kind        TEXT CHECK(kind IN ('belirti','hata_kodu','urun','kisaltma'))
);
CREATE INDEX idx_alias_norm ON aliases(term_norm);

CREATE TABLE nodes (
  id             INTEGER PRIMARY KEY,
  runbook_id     INTEGER NOT NULL REFERENCES runbooks(id) ON DELETE CASCADE,
  key            TEXT NOT NULL,            -- 'N10' (runbook içinde tekil)
  type           TEXT NOT NULL CHECK(type IN
                   ('soru','talimat','olcum','karar','cozum','eskalasyon')),
  title          TEXT NOT NULL,
  body_md        TEXT,                     -- Markdown gövde
  verify_text    TEXT,                     -- "Nasıl anlarsın: LED beyaz yanar"
  risk           TEXT CHECK(risk IN ('dusuk','orta','yuksek')) DEFAULT 'dusuk',
  requires_admin INTEGER NOT NULL DEFAULT 0,
  requires_user_downtime INTEGER NOT NULL DEFAULT 0,
  est_seconds    INTEGER,
  rollback_md    TEXT,                     -- geri alma talimatı (riskli adımlar)
  media          TEXT,                     -- JSON: ["img/dp-port.png"]
  UNIQUE(runbook_id, key)
);

CREATE TABLE edges (
  id           INTEGER PRIMARY KEY,
  node_id      INTEGER NOT NULL REFERENCES nodes(id) ON DELETE CASCADE,
  label        TEXT NOT NULL,              -- "Hayır, hiç yanmıyor"
  target_key   TEXT NOT NULL,              -- 'N20'
  order_idx    INTEGER NOT NULL DEFAULT 0,
  condition    TEXT                        -- ops. ifade: 'os == "windows"'
);

CREATE TABLE resolutions (
  id            INTEGER PRIMARY KEY,
  node_id       INTEGER NOT NULL UNIQUE REFERENCES nodes(id) ON DELETE CASCADE,
  root_cause    TEXT NOT NULL,             -- 'GUC_KAYNAGI_ARIZA'
  closure_note  TEXT,
  followup_md   TEXT,                      -- "Kullanıcıya 24 saat sonra dön"
  part_required TEXT                       -- 'monitor_guc_adaptoru'
);

-- ── Arama indeksi ─────────────────────────────────────────
CREATE VIRTUAL TABLE fts_runbooks USING fts5(
  title, aliases, body, tags,
  runbook_id UNINDEXED,
  tokenize = "unicode61 remove_diacritics 2"
);

-- ── Telemetri (yerel) ─────────────────────────────────────
CREATE TABLE sessions (
  id             INTEGER PRIMARY KEY,
  started_at     TEXT NOT NULL,
  ended_at       TEXT,
  query_raw      TEXT NOT NULL,
  query_norm     TEXT NOT NULL,
  runbook_id     INTEGER REFERENCES runbooks(id),
  match_score    REAL,
  match_method   TEXT,                     -- 'alias_exact' | 'fts' | 'manuel'
  outcome        TEXT CHECK(outcome IN
                   ('cozuldu','eskalasyon','terk','cozulemedi')),
  resolution_id  INTEGER REFERENCES resolutions(id),
  duration_s     INTEGER,
  agent_ref      TEXT                      -- takma ad/hash, §12.3
);

CREATE TABLE session_steps (
  id          INTEGER PRIMARY KEY,
  session_id  INTEGER NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
  node_id     INTEGER NOT NULL REFERENCES nodes(id),
  answer      TEXT,
  entered_at  TEXT NOT NULL,
  dwell_s     INTEGER,
  order_idx   INTEGER NOT NULL
);

CREATE TABLE knowledge_gaps (
  id          INTEGER PRIMARY KEY,
  query_norm  TEXT NOT NULL UNIQUE,
  hit_count   INTEGER NOT NULL DEFAULT 1,
  best_score  REAL,
  first_seen  TEXT NOT NULL,
  last_seen   TEXT NOT NULL,
  status      TEXT DEFAULT 'acik'          -- acik | icerik_yazildi | gecersiz
);

-- ── Sözlük ────────────────────────────────────────────────
CREATE TABLE lexicon (
  id        INTEGER PRIMARY KEY,
  kind      TEXT CHECK(kind IN ('cihaz','ariza','stopword','esanlam','kisaltma')),
  term      TEXT NOT NULL,
  canonical TEXT,                          -- 'ekran' -> 'monitor'
  weight    REAL DEFAULT 1.0,
  UNIQUE(kind, term)
);
```

---

## 7. İçerik Formatı

Her runbook tek bir YAML dosyasıdır: `content/runbooks/DSP-001-monitor-goruntu-yok.yaml`

### 7.1 Şema

```yaml
code: DSP-001
title: "Monitörde görüntü yok"
summary: "Monitör hiç görüntü vermiyor veya 'No Signal' gösteriyor."
category: donanim/goruntu
severity: orta
status: yayinda
author: "albora"
reviewed_at: 2026-09-01
review_period_days: 180
scope:
  os: [windows, macos, linux]
  asset: [masaustu, laptop, dock]

aliases:
  - { term: "monitör çalışmıyor",  kind: belirti, weight: 1.0 }
  - { term: "ekran açılmıyor",     kind: belirti, weight: 1.0 }
  - { term: "görüntü gelmiyor",    kind: belirti, weight: 1.0 }
  - { term: "ekran siyah",         kind: belirti, weight: 0.9 }
  - { term: "no signal",           kind: hata_kodu, weight: 1.0 }
  - { term: "sinyal yok",          kind: hata_kodu, weight: 1.0 }
  - { term: "monitör kararıyor",   kind: belirti, weight: 0.7 }
  - { term: "display yok",         kind: belirti, weight: 0.8 }

entry: N10
nodes:
  - key: N10
    type: soru
    title: "Monitörün güç LED'i yanıyor mu?"
    body_md: |
      Monitörün ön yüzünde veya alt çerçevesindeki güç ışığına bak.
      Kullanıcıdan tarif etmesini iste: **renk** ve **sabit mi yanıp sönüyor mu**.
    est_seconds: 20
    edges:
      - { label: "Hayır, hiç yanmıyor",          to: N20 }
      - { label: "Evet, turuncu/yanıp sönüyor",  to: N40 }
      - { label: "Evet, beyaz/mavi sabit",       to: N60 }

  - key: N20
    type: talimat
    title: "Güç zincirini kontrol et"
    body_md: |
      1. Güç kablosunu monitör ve priz tarafında **çıkar, tekrar tak**.
      2. Uzatma/priz grubu kullanılıyorsa monitörü **doğrudan duvar prizine** bağla.
      3. Prizde başka bir cihazın çalıştığını doğrula.
    verify_text: "Güç LED'i yandı mı?"
    est_seconds: 90
    edges:
      - { label: "LED yandı, sorun çözüldü", to: N21 }
      - { label: "Hâlâ LED yok",             to: N30 }

  - key: N21
    type: cozum
    title: "Çözüldü — güç bağlantısı/priz kaynaklı"
    root_cause: GUC_BAGLANTI
    closure_note: "Gevşek güç kablosu veya arızalı uzatma."
    followup_md: "Arızalı uzatma grubu tespit edildiyse kullanımdan kaldır."

  - key: N30
    type: talimat
    title: "Güç adaptörünü/kabloyu çapraz test et"
    body_md: |
      Aynı model çalışan bir monitörün güç kablosunu (harici adaptörlü modellerde
      adaptörünü) tak. Harici adaptörlü modellerde adaptör LED'ini kontrol et.
    risk: dusuk
    est_seconds: 180
    edges:
      - { label: "Başka kabloyla açıldı", to: N31 }
      - { label: "Yine açılmadı",         to: N90 }

  - key: N31
    type: cozum
    title: "Çözüldü — arızalı güç kablosu/adaptörü"
    root_cause: GUC_KABLO_ARIZA
    part_required: monitor_guc_kablosu

  - key: N40
    type: soru
    title: "Ekranda 'No Signal' / 'Sinyal Yok' mesajı görünüyor mu?"
    body_md: |
      Turuncu veya yanıp sönen LED genelde **uyku modu** ya da **sinyal yok**
      demektir. Monitörün menü tuşuna basınca OSD menüsü açılıyorsa panel sağlamdır.
    edges:
      - { label: "Evet, sinyal yok yazıyor",      to: N50 }
      - { label: "Hayır, tamamen siyah, OSD de yok", to: N90 }
      - { label: "OSD açılıyor ama giriş yanlış",  to: N45 }

  - key: N45
    type: talimat
    title: "Monitör giriş kaynağını (input source) doğru porta al"
    body_md: |
      Monitör menüsünden **Input / Source** seçeneğine gir, kablonun fiziksel
      olarak takılı olduğu portu seç (HDMI-1, DP, USB-C...).
      Otomatik algılama varsa kapatıp elle seçmeyi dene.
    est_seconds: 60
    edges:
      - { label: "Görüntü geldi", to: N46 }
      - { label: "Gelmedi",       to: N50 }

  - key: N46
    type: cozum
    title: "Çözüldü — yanlış giriş kaynağı seçili"
    root_cause: YANLIS_INPUT_SOURCE

  - key: N50
    type: soru
    title: "Bilgisayar ile monitör arasında dock/adaptör var mı?"
    edges:
      - { label: "Evet, dock veya USB-C hub var", to: N70 }
      - { label: "Hayır, doğrudan bağlı",          to: N52 }

  - key: N52
    type: talimat
    title: "Video kablosunu yeniden bağla ve çapraz test et"
    body_md: |
      1. Kabloyu iki uçtan da çıkar, 10 sn bekle, sıkıca tak (DP'de klips mandalına dikkat).
      2. Bilgisayarda **farklı bir video portu** dene.
      3. **Farklı bir kablo** ile dene (özellikle uzun/ucuz HDMI kablolar).
      4. Monitörü **başka bir bilgisayara** bağlayarak monitörü izole et.
    verify_text: "Hangi kombinasyonda görüntü geldi?"
    est_seconds: 300
    edges:
      - { label: "Farklı kabloyla geldi",  to: N53 }
      - { label: "Farklı portla geldi",    to: N54 }
      - { label: "Başka PC'de monitör çalıştı, kendi PC'sinde çalışmıyor", to: N80 }
      - { label: "Başka PC'de de çalışmadı", to: N90 }

  - key: N53
    type: cozum
    title: "Çözüldü — arızalı video kablosu"
    root_cause: VIDEO_KABLO_ARIZA
    part_required: video_kablo

  - key: N54
    type: cozum
    title: "Çözüldü — arızalı video portu (monitör veya GPU tarafı)"
    root_cause: VIDEO_PORT_ARIZA
    followup_md: "Arızalı portu etiketle; GPU tarafıysa donanım kaydı aç."

  - key: N60
    type: soru
    title: "LED sabit yanıyor ama ekran karanlık — OSD menüsü açılıyor mu?"
    body_md: |
      Monitör menü tuşuna bas. Menü görünüyorsa panel ve arka aydınlatma çalışıyordur;
      sorun sinyal veya parlaklık tarafındadır.
    edges:
      - { label: "Menü görünüyor",       to: N61 }
      - { label: "Menü de görünmüyor",   to: N90 }

  - key: N61
    type: talimat
    title: "Parlaklık ve görüntüleme modunu kontrol et"
    body_md: |
      1. Monitör OSD'den parlaklığı yükselt (0'a çekilmiş olabilir).
      2. Windows'ta `Win + P` → **Genişlet / Yalnızca ikinci ekran** seçeneklerini dene.
      3. `Win + Ctrl + Shift + B` ile grafik sürücüsünü sıfırla (ekran 1 sn kararır).
      4. Ayarlar → Sistem → Ekran → **Algıla**.
    risk: dusuk
    est_seconds: 120
    edges:
      - { label: "Görüntü geldi", to: N62 }
      - { label: "Gelmedi",       to: N80 }

  - key: N62
    type: cozum
    title: "Çözüldü — görüntüleme modu / parlaklık ayarı"
    root_cause: EKRAN_AYARI

  - key: N70
    type: talimat
    title: "Dock / USB-C hub'ı izole et"
    body_md: |
      1. Dock'un **kendi güç adaptörünü** çıkar-tak (30 sn bekle).
      2. Monitörü dock yerine **doğrudan bilgisayara** bağla.
      3. Dock firmware sürümünü üretici aracıyla kontrol et.
      4. USB-C kablosunun **video (DP Alt Mode) destekli** olduğunu doğrula —
         sadece şarj kablosu görüntü taşımaz.
    risk: orta
    est_seconds: 300
    edges:
      - { label: "Doğrudan bağlantıda görüntü var", to: N71 }
      - { label: "Doğrudan bağlantıda da yok",      to: N52 }

  - key: N71
    type: cozum
    title: "Çözüldü — dock arızası / firmware / yanlış USB-C kablo"
    root_cause: DOCK_ARIZA
    followup_md: "Dock firmware güncellemesi planla veya dock'u değiştir."

  - key: N80
    type: talimat
    title: "Grafik sürücüsü ve güç profilini kontrol et"
    body_md: |
      1. Aygıt Yöneticisi → Görüntü bağdaştırıcıları → sarı ünlem var mı?
      2. Sürücüyü üretici sitesinden **temiz kurulum** ile yeniden yükle.
      3. Laptop'ta hibrit grafik varsa harici çıkışın hangi GPU'ya bağlı olduğunu doğrula.
      4. Güç seçeneklerinde **hızlı başlatmayı** kapatıp tam kapatma yap.
    risk: orta
    requires_admin: true
    rollback_md: "Sürücü sorun çıkarırsa Aygıt Yöneticisi → Sürücüyü geri al."
    est_seconds: 900
    edges:
      - { label: "Çözüldü", to: N81 }
      - { label: "Çözülmedi", to: N90 }

  - key: N81
    type: cozum
    title: "Çözüldü — grafik sürücüsü / güç yönetimi"
    root_cause: GPU_SURUCU

  - key: N90
    type: eskalasyon
    title: "L2 / donanım servisine eskale et"
    body_md: |
      Yerinde çözülemedi. Aşağıdaki özeti vakaya ekle ve donanım ekibine yönlendir.
    escalate_to: "L2-Donanim"
    summary_template: |
      Belirti: {{query_raw}}
      Runbook: {{runbook_code}} v{{version}}
      İzlenen adımlar:
      {{#each steps}}- {{title}} → {{answer}}
      {{/each}}
      Toplam süre: {{duration_min}} dk
      Sonuç: Çözülemedi. Monitör/GPU donanım testi gerekiyor.
```

### 7.2 Doğrulama kuralları (compile zamanında zorunlu)

Derleyici şu durumlarda **hata** verir:

- Ulaşılamayan düğüm (entry'den erişilemiyor)
- Terminal olmayan düğümde kenar yok (çıkmaz)
- `to:` hedefi tanımsız
- Döngü tespiti (aynı düğüme koşulsuz geri dönüş)
- `cozum` düğümünde `root_cause` eksik
- `risk: yuksek` olup `rollback_md` yok
- Aynı alias iki farklı runbook'ta tam eşleşmeyle tanımlı (belirsizlik)
- `review_due` geçmiş → **uyarı** (bayat içerik)

---

## 8. Türkçe Arama ve Eşleştirme Motoru

Bu, projenin en kritik ve en çok emek isteyen parçası. Türkçe eklemeli bir dil olduğu için düz `LIKE '%monitor%'` araması çalışmaz: "monitörüm", "monitörde", "monitörün" hepsi farklı dizeler.

### 8.1 Normalizasyon hattı

```
Ham sorgu: "MONİTORUM calismiyor!!"
   │
   ├─ 1. Unicode NFC normalizasyonu
   ├─ 2. Türkçe-duyarlı küçültme (İ→i, I→ı)      → "monitorum calismiyor!!"
   ├─ 3. Noktalama temizliği                      → "monitorum calismiyor"
   ├─ 4. ASCII-katlama (çift indeksleme)          → "monitorum calismiyor"
   │      Not: hem "monitör" hem "monitor" biçimi indekslenir; kullanıcı
   │      Türkçe karakter yazmadığında da eşleşme garanti olur.
   ├─ 5. Tokenizasyon                             → [monitorum, calismiyor]
   ├─ 6. Stopword çıkarma (benim, bir, bu, ve...)
   ├─ 7. Gövdeleme (stemming)                     → [monitor, calis+NEG]
   ├─ 8. Eşanlam genişletme (lexicon)             → [monitor, ekran, display, lcd]
   └─ 9. Niyet çıkarımı (§8.2)                    → intent = CALISMIYOR
```

**Gövdeleme seçenekleri (karşılaştırma):**

| Yöntem | Artı | Eksi | Karar |
|---|---|---|---|
| Kural tabanlı ek soyma (kendi yazdığımız) | Sıfır bağımlılık, hızlı, IT sözcük dağarcığına özel | Genel Türkçede zayıf | **v1'de bu** |
| Snowball Turkish stemmer | Hazır, hafif | Aşırı kesme yapabilir ("monitör"→"mon") | v1'de yedek |
| Zemberek (Java) | En doğru Türkçe morfoloji | JVM bağımlılığı, paketleme derdi | v2 opsiyonel |
| Yerel embedding (ör. küçük çok dilli model) | Anlamsal eşleşme, "yazı çıkmıyor"≈"yazıcı basmıyor" | ~100-400 MB model, CPU maliyeti | v2, opsiyonel eklenti |

**v1 kararı:** Kural tabanlı ek soyma + geniş alias sözlüğü. IT alanında kelime dağarcığı dar (belki 300 cihaz terimi, 80 arıza fiili) — bu iyi bir tahterevalli. Alias listesi zenginse morfolojiye az ihtiyaç duyulur.

### 8.2 Niyet (arıza fiili) sınıflandırması

Sözlükten çıkarılan arıza sinyalleri, aday runbook'ları daraltır:

| Sinyal sınıfı | Türkçe ifadeler |
|---|---|
| `CALISMIYOR` | çalışmıyor, bozuldu, arızalı, gitmiyor |
| `ACILMIYOR` | açılmıyor, başlamıyor, boot etmiyor, kalkmıyor |
| `GORUNTU_YOK` | görüntü gelmiyor, siyah ekran, no signal, sinyal yok |
| `YAVAS` | yavaş, donuyor, kasıyor, takılıyor, kilitleniyor |
| `BAGLANTI_YOK` | bağlanmıyor, internet yok, ağ gitmiyor, vpn düşüyor |
| `SES_YOK` | ses gelmiyor, mikrofon çalışmıyor, duyulmuyor |
| `YAZDIRMA` | yazdırmıyor, çıktı almıyor, kuyrukta kalıyor |
| `KIMLIK` | şifre kabul etmiyor, hesap kilitli, giriş yapamıyorum |
| `HATA_MESAJI` | hata veriyor, error, kod veriyor + [kod yakalama] |

Ayrıca **hata kodu deseni** regex ile yakalanır: `0x[0-9A-F]{8}`, `STOP CODE`, `Error \d+` → doğrudan `kind: hata_kodu` alias eşleşmesine gider ve çok yüksek ağırlık alır.

### 8.3 Skorlama

```
skor = 0.40 · BM25_norm(FTS5)
     + 0.25 · alias_tam_eslesme
     + 0.15 · niyet_uyumu
     + 0.10 · baglam_uyumu      (OS / cihaz tipi eşleşiyor mu)
     + 0.10 · basari_gecmisi    (bu runbook son 90 günde ne kadar çözdü)
```

`basari_gecmisi` bileşeni, kullanımın aramayı zamanla iyileştirmesini sağlar — çok kullanılan ve gerçekten çözen runbook'lar yukarı çıkar.

### 8.4 Güven eşikleri ve davranış

| Skor | Davranış |
|---|---|
| ≥ 0.75 | Runbook doğrudan açılır (üstte "farklı bir şey mi arıyordun?" bağlantısıyla) |
| 0.40 – 0.75 | En iyi 5 aday liste halinde, skor rozetiyle |
| 0.20 – 0.40 | **Netleştirme modu**: kategori ağacından daraltma soruları |
| < 0.20 | Sonuç yok → `knowledge_gaps` kaydı + serbest arama + kategori tarama |

### 8.5 Netleştirme (disambiguation)

Birden çok aday aynı cihaz terimini paylaşıyorsa, sistem onları **ayıran** özelliğe göre soru üretir:

> "bilgisayar açılmıyor" → 3 aday
> Netleştirme: **"Kasadan fan sesi / ışık geliyor mu?"**
> - Hayır, hiç tepki yok → `PWR-001 Bilgisayar hiç açılmıyor`
> - Evet ama ekran yok → `DSP-001 Monitörde görüntü yok`
> - Evet, logo geliyor ama Windows açılmıyor → `BOOT-001 İşletim sistemi başlamıyor`

Bu sorular elle yazılabilir (`disambiguation` bloğu) veya adayların ilk düğümlerinin farklılığından otomatik türetilebilir. v1'de **elle yazılan** yaklaşım — daha öngörülebilir.

---

## 9. Karar Ağacı Yürütme Motoru

### 9.1 Düğüm tipleri

| Tip | Anlam | Kenar gerekir mi | UI |
|---|---|---|---|
| `soru` | Bilgi toplar, dallanır | Evet (≥2) | Seçenek butonları |
| `talimat` | Yapılacak iş + doğrulama | Evet (≥1) | Adım listesi + "sonuç ne oldu?" |
| `olcum` | Sayısal/kategorik değer girilir | Evet | Giriş alanı + koşullu kenar |
| `karar` | Otomatik dallanma (bağlamdan) | Evet | Gösterilmez, sessiz geçilir |
| `cozum` | Terminal — başarı | Hayır | Yeşil kapanış kartı |
| `eskalasyon` | Terminal — devir | Hayır | Turuncu kart + özet üretimi |

### 9.2 Yürütme durumu

```json
{
  "session_id": 412,
  "runbook": "DSP-001",
  "version": 3,
  "current_node": "N52",
  "path": ["N10", "N40", "N50", "N52"],
  "answers": { "N10": "Evet, turuncu/yanıp sönüyor", "N40": "Evet, sinyal yok yazıyor" },
  "context": { "os": "windows", "asset": "laptop", "dock": true },
  "started_at": "2026-09-12T09:14:03",
  "elapsed_s": 284
}
```

### 9.3 Motor davranışları

- **Geri alma:** Yanlış cevap verildiğinde `path` bir adım geri sarılır; sonraki cevaplar geçersizleşir.
- **Atlama:** Teknisyen "bunu zaten denedim" derse düğüm `atlandi` olarak işaretlenir, kenar seçtirilerek devam edilir. Atlanan adımlar oturum kaydında ayrı tutulur (eskalasyonda önemli).
- **Döngü koruması:** Bir düğüm aynı oturumda 2 kereden fazla ziyaret edilirse uyarı verilir.
- **Versiyon sabitleme:** Oturum başladığı runbook versiyonuna sabitlenir; ortada içerik güncellense bile yol değişmez.
- **Bağlam ön-doldurma:** Varlık envanteri bağlıysa (`asset_tag` girildiyse) OS/model bilinir, `karar` düğümleri otomatik geçilir.

---

## 10. Kullanıcı Arayüzü Akışı

### 10.1 Ekranlar

```
[1] ARAMA
    ┌────────────────────────────────────────────────┐
    │  🔍  monitorum calismiyor                      │
    ├────────────────────────────────────────────────┤
    │  ●●●●●○  %91   DSP-001 Monitörde görüntü yok   │
    │          Donanım › Görüntü · ~6 dk · 14 adım   │
    │  ●●●○○○  %48   DSP-004 Ekran titriyor          │
    │  ●●○○○○  %31   DOC-002 Dock bağlıyken ekran... │
    ├────────────────────────────────────────────────┤
    │  Son kullanılanlar · Kategoriye göz at         │
    └────────────────────────────────────────────────┘

[2] RUNBOOK YÜRÜTME
    ┌────────────────────────────────────────────────┐
    │  DSP-001 Monitörde görüntü yok      ⏱ 02:14    │
    │  ●─●─●─○────────────────  Adım 3/~7            │
    ├────────────────────────────────────────────────┤
    │  Bilgisayar ile monitör arasında dock var mı?  │
    │                                                 │
    │  ℹ️  USB-C hub'lar da dock sayılır.             │
    │                                                 │
    │  [ Evet, dock/hub var ]  [ Hayır, doğrudan ]   │
    │                                                 │
    │  ← Geri   ⤼ Bu adımı atla   ⚑ İçerik hatalı   │
    └────────────────────────────────────────────────┘

[3] SONUÇ
    ┌────────────────────────────────────────────────┐
    │  ✅ Çözüldü — Arızalı video kablosu            │
    │  Kök neden: VIDEO_KABLO_ARIZA                  │
    │  Süre: 5 dk 40 sn · 5 adım                     │
    │  Parça talebi: video_kablo                     │
    ├────────────────────────────────────────────────┤
    │  📋 Vaka özetini kopyala                       │
    │  📝 Bu runbook eksikti, not bırak              │
    └────────────────────────────────────────────────┘
```

### 10.2 UX kuralları

- **Klavye öncelikli.** `/` arama, `1-9` seçenek, `Backspace` geri, `Esc` çıkış. Telefondaki teknisyen fareye uzanmaz.
- **Tek ekranda tek soru.** Kaydırma gerekmesin.
- **Risk görünür olsun.** `risk: yuksek` veya `requires_user_downtime: true` adımlarda kırmızı şerit + "Kullanıcıyı bilgilendir" uyarısı.
- **Her adımda kaçış yolu.** "Bu adım işe yaramıyor / anlamadım" → alternatif yol veya eskalasyon.
- **İçerik hatası bildirimi** her ekranda tek tıkla — editöre iş kuyruğu oluşturur.

---

## 11. Telemetri, Raporlama ve İçerik Kalite Döngüsü

Tüm veri **yereldedir**, dışarı çıkmaz. Amaç içeriği iyileştirmek, personeli izlemek değil.

### 11.1 Yönetici raporları

| Rapor | Sorduğu soru | Aksiyon |
|---|---|---|
| **Bilgi boşlukları** | Hangi aramalar sonuçsuz kaldı? | Yeni runbook yaz |
| **Ölü uçlar** | Hangi düğümlerde oturum terk ediliyor? | O dalı yeniden yaz |
| **Eskalasyon isabetleri** | Hangi runbook'lar sık eskale ediliyor? | Dalları derinleştir |
| **Atlanan adımlar** | Hangi adımlar hep atlanıyor? | Gereksizse sil |
| **Süre dağılımı** | Hangi adım beklenenden uzun sürüyor? | Talimatı netleştir |
| **Bayat içerik** | `review_due` geçmiş runbook'lar | Gözden geçirme ata |
| **Kök neden dağılımı** | En sık kök nedenler neler? | Kalıcı düzeltme (ör. tüm ucuz HDMI kabloları değiştir) |

Son madde önemli: bu uygulama sadece arıza çözmez, **arızanın tekrarını önleyecek veriyi** de üretir. "Son 90 günde 23 vaka `VIDEO_KABLO_ARIZA`" cümlesi bir satın alma kararına dönüşür.

---

## 12. Güvenlik ve Gizlilik

### 12.1 Tehdit yüzeyi

| Tehdit | Önlem |
|---|---|
| Yerel HTTP sunucusuna ağdan erişim | Yalnız `127.0.0.1` bağlama; `0.0.0.0` yasak. Rastgele port + oturum token'ı. |
| CSRF (tarayıcıdan yerel API'ye) | `Origin`/`Host` doğrulaması, `SameSite=Strict` çerez, CSRF token |
| Runbook Markdown'ında XSS | Markdown render'da HTML kapalı; sıkı CSP (`script-src 'self'`) |
| YAML deserialization | `yaml.safe_load` zorunlu; asla `yaml.load` |
| İçerik kurcalama (yetkisiz düzenleme) | Derlenmiş DB'de `content_hash`; başlangıçta bütünlük kontrolü. Editör rolü ayrı. |
| Veritabanı hırsızlığı | DB'de kimlik bilgisi/parola **asla** tutulmaz (§12.2) |
| Path traversal (medya dosyaları) | Medya yolları allowlist + `media/` dizinine kilitli |

### 12.2 İçerik hijyeni — kesin kural

Runbook'lar **asla** şunları içermez: yönetici parolaları, lisans anahtarları, API token'ları, VPN ön-paylaşımlı anahtarları, servis hesabı bilgileri. Bunlar parola kasasına (KeePass/Vaultwarden/Bitwarden) referansla anılır:

```yaml
body_md: |
  Yerel yönetici hesabıyla giriş yap.
  Kimlik bilgisi: parola kasasında **"Local-Admin-Workstations"** kaydı.
```

Derleyici, gövdede parola/token benzeri desen (`password:`, `AKIA[0-9A-Z]{16}`, uzun base64) bulursa **derlemeyi durdurur**. Bu bir "güzel olur" değil, zorunlu kalite kapısıdır — bilgi tabanları sızıntının klasik kaynağıdır.

### 12.3 Kişisel veri (KVKK)

- Oturum kayıtlarında son kullanıcı adı/e-postası **tutulmaz**; yalnız cihaz etiketi ve kategori.
- `agent_ref` alanı takma addır veya yereldir; performans değerlendirme amaçlı kullanılmayacağı ekip içinde yazılı belirtilir.
- Serbest metin sorgusu PII içerebilir ("ahmet yılmaz outlook açamıyor") → derlemede basit maskeleme: ad-soyad deseni `[KULLANICI]` ile değiştirilir.
- Yerel veri saklama süresi yapılandırılabilir (varsayılan 365 gün), sonrası otomatik temizlik.

### 12.4 Otomatik komut çalıştırma (v2 — dikkatli tasarım)

İleride "bu adımı otomatik çalıştır" özelliği eklenirse:

- Yalnız **allowlist'teki** salt-okunur teşhis komutları (`ipconfig /all`, `Get-NetAdapter`, `systeminfo`)
- Kabuk enterpolasyonu yok — argümanlar dizi olarak, `shell=True` yasak
- Değişiklik yapan her komut açık onay + `rollback_md` zorunlu
- Çalıştırılan her komut oturum kaydına yazılır
- Runbook YAML'ında komut alanı, imzalı içerik paketlerinde yer alabilir (imzasız içerikte komut çalıştırılmaz)

---

## 13. Teknoloji Seçenekleri ve Öneri

| | **A: Python + FastAPI + SQLite + yerel web UI** | **B: Tauri/Electron masaüstü** | **C: Saf CLI/TUI** |
|---|---|---|---|
| Geliştirme hızı | Yüksek | Orta | Çok yüksek |
| Dağıtım | PyInstaller tek exe | Tek installer, en cilalı | Tek exe, en küçük |
| UI kalitesi | İyi (tarayıcı) | En iyi | Sınırlı |
| Öğrenme maliyeti | Düşük (Python bilgin var) | Rust/JS gerekir | Düşük |
| Çok kullanıcı (ekip içi) | Kolay (LAN'a açılabilir) | Zor | Zor |
| Paket boyutu | ~40 MB | ~90 MB | ~15 MB |

**Öneri: A.** Gerekçe:

- Python + SQLite + FTS5 kombinasyonu bu iş için neredeyse ideal; FTS5 SQLite'ın içinde, ek bağımlılık yok.
- Aynı kod tabanı hem tek kişilik masaüstü (loopback) hem ekip içi yerel sunucu (LAN) olarak çalışabilir — ileride şart değiştirse yeniden yazmazsın.
- Sen zaten Python ile çalışıyorsun; Sigma lab projenle araç zinciri ortak olur.
- CLI (`C`) ise **ayrı bir ürün değil**, aynı çekirdeğin ikinci arayüzü olarak yazılır (`helpdesk search "monitör"`). Bu, ileride betiklere gömmeyi de mümkün kılar.

### 13.1 Bağımlılıklar (hedef: minimum)

```
fastapi, uvicorn      — API + yerel sunucu
pydantic              — şema doğrulama (runbook YAML → model)
ruamel.yaml           — YAML (yorum koruyan, editör için önemli)
jinja2                — sunucu tarafı şablon / eskalasyon özeti
markdown-it-py        — güvenli Markdown render
rich / typer          — CLI
pytest                — test
```

SQLite standart kütüphanede. ORM kullanılmaz — sorgular elle yazılır, FTS5 üzerinde kontrol için daha iyi.

### 13.2 Dizin yapısı

```
helpdesk-guide/
├─ content/
│  ├─ runbooks/          DSP-001-monitor-goruntu-yok.yaml ...
│  ├─ lexicon/           cihazlar.yaml, arizalar.yaml, stopwords.yaml
│  └─ media/             img/, pdf/
├─ src/helpdesk/
│  ├─ core/
│  │  ├─ normalize.py    Türkçe normalizasyon + gövdeleme
│  │  ├─ matcher.py      skorlama, eşikler, netleştirme
│  │  ├─ engine.py       karar ağacı yürütücüsü
│  │  └─ session.py      oturum durumu + kayıt
│  ├─ content/
│  │  ├─ schema.py       pydantic modelleri
│  │  ├─ compiler.py     YAML → SQLite
│  │  └─ validator.py    graf doğrulama, gizli-bilgi taraması
│  ├─ db/
│  │  ├─ schema.sql
│  │  └─ repo.py
│  ├─ api/               routes/*.py
│  ├─ web/               templates/, static/
│  └─ cli.py
├─ tests/
│  ├─ test_normalize.py  (Türkçe kenar durumları — kritik)
│  ├─ test_matcher.py    (altın sorgu seti)
│  └─ test_engine.py
└─ data/helpdesk.db
```

### 13.3 Test stratejisi — "altın sorgu seti"

Arama kalitesi ancak ölçülebilirse iyileşir. `tests/golden_queries.yaml`:

```yaml
- { query: "monitorum calismiyor",        expect: DSP-001, min_rank: 1 }
- { query: "ekran siyah",                 expect: DSP-001, min_rank: 1 }
- { query: "MONİTÖR AÇILMIYOR",           expect: DSP-001, min_rank: 1 }
- { query: "no signal yaziyor",           expect: DSP-001, min_rank: 1 }
- { query: "dock takınca görüntü gitti",  expect: DOC-002, min_rank: 2 }
- { query: "yazıcı kağıt çekmiyor",       expect: PRN-004, min_rank: 1 }
```

CI'da her içerik değişikliğinde koşar. Recall@1 ve Recall@3 metrikleri raporlanır. Yeni runbook eklendiğinde eski sorgular bozulursa hemen görülür.

---

## 14. Paketleme ve Dağıtım

- **Tek dosya çalıştırılabilir:** PyInstaller `--onefile`; içinde derlenmiş `helpdesk.db` gömülü.
- **İlk çalıştırma:** DB'yi `%LOCALAPPDATA%\helpdesk-guide\` altına açar, tarayıcıyı `http://127.0.0.1:<port>/?t=<token>` ile başlatır.
- **İçerik güncellemesi:** `content-2026-09.hgpack` (imzalı zip) dosyası uygulamaya sürüklenir → doğrulanır → DB güncellenir. Uygulamayı yeniden kurmaya gerek yok.
- **Ekip içi mod:** Aynı ikili `--serve 0.0.0.0:8080 --auth` ile küçük bir ekip sunucusu olur; içerik merkezden güncellenir.

---

## 15. Yol Haritası

### Faz 0 — İskelet (1 hafta)
SQLite şeması, YAML şeması, derleyici, 3 örnek runbook, CLI ile arama ve düz yürütme. **Çıktı:** terminalden uçtan uca çalışan demo.

### Faz 1 — MVP (2-3 hafta)
Türkçe normalizasyon + FTS5 + skorlama · web UI (arama, yürütme, sonuç) · oturum kaydı · eskalasyon özeti · 15-20 runbook. **Çıktı:** gerçek bir help desk masasında kullanılabilir.

### Faz 2 — Olgunlaşma (3-4 hafta)
Netleştirme modu · içerik editörü UI'ı · raporlar (bilgi boşlukları, ölü uçlar) · altın sorgu seti + CI · içerik paketi imzalama · 50+ runbook.

### Faz 3 — Genişleme
Ekip sunucusu modu + rol tabanlı erişim · varlık envanteri entegrasyonu · ticket sistemi adaptörü (Jira/GLPI/osTicket) · opsiyonel yerel embedding ile anlamsal arama · son kullanıcı self-servis modu (riskli adımlar gizlenmiş).

---

## 16. Başarı Metrikleri

| Metrik | Hedef (6 ay) | Ölçüm |
|---|---|---|
| Arama isabet oranı (Recall@1) | ≥ %85 | Altın sorgu seti |
| İlk temasta çözüm oranı | %55 → %75 | Oturum sonuçları |
| Ortalama çözüm süresi | −%30 | `sessions.duration_s` |
| Sonuçsuz arama oranı | ≤ %8 | `knowledge_gaps` / toplam |
| Runbook kapsamı | Vakaların %80'i | Kategori dağılımı |
| İçerik tazeliği | %90'ı `review_due` içinde | Bayat içerik raporu |

---

## 17. Riskler

| Risk | Etki | Azaltma |
|---|---|---|
| **İçerik yazılmaz, proje boş kalır** (en büyük risk) | Kritik | Faz 0'da 3 runbook yaz, "en sık 20 arıza" listesiyle başla. Kod değil içerik darboğazdır. |
| Türkçe arama beklendiği gibi çalışmaz | Yüksek | Altın sorgu seti baştan kurulur; alias listesi morfolojiyi telafi eder |
| Ağaçlar çok derinleşir, teknisyen sıkılır | Orta | Hedef: çözüme ≤7 adım. Derinlik metriği derlemede uyarı verir |
| İçerik bayatlar (Windows sürümü değişir) | Orta | `review_due` + bayat içerik raporu |
| Teknisyen "zaten biliyorum" deyip kullanmaz | Orta | Değeri eskalasyon özetinde göster — o özet elle yazılırsa 5 dk sürer |
| Kapsam şişmesi (ticket sistemi yazmaya kalkışmak) | Orta | v1 kapsam dışı listesine sadık kal (§1.4) |

---

## 18. Verilen Kararlar

| # | Konu | Karar | Tasarıma etkisi |
|---|---|---|---|
| 1 | Dağıtım | **Açık kaynak, GitHub.** Tek kullanıcı yerelde çalıştırır. | Ekip sunucusu / rol modeli / kimlik doğrulama **kapsam dışı**. Yerine paketleme, dokümantasyon ve katkı akışı öne çıkıyor (§19). |
| 2 | Arayüz | **Yerel web UI** (FastAPI + loopback), CLI ikinci arayüz olarak aynı çekirdek üstünde. | §13 Seçenek A onaylandı. |
| 3 | İçerik | **Sıfırdan yazılacak.** | İçe aktarma aracı kapsam dışı. Bunun yerine yazım şablonu, kalite kapıları ve ilk 20 runbook planı (§20). |

### Hâlâ açık

4. **Repo ve UI dili.** Şema çok dilli, ama v1'i tek dilde bitirmek daha hızlı. Önerim: **arayüz + README İngilizce, içerik Türkçe başlasın**, `lang: en` runbook'lara kapı açık kalsın (§19.5).
5. **Lisans ikilisi.** Kod ve içerik ayrı lisanslanmalı (§19.2).
6. **Varlık envanteri entegrasyonu.** Kapsam dışı bırakıldı; `context` alanı elle doldurulur.

---

## 19. Açık Kaynak Proje Tasarımı

Bu bölüm, projenin GitHub'da yaşayan bir proje olmasından doğan gereksinimleri kapsar. Kapalı bir iç araçtan farkı: **yabancı biri 5 dakikada kurup çalıştırabilmeli**, ve **kendi içeriğini eklemek kod bilmeden mümkün olmalı**.

### 19.1 Motor / içerik ayrımı — en kritik karar

Her kurumun runbook'u kendine özeldir ve çoğu **gizlidir** (iç sistem adları, sunucu isimleri, süreçler). Bu yüzden mimari baştan üç katmana ayrılır:

```
helpdesk-guide/
├─ src/helpdesk/          ← MOTOR   (MIT, herkese açık)
├─ content/               ← ÇEKİRDEK İÇERİK (CC BY-SA, herkese açık, jenerik)
│  └─ runbooks/tr/*.yaml     "Monitörde görüntü yok" gibi evrensel arızalar
└─ content-local/         ← ÖZEL İÇERİK (.gitignore'da, asla commit edilmez)
   └─ runbooks/*.yaml        "Acme VPN bağlanmıyor", iç prosedürler
```

Derleyici her iki dizini de okur, `content-local/` varsa üstüne biner (override). Böylece:

- Bir kurum projeyi klonlar, kendi gizli runbook'larını `content-local/`'e yazar, upstream güncellemelerini çakışmasız alır.
- Sen çekirdek içeriği herkesle paylaşırken kendi notlarını gizli tutabilirsin.
- `.gitignore` içinde `content-local/` **ilk satır** olmalı; ayrıca pre-commit hook ile korunmalı (kaza ile `git add -f` yapılmasına karşı uyarı).

### 19.2 Lisanslama

| Varlık | Lisans | Gerekçe |
|---|---|---|
| Kod (`src/`) | **MIT** | En az sürtünme, kurumsal benimseme kolay. Apache-2.0 patent koruması istersen alternatif. |
| İçerik (`content/`) | **CC BY-SA 4.0** | Runbook'lar yazılım değil, dokümantasyon. SA maddesi, iyileştirmelerin geri dönmesini teşvik eder. |
| Medya (`media/`) | CC BY-SA 4.0 | Ekran görüntülerinde **kurum logosu / gerçek kullanıcı adı olmamalı** — §12.3 taraması buraya da uygulanır. |

`LICENSE` (MIT) + `content/LICENSE` (CC BY-SA) olarak iki dosya, README'de açıkça belirtilir.

### 19.3 Depo iskeleti

```
.github/
├─ workflows/
│  ├─ ci.yml              lint + pytest + içerik derleme + altın sorgu seti
│  ├─ content-check.yml   sadece content/ değişince: şema + sır taraması + graf doğrulama
│  └─ release.yml         etiket atılınca 3 platform için tek-dosya binary + PyPI
├─ ISSUE_TEMPLATE/
│  ├─ bug_report.yml
│  ├─ runbook_request.yml    "şu arıza için runbook lazım" — knowledge_gaps'in dış karşılığı
│  └─ runbook_error.yml      "şu adım yanlış/eksik"
└─ PULL_REQUEST_TEMPLATE.md
README.md            İngilizce, demo GIF, 3 satırlık kurulum
README.tr.md         Türkçe
CONTRIBUTING.md      kod katkısı
CONTRIBUTING-CONTENT.md   ← runbook yazma rehberi (asıl önemli olan bu)
CODE_OF_CONDUCT.md
SECURITY.md          güvenlik açığı bildirim adresi/süreci
CHANGELOG.md         Keep a Changelog formatı
LICENSE / content/LICENSE
```

### 19.4 İçerik katkı akışı (kod bilmeyenler için)

Bir runbook katkısının kod katkısı kadar zor olmaması gerekir. Hedef akış:

1. Katkıcı `content/runbooks/tr/_TEMPLATE.yaml` dosyasını kopyalar.
2. Doldurur, PR açar.
3. **CI otomatik doğrular:** şema, graf bütünlüğü (çıkmaz/ulaşılamaz düğüm), sır taraması, altın sorgu regresyonu.
4. CI, PR yorumuna **ASCII karar ağacı diyagramı** basar — inceleyen kişi YAML okumadan mantığı görür:

```
N10 Güç LED'i yanıyor mu?
 ├─ Hayır ──────────► N20 Güç zinciri
 │                     ├─ Yandı ─► ✅ N21 GUC_BAGLANTI
 │                     └─ Yok ───► N30 Çapraz test
 ├─ Turuncu ────────► N40 No Signal?
 └─ Beyaz sabit ────► N60 OSD açılıyor mu?
```

5. İnsan incelemesi: teknik doğruluk + §20.3 kontrol listesi.

Ayrıca `helpdesk new` CLI komutu interaktif bir sihirbazla iskelet YAML üretir — boş sayfa korkusunu kaldırır.

### 19.5 Çok dillilik

Şema baştan hazır tutulur, v1'de tek dil doldurulur:

- Runbook dosya yolu dili taşır: `content/runbooks/tr/`, `content/runbooks/en/`
- `code` alanı dilden bağımsızdır (`DSP-001`) → aynı arıza farklı dillerde aynı koda bağlanır, raporlar birleşir
- Arayüz metinleri `src/helpdesk/web/locales/{tr,en}.json`
- FTS5 indeksi dil başına ayrı tablo (normalizasyon kuralları dile özgüdür — Türkçe `i/ı` mantığı İngilizcede yanlış sonuç verir)

### 19.6 Kurulum deneyimi — "5 dakika kuralı"

README'nin ilk bloğu bu kadar olmalı:

```bash
pipx install helpdesk-guide
helpdesk serve          # tarayıcı açılır, hazır
```

Alternatifler: GitHub Releases'tan tek dosya binary (Windows/macOS/Linux), `docker run`, ve kaynaktan kurulum. Proje ilk denemede çalışmazsa ikinci bir şans olmaz.

### 19.7 Açık kaynak projede güvenlik hijyeni

Bu maddeler hem doğru mühendislik hem de deponun kendisi hakkında bir şey söylüyor:

- `pip-audit` / Dependabot CI'da; bağımlılıklar `requirements.lock` ile sabitli
- Release artifact'ları için **SBOM** (CycloneDX) ve SHA-256 checksum yayını
- `SECURITY.md` ile sorumlu açık bildirimi süreci
- Gizli bilgi taraması iki katmanlı: `gitleaks` (depo geneli) + derleyicinin kendi runbook taraması (§12.2)
- Yerel sunucu varsayılanı `127.0.0.1`; `--host` ile dışarı açmak açık bir bayrak gerektirir ve konsola uyarı basar

### 19.8 Sürümleme

Motor ve içerik ayrı sürümlenir: `engine v1.2.0` (SemVer) ve `content-tr 2026.09` (takvim). Derleyici uyumluluğu `min_engine_version` alanıyla kontrol eder.

---

## 20. İçerik Başlangıç Planı

Sıfırdan yazılacağı için bu bölüm projenin gerçek zorluğunu ele alıyor. **Kod 3 haftada biter, içerik hiç bitmez** — o yüzden içerik üretimi bir süreç olarak tasarlanmalı.

### 20.1 Yazım sırası

Önce **5 runbook** yaz, motoru bunlarla doğrula, sonra hızlan. İlk beş, birbirinden farklı yapıda seçilmeli ki motorun her özelliğini zorlasın:

| Sıra | Runbook | Neyi test eder |
|---|---|---|
| 1 | `DSP-001` Monitörde görüntü yok | Derin dallanma, çapraz test mantığı |
| 2 | `NET-001` İnternete bağlanamıyor | `karar` düğümü (kablolu/kablosuz otomatik dallanma) |
| 3 | `ACC-001` Hesap kilitli / parola kabul etmiyor | Yetki gerektiren adım, parola kasası referansı (§12.2) |
| 4 | `PRN-001` Yazıcı çıktı vermiyor | Uzun düz zincir, az dallanma |
| 5 | `SEC-001` Şüpheli e-posta bildirimi | Eskalasyon ağırlıklı, "hiçbir şey yapma, ilet" akışı |

### 20.2 İlk sürüm kapsamı (v1 hedefi: 20 runbook)

**Donanım / Görüntü**
- `DSP-001` Monitörde görüntü yok
- `DSP-002` Ekran titriyor / çözünürlük bozuk
- `DOC-001` Dock'a takınca ekran/USB çalışmıyor

**Güç ve önyükleme**
- `PWR-001` Bilgisayar hiç açılmıyor
- `PWR-002` Laptop şarj olmuyor
- `BOOT-001` Windows açılmıyor / mavi ekran

**Performans**
- `PRF-001` Bilgisayar çok yavaş / donuyor
- `PRF-002` Disk doldu uyarısı

**Ağ**
- `NET-001` İnternete bağlanamıyor
- `NET-002` Wi-Fi bağlanmıyor / sürekli düşüyor
- `VPN-001` VPN bağlanmıyor veya kopuyor

**Yazıcı**
- `PRN-001` Yazıcı çıktı vermiyor / kuyrukta kalıyor
- `PRN-002` Ağ yazıcısı bulunamıyor / eklenemiyor

**Hesap ve erişim**
- `ACC-001` Hesap kilitli / parola kabul etmiyor
- `ACC-002` MFA cihazı değişti, giriş yapılamıyor
- `ACC-003` Paylaşılan klasöre erişilemiyor

**Uygulama**
- `APP-001` Outlook açılmıyor / posta gönderilemiyor
- `APP-002` Teams/toplantıda ses veya mikrofon yok

**Çevre birimi**
- `PER-001` Klavye/mouse veya USB aygıtı tanınmıyor

**Güvenlik**
- `SEC-001` Şüpheli e-posta (phishing) bildirimi
- `SEC-002` Antivirüs uyarısı / dosya karantinaya alındı

Son iki madde çoğu help desk bilgi tabanında eksiktir; hem gerçek bir boşluğu doldurur hem de projenin ayırt edici tarafı olur. `SEC-001`'in çıktısı genelde "çöz" değil **"kullanıcıya hiçbir şeye tıklatma, başlığı ilet, güvenlik ekibine eskale et"** olmalı — yanlış tasarlanırsa teknisyeni zararlı eki açmaya iten bir akış yazılmış olur.

### 20.3 Runbook "bitti" tanımı (Definition of Done)

Bir runbook ancak şunların hepsi sağlanınca `status: yayinda` olur:

- [ ] En az 5 gerçekçi alias, hem Türkçe karakterli hem karaktersiz varyantlarıyla
- [ ] Çözüme ulaşan en uzun yol **≤ 7 adım**
- [ ] Her `talimat` düğümünde `verify_text` dolu
- [ ] Her dal ya `cozum` ya `eskalasyon` ile bitiyor
- [ ] En az bir `eskalasyon` düğümü var (her arıza yerinde çözülmez)
- [ ] `risk: yuksek` adımlarda `rollback_md` dolu
- [ ] Parola, anahtar, iç sunucu adı yok (derleyici taraması geçti)
- [ ] Altın sorgu setine en az 3 sorgu eklendi ve Recall@1 sağlanıyor
- [ ] `review_period_days` ayarlandı
- [ ] Yazan kişi dışında biri ağacı baştan sona yürüdü

### 20.4 Yazım disiplini

- **Günde 1 runbook** hedefiyle 20 iş günü. Tek oturuşta 20 tane yazmaya çalışmak, 20'sinin de yarım kalması demektir.
- **Gerçek vakadan yaz.** Hafızadan yazılan runbook eksik olur. Her çözdüğün arızayı çözerken not al, akşam runbook'a çevir.
- **Önce iskelet, sonra gövde.** Düğümleri ve dalları çıkar, `body_md`'leri ikinci turda doldur.
- **Kendi runbook'unu kullan.** Yazdığın ağacı bir sonraki gerçek vakada takip et; ilk 3 dakikada eksikleri görürsün.

---

## 21. Güncellenmiş Yol Haritası

| Faz | Süre | Çıktı | Bitiş kriteri |
|---|---|---|---|
| **0 — İskelet** | 1 hafta | Şema, derleyici, doğrulayıcı, CLI arama + yürütme, 2 runbook | `helpdesk search "monitör"` terminalden çözüme ulaştırıyor |
| **1 — Arama** | 1 hafta | Türkçe normalizasyon, FTS5, skorlama, altın sorgu seti, 5 runbook | Recall@1 ≥ %80 |
| **2 — Web UI** | 1-2 hafta | Arama / yürütme / sonuç ekranları, oturum kaydı, eskalasyon özeti | Uçtan uca tarayıcıdan kullanılabiliyor |
| **3 — İçerik** | 4 hafta | 20 runbook, `_TEMPLATE.yaml`, `helpdesk new` sihirbazı | v1 kapsamı tamam |
| **4 — Yayın** | 1 hafta | README + demo GIF, CI, release binary'leri, lisanslar, CONTRIBUTING | `v1.0.0` etiketi, kurulum 5 dakikada |
| **5 — Sonrası** | — | Netleştirme modu, içerik editörü UI, raporlar, EN içerik, opsiyonel anlamsal arama | Topluluk katkısı gelmeye başladı |

Faz 3 en uzun ve en çok terk edilen faz. Buradan sağ çıkmanın yolu, Faz 2'yi bitirir bitirmez aracı **gerçekten kullanmaya başlamak** — kullanmadığın bir bilgi tabanını doldurmaya devam edemezsin.

---

## 22. Ölçek: 5.000 Kayda Giden Yol

Hedef 20 runbook'tan 5.000'e çıkınca bu artık "aynı şeyden daha fazla yapmak" değil, **başka bir ürün** oluyor. Bu bölüm neyin değişmesi gerektiğini anlatıyor.

### 22.1 Önce dürüst matematik

| İçerik tipi | Yazma + test süresi | 5.000 adet |
|---|---|---|
| Tam karar ağacı (15-20 düğüm) | 2-4 saat | **10.000-20.000 saat ≈ 5-10 kişi-yıl** |
| Doğrusal kontrol listesi (5-8 adım) | 20-40 dk | ~2.500 saat ≈ 1,5 kişi-yıl |
| Referans kaydı (belirti → neden → çözüm) | 5-15 dk | ~800 saat ≈ 6 ay |

Tek kişi, günde 1 karar ağacı yazarak 5.000'e **20 yılda** ulaşır. Yani "5.000 runbook" hedefi bu haliyle ulaşılabilir değil — ama **"5.000 kayıt"** hedefi ulaşılabilir. Fark, kayıtların hepsinin karar ağacı olmak zorunda olmamasında.

### 22.2 Zaten karar ağacı istemiyorsun

Bu bir ödün değil, daha doğru tasarım. Karar ağacı, **belirsizliği daraltmak** için vardır: "monitör çalışmıyor" 6 farklı kök nedene işaret eder, ağaç bunları eler. Ama nadir sorunlar genelde belirsiz değildir:

> `0x0000007B` STOP kodu → depolama denetleyicisi modu değişmiş → BIOS'ta AHCI/RAID ayarını geri al.

Burada dallanacak bir şey yok. Bu sorunu 12 düğümlük bir ağaca sokmak teknisyeni yavaşlatır. **Uzun kuyruk için kart, sık vakalar için ağaç** doğru cevaptır.

### 22.3 "5.000 sorun" aslında kaç sorun?

Dikkat edilmezse 5.000 kayıt, 300 sorunun 5.000 farklı şekilde yazılmış hali olur. Bu bilgi tabanını **daha kötü** yapar: arama 8 benzer kayıt döndürür, teknisyen hangisini açacağını bilemez.

Gerçekçi bir taksonomi:

| Katman | Yaklaşık sayı | Örnek |
|---|---|---|
| Ayrı **kök neden** sınıfı | ~800-1.200 | "Video kablosu arızalı", "DHCP kirası alınamıyor" |
| Ayrı **belirti** sınıfı | ~400-600 | "Ekranda görüntü yok", "İnternet yok" |
| **Hata kodu / mesaj** kaydı | 3.000+ | Windows STOP kodları, Exchange NDR, HTTP, SMART, yazıcı kodları |
| **Ürüne özgü** kayıt | sınırsız | "HP LaserJet M404 kağıt sıkışması E3" |

5.000'e giden yolun büyük kısmı, belirti çeşitliliğinden değil **hata kodu ve ürün kataloğundan** gelir. Bu iyi haber: bu kayıtlar yapılandırılmış kaynaklardan üretilebilir, elle yazılmaları gerekmez.

### 22.4 Üç katmanlı içerik modeli

Şemaya `tier` alanı eklenir. Motor üçünü de aynı arama havuzunda tutar; yalnızca **yürütme biçimi** farklıdır.

| Katman | Tip | Yapı | Hedef adet | Yürütme |
|---|---|---|---|---|
| **T1** | `runbook` | Dallanan karar ağacı (§7) | 100-200 | Ağaç yürütücüsü |
| **T2** | `guide` | Doğrusal kontrol listesi, 3-8 adım, dallanma yok | 800-1.500 | Adım adım işaretleme |
| **T3** | `reference` | Belirti → olası nedenler → çözüm, 1-3 cümle | 3.000-4.000 | Tek kart |

Kritik kural: **T3 kayıtları T1'e bağlanır.** Bir hata kodu kartı, ilgili karar ağacına `related_runbook` ile işaret eder. Teknisyen kartta çözüm bulamazsa ağaca atlar. Böylece 5.000 kayıt dağınık bir yığın değil, 150 ağacın etrafında örülmüş bir ağ olur.

**T2 örneği:**

```yaml
code: NET-014
tier: guide
title: "Ağ sürücüsü (mapped drive) açılışta bağlanmıyor"
category: ag/paylasim
verification: dogrulanmis
aliases:
  - { term: "ağ sürücüsü kayboldu", kind: belirti }
  - { term: "mapped drive bağlanmıyor", kind: belirti }
  - { term: "kırmızı çarpı ağ sürücüsü", kind: belirti }
steps:
  - title: "Sürücüye çift tıklayarak yeniden bağlanmayı dene"
    note: "Windows tembel bağlanır; çoğu vakada tek tıkla çözülür."
  - title: "Kimlik bilgisi Credential Manager'da kayıtlı mı kontrol et"
  - title: "Grup ilkesiyle mi dağıtılıyor, elle mi eklenmiş belirle"
    note: "gpresult /r çıktısında sürücü eşleme ilkesini ara."
  - title: "Hızlı başlatmayı kapat, tam kapatma yaptır"
related_runbook: NET-001
```

**T3 örneği:**

```yaml
code: ERR-WIN-0x7B
tier: reference
title: "Mavi ekran 0x0000007B — INACCESSIBLE_BOOT_DEVICE"
category: donanim/onyukleme
verification: incelendi
aliases:
  - { term: "0x0000007b", kind: hata_kodu, weight: 1.5 }
  - { term: "inaccessible boot device", kind: hata_kodu, weight: 1.5 }
likely_causes:
  - "BIOS/UEFI'de SATA modu AHCI ↔ RAID/IDE arasında değişmiş"
  - "Depolama denetleyici sürücüsü bozuk veya eksik"
  - "Disk klonlama/imaj sonrası sürücü uyumsuzluğu"
fix_summary: |
  BIOS'ta SATA modunu eski değerine döndür. Kasıtlı değiştirildiyse
  Windows'u Güvenli Mod'da bir kez açarak sürücüyü yükletip geri dön.
related_runbook: BOOT-001
source:
  name: "Microsoft Learn — Bug check reference"
  url: "https://learn.microsoft.com/..."
  note: "Bağlantı verildi, metin kopyalanmadı."
```

### 22.5 Güven merdiveni — 5.000'in tek gerçek riski

5.000 kaydın %20'si yanlışsa bu bilgi tabanı **hiç olmamasından kötüdür**: teknisyen yanlış adımı uygular, arızayı büyütür, ve bir daha araca güvenmez. Ölçek büyüdükçe doğrulama bir metadata alanı olmaktan çıkıp ürünün merkezine geçer.

```yaml
verification: dogrulanmis   # gerçek vakada uygulandı ve çözdü — kim, ne zaman
                 incelendi  # konuyu bilen biri okudu, onayladı; sahada test edilmedi
                 taslak     # yazıldı, incelenmedi
                 otomatik   # üretildi/aktarıldı, hiç insan görmedi
```

Motor davranışı:

- Arayüzde her kartta **görünür rozet**. `otomatik` kayıtlar sarı uyarı şeridiyle açılır.
- Skorlamada çarpan: `dogrulanmis` ×1.0, `incelendi` ×0.9, `taslak` ×0.7, `otomatik` ×0.5. Doğrulanmış içerik her zaman öne çıkar.
- `risk: yuksek` **ve** `verification: otomatik` olan bir adım **hiç gösterilmez** — derleyici bunu hata sayar. Kayıt silmeyi, BIOS ayarı değiştirmeyi, registry düzenlemeyi doğrulanmamış içerik öneremez.
- Her kartta "Bu işe yaradı / yaramadı" geri bildirimi; 3 kez "yaramadı" alan `otomatik` kayıt otomatik olarak inceleme kuyruğuna düşer.

### 22.6 İçerik kaynakları ve lisans

Açık kaynak bir depoya başkasının dokümantasyonunu kopyalamak hem lisans ihlali hem bakım kâbusudur. Kaynak türüne göre kural:

| Kaynak | Kullanım |
|---|---|
| Arch Wiki, Ubuntu Docs (CC BY-SA) | **Uyarlanabilir**, atıf + aynı lisans şartıyla |
| Microsoft Learn, HP/Dell/Lenovo destek | **Kopyalanamaz.** Sadece bağlantı + kendi cümlenle 2-3 satır özet |
| Hata kodu listeleri (olgusal veri) | Kod ve kısa açıklama olgudur, telif konusu değil; **çözüm metni kendi yazımın olmalı** |
| NIST/CISA, kamu kurumu yayınları | Genelde serbest, kaynağı kontrol et |
| Stack Overflow / forumlar | CC BY-SA ama **güvenilirliği düşük**; `taslak` olarak girer, doğrulanmadan yayınlanmaz |

Pratik sonuç: T3 kayıtlarının doğru biçimi **"kısa kendi özetin + otoriter kaynağa bağlantı"**. Bu hem yasal, hem bakımı ucuz (kaynak güncellenince senin metnin bayatlamaz), hem de dürüst.

### 22.7 Üretim hattı

```
Kaynak (yapılandırılmış liste / vaka notu / topluluk PR'ı)
   │
   ├─ 1. Çıkarım       → ham kayıt taslağı (kod, başlık, belirti)
   ├─ 2. LLM taslağı   → alias üretimi, çözüm özeti taslağı        [opsiyonel]
   ├─ 3. Otomatik kapı → şema, sır taraması, lisans alanı, tekillik
   ├─ 4. Tekilleştirme → §22.8
   ├─ 5. İnsan kapısı  → inceleme kuyruğu, `taslak` → `incelendi`   [ZORUNLU]
   └─ 6. Yayın         → derleme, indeksleme
```

**LLM hakkında net olalım:** alias üretmek ("ekran siyah", "görüntü gelmiyor", "no signal" varyantları) için mükemmel — burada hata maliyeti sıfıra yakın, sadece arama isabeti artar. Ama **çözüm adımı üretmek** için tek başına kullanılamaz. Bir modelin uydurduğu registry anahtarı veya var olmayan bir BIOS ayarı, gerçek bir makineyi bozar. Bu yüzden 5. adım zorunlu ve atlanamaz bir kapıdır; `otomatik` durumundaki hiçbir kayıt insan onayı olmadan `incelendi` olamaz.

İnceleme kuyruğu için ayrı bir arayüz gerekir: `helpdesk review` — kayıt göster, kaynağı yanında aç, onayla / düzelt / reddet. Hedef: kayıt başına 2 dakika. 3.000 kayıt = ~100 saat inceleme. Bu, tek kişi için bile 3 ayda biter; topluluk varsa çok daha hızlı.

### 22.8 Tekilleştirme

Kayıt sayısı arttıkça en büyük kalite tehdidi çakışmadır. Derleme zamanında zorunlu kontroller:

- Normalize edilmiş başlık benzerliği (trigram/Jaccard) > 0,85 → **uyarı**
- İki kayıt aynı alias'a **tam eşleşmeyle** sahipse → **hata**, derleme durur
- Aynı `root_cause` + aynı kategori 3'ten fazla kayıtta → birleştirme önerisi
- Gömü (embedding) kosinüs benzerliği > 0,92 → inceleme kuyruğuna "olası kopya"

Bir kayıt silinmez, `merged_into: XXX-000` ile diğerine yönlendirilir; eski alias'lar korunur, arama yine bulur.

### 22.9 Arama 5.000'de değişir

20 kayıtta alias eşleşmesi yeter. 5.000 kayıtta yetmez — her sorgu düzinelerce aday döndürür.

- **Hibrit arama zorunlu hale gelir.** BM25 + yerel gömü vektörü. `sqlite-vec` uzantısı ile aynı SQLite dosyasında tutulabilir. 5.000 × 384 boyut × 4 bayt ≈ **7,7 MB** — sorun değil. Model (~120 MB) ikili pakete gömülmez, ilk çalıştırmada **isteğe bağlı** indirilir; indirilmezse sistem yalnız BM25 ile çalışmaya devam eder.
- **Faset filtreleri** arayüze girer: kategori, işletim sistemi, katman, doğrulama durumu.
- **Netleştirme artık istisna değil kural.** 0,40-0,75 bandı çok kalabalıklaşacağı için, adayları ayıran soru üretimi (§8.5) elle yazılmaktan çıkıp kategori ağacından otomatik türetilmeli.
- **Skorlamaya iki çarpan eklenir:** doğrulama durumu (§22.5) ve katman önceliği (eşit skorda T1 önce gelir — ağaç, karttan daha çok yol gösterir).

Performans açısından endişe yok: FTS5 5.000 kayıtta milisaniyeler mertebesinde çalışır. Darboğaz her zaman içerik kalitesi olacak, sorgu hızı değil.

### 22.10 Bakım yükü — genelde gözden kaçan maliyet

5.000 kaydın hepsi yılda bir gözden geçirilecek olsa, **her iş günü 20 inceleme** demektir. Sürdürülebilir değil. Risk tabanlı politika gerekir:

| Katman | Gözden geçirme | Tetikleyici |
|---|---|---|
| T1 `runbook` | 6 ay | Takvim + eskalasyon oranı artışı |
| T2 `guide` | 18 ay | Takvim + "işe yaramadı" geri bildirimi |
| T3 `reference` | Takvim **yok** | Yalnız sinyalle: olumsuz geri bildirim, kırık kaynak bağlantısı, ilgili T1 değişti |

Ek olarak: 18 aydır hiç açılmamış ve hiç çözüm üretmemiş kayıtlar **arşiv adayı** olarak raporlanır. Bilgi tabanı büyümek kadar küçülmeyi de bilmeli.

### 22.11 İçerik paketleri

5.000 kaydı tek pakette dağıtmak yanlış. Depo ve ikili dosya şişer, kullanıcı ilgilenmediği 2.000 yazıcı hata koduyla uğraşır.

```
core         (T1 + seçili T2, ~300 kayıt, ~6 MB)   — varsayılan, ikiliye gömülü
pack-windows-errors   (~1.500)                     — isteğe bağlı indirme
pack-network          (~600)
pack-printers         (~800)
pack-macos            (~400)
pack-linux            (~500)
pack-m365             (~700)
```

Her paket ayrı sürümlenir, ayrı bakım sahibi olabilir, topluluk kendi paketini yayınlayabilir. `helpdesk pack install windows-errors` ile eklenir. Bu yapı, 5.000 hedefini **tek kişinin sırtından alıp** dağıtılabilir hale getiren şeydir.

Boyut tahmini: 5.000 kayıt düz metin ~15 MB, FTS indeksiyle ~35 MB, gömülerle ~43 MB. Medya hariç tamamen makul.

### 22.12 Aşamalı hedefler

| Kilometre taşı | Kayıt | Kapsanan vaka hacmi | Gereken şey |
|---|---|---|---|
| **M1** | 20 (hepsi T1) | ~%55 | Elle yazım — §20 planı |
| **M2** | 60 T1 + 150 T2 | ~%80 | T2 şeması, `helpdesk new` sihirbazı |
| **M3** | 150 T1 + 500 T2 | ~%90 | Hibrit arama, faset filtreleri, inceleme kuyruğu |
| **M4** | + 1.500 T3 | ~%94 | Hata kodu aktarım hattı, tekilleştirme, lisans disiplini |
| **M5** | **5.000 toplam** | ~%97 | İçerik paketleri, topluluk katkısı, risk tabanlı bakım |

Dikkat: **M2'de zaten vakaların %80'ini karşılıyorsun.** M2'den M5'e giden yol, kalan %17 için harcanan emektir. Bu emeğe değer — ama ancak ilk %80 gerçekten çalışıyorsa. M1 ve M2 atlanıp doğrudan hacim peşine düşülürse, ortaya kimsenin güvenmediği 5.000 kayıtlık bir çöplük çıkar.

**Sıra bu yüzden değişmiyor:** önce 20 tane gerçekten iyi karar ağacı, sonra ölçek.
