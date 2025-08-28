Changelog
Tüm önemli değişiklikler bu dosyada belgelenmektedir.
 
[1.0.0] - 2025-08-18
Bu, projenin ilk stabil ve zengin özellikli sürümüdür. Manuel indirme yeteneklerinin üzerine, tam otomasyon ve gelişmiş hata yönetimi mekanizmaları eklenmiştir.

✨ Eklenen Özellikler
Otomatik İndirme Yöneticisi: Kuyruğa eklenen videoları sırayla ve otomatik olarak indiren bir arka plan yöneticisi eklendi. Arayüzden tek bir butonla aktif/pasif hale getirilebilir.

Görsel Arayüz: Ana sayfadaki indirme listesine, her film için otomatik olarak çekilen poster resimleri eklendi.

Disk'ten Sil Fonksiyonu: İndirmesi tamamlanmış videolar için, video dosyasını doğrudan diskten kalıcı olarak silen bir buton eklendi.

Tekrar Engelleme: Aynı URL'ye sahip bir videonun kuyruğa birden fazla kez eklenmesi engellendi.

Modüler Altyapı: Veritabanına source_site sütunu eklenerek, gelecekte farklı sitelerden indirme yapabilmek için modüler bir altyapının temeli atıldı.

🐛 Hata Düzeltmeleri ve İyileştirmeler
Sunucu Korumaları: yt-dlp'nin 403 Forbidden hatası almasını engellemek için, Selenium tarayıcı oturumundan alınan çerez (cookie) ve header bilgilerinin yt-dlp'ye aktarılması sağlandı.

Platform Uyumluluğu: Devam eden bir indirme işlemini durdurma mekanizması, hem Windows (taskkill) hem de Unix tabanlı sistemlerde (killpg) sorunsuz çalışacak şekilde yeniden düzenlendi.

Karakter Kodlama Sorunları:

yt-dlp'den gelen ve UnicodeDecodeError hatasına neden olan Windows'a özgü karakterlerin sorunsuz okunması sağlandı.

İndirilen dosya adlarında oluşabilecek uyumsuzlukları engellemek için, film başlıklarındaki özel karakterleri temizleyen bir "güvenli isimlendirme" mekanizması eklendi.

Web Kazıma (Scraping) Dayanıklılığı: Meta veri çekme fonksiyonu, öncelikli olarak JSON-LD verisini okuyan, başarısız olması durumunda ise standart HTML etiketlerini analiz eden hibrit bir yapıya dönüştürüldü. Bu, sitenin görsel değişikliklerine karşı programı çok daha dayanıklı hale getirdi.

Arayüz (UI/UX): İndirme tamamlandığında veya hata oluştuğunda butonların ve durum bilgisinin anlık ve doğru bir şekilde güncellenmesi sağlandı. İlerleme çubuğunun takılı kalma sorunu giderildi.

🔧 Altyapı ve Kod Kalitesi
Merkezi Loglama: Projedeki tüm print() ifadeleri, hem konsola hem de app.log dosyasına yazan, seviyelendirilmiş ve otomatik dosya rotasyonu yapan profesyonel bir logging sistemiyle değiştirildi.

Modülerleşme: Proje, ayarlar için config.py, veritabanı işlemleri için database.py ve loglama için logging_config.py gibi ayrı modüllere bölünerek daha okunabilir ve yönetilebilir hale getirildi.

[0.1.0] - Başlangıç Sürümü
Flask tabanlı temel web uygulaması ve güvenli giriş sistemi.

Videoları manuel olarak sıraya ekleme ve indirmeyi başlatma.

multiprocessing ile indirme işlemlerini arka plana taşıma.

Fetch API ile temel canlı durum takibi.
