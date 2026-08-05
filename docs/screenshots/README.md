# Скриншоты UI

1360×900, Playwright, локальный сервер `http://127.0.0.1:8099`.

| Файл | URL |
|------|-----|
| `read.png` | `/read?focus=90` — клик по строке 13 |
| `search.png` | `/` — главная без фильтра по языку |
| `poem.png` | `/poem/90?line=13` |
| `sources.png` | `/sources` |

Переснять:

```bash
python -m petrarch_search serve --port 8099
# в другом терминале — playwright-скрипт или вручную
```

Используются в [`README.md`](../../README.md).
