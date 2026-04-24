# Madrid TCG Events

Агрегатор расписания TCG-событий из магазинов Мадрида.

## Что делает этот проект

Скрипты обходят сайты магазинов, собирают анонсы турниров и игровых вечеров,
склеивают всё в один файл `public/events.json`, который отображается на
статическом сайте через GitHub Pages. GitHub Actions запускает обновление
автоматически по расписанию.

## Структура

```
madrid-tcg/
├── scrapers/          # По одному .py файлу на каждый магазин
├── aggregator.py      # Собирает данные от всех скраперов в events.json
├── public/            # Фронтенд: index.html + events.json
├── .github/workflows/ # Автозапуск через GitHub Actions
└── requirements.txt   # Python-зависимости
```

## Запуск локально

```bash
# 1. Создать виртуальное окружение
python -m venv .venv
source .venv/bin/activate   # macOS/Linux
# .venv\Scripts\activate    # Windows

# 2. Установить зависимости
pip install -r requirements.txt

# 3. Установить браузер для Playwright (нужен для сайтов с JS)
playwright install chromium

# 4. Запустить агрегатор
python aggregator.py
```

## Магазины

<!-- Список будет пополняться -->
- [ ] (добавь сюда названия магазинов)

## Технологии

- Python (requests, BeautifulSoup, Playwright)
- Статический HTML + GitHub Pages
- GitHub Actions (автообновление по расписанию)
