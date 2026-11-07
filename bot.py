import os
import random
import sqlite3
import logging
import asyncio
import signal

from dotenv import load_dotenv
from aiohttp import web
from anthropic import AsyncAnthropic

from telegram import Update
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    MessageHandler,
    filters,
    ContextTypes,
)

# ==========================================
# ENVIRONMENT VARIABLES
# ==========================================

load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN")
ASILA_ID = int(os.getenv("ASILA_ID", "0"))
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY")

BOT_NAME = os.getenv("BOT_NAME", "Do'stcha")
MODEL = os.getenv("MODEL", "claude-sonnet-5-5")
DB_PATH = os.getenv("DB_PATH", "data.db")
PORT = int(os.getenv("PORT", "10000"))

if not BOT_TOKEN:
    raise ValueError("BOT_TOKEN Environment Variable topilmadi!")

if not ASILA_ID:
    raise ValueError("ASILA_ID Environment Variable topilmadi!")

if not ANTHROPIC_API_KEY:
    raise ValueError("ANTHROPIC_API_KEY Environment Variable topilmadi!")

# ==========================================
# LOGGING
# ==========================================

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s"
)

log = logging.getLogger("bot")

# ==========================================
# ANTHROPIC CLIENT
# ==========================================

client = AsyncAnthropic(api_key=ANTHROPIC_API_KEY)

only_asila = filters.User(user_id=ASILA_ID)

# ==========================================
# BOT PERSONALITY
# ==========================================

PERSONA = f"""Sen {BOT_NAME}san, Asilaning samimiy AI suhbatdoshi.

Faqat Asila bilan gaplashasan.
Uslub: iliq, samimiy, hazilkash, "sen" deb murojaat qil.
O'zbek tilida yoz, u boshqa tilda yozsa, o'sha tilda javob ber.
Xabarlar qisqa va tabiiy bo'lsin.
Emojilarni me'yorida ishlat.

U aytgan muhim ma'lumotlarni suhbat davomida hisobga ol.
Faqat maslahat berib turma: avval tingla, savol ber, suhbatga qiziqish bildir.
Kayfiyatiga moslash: xafa bo'lsa qo'llab-quvvatla, xursand bo'lsa birga quvon.

Bu botni Asilaga Behruz sovg'a qilgan.
Agar chin dildan so'rasa, AI ekaningni yashirma.
Hech qachon o'zingni haqiqiy inson deb ko'rsatma.
Agar og'ir ruhiy holat sezilsa, e'tibor bilan gapir
va kerak bo'lsa yaqinlari yoki mutaxassis bilan gaplashishni tavsiya qil.
"""

FRIEND_HINT = (
    "\n\nMUHIM: Suhbatga mos bo'lsa, Behruz Asilani qadrlashini "
    "va unga yaxshi tilaklarini bildirganini tabiiy tarzda eslat. "
    "Buni qisqa, samimiy va bosimsiz qil. "
    "Mavzu mos kelmasa, bu eslatmani ishlatma."
)

START_TEXT = (
    "Salom, Asila! 🌸 Bu botni senga Behruz sovg'a qildi. "
    "Men sening shaxsiy AI suhbatdoshingman.\n\n"
    "Men sun'iy intellektman. Suhbatimizdagi ma'lumotlar "
    "xotirada saqlanishi mumkin.\n\n"
    "/forget - suhbat xotirasini o'chirish\n"
    "/behruz - Behruz haqidagi eslatmalarni yoqish/o'chirish\n\n"
    "Qani, kuning qanday o'tdi?"
)

# ==========================================
# DATABASE
# ==========================================

if DB_PATH != ":memory:":
    os.makedirs(
        os.path.dirname(os.path.abspath(DB_PATH)),
        exist_ok=True
    )

db = sqlite3.connect(DB_PATH, check_same_thread=False)

db.executescript("""
CREATE TABLE IF NOT EXISTS messages(
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    role TEXT NOT NULL,
    content TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS memory(
    id INTEGER PRIMARY KEY CHECK (id=1),
    notes TEXT DEFAULT '',
    counter INTEGER DEFAULT 0
);

CREATE TABLE IF NOT EXISTS love(
    id INTEGER PRIMARY KEY CHECK (id=1),
    cnt INTEGER DEFAULT 0,
    next_at INTEGER DEFAULT 12,
    on_off INTEGER DEFAULT 1
);

INSERT OR IGNORE INTO memory(id) VALUES(1);
INSERT OR IGNORE INTO love(id) VALUES(1);
""")

db.commit()


def save(role, text):
    db.execute(
        "INSERT INTO messages(role, content) VALUES(?,?)",
        (role, text)
    )
    db.commit()


def history(n=30):
    rows = db.execute(
        "SELECT role, content FROM messages ORDER BY id DESC LIMIT ?",
        (n,)
    ).fetchall()

    msgs = [
        {"role": r, "content": c}
        for r, c in reversed(rows)
    ]

    while msgs and msgs[0]["role"] != "user":
        msgs.pop(0)

    return msgs


def get_notes():
    return db.execute(
        "SELECT notes FROM memory WHERE id=1"
    ).fetchone()[0]


def love_due():
    cnt, next_at, on = db.execute(
        "SELECT cnt, next_at, on_off FROM love WHERE id=1"
    ).fetchone()

    cnt += 1

    if on and cnt >= next_at:
        db.execute(
            "UPDATE love SET cnt=0, next_at=? WHERE id=1",
            (random.randint(10, 15),)
        )
        db.commit()
        return True

    db.execute(
        "UPDATE love SET cnt=? WHERE id=1",
        (cnt,)
    )
    db.commit()

    return False


# ==========================================
# LONG-TERM MEMORY
# ==========================================

async def update_notes():

    recent = "\n".join(
        f"{m['role']}: {m['content']}"
        for m in history(14)
    )

    resp = await client.messages.create(
        model=MODEL,
        max_tokens=700,
        system=(
            "Sen xotira yozuvchisan. Eski yozuv va yangi suhbatdan "
            "Asila haqida muhim faktlarni qisqa punktlar bilan yangila. "
            "Faqat yozuvning o'zini qaytar."
        ),
        messages=[
            {
                "role": "user",
                "content": (
                    f"ESKI YOZUV:\n{get_notes()}\n\n"
                    f"YANGI SUHBAT:\n{recent}"
                )
            }
        ],
    )

    db.execute(
        "UPDATE memory SET notes=?, counter=0 WHERE id=1",
        (resp.content[0].text,)
    )

    db.commit()


# ==========================================
# TELEGRAM HANDLERS
# ==========================================

async def start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(START_TEXT)


async def forget(update: Update, ctx: ContextTypes.DEFAULT_TYPE):

    db.execute("DELETE FROM messages")

    db.execute(
        "UPDATE memory SET notes='', counter=0 WHERE id=1"
    )

    db.commit()

    await update.message.reply_text(
        "Suhbat xotirasini o'chirdim. Qaytadan boshlaymiz 🙂"
    )


async def behruz_toggle(update: Update, ctx: ContextTypes.DEFAULT_TYPE):

    on = db.execute(
        "SELECT on_off FROM love WHERE id=1"
    ).fetchone()[0]

    db.execute(
        "UPDATE love SET on_off=? WHERE id=1",
        (0 if on else 1,)
    )

    db.commit()

    await update.message.reply_text(
        "Yaxshi, endi Behruz haqida eslatmayman 🙂"
        if on
        else "Mayli, yana ba'zan eslataman 😊"
    )


async def non_text(update: Update, ctx: ContextTypes.DEFAULT_TYPE):

    await update.message.reply_text(
        "Hozircha faqat matnli xabarlarni tushunaman 🌸 Yozib yubor."
    )


async def chat(update: Update, ctx: ContextTypes.DEFAULT_TYPE):

    save("user", update.message.text)

    notes = get_notes()

    system = PERSONA

    if notes:
        system += f"\n\nAsila haqida bilganlaring:\n{notes}"

    if love_due():
        system += FRIEND_HINT

    await ctx.bot.send_chat_action(
        update.effective_chat.id,
        "typing"
    )

    try:

        resp = await client.messages.create(
            model=MODEL,
            max_tokens=500,
            system=system,
            messages=history()
        )

        answer = resp.content[0].text

    except Exception:

        log.exception("Claude API xatosi")

        await update.message.reply_text(
            "Hozir ozgina ulanishda muammo bo'ldi, "
            "birozdan keyin yana yozib ko'r 🙏"
        )

        return

    save("assistant", answer)

    await update.message.reply_text(answer)

    db.execute(
        "UPDATE memory SET counter=counter+1 WHERE id=1"
    )

    db.commit()

    counter = db.execute(
        "SELECT counter FROM memory WHERE id=1"
    ).fetchone()[0]

    if counter >= 10:

        try:
            await update_notes()

        except Exception:
            log.exception("Xotirani yangilashda xato")


# ==========================================
# AIOHTTP WEB SERVER
# ==========================================

async def home(request):

    return web.json_response({
        "status": "online",
        "bot": BOT_NAME,
        "message": "Telegram bot ishlayapti"
    })


async def health(request):

    return web.Response(
        text="OK",
        status=200
    )


async def start_web_server():

    web_app = web.Application()

    web_app.router.add_get("/", home)
    web_app.router.add_get("/health", health)

    runner = web.AppRunner(web_app)

    await runner.setup()

    site = web.TCPSite(
        runner,
        host="0.0.0.0",
        port=PORT
    )

    await site.start()

    log.info(f"Web server ishga tushdi: PORT={PORT}")

    return runner


# ==========================================
# MAIN: BOT + WEB SERVER
# ==========================================

async def main():

    app = ApplicationBuilder().token(BOT_TOKEN).build()

    app.add_handler(
        CommandHandler("start", start, filters=only_asila)
    )

    app.add_handler(
        CommandHandler("forget", forget, filters=only_asila)
    )

    app.add_handler(
        CommandHandler("behruz", behruz_toggle, filters=only_asila)
    )

    app.add_handler(
        MessageHandler(
            only_asila & filters.TEXT & ~filters.COMMAND,
            chat
        )
    )

    app.add_handler(
        MessageHandler(
            only_asila & ~filters.TEXT & ~filters.COMMAND,
            non_text
        )
    )

    runner = None
    stop_event = asyncio.Event()

    loop = asyncio.get_running_loop()

    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, stop_event.set)
        except (NotImplementedError, RuntimeError):
            pass

    try:

        # Telegram botni ishga tushirish
        await app.initialize()
        await app.start()
        await app.updater.start_polling()

        log.info("Telegram polling ishga tushdi")

        # Web serverni ishga tushirish
        runner = await start_web_server()

        log.info("Bot va web server bir vaqtda ishlayapti")

        # To'xtatilish signalini kutish
        await stop_event.wait()

    finally:

        log.info("Dastur to'xtatilmoqda...")

        if runner is not None:
            await runner.cleanup()

        if app.updater and app.updater.running:
            await app.updater.stop()

        if app.running:
            await app.stop()

        await app.shutdown()

        await client.close()

        db.close()

        log.info("Barcha xizmatlar to'xtatildi")


if __name__ == "__main__":

    try:
        asyncio.run(main())

    except KeyboardInterrupt:
        log.info("Dastur qo'lda to'xtatildi")