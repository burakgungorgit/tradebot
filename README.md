# AVAXUSDT Al-Sat Botu

Bu bot, 30 dakikalık zaman diliminde EMA20'nin EMA50'yi yukarı kestiği sinyaliyle alım yapar. %4 kâr veya %1.5 zarar durumunda satış yapar. Binance API ve Telegram entegrasyonu içerir.

## Özellikler
- EMA20-EMA50 kesişimiyle alım
- %4 kâr veya %1.5 zarar hedefli satış
- Telegram bildirimi
- Komisyon dahil PnL hesaplama
- Web panel için JSON API çıkışı (opsiyonel)
- Systemd ile 7/24 çalışma desteği

## Kurulum

```bash
git clone https://github.com/kullanici/avax-bot.git
cd avax-bot
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
