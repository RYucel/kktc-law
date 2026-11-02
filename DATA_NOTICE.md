# Veri Kaynağı ve Lisans Notu

Bu depodaki **kod** `LICENSE` dosyasındaki MIT lisansıyla dağıtılır.

Depo, `mahkemeler.net` (KKTC Yüksek Mahkeme Mevzuat Bilgi Sistemi) üzerinden alınan
KKTC mevzuat metinlerini deterministik bir Git külliyatına dönüştürür. Mevzuat metinleri
kamuya açık hukuki kaynaklardır; ancak kaynak sitenin kendi kullanım/atıf koşulları
geçerli olabilir.

- Ham belgeler (`raw_cache/`) bu depoya dahil **edilmez**; yalnızca SHA-256 manifest'i
  (`raw_cache/manifest.json`) ve üretilen deterministik `corpus_repo/` ayrı tutulur.
- Yeniden üretim yönergeleri için `REPRODUCE.md` dosyasına bakın.
- Resmi kaynak: <https://www.mahkemeler.net>
