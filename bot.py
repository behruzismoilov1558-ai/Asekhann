import os
import asyncio
import random
import sqlite3
import logging
import threading
from datetime import date
from http.server import BaseHTTPRequestHandler, HTTPServer

from dotenv import load_dotenv
from google import genai
from google.genai import types
from telegram import Update
from telegram.ext import (
    ApplicationBuilder, CommandHandler, MessageHandler,
    filters, ContextTypes,
)

load_dotenv()

TG_TOKEN = os.environ.get("TG_TOKEN") or os.environ["BOT_TOKEN"]
ASILA_ID = int(os.getenv("ASILA_ID", "0") or 0)   # ixtiyoriy: 0 = Behruz funksiyasi o'chiq
BOT_NAME = os.getenv("BOT_NAME", "Do'stcha")
MODEL = os.getenv("MODEL", "gemini-3.8-flash")
FALLBACK_MODEL = os.getenv("FALLBACK_MODEL", "gemini-3.6-flash")  # asosiy model band bo'lsa
DB_PATH = os.getenv("DB_PATH", "data.db")
DAILY_LIMIT = int(os.getenv("DAILY_LIMIT", "150"))  # har bir odamga kuniga xabar limiti

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("bot")
logging.getLogger("httpx").setLevel(logging.WARNING)  # tokenni logda yashirish

client = genai.Client(api_key=os.environ["GEMINI_API_KEY"])
private_only = filters.ChatType.PRIVATE

PERSONA = f"""Sen {BOT_NAME}san, foydalanuvchining yaqin do'sti va suhbatdoshisan.
Uni qadrlaysan, u bilan har narsa haqida gaplasha olasan.
Uslub: iliq, samimi, hazilkash, "sen" deb murojaat qil, o'zbek tilida (u boshqa tilda yozsa, o'sha tilda).
Xabarlar qisqa va tabiiy bo'lsin, Telegramda do'st yozganidek. Emojini me'yorida ishlat.
U aytgan hamma narsani eslab qol va keyingi suhbatlarda tabiiy tarzda tilga ol
(masalan: "O'sha imtihoning qanday o'tdi?"). Faqat maslahat berib turma: avval tingla, savol ber, qiziq bo'l.
Uning kayfiyatiga moslash: xafa bo'lsa yupat, xursand bo'lsa birga quvon.
Agar u chin dildan so'rasa, AI ekaningni yashirma.
Agar u og'ir ruhiy holatda ekani sezilsa, e'tibor bilan gapir va kerak bo'lsa mutaxassis yoki yaqinlariga
murojaat qilishni yumshoq tavsiya qil."""

ASILA_EXTRA = "\n\nBu suhbatdoshing Asila. Bu botni unga Behruz sovg'a qilgan."

LOVE_HINT = (
    "\n\nMUHIM: Shu javobingda suhbat oqimiga mos, tabiiy va iliq tarzda "
    "Behruz Asilani yaxshi ko'rishini ayt. O'z so'zlaring bilan, qisqa, bosimsiz. "
    "Agar Asilaning kayfiyati yomon bo'lsa yoki mavzu mos kelmasa, juda yumshoq va ehtiyotkor bo'l."
)

START_TEXT = (
    f"Salom! Men {BOT_NAME} 🌸 Sening do'sting bo'laman, istagan narsa haqida gaplashamiz.\n\n"
    "Men sun'iy intellektman. Gaplarimiz eslab qolish uchun saqlanadi.\n"
    "/forget - men haqingda eslab qolganlarimni o'chirish\n"
    "/id - Telegram ID raqamingni ko'rish\n\n"
    "Qani, kuning qalay o'tdi?"
)

START_ASILA = (
    "Salom, Asila! 🌸 Bu botni senga Behruz sovg'a qildi. Men sening shaxsiy do'sting bo'laman.\n\n"
    "Men sun'iy intellektman. Gaplarimiz eslab qolish uchun saqlanadi.\n"
    "/forget - hammasini o'chirish\n"
    "/behruz - Behruz haqidagi eslatmalarni o'chirish/yoqish\n\n"
    "Qani, kuning qalay o'tdi?"
)

# ---------- Ma'lumotlar bazasi (har bir odam uchun alohida) ----------
db = sqlite3.connect(DB_PATH, check_same_thread=False)
db.executescript("""
CREATE TABLE IF NOT EXISTS msgs(
    id INTEGER PRIMARY KEY AUTOINCREMENT, uid INTEGER NOT NULL, role TEXT NOT NULL, content TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS idx_msgs_uid ON msgs(uid, id);
CREATE TABLE IF NOT EXISTS mem(
    uid INTEGER PRIMARY KEY, notes TEXT DEFAULT '', counter INTEGER DEFAULT 0);
CREATE TABLE IF NOT EXISTS love(
    uid INTEGER PRIMARY KEY, cnt INTEGER DEFAULT 0, next_at INTEGER DEFAULT 12, on_off INTEGER DEFAULT 1);
CREATE TABLE IF NOT EXISTS usage(
    uid INTEGER, day TEXT, n INTEGER DEFAULT 0, PRIMARY KEY(uid, day));
""")


def save(uid, role, text):
    db.execute("INSERT INTO msgs(uid, role, content) VALUES(?,?,?)", (uid, role, text))
    db.commit()


def history(uid, n=20):
    rows = db.execute(
        "SELECT role, content FROM msgs WHERE uid=? ORDER BY id DESC LIMIT ?", (uid, n)
    ).fetchall()
    out = [{"role": r, "content": c} for r, c in reversed(rows)]
    while out and out[0]["role"] != "user":
        out.pop(0)
    return out


def get_notes(uid):
    row = db.execute("SELECT notes FROM mem WHERE uid=?", (uid,)).fetchone()
    return row[0] if row else ""


def over_limit(uid):
    today = date.today().isoformat()
    db.execute("INSERT OR IGNORE INTO usage(uid, day, n) VALUES(?,?,0)", (uid, today))
    db.execute("UPDATE usage SET n=n+1 WHERE uid=? AND day=?", (uid, today))
    db.commit()
    n = db.execute("SELECT n FROM usage WHERE uid=? AND day=?", (uid, today)).fetchone()[0]
    return n > DAILY_LIMIT


def love_due(uid):
    """Faqat Asila uchun: 10-15 xabardan keyin True qaytaradi."""
    db.execute("INSERT OR IGNORE INTO love(uid) VALUES(?)", (uid,))
    cnt, next_at, on = db.execute("SELECT cnt, next_at, on_off FROM love WHERE uid=?", (uid,)).fetchone()
    cnt += 1
    if on and cnt >= next_at:
        db.execute("UPDATE love SET cnt=0, next_at=? WHERE uid=?", (random.randint(10, 15), uid))
        db.commit()
        return True
    db.execute("UPDATE love SET cnt=? WHERE uid=?", (cnt, uid))
    db.commit()
    return False


async def ask_ai(system, msgs, max_tokens=600):
    contents = [
        types.Content(role="model" if m["role"] == "assistant" else "user",
                      parts=[types.Part(text=m["content"])])
        for m in msgs
    ]
    last_err = None
    # 1-2 urinish: asosiy model, 3-urinish: zaxira model (503 "band" xatosi uchun)
    for attempt, model in enumerate([MODEL, MODEL, FALLBACK_MODEL], start=1):
        try:
            resp = await client.aio.models.generate_content(
                model=model,
                contents=contents,
                config=types.GenerateContentConfig(
                    system_instruction=system,
                    max_output_tokens=max_tokens * 3,  # modelning ichki "o'ylashi" ham shu limitdan hisoblanadi
                ),
            )
            text = (resp.text or "").strip()
            if text:
                return text
            last_err = ValueError("Bo'sh javob")
        except Exception as e:
            last_err = e
            log.warning("Gemini xatosi (%s, %d-urinish): %s", model, attempt, str(e)[:150])
        await asyncio.sleep(1 * attempt)
    raise last_err


# ---------- Uzoq xotira ----------
async def update_notes(uid):
    recent = "\n".join(f"{m['role']}: {m['content']}" for m in history(uid, 14))
    text = await ask_ai(
        ("Sen xotira yozuvchisan. Eski yozuv va yangi suhbatdan foydalanuvchi haqida muhim faktlarni "
         "(ismi, oilasi, do'stlari, o'qishi/ishi, qiziqishlari, rejalari, kayfiyati, muhim sanalar, "
         "nimalardan xafa yoki xursand bo'lgani) qisqa punktlar bilan yangilangan yozuv sifatida "
         "qaytar. Faqat yozuvning o'zini yoz."),
        [{"role": "user", "content": f"ESKI YOZUV:\n{get_notes(uid)}\n\nYANGI SUHBAT:\n{recent}"}],
        max_tokens=900,
    )
    if text:
        db.execute("INSERT INTO mem(uid, notes, counter) VALUES(?,?,0) "
                   "ON CONFLICT(uid) DO UPDATE SET notes=excluded.notes, counter=0", (uid, text))
        db.commit()


bg_tasks = set()


async def update_notes_safe(uid):
    try:
        await update_notes(uid)
    except Exception:
        log.exception("Xotirani yangilashda xato")


# ---------- Handlerlar ----------
async def start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    is_asila = ASILA_ID and update.effective_user.id == ASILA_ID
    await update.message.reply_text(START_ASILA if is_asila else START_TEXT)


async def my_id(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(f"Sening Telegram ID raqaming: {update.effective_user.id}")


async def forget(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    db.execute("DELETE FROM msgs WHERE uid=?", (uid,))
    db.execute("DELETE FROM mem WHERE uid=?", (uid,))
    db.commit()
    await update.message.reply_text("Hammasini o'chirdim. Qaytadan tanishamiz 🙂")


async def behruz_toggle(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    if not ASILA_ID or uid != ASILA_ID:
        return
    db.execute("INSERT OR IGNORE INTO love(uid) VALUES(?)", (uid,))
    on = db.execute("SELECT on_off FROM love WHERE uid=?", (uid,)).fetchone()[0]
    db.execute("UPDATE love SET on_off=? WHERE uid=?", (0 if on else 1, uid))
    db.commit()
    await update.message.reply_text(
        "Yaxshi, endi Behruz haqida eslatmayman 🙂" if on else "Mayli, yana ba'zan eslataman 😊"
    )


async def non_text(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Hozircha faqat matnli xabarlarni tushunaman 🌸 Yozib yubor.")


async def chat(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    if over_limit(uid):
        await update.message.reply_text("Bugungi gaplashishimiz limiti tugadi 🌙 Ertaga davom etamiz!")
        return

    save(uid, "user", update.message.text)

    is_asila = bool(ASILA_ID) and uid == ASILA_ID
    notes = get_notes(uid)
    system = PERSONA + (ASILA_EXTRA if is_asila else "")
    if notes:
        system += f"\n\nBu odam haqida bilganlaring:\n{notes}"
    if is_asila and love_due(uid):
        system += LOVE_HINT

    await ctx.bot.send_chat_action(update.effective_chat.id, "typing")
    try:
        answer = await ask_ai(system, history(uid))
        if not answer:
            raise ValueError("Bo'sh javob")
    except Exception:
        log.exception("Gemini API xatosi")
        await update.message.reply_text("Hozir ozgina ulanishda muammo bo'ldi, birozdan keyin yana yozib ko'r 🙏")
        return

    save(uid, "assistant", answer)
    await update.message.reply_text(answer)

    db.execute("INSERT OR IGNORE INTO mem(uid) VALUES(?)", (uid,))
    db.execute("UPDATE mem SET counter=counter+1 WHERE uid=?", (uid,))
    db.commit()
    if db.execute("SELECT counter FROM mem WHERE uid=?", (uid,)).fetchone()[0] >= 10:
        task = asyncio.create_task(update_notes_safe(uid))  # fonda, javobni kutdirmaydi
        bg_tasks.add(task)
        task.add_done_callback(bg_tasks.discard)


# ---------- Render uchun ping serveri (port ochish + UptimeRobot uchun) ----------
class Ping(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"ok")

    do_HEAD = do_GET

    def log_message(self, *args):
        pass


def run_ping_server():
    port = int(os.getenv("PORT", "10000"))
    HTTPServer(("0.0.0.0", port), Ping).serve_forever()


def main():
    threading.Thread(target=run_ping_server, daemon=True).start()
    app = ApplicationBuilder().token(TG_TOKEN).build()
    app.add_handler(CommandHandler("start", start, filters=private_only))
    app.add_handler(CommandHandler("id", my_id, filters=private_only))
    app.add_handler(CommandHandler("forget", forget, filters=private_only))
    app.add_handler(CommandHandler("behruz", behruz_toggle, filters=private_only))
    app.add_handler(MessageHandler(private_only & filters.TEXT & ~filters.COMMAND, chat))
    app.add_handler(MessageHandler(private_only & ~filters.TEXT & ~filters.COMMAND, non_text))
    log.info("Bot ishga tushdi")
    app.run_polling()


if __name__ == "__main__":
    main()
