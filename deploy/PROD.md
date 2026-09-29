# Прод-деплой apple-store-tg

Короткая инструкция по первому разворачиванию на сервере заказчика и по
типовым операциям после. Про lab (staging под подпутём на общем сервере) здесь
ничего нет - это отдельный процесс, см. `deploy/lab-deploy.sh`.

## 1. Требования к серверу

- Любой VPS с публичным IP, 1 vCPU / 1 ГБ RAM достаточно для старта.
- Ubuntu/Debian (или любой Linux с systemd).
- Docker Engine + Docker Compose plugin (`docker compose version` должен
  работать). Установка: https://docs.docker.com/engine/install/.
- Открытые порты 80 и 443 (входящие) - их слушает Caddy.
- Домен заказчика, указывающий A-записью на IP сервера (см. ниже).

## 2. DNS

У регистратора/DNS-провайдера домена заказчика создать:

```
A    <домен или поддомен>    <IP сервера>
```

Например `shop.example.com A 203.0.113.10`. Подождать, пока запись
распространится (`dig +short shop.example.com` должен вернуть IP сервера).
Caddy сам получит TLS-сертификат через Let's Encrypt при первом запуске -
домен должен резолвиться в IP сервера уже к этому моменту.

## 3. Клонировать репозиторий

```bash
ssh root@<IP сервера>
git clone <URL репозитория> apple-store-tg
cd apple-store-tg
```

## 4. Заполнить `.env`

```bash
cp .env.example .env
nano .env   # или любой редактор
```

Что и где взять:

| Переменная | Где взять |
|---|---|
| `BOT_TOKEN` | [@BotFather](https://t.me/BotFather) → `/newbot` (или токен существующего бота) |
| `BOT_USERNAME` | username бота без `@`, тот же BotFather |
| `MINIAPP_SHORT_NAME` | BotFather → выбрать бота → `/newapp` → задать короткое имя Mini App |
| `CHANNEL_ID` | id канала (`-100...`). Способ: переслать любое сообщение из канала боту [@userinfobot](https://t.me/userinfobot), либо временно поставить `SET_WEBHOOK_ON_START=0`, запустить бота локально/на сервере и посмотреть `chat.id` через `getUpdates` (`https://api.telegram.org/bot<TOKEN>/getUpdates`) после того, как бот добавлен в канал администратором |
| `ADMIN_IDS` | telegram_id продавца/заказчика через запятую. Узнать свой id: написать [@userinfobot](https://t.me/userinfobot) |
| `SELLER_USERNAME` | `@username` продавца без `@`, для кнопки «Написать продавцу» в посте канала |
| `LEADS_CHAT_ID` | куда бот шлёт заявки покупателей; можно оставить пустым - тогда уйдёт первому из `ADMIN_IDS` |
| `PUBLIC_BASE_URL` | `https://<домен>` - тот же домен, что в DNS-записи (шаг 2), без слэша на конце |
| `WEBHOOK_SECRET` | сгенерировать: `openssl rand -hex 32` |
| `BACKUP_CHAT_ID` | chat_id, куда слать ежедневный бэкап базы (например, личка админа или отдельный приватный чат). Тот же способ, что для `CHANNEL_ID`/`ADMIN_IDS`. Пусто или `0` - бэкапы выключены |
| `BACKUP_HOUR_UTC` | час (0-23, UTC) ежедневного бэкапа; по умолчанию `3` - можно не трогать |

`DB_PATH` и `MEDIA_DIR` в `.env` можно оставить как есть - в проде их
переопределяет `deploy/docker-compose.yml` (`/data/app.db`, `/data/media`
внутри именованного volume `appdata`).

## 5. Запуск

```bash
DOMAIN=shop.example.com docker compose -f deploy/docker-compose.yml up -d --build
```

Первый запуск: собирается образ (фронт + бэкенд), поднимается контейнер
`app` (внутри - миграции `alembic upgrade head`, затем uvicorn) и `caddy`
(получает TLS-сертификат для `DOMAIN`). Проверить:

```bash
docker compose -f deploy/docker-compose.yml ps
docker compose -f deploy/docker-compose.yml logs -f app
```

## 6. BotFather: указать Mini App и права бота

1. BotFather → выбрать бота → `Bot Settings` → `Menu Button` (или
   `/mybots` → бот → `Edit Bot` → `Edit Menu Button`) → задать URL
   `https://<домен>/` - это открывает Mini App по кнопке в чате с ботом.
2. Тот же short name из шага 4 (`/newapp`) уже привязан к боту - ссылки вида
   `https://t.me/<BOT_USERNAME>/<MINIAPP_SHORT_NAME>` заработают сразу после
   деплоя, ничего дополнительно менять не нужно.
3. Добавить бота администратором канала (`CHANNEL_ID`) с правами:
   - публиковать сообщения (Post Messages),
   - редактировать сообщения (Edit Messages of Others) - нужно для правки
     цены/заметки из AdminPage,
   - удалять сообщения (Delete Messages of Others) - нужно для `/sold`.

## 7. Проверка после деплоя

- `curl https://<домен>/api/health` → `{"status":"ok"}`.
- Открыть `https://<домен>/` в браузере - витрина (сайт).
- В Telegram: `/new` в чате с ботом → пройти визард до конца →
  «Опубликовать» → пост появился в канале с двумя кнопками.
- Нажать «Открыть в приложении» на посте → открывается Mini App на нужном
  лоте.
- Кнопка «Купить» из Mini App и с сайта → заявка приходит в `LEADS_CHAT_ID`.
- `/sold <public_id>` (или кнопка «Продано» в `/list` / AdminPage) → пост
  пропадает из канала, лот пропадает с витрины.
- `/backup` → в `BACKUP_CHAT_ID` приходит файл `apple-store-tg-*.db.gz`.

## 8. Обновление (деплой новой версии)

```bash
cd apple-store-tg
git pull
DOMAIN=shop.example.com docker compose -f deploy/docker-compose.yml up -d --build
```

Миграции применяются автоматически при старте контейнера `app`
(`alembic upgrade head` в entrypoint). Данные (`appdata` volume) не
затрагиваются пересборкой образа.

## 9. Восстановление из бэкапа

```bash
cd apple-store-tg
gunzip -k apple-store-tg-YYYYMMDD-HHMM.db.gz   # -k: оставить .gz на всякий случай

docker compose -f deploy/docker-compose.yml stop app
docker cp apple-store-tg-YYYYMMDD-HHMM.db "$(docker compose -f deploy/docker-compose.yml ps -q app || echo apple-store-tg-app-1)":/data/app.db
# если контейнер уже остановлен и docker cp в него не работает - скопировать
# через временный контейнер на том же volume:
docker run --rm -v apple-store-tg_appdata:/data -v "$(pwd)":/backup alpine \
  sh -c "cp /backup/apple-store-tg-YYYYMMDD-HHMM.db /data/app.db"

docker compose -f deploy/docker-compose.yml start app
docker compose -f deploy/docker-compose.yml logs -f app   # убедиться, что стартовал без ошибок
```

## 10. Перенос данных с lab на прод

С lab (`$DEPLOY_HOST`, см. `deploy/lab-deploy.sh`):

```bash
ssh "$DEPLOY_HOST" "systemctl stop apple-store-tg"
scp "$DEPLOY_HOST":/var/lib/apple-store-tg/app.db ./app.db
scp -r "$DEPLOY_HOST":/var/lib/apple-store-tg/media ./media
ssh "$DEPLOY_HOST" "systemctl start apple-store-tg"
```

На проде (после первого `docker compose up`, чтобы volume `appdata` уже
существовал):

```bash
docker compose -f deploy/docker-compose.yml stop app
docker run --rm -v apple-store-tg_appdata:/data -v "$(pwd)":/src alpine \
  sh -c "cp /src/app.db /data/app.db && cp -r /src/media/. /data/media/"
docker compose -f deploy/docker-compose.yml start app
```

Имя volume всегда `apple-store-tg_appdata`: имя проекта зафиксировано в
`deploy/docker-compose.yml` (`name: apple-store-tg`). Проверить: `docker volume ls`.
