# @author: MembaCo.

# Temel imaj olarak Python 3.11'in kararlı (bullseye) sürümünü kullanıyoruz.
# 'slim' sürümü bazen temel kütüphaneleri eksik bırakabilir.
FROM python:3.11-bullseye

# Konteyner içindeki çalışma dizinini belirliyoruz.
WORKDIR /app

# --- GÜNCELLEME: Chrome ve Bağımlılık Kurulumu ---
# Chrome'un ihtiyaç duyduğu tüm araçları ve kütüphaneleri en baştan kuruyoruz.
RUN apt-get update && apt-get install -y \
    wget \
    gnupg \
    unzip \
    jq \
    --no-install-recommends \
    # Google Chrome'un resmi GPG anahtarını indirip ekliyoruz.
    && wget -q -O - https://dl.google.com/linux/linux_signing_key.pub | gpg --dearmor -o /usr/share/keyrings/google-chrome-keyring.gpg \
    # Google Chrome'un resmi deposunu sistemin paket kaynaklarına ekliyoruz.
    && echo "deb [arch=amd64 signed-by=/usr/share/keyrings/google-chrome-keyring.gpg] http://dl.google.com/linux/chrome/deb/ stable main" > /etc/apt/sources.list.d/google-chrome.list \
    # Paket listesini yeni depoyla birlikte güncelliyoruz.
    && apt-get update \
    # Google Chrome'u resmi depodan kuruyoruz. Bu komut, GEREKLİ TÜM KÜTÜPHANELERİ OTOMATİK OLARAK YÜKLER.
    && apt-get install -y google-chrome-stable \
    # İndirme sonrası gereksiz dosyaları temizliyoruz.
    && rm -rf /var/lib/apt/lists/*
# --- GÜNCELLEME SONU ---

# --- GÜNCELLEME: Uyumlu ChromeDriver Kurulumu ---
# Az önce kurduğumuz Chrome'un tam sürümünü alıp ona uygun ChromeDriver'ı indiriyoruz.
RUN CHROME_VERSION=$(google-chrome --product-version) \
    && echo "Kurulan Chrome Sürümü: ${CHROME_VERSION}" \
    && LATEST_VERSION=$(wget -q -O - "https://googlechromelabs.github.io/chrome-for-testing/latest-versions-per-milestone-with-downloads.json" | jq -r --arg ver "${CHROME_VERSION}" '.milestones[$ver | split(".")[0]].version') \
    && echo "Uyumlu ChromeDriver Sürümü: ${LATEST_VERSION}" \
    && wget -q --continue -P /tmp "https://storage.googleapis.com/chrome-for-testing-public/${LATEST_VERSION}/linux64/chromedriver-linux64.zip" \
    && unzip -q /tmp/chromedriver-linux64.zip -d /usr/bin \
    && ln -s /usr/bin/chromedriver-linux64/chromedriver /usr/bin/chromedriver \
    && rm /tmp/chromedriver-linux64.zip
# --- GÜNCELLEME SONU ---

# Python bağımlılıklarını kuruyoruz.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Projenin geri kalan tüm dosyalarını çalışma dizinine kopyalıyoruz.
COPY . .

# Flask uygulaması için 5000 portunu açıyoruz.
EXPOSE 5000

# Konteyner başlatıldığında çalıştırılacak komut.
CMD ["python", "app.py"]
 