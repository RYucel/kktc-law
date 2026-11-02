# Katkıda Bulunma

`kktc-law` projesine katkınız için teşekkürler. Bu proje deterministik, denetlenebilir ve
açık kaynaklı bir KKTC mevzuat külliyatı kurmayı hedefler.

## Geliştirme Ortamı

```sh
uv sync --extra dev
uv run pytest -q
uv run ruff check .
```

## Katkı Akışı

1. Bir konuyu açıklayan GitHub issue açın (veya mevcut issue'ya atıf yapın).
2. Repoyu fork edin ve bir özellik dalı oluşturun: `git checkout -b feature/xxx`.
3. Değişikliklerinizi yapın; modül bazında test ekleyin.
4. Biçim ve lint kontrollerini çalıştırın: `uv run ruff check .`.
5. Testleri çalıştırın: `uv run pytest -q`.
6. Küçük, açıklayıcı commit'lerle gönderin ve bir pull request açın.

## Kod Standartları

- Python 3.11+, sıkı tipli (`from __future__ import annotations`).
- Determinizm kuralı: çalışma zamanı zaman damgası üretilen dosyalara yazılmaz.
- Salt-okunur sorgular `git checkout` kullanmaz; `git show`/`git log`/`git grep`/`git diff` ile çalışır.
- Tam yönergeler için `AGENTS.md` dosyasına bakın.

## Parser / Scraper Katkıları

Gerçek `mahkemeler.net` belgelerindeki yeni bir biçim bozukluğunu düzeltirken:

- `tests/fixtures/` altına küçük bir örnek ekleyin.
- Çözümü deterministik biçimde uygulayın.
- Regresyon testiyle doğrulayın.

## Veri Lisansı

Kod MIT lisanslıdır. Mevzuat metinlerinin kaynak/atıf koşulları için `DATA_NOTICE.md`
dosyasına bakın.
