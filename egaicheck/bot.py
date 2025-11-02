"""Telegram bot entry point for checking FS RAR marks."""
from __future__ import annotations

import asyncio
import logging
from getpass import getpass
from pathlib import Path
from tempfile import NamedTemporaryFile

from telegram import Update
from telegram.ext import (
    AIORateLimiter,
    Application,
    ApplicationBuilder,
    CommandHandler,
    ContextTypes,
    ConversationHandler,
    MessageHandler,
    filters,
)

from .check_client import Check1FsrarClient, PendingCheck
from .ocr import decode_mark_from_image

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
LOGGER = logging.getLogger(__name__)

WAITING_FOR_PHOTO, WAITING_FOR_CAPTCHA = range(2)


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    await update.message.reply_text(
        "Здравствуйте! Отправьте фотографию или скан марки ЕГАИС, чтобы я мог её проверить."
    )
    return WAITING_FOR_PHOTO


async def request_photo_again(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    await update.message.reply_text("Пожалуйста, отправьте изображение марки.")
    return WAITING_FOR_PHOTO


async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if not update.message:
        return await request_photo_again(update, context)

    telegram_file = None
    if update.message.photo:
        telegram_file = await update.message.photo[-1].get_file()
    elif update.message.document and (update.message.document.mime_type or "").startswith("image/"):
        telegram_file = await update.message.document.get_file()
    else:
        return await request_photo_again(update, context)

    with NamedTemporaryFile(delete=False, suffix=".jpg") as tmp_file:
        temp_path = Path(tmp_file.name)
        await telegram_file.download_to_drive(custom_path=tmp_file.name)

    try:
        loop = asyncio.get_running_loop()
        mark_code = await loop.run_in_executor(None, decode_mark_from_image, temp_path)
    except Exception as exc:  # noqa: BLE001 - we report the error to the user
        LOGGER.exception("Failed to decode mark")
        await update.message.reply_text(
            "Не удалось распознать марку. Попробуйте отправить более чёткое изображение."
        )
        temp_path.unlink(missing_ok=True)
        return WAITING_FOR_PHOTO

    client = Check1FsrarClient()
    try:
        loop = asyncio.get_running_loop()
        pending, captcha_bytes = await loop.run_in_executor(None, client.prepare_check, mark_code)
    except Exception as exc:  # noqa: BLE001
        LOGGER.exception("Failed to request captcha from FS RAR")
        await update.message.reply_text(
            "Марка распознана как:\n%s\n\nНе удалось получить капчу с сайта ФСРАР. Попробуйте позже." % mark_code
        )
        temp_path.unlink(missing_ok=True)
        return WAITING_FOR_PHOTO

    temp_path.unlink(missing_ok=True)

    context.user_data["session"] = {"client": client, "pending": pending}

    await update.message.reply_photo(
        captcha_bytes,
        caption=(
            "Код марки:\n{code}\n\nВведите текст с картинки, чтобы выполнить проверку.".format(
                code=mark_code
            )
        ),
    )
    return WAITING_FOR_CAPTCHA


async def handle_captcha(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if not update.message or not update.message.text:
        await update.message.reply_text("Пожалуйста, отправьте текст с капчи.")
        return WAITING_FOR_CAPTCHA

    session_data = context.user_data.get("session")
    if not session_data:
        await update.message.reply_text("Сессия проверки не найдена. Отправьте марку ещё раз.")
        return WAITING_FOR_PHOTO

    captcha_value = update.message.text.strip()
    if not captcha_value:
        await update.message.reply_text("Ответ на капчу не может быть пустым.")
        return WAITING_FOR_CAPTCHA

    client: Check1FsrarClient = session_data["client"]
    pending: PendingCheck = session_data["pending"]

    try:
        loop = asyncio.get_running_loop()
        result = await loop.run_in_executor(None, client.submit_check, pending, captcha_value)
    except Exception as exc:  # noqa: BLE001
        LOGGER.exception("Failed to submit check request")
        await update.message.reply_text(
            "Не удалось завершить проверку марки. Проверьте правильность капчи и попробуйте снова."
        )
        return WAITING_FOR_CAPTCHA
    finally:
        context.user_data.pop("session", None)

    if isinstance(result, dict):
        formatted = "Результат проверки:\n" + "\n".join(
            f"- {key}: {value}" for key, value in result.items()
        )
    else:
        formatted = str(result)

    await update.message.reply_text(formatted)
    return ConversationHandler.END


async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data.pop("session", None)
    await update.message.reply_text("Проверка прервана. Отправьте /start, чтобы начать заново.")
    return ConversationHandler.END


def prompt_token() -> str:
    token = ""
    while not token:
        token = getpass("Введите токен Telegram-бота: ").strip()
    return token


def build_application(token: str) -> Application:
    return (
        ApplicationBuilder()
        .token(token)
        .rate_limiter(AIORateLimiter())
        .build()
    )


def main() -> None:
    token = prompt_token()
    app = build_application(token)

    conversation = ConversationHandler(
        entry_points=[CommandHandler("start", start)],
        states={
            WAITING_FOR_PHOTO: [MessageHandler((filters.PHOTO | filters.Document.IMAGE), handle_photo)],
            WAITING_FOR_CAPTCHA: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_captcha)],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
    )
    app.add_handler(conversation)
    app.add_handler(CommandHandler("cancel", cancel))

    LOGGER.info("Starting bot polling")
    app.run_polling(close_loop=False)


if __name__ == "__main__":
    main()

