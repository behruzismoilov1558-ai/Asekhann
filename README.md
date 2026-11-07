# Asila uchun shaxsiy bot

Faqat Asila bilan gaplashadi, hamma gapini eslab qoladi, do'stdek munosabatda bo'ladi
va har 10-15 xabardan keyin suhbatga mos holda Behruz uni yaxshi ko'rishini aytadi.

## Fayllar
- `bot.py` - bot kodi
- `requirements.txt` - kutubxonalar
- `.env.example` - sozlamalar namunasi (`.env` qilib nusxalang)
- `.gitignore` - maxfiy fayllar GitHub'ga ketmasligi uchun

## O'rnatish
1. @BotFather orqali bot yarating va token oling.
2. Asila @userinfobot ga `/start` yozib, o'z ID sini yuborsin.
3. Anthropic API kalitini https://console.anthropic.com dan oling.
4. Terminalda:

```bash
python -m venv venv
source venv/bin/activate        # Windows: venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env            # va .env ni to'ldiring
python bot.py
```

5. Asila botga kirib `/start` bosadi.

## Buyruqlar (faqat Asila uchun)
- `/start` - tanishuv
- `/forget` - barcha suhbat va xotirani o'chirish
- `/behruz` - Behruz haqidagi eslatmalarni o'chirish/yoqish

## Doimiy ishlatish
VPS (yoki Railway/Render) da ishga tushiring. `data.db` fayli saqlanib qolishiga e'tibor bering.
