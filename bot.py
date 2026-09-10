# -*- coding: utf-8 -*-
"""
Telegram-бот расписания Финансового университета (Краснодарский филиал).
Парсит .xls-файлы расписания с fa.ru и рассылает по группам.
"""
import asyncio
import os
import io
import logging
import sqlite3
from datetime import datetime

import requests
import pandas as pd
from dotenv import load_dotenv
from aiogram import Bot, Dispatcher
from aiogram.types import Message
from aiogram.filters import Command
from aiogram.enums import ParseMode
from apscheduler.schedulers.asyncio import AsyncIOScheduler

load_dotenv()
BOT_TOKEN = os.getenv("BOT_TOKEN")
ADMIN_IDS = [int(x) for x in os.getenv("ADMIN_IDS", "").split(",") if x.strip()]

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("fa_bot")

DB_PATH = "schedule.db"

# --- Ссылки на .xls по курсам ---
SCHEDULE_URLS = {
    "1 курс ОФО НЧН": "https://www.fa.ru/upload/constructor/f52/i3c4tmq6tccoj9bofb9igib8ela5xw3j/1-kurs-OFO-NCHN_-_07.09.2026_12.09.2026.xls",
    "2 курс ОФО НЧН": "https://www.fa.ru/upload/constructor/394/41aghprwnuiv4o3liit606yuyygfe5vo/2-kurs-OFO-NCHN_-_07.09.2026_12.09.2026.xls",
    "3 курс ОФО НЧН": "https://www.fa.ru/upload/constructor/c75/70x5kjh836vc1ivlelq20e91xij41sg9/3-kurs-OFO-NCHN_-_07.09.2026_12.09.2026.xls",
    "4 курс ОФО НЧН": "https://www.fa.ru/upload/constructor/315/qtoyt68dun8j4lniguh5ozzlwxoymtug/4-kurs-OFO-NCHN_-_07.09.2026_12.09.2026.xls",
}

# ---------- БД ----------
def init_db():
    con = sqlite3.connect(DB_PATH)
    cur = con.cursor()
    cur.execute("""
        CREATE TABLE IF NOT EXISTS users (
            telegram_id INTEGER PRIMARY KEY,
            role TEXT DEFAULT 'student',
            group_name TEXT
        )
    """)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS schedule_cache (
            course TEXT PRIMARY KEY,
            content TEXT,
            updated_at TEXT
        )
    """)
    con.commit()
    con.close()

def get_user(tg_id: int):
    con = sqlite3.connect(DB_PATH)
    cur = con.cursor()
    cur.execute("SELECT role, group_name FROM users WHERE telegram_id=?", (tg_id,))
    row = cur.fetchone()
    con.close()
    return row

def upsert_user(tg_id: int, group_name: str, role: str = "student"):
    con = sqlite3.connect(DB_PATH)
    cur = con.cursor()
    cur.execute("""
        INSERT INTO users (telegram_id, role, group_name)
        VALUES (?, ?, ?)
        ON CONFLICT(telegram_id) DO UPDATE SET group_name=excluded.group_name, role=excluded.role
    """, (tg_id, role, group_name))
    con.commit()
    con.close()

def all_users():
    con = sqlite3.connect(DB_PATH)
    cur = con.cursor()
    cur.execute("SELECT telegram_id, group_name FROM users")
    rows = cur.fetchall()
    con.close()
    return rows

def save_schedule(course: str, content: str):
    con = sqlite3.connect(DB_PATH)
    cur = con.cursor()
    cur.execute("""
        INSERT INTO schedule_cache (course, content, updated_at)
        VALUES (?, ?, ?)
        ON CONFLICT(course) DO UPDATE SET content=excluded.content, updated_at=excluded.updated_at
    """, (course, content, datetime.now().isoformat(timespec="seconds")))
    con.commit()
    con.close()

def get_schedule(course: str):
    con = sqlite3.connect(DB_PATH)
    cur = con.cursor()
    cur.execute("SELECT content, updated_at FROM schedule_cache WHERE course=?", (course,))
    row = cur.fetchone()
    con.close()
    return row

# ---------- Парсер .xls ----------
def download_xls(url: str) -> bytes:
    r = requests.get(url, timeout=30)
    r.raise_for_status()
    return r.content

def parse_xls(raw: bytes) -> str:
    """Читает .xls и превращает в текстовое представление для Telegram."""
    xls = pd.ExcelFile(io.BytesIO(raw))
    parts = []
    for sheet in xls.sheet_names:
        df = xls.parse(sheet, header=None)
        df = df.dropna(how="all").dropna(axis=1, how="all")
        if df.empty:
            continue
        parts.append(f"Лист: {sheet}")
        for _, row in df.iterrows():
            cells = [str(c).strip() for c in row.tolist() if str(c).strip() and str(c) != "nan"]
            if cells:
                parts.append(" | ".join(cells))
    text = "\n".join(parts).strip()
    return text[:3900]  # лимит Telegram-сообщения

def fetch_all_schedules() -> dict:
    result = {}
    for course, url in SCHEDULE_URLS.items():
        try:
            raw = download_xls(url)
            text = parse_xls(raw)
            result[course] = text
            save_schedule(course, text)
            log.info("Обновлено расписание: %s (%d симв.)", course, len(text))
        except Exception as e:
            log.exception("Ошибка загрузки %s: %s", course, e)
    return result

# ---------- Бот ----------
bot = Bot(token=BOT_TOKEN, parse_mode=ParseMode.HTML)
dp = Dispatcher()

HELP = (
    "Бот расписания Финансового университета (Краснодар)\n\n"
    "Команды:\n"
    "/start — регистрация\n"
    "/setgroup 1 курс ОФО НЧН — выбрать группу\n"
    "/schedule — текущее расписание\n"
    "/update — обновить расписание (админ)\n"
    "/broadcast текст — рассылка (админ)\n"
    "/stats — статистика (админ)\n"
)

@dp.message(Command("start"))
async def cmd_start(msg: Message):
    await msg.answer(HELP)
    await msg.answer("Укажи свою группу командой:\n/setgroup 1 курс ОФО НЧН")

@dp.message(Command("help"))
async def cmd_help(msg: Message):
    await msg.answer(HELP)

@dp.message(Command("setgroup"))
async def cmd_setgroup(msg: Message):
    args = msg.text.split(maxsplit=1)
    if len(args) < 2:
        await msg.answer("Формат: /setgroup 1 курс ОФО НЧН")
        return
    group = args[1].strip()
    if group not in SCHEDULE_URLS:
        await msg.answer("Доступные группы:\n" + "\n".join(f"• {g}" for g in SCHEDULE_URLS))
        return
    upsert_user(msg.from_user.id, group)
    await msg.answer(f"Группа сохранена: {group}")

@dp.message(Command("schedule"))
async def cmd_schedule(msg: Message):
    user = get_user(msg.from_user.id)
    if not user or not user[1]:
        await msg.answer("Сначала укажи группу: /setgroup 1 курс ОФО НЧН")
        return
    group = user[1]
    row = get_schedule(group)
    if not row:
        await msg.answer("Расписание ещё не загружено. Попробуйте позже.")
        return
    content, updated = row
    await msg.answer(f"{group}\nОбновлено: {updated}\n\n{content}")

@dp.message(Command("update"))
async def cmd_update(msg: Message):
    if msg.from_user.id not in ADMIN_IDS:
        await msg.answer("Нет доступа.")
        return
    await msg.answer("Обновляю расписание...")
    data = fetch_all_schedules()
    await msg.answer(f"Обновлено: {len(data)} курсов.")

@dp.message(Command("broadcast"))
async def cmd_broadcast(msg: Message):
    if msg.from_user.id not in ADMIN_IDS:
        return
    args = msg.text.split(maxsplit=1)
    if len(args) < 2:
        await msg.answer("Формат: /broadcast текст объявления")
        return
    text = args[1]
    sent = 0
    for tg_id, _ in all_users():
        try:
            await bot.send_message(tg_id, f"Объявление\n\n{text}")
            sent += 1
        except Exception:
            pass
    await msg.answer(f"Отправлено: {sent}")

@dp.message(Command("stats"))
async def cmd_stats(msg: Message):
    if msg.from_user.id not in ADMIN_IDS:
        return
    users = all_users()
    await msg.answer(f"Пользователей: {len(users)}")

# ---------- Планировщик ----------
async def scheduled_update():
    fetch_all_schedules()

async def main():
    init_db()
    scheduler = AsyncIOScheduler()
    scheduler.add_job(scheduled_update, "interval", minutes=30)
    scheduler.start()
    await asyncio.to_thread(fetch_all_schedules)
    log.info("Бот запущен")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())