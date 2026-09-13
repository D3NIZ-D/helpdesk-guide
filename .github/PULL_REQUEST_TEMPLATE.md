## Ne değişti? / What changed?

<!-- Bir iki cümle. Neden gerektiğini de yaz. -->

## Tür / Type

- [ ] İçerik — yeni kayıt / New content record
- [ ] İçerik — mevcut kaydın düzeltilmesi / Content fix
- [ ] Kod — hata düzeltme / Bug fix
- [ ] Kod — yeni özellik / Feature
- [ ] Dokümantasyon / Docs

---

## İçerik katkısıysa / If this is content

Kontrol listesinin tamamı `CONTRIBUTING-CONTENT.md` içinde. Kısa hali:

- [ ] En az 3 gerçekçi alias var, kullanıcının ağzından yazılmış
- [ ] Her `talimat` düğümünde `verify_text` dolu
- [ ] Her dal ya `cozum` ya `eskalasyon` ile bitiyor
- [ ] Çözüme giden en uzun yol ≤ 7 adım
- [ ] `risk: yuksek` adımlarda `rollback_md` dolu
- [ ] Parola, anahtar, iç sunucu adı **yok** (`helpdesk lint` geçti)
- [ ] `tests/golden_queries.yaml` dosyasına en az 3 sorgu eklendi
- [ ] `helpdesk compile --strict && helpdesk eval` yerelde geçiyor
- [ ] Ağacı baştan sona bir kez yürüdüm

**Bu içeriği nereden biliyorsun?** <!-- gerçek vaka / üretici dokümanı + kendi cümlelerim / test ettim -->

**Doğrulama seviyesi:** <!-- taslak | incelendi | dogrulanmis -->

## Kod katkısıysa / If this is code

- [ ] `pytest -q` geçiyor
- [ ] `ruff check src tests` temiz
- [ ] Davranış değiştiyse test eklendi
- [ ] Türkçe metin değiştiyse hem `tr.json` hem `en.json` güncellendi
