# Happ Toolkit

Бесплатный интерфейс для просмотра и конвертации VPN-подписок. Статика публикуется через GitHub Pages, запросы к подписке выполняет Cloudflare Worker из `worker/`.

## Развёртывание backend

1. Создайте бесплатный аккаунт Cloudflare и выполните `cd worker && npx wrangler deploy`.
2. Сохраните приватную ссылку командой `npx wrangler secret put SUBSCRIPTION_URL`.
3. Откройте GitHub Pages и один раз укажите адрес Worker в поле **Backend URL**.

URL сохраняется только в секрете Cloudflare. Не добавляйте его в файлы репозитория, Actions-логи или frontend.

Поддерживаются прямые HTTP(S)-подписки и `happ://add/`. Форматы `happ://crypt*` невозможно независимо расшифровать без соответствующих приватных ключей Happ; такие ключи намеренно не включены.
