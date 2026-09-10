# Telegram-бот расписания ФА (Краснодар)

Бот собирает расписание с сайта krasnodar.fa.ru и рассылает его по группам.

## Установка

pip install -r requirements.txt

## Настройка

Создай .env на основе .env.example и укажи BOT_TOKEN и ADMIN_IDS.

## Запуск

python bot.py

## Команды

/start — регистрация
/setgroup <название> — выбрать группу
/schedule — расписание
/update — обновить (админ)
/broadcast <текст> — рассылка (админ)
/stats — статистика (админ)