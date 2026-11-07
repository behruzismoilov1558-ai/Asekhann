import os
import asyncio
import random
import sqlite3
import logging
import threading
from datetime import date
from http.server import BaseHTTPRequestHandler, HTTPServer

from dotenv import load_dotenv
from groq import AsyncGroq
from telegram import Update
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    MessageHandler,
    filters,
    ContextTypes,
)

load_dotenv()

# Render Environment Variables
TG_TOKEN = os.getenv("BOT_TOKEN") or os.getenv("TG_TOKEN")
GROQ_API_KEY = os.getenv("GROQ_API_KEY")

if not TG_TOKEN:
    raise RuntimeError("BOT_TOKEN (yoki TG_TOKEN) Environment Variable topilmadi.")
if not GROQ_API_KEY:
    raise RuntimeError("GROQ_API_KEY Environment Variable topilmadi.")

ASILA_ID = int(os.getenv("ASILA_ID", "0") or 0)
BOT_NAME = os.getenv("BOT_NAME", "Do'stcha")
GROQ_MODEL = os.getenv("GROQ_MODEL", "llama-3.3-70b-versatile")
DB_PATH = os.getenv("DB_PATH", "data.db")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
)
log = logging.getLogger("bot")
logging.getLogger("httpx").setLevel(logging.WARNING)

client = AsyncGroq(api_key=GROQ_API_KEY)
private_only = filters.ChatType.PRIVATE

PERSONA = f"""Sen {BOT_NAME}san, foydalanuvchining do'stona suhbatdoshi.
Uslub: iliq, samimiy, hazilkash, "sen" deb murojaat qil, o'zbek tilida
(u boshqa tilda yozsa, o'sha tilda javob ber).
Xabarlar qisqa va tabiiy bo'lsin, Telegramda do'st yozgandek.
Emojini me'yorida ishlat. Foydalanuvchini diqqat bilan tingla,
kerak bo'lsa savol ber. Faqat maslahat berib turma.
Kayfiyatiga moslash: xafa bo'lsa dalda ber, xursand bo'lsa quvonchini bo'lish.
Agar chin dildan so'rasa, AI ekaningni yashirma.
Agar foydalanuvchi og'ir vaziyat haqida gapirsa, e'tibor bilan javob ber
va zarur bo'lsa ishonchli yaqin kishisi yoki mutaxassisga murojaat qilishni tavsiya qil."""

ASILA_EXTRA = "\n\nBu suhbatdoshing Asila. Bu botni unga Behruz sovg'a qilgan."

LOVE_HINT = (
    "\n\nAgar suhbat mavzusiga tabiiy mos kelsa, Behruz Asilaga iliq munosabatda "
    "ekanini qisqa va bosimsiz tarzda eslatishing mumkin. Mavzu yoki kayfiyat mos "
    "kelmasa, bu haqda gapirma."
)

START_TEXT = (
    f"Salom! Men {BOT_NAME} 🌸 Sening do'sting bo'laman, istagan narsa haqida gaplashamiz.\n\n"
    "Men sun'iy intellektman. Suhbatimizdagi ayrim ma'lumotlar keyingi javoblar uchun saqlanadi.\n"
    "/forget - suhbat xotirasi va eslatmalarni o'chirish\n"
    "/id - Telegram ID raqamingni ko'rish\n\n"
    "Qani, kuning qalay o'tdi?"
)

START_ASILA = (
    "Salom, Asila! 🌸 Bu botni senga Behruz sovg'a qildi. Men sen bilan suhbatlashaman.\n\n"
    "Men sun'iy intellektman. Suhbatimizdagi ayrim ma'lumotlar keyingi javoblar uchun saqlanadi.\n"
    "/forget - xotirani o'chirish\n"
    "/behruz - Behruz haqidagi eslatmalarni yoqish/o'chirish\n\n"
    "Qani, kuning qalay o'tdi?"
)

# ---------- Ma'lumotlar bazasi ----------
db = sqlite3.connect(DB_PATH, check_same_thread=False)
db.executescript("""
CREATE TABLE IF NOT EXISTS msgs(
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    uid INTEGER NOT NULL,
    role TEXT NOT NULL,
    content TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_msgs_uid ON msgs(uid, id);
CREATE TABLE IF NOT EXISTS mem(
    uid INTEGER PRIMARY KEY,
    notes TEXT DEFAULT '',
    counter INTEGER DEFAULT 0
);
CREATE TABLE IF NOT EXISTS love(
    uid INTEGER PRIMARY KEY,
    cnt INTEGER DEFAULT 0,
    next_at INTEGER DEFAULT 12,
    on_off INTEGER DEFAULT 1
);
CREATE TABLE IF NOT EXISTS usage(
    uid INTEGER,
    day TEXT,
    n INTEGER DEFAULT 0,
    PRIMARY KEY(uid, day)
);
""")
db.commit()


def save(uid: int, role: str, content: str):
    db.execute(
        "INSERT INTO msgs(uid, role, content) VALUES(?,?,?)",
        (uid, role, content),
    )
    db.commit()


def history(uid: int, n: int = 20):
    rows = db.execute(
        "SELECT role, content FROM msgs WHERE uid=? ORDER BY id DESC LIMIT ?",
        (uid, n),
    ).fetchall()
    result = [{"role": role, "content": content} for role, content in reversed(rows)]
    while result and result[0]["role"] != "user":
        result.pop(0)
    return result


def get_notes(uid: int):
    row = db.execute("SELECT notes FROM mem WHERE uid=?", (uid,)).fetchone()
    return row[0] if row else ""


def love_due(uid: int):
    """Faqat Asila uchun: belgilangan xabarlar sonidan keyin True qaytaradi."""
    db.execute("INSERT OR IGNORE INTO love(uid) VALUES(?)", (uid,))
    count, next_at, enabled = db.execute(
        "SELECT cnt, next_at, on_off FROM love WHERE uid=?",
        (uid,),
    ).fetchone()

    count += 1
    if enabled and count >= next_at:
        db.execute(
            "UPDATE love SET cnt=0, next_at=? WHERE uid=?",
            (random.randint(10, 15), uid),
        )
        db.commit()
        return True

    db.execute("UPDATE love SET cnt=? WHERE uid=?", (count, uid))
    db.commit()
    return False


# ---------- Groq AI ----------
async def ask_ai(system: str, messages: list, max_tokens: int = 600):
    groq_messages = [{"role": "system", "content": system}]
    for message in messages:
        role = message["role"]
        if role in ("user", "assistant") and message.get("content"):
            groq_messages.append({
                "role": role,
                "content": message["content"],
            })

    last_error = None
    for attempt in range(1, 4):
        try:
            response = await client.chat.completions.create(
                model=GROQ_MODEL,
                messages=groq_messages,
                temperature=0.7,
                max_tokens=max_tokens,
            )
            answer = (response.choices[0].message.content or "").strip()
            if answer:
                return answer
            last_error = ValueError("Groq bo'sh javob qaytardi.")
        except Exception as error:
            last_error = error
            log.warning(
                "Groq API xatosi (%d-urinish): %s",
                attempt,
                str(error)[:250],
            )
        if attempt < 3:
            await asyncio.sleep(attempt)

    raise last_error


# ---------- Uzoq xotira ----------
async def update_notes(uid: int):
    recent = "\n".join(
        f"{message['role']}: {message['content']}"
        for message in history(uid, 14)
    )
    notes = await ask_ai(
        (
            "Sen xotira yozuvchisan. Eski yozuv va yangi suhbatdan foydalanuvchi "
            "haqida keyingi suhbatlarda foydali bo'lishi mumkin bo'lgan muhim "
            "faktlarni qisqa punktlar bilan yangila. Faqat yozuvning o'zini qaytar."
        ),
        [{
            "role": "user",
            "content": f"ESKI YOZUV:\n{get_notes(uid)}\n\nYANGI SUHBAT:\n{recent}",
        }],
        max_tokens=700,
    )
    if notes:
        db.execute(
            "INSERT INTO mem(uid, notes, counter) VALUES(?,?,0) "
            "ON CONFLICT(uid) DO UPDATE SET notes=excluded.notes, counter=0",
            (uid, notes),
        )
        db.commit()


bg_tasks = set()


async def update_notes_safe(uid: int):
    try:
        await update_notes(uid)
    except Exception:
        log.exception("Xotirani yangilashda xato")


# ---------- Telegram handlerlar ----------
async def start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    is_asila = bool(ASILA_ID) and update.effective_user.id == ASILA_ID
    await update.message.reply_text(START_ASILA if is_asila else START_TEXT)


async def my_id(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        f"Sening Telegram ID raqaming: {update.effective_user.id}"
    )


async def forget(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    db.execute("DELETE FROM msgs WHERE uid=?", (uid,))
    db.execute("DELETE FROM mem WHERE uid=?", (uid,))
    db.commit()
    await update.message.reply_text("Xotiradagi ma'lumotlarni o'chirdim. 🙂")


async def behruz_toggle(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    if not ASILA_ID or uid != ASILA_ID:
        return

    db.execute("INSERT OR IGNORE INTO love(uid) VALUES(?)", (uid,))
    enabled = db.execute(
        "SELECT on_off FROM love WHERE uid=?",
        (uid,),
    ).fetchone()[0]
    db.execute(
        "UPDATE love SET on_off=? WHERE uid=?",
        (0 if enabled else 1, uid),
    )
    db.commit()

    await update.message.reply_text(
        "Yaxshi, endi Behruz haqidagi eslatmalarni yubormayman 🙂"
        if enabled
        else "Mayli, eslatmalar yana yoqildi 😊"
    )


async def non_text(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Hozircha faqat matnli xabarlarni tushunaman 🌸 Yozib yubor."
    )


async def chat(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id

    user_text = update.message.text
    save(uid, "user", user_text)

    is_asila = bool(ASILA_ID) and uid == ASILA_ID
    notes = get_notes(uid)
    system = PERSONA + (ASILA_EXTRA if is_asila else "")

    if notes:
        system += f"\n\nBu odam haqida avvalgi suhbatlardan saqlangan eslatmalar:\n{notes}"

    if is_asila and love_due(uid):
        system += LOVE_HINT

    await ctx.bot.send_chat_action(
        chat_id=update.effective_chat.id,
        action="typing",
    )

    try:
        answer = await ask_ai(system, history(uid))
        if not answer:
            raise ValueError("Groq bo'sh javob qaytardi.")
    except Exception:
        log.exception("Groq API bilan ishlashda xato")
        await update.message.reply_text(
            "Hozir ulanishda muammo bo'ldi. Birozdan keyin yana yozib ko'r 🙏"
        )
        return

    save(uid, "assistant", answer)
    await update.message.reply_text(answer)

    db.execute("INSERT OR IGNORE INTO mem(uid) VALUES(?)", (uid,))
    db.execute("UPDATE mem SET counter=counter+1 WHERE uid=?", (uid,))
    db.commit()

    counter = db.execute(
        "SELECT counter FROM mem WHERE uid=?",
        (uid,),
    ).fetchone()[0]

    if counter >= 10:
        task = asyncio.create_task(update_notes_safe(uid))
        bg_tasks.add(task)
        task.add_done_callback(bg_tasks.discard)


# ---------- Render uchun ping server ----------
class Ping(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.end_headers()
        self.wfile.write(b"ok")

    do_HEAD = do_GET

    def log_message(self, *_args):
        pass


def run_ping_server():
    port = int(os.getenv("PORT", "10000"))
    server = HTTPServer(("0.0.0.0", port), Ping)
    log.info("Ping server %s-portda ishga tushdi", port)
    server.serve_forever()


def main():
    threading.Thread(target=run_ping_server, daemon=True).start()

    app = ApplicationBuilder().token(TG_TOKEN).build()
    app.add_handler(CommandHandler("start", start, filters=private_only))
    app.add_handler(CommandHandler("id", my_id, filters=private_only))
    app.add_handler(CommandHandler("forget", forget, filters=private_only))
    app.add_handler(CommandHandler("behruz", behruz_toggle, filters=private_only))
    app.add_handler(
        MessageHandler(private_only & filters.TEXT & ~filters.COMMAND, chat)
    )
    app.add_handler(
        MessageHandler(private_only & ~filters.TEXT & ~filters.COMMAND, non_text)
    )

    log.info("Telegram bot ishga tushmoqda. Groq modeli: %s", GROQ_MODEL)
    app.run_polling()


if __name__ == "__main__":
    main()
