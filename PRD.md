## 1. Proje Özeti ve Vizyon

- **Proje Adı:** `kktc-law` (Git for TRNC Law)
    
      
    
- **Mimari Referans:** `lexifina/global-law` alt modül (submodule) ve deterministik `REPRODUCE.md` standardı.
    
      
    
- **Temel Amaç:** Kuzey Kıbrıs Türk Cumhuriyeti (KKTC) mevzuatını (Fasıllar, Yasalar, KHK'lar ve Tüzükler) her maddesi ayrı bir dosya, yürürlüğe giren her değişiklik yasası ise tarihsel bir `git commit` olacak şekilde versiyonlamak; bu yapıyı CLI ve Model Context Protocol (MCP) üzerinden sıfır halüsinasyonla sorgulanabilir hale getirmek.
    
      
    
- **Geliştirme Ortamı:** OpenCode (Terminal tabanlı AI Coding Agent) + DeepSeek (V3 / R1).
    
      
    

## 2. Sistem Mimarisi ve Veri Akışı

Sistem 5 ana katmandan oluşur:

  

1. **Scraper (Veri Çekici):** `mahkemeler.net` (Konsolide/Birleştirilmiş Mevzuat) ve `meclis.gov.ct.tr` / `resmigazete.gov.ct.tr` üzerinden ham HTML/DOC/PDF verilerini asenkron olarak indirir ve önbelleğe (`raw_cache/`) alır.
    
      
    
2. **Parser (Ayrıştırıcı):** Ham mevzuat metinlerini Kısım (Part), Bölüm (Chapter) ve Madde (Article/Section) hiyerarşisine böler. Dipnotlarda yer alan tarihsel değişiklik bilgilerini (Örn: _`34/2015 s.y. ile değişik`_) Regex ve AST mantığıyla ayıklar.
    
      
    
3. **Chronicle / Git Engine (Zaman Motoru):** Çıkarılan yasaları ve değişiklikleri yürürlük tarihine göre sıralar. `GIT_AUTHOR_DATE` ve `GIT_COMMITTER_DATE` ortam değişkenlerini manipüle ederek geçmişten bugüne deterministik Git geçmişini (commit history) inşa eder.
    
      
    
4. **Query CLI (`tools/law.py`):** Belirli bir tarihteki (`--as-of YYYY-MM-DD`) yasa ağacını okuyan, iki tarih arasındaki yasa farklarını (`diff`) çıkaran ve madde bazlı arama yapan komut satırı aracı.
    
      
    
5. **MCP Sunucusu (`mcp_server.py`):** LLM'lerin ve otonom ajanların doğrudan Git ağacındaki maddelere zaman damgalı erişmesini sağlayan FastMCP tabanlı sunucu.
    
      
    

## 3. Hedef Dizin ve Dosya Yapısı (`lexifina` Uyumlu)

Üretilecek hedef mevzuat reposunun (`corpus_repo/`) yapısı aşağıdaki gibi olmalıdır:

  

Plaintext

```
kktc-law/
├── corpus.json                 # Lexifina uyumlu külliyat üst verisi (metadata)
├── REPRODUCE.md                # Deterministik yeniden üretim kuralları ve komutları
├── README.md                   # Proje dokümantasyonu
├── laws/                       # Mevzuat kök dizini
│   ├── anayasa/                # KKTC Anayasası
│   │   ├── metadata.json
│   │   ├── madde-001.md
│   │   └── ...
│   ├── fasil-113/              # Şirketler Yasası (Fasıl 113)
│   │   ├── metadata.json
│   │   ├── madde-001.md
│   │   ├── madde-002.md
│   │   └── ...
│   └── yasa-2024-42/           # Yıl/Sayı bazlı modern yasalar
│       ├── metadata.json
│       └── madde-001.md
├── tools/
│   ├── scraper.py              # Mahkemeler.net ve Resmi Gazete veri çekici
│   ├── parser.py               # Madde ve değişiklik geçmişi ayrıştırıcı
│   ├── git_engine.py           # Deterministik Git commit oluşturucu
│   ├── law.py                  # Lexifina uyumlu sorgulama CLI aracı
│   └── mcp_server.py           # LLM entegrasyonu için MCP sunucusu
└── tests/
    ├── test_parser.py
    └── test_determinism.py
```

## 4. Veri Şemaları (Data Schemas)

### 4.1. `corpus.json` Şeması

JSON

```
{
  "jurisdiction": "trnc",
  "name": "Turkish Republic of Northern Cyprus",
  "label": "KKTC Mevzuatı (Birleştirilmiş Yasalar ve Fasıllar)",
  "language": ["tr"],
  "sources": [
    {
      "id": "mahkemeler",
      "name": "KKTC Mahkemeler Mevzuat Bilgi Sistemi",
      "url": "https://www.mahkemeler.net"
    },
    {
      "id": "meclis",
      "name": "KKTC Cumhuriyet Meclisi",
      "url": "https://www.cm.gov.ct.tr"
    }
  ],
  "checked_on": "2026-11-02",
  "history": "replayed"
}
```

### 4.2. Yasa `metadata.json` Şeması

JSON

```
{
  "law_id": "fasil-113",
  "type": "fasil",
  "number": "113",
  "title": "Şirketler Yasası",
  "original_date": "1951-06-01",
  "consolidation_history": [
    {
      "amending_law": "28/1974",
      "effective_date": "1974-12-15",
      "affected_articles": ["madde-002", "madde-014"]
    },
    {
      "amending_law": "41/2018",
      "effective_date": "2018-09-05",
      "affected_articles": ["madde-004"]
    }
  ]
}
```

### 4.3. Madde Dosyası (`madde-001.md`) Standardı

Her madde dosyası saf metin/Markdown içermeli, ekstra dinamik zaman damgası barındırmamalıdır:

  

Markdown

```
# Madde 1: Kısa İsim

Bu Yasa, Şirketler Yasası olarak isimlendirilir.
```

## 5. Çekirdek Modül Gereksinimleri (Fonksiyonel Spesifikasyonlar)

### Modül 1: Asenkron Veri Çekici (`tools/scraper.py`)

- **Teknoloji:** Python `httpx` (async), `BeautifulSoup4`, `tenacity` (retry mekanizması).
    
      
    
- **Gereksinimler:**
    
      
    - `mahkemeler.net` üzerindeki alfabetik ve fasıl/yıl bazlı indeks sayfalarını tarayarak tüm yasa URL'lerini ve son güncellenme tarihlerini çıkarmalıdır.
        
          
        
    - İndirilen dosyalar `SHA-256` hash değerleriyle birlikte `raw_cache/` dizinine kaydedilmelidir (Aynı dosya tekrar indirilmemeli, idempotent çalışmalıdır).
        
          
        
    - Karakter kodlaması sorunları (`windows-1254` / `iso-8859-9` -> `utf-8`) otomatik normalize edilmelidir.
        
          
        

### Modül 2: Mevzuat Ayrıştırıcı (`tools/parser.py`)

- **Teknoloji:** `re` (Regex), `lxml`, `pydantic`.
    
      
    
- **Gereksinimler:**
    
      
    - KKTC yasalarındaki standart madde başlıklarını (`"Madde 1."`, `"1."`, `"Kısa İsim [2(a) 34/2019]"`) yakalayıp her maddeyi izole bir `Article` Pydantic modeline dönüştürmelidir.
        
          
        
    - Madde kenar notlarında (marginal notes) yer alan değişiklik referanslarını (Örn: `3(1) 45/2008`) ayrıştırarak hangi maddenin hangi değişiklik yasasıyla değiştiğini `consolidation_history` listesine işlemelidir.
        
          
        
    - İçindeki boşluklar, satır sonları (`\n` LF standardı) ve noktalama işaretleri tamamen temizlenip normalize edilmelidir.
        
          
        

### Modül 3: Deterministik Git Motoru (`tools/git_engine.py`)

- **Teknoloji:** `subprocess` veya `dulwich` (saf Python Git implementasyonu).
    
      
    
- **Kritik Kural (Deterministik Hash):** Aynı ham veriyle script farklı bilgisayarlarda veya farklı günlerde çalıştırıldığında **birebir aynı Git commit hash'lerini** üretmek zorundadır.
    
      
    
- **Sabitlenecek Parametreler:**
    
      
    - `GIT_AUTHOR_NAME`: `"KKTC Cumhuriyet Meclisi"` (veya Fasılların ilk hali için `"Kıbrıs Koloni İdaresi"`)
        
          
        
    - `GIT_AUTHOR_EMAIL`: `"mevzuat@kktc-law.local"`
        
          
        
    - `GIT_COMMITTER_NAME`: `"kktc-law-bot"`
        
          
        
    - `GIT_COMMITTER_EMAIL`: `"bot@kktc-law.local"`
        
          
        
    - `GIT_AUTHOR_DATE` & `GIT_COMMITTER_DATE`: Yasanın yürürlüğe girdiği tarih, sabit saat ve UTC dilimiyle (`"YYYY-MM-DDT00:00:00+00:00"`).
        
          
        
    - Dosya izinleri (`0644`) ve dosya sıralaması alfabetik olarak sabitlenmelidir.
        
          
        
    - **Tagleme Standardı:** Her yıl veya her resmi gazete dönemi için `vYYYY-MM-DD` formatında Git tag'i oluşturulmalıdır.
        
          
        

### Modül 4: Sorgulama CLI ve MCP Sunucusu (`tools/law.py` & `tools/mcp_server.py`)

- **Teknoloji:** `typer` (CLI), `mcp` (FastMCP).
    
      
    
- **CLI Komutları (`law.py`):**
    
      
    - `python tools/law.py read fasil-113 madde-002 --as-of 2019-01-01` (Git log üzerinden ilgili tarihten önceki son commit'teki dosya içeriğini `git show <commit>:<path>` ile bellekte okur, working tree'yi bozmaz).
        
          
        
    - `python tools/law.py history fasil-113 madde-002` (İlgili maddenin tüm değişiklik geçmişini ve hangi yasalarla değiştiğini listeler).
        
          
        
    - `python tools/law.py search "tasfiye memuru" --as-of 2026-01-01` (`git grep` kullanarak milisaniyeler içinde tam metin araması yapar).
        
          
        
- **MCP Araçları (`mcp_server.py`):**
    
      
    - `get_law_article(law_id: str, article_id: str, as_of_date: str | None)`
        
          
        
    - `get_article_amendment_history(law_id: str, article_id: str)`
        
          
        
    - `search_legislation(query: str, as_of_date: str | None)`
        
          
        

## 6. KKTC Mevzuatına Özgü Edge-Case'ler (Özel Durumlar)

DeepSeek ile kod yazarken parser modülünde şu özel durumların ele alınması şarttır:

  

1. **İki Katmanlı Mevzuat Yapısı:** 1960 öncesi İngiliz Koloni döneminden kalan yasalar **Fasıl (Chapter)** numarasıyla (Örn: `Fasıl 113`, `Fasıl 148`), 1974 sonrası yasalar ise **`Sayı/Yıl`** (Örn: `42/2024 Sayılı Yasa`) formatıyla anılır. `law_id` slug üretici her ikisini de standartlaştırmalıdır (`fasil-113` ve `yasa-2024-42`).
    
      
    
2. **Geçmiş Metinlerin Eksikliği (Konsolide vs. Tarihsel):** `mahkemeler.net` çoğunlukla yasanın **son birleştirilmiş (konsolide)** halini sunar; değiştirilen eski maddenin 1990'daki tam metni HTML içinde olmayabilir, sadece kenar notunda `"12/1990 ile değişik"` yazar.
    
      
    - **Çözüm (Aşama 1 - Hibrit Replay):** İlk aşamada mevcut konsolide metinler alınır, kenar notlarındaki değişiklik yasası tarihlerine göre sentetik/kronolojik commit zinciri oluşturulur veya Resmi Gazete arşivinden (`resmigazete.gov.ct.tr`) çekilen değişiklik yasalarıyla (Örn: _"Madde 4 kaldırılarak yerine aşağıdaki yeni Madde 4 konur"_) geriye/ileriye doğru yama (patch) uygulanır.
        
          
        

## 7. OpenCode + DeepSeek İçin Uygulama Yol Haritası (Adım Adım Prompt Planı)

DeepSeek'in bağlam penceresini (context window) en verimli şekilde kullanmak ve hatasız kod üretmek için OpenCode içerisinde projeyi şu **4 fazda** kodlatmalısın:

  

- **Faz 1: İskelet ve Deterministik Git Motoru**
    
      
    - _OpenCode Görevi:_ `pyproject.toml`, dizin yapısı, `corpus.json` ve `tools/git_engine.py` dosyasının yazılması. Sahte (mock) bir yasa verisiyle iki farklı tarihte commit atıp, repo silinip tekrar çalıştırıldığında **aynı SHA-1 commit hash'inin** üretildiğini doğrulayan `tests/test_determinism.py` testinin geçilmesi.
        
          
        
- **Faz 2: Parser ve Normalizasyon Motoru**
    
      
    - _OpenCode Görevi:_ Örnek bir KKTC yasası HTML/metin dosyası (`tests/fixtures/fasil_113_sample.html`) üzerinden `tools/parser.py` modülünün yazılması. Maddelerin ve kenar notlarındaki değişiklik yasası referanslarının (`[2(a) 34/2019]`) hatasız ayrıştırılıp Pydantic modellerine dökülmesi.
        
          
        
- **Faz 3: Scraper ve Uçtan Uca Pipeline**
    
      
    - _OpenCode Görevi:_ `tools/scraper.py` ile `mahkemeler.net` bağlantısının kurulması, hız sınırlandırma (rate-limiting) ve önbellekleme mekanizmasının eklenmesi. Scraper -> Parser -> Git Engine boru hattının `REPRODUCE.md` içindeki tek bir komutla çalışır hale getirilmesi.
        
          
        
- **Faz 4: `law.py` CLI ve FastMCP Sunucusu**
    
      
    - _OpenCode Görevi:_ Çalışma dizinini (`HEAD`) değiştirmeden doğrudan Git nesne veritabanı (`git show`, `git log -S`, `git grep`) üzerinden sorgu yapan CLI aracının ve MCP sunucusunun yazılması.