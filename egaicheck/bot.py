"""Telegram bot entry point for checking FS RAR marks."""
from __future__ import annotations

import asyncio
import configparser
import logging
import sys
from pathlib import Path
from tempfile import NamedTemporaryFile

try:
    from tkinter import Tk
    from tkinter.simpledialog import askstring
except Exception:  # pragma: no cover - GUI import may fail in headless environments
    Tk = None  # type: ignore[assignment]
    askstring = None  # type: ignore[assignment]

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

LOG_FILE = Path(__file__).resolve().parent.parent / "egaicheck.log"
LOG_FILE.parent.mkdir(parents=True, exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[
        logging.StreamHandler(stream=sys.stdout),
        logging.FileHandler(LOG_FILE, encoding="utf-8"),
    ],
)
LOGGER = logging.getLogger(__name__)

CONFIG_DIR = Path.home() / ".egaicheck"
CONFIG_PATH = CONFIG_DIR / "config.ini"

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
LOGGER = logging.getLogger(__name__)

WAITING_FOR_PHOTO, WAITING_FOR_CAPTCHA = range(2)


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if update.message:
        LOGGER.info("Пользователь %s начал проверку", update.message.from_user.id if update.message.from_user else "unknown")
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

    LOGGER.info("Получено изображение для проверки от пользователя %s", update.message.from_user.id if update.message.from_user else "unknown")

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
    LOGGER.info("Пользователю %s отправлена капча для проверки", update.message.from_user.id if update.message.from_user else "unknown")
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

    LOGGER.info("Получен ответ на капчу от пользователя %s", update.message.from_user.id if update.message.from_user else "unknown")

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

    LOGGER.info("Отправлен результат проверки пользователю %s", update.message.from_user.id if update.message.from_user else "unknown")
    await update.message.reply_text(formatted)
    return ConversationHandler.END


async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data.pop("session", None)
    if update.message:
        LOGGER.info("Пользователь %s отменил проверку", update.message.from_user.id if update.message.from_user else "unknown")
    await update.message.reply_text("Проверка прервана. Отправьте /start, чтобы начать заново.")
    return ConversationHandler.END


def _load_token_from_config() -> str | None:
    if not CONFIG_PATH.exists():
        LOGGER.info("Конфигурационный файл токена не найден, потребуется ввод")
        return None

    parser = configparser.ConfigParser()
    try:
        parser.read(CONFIG_PATH, encoding="utf-8")
    except OSError:
        LOGGER.warning("Не удалось прочитать конфигурационный файл токена")
        return None

    token = parser.get("telegram", "token", fallback="").strip()
    if not token:
        LOGGER.info("В конфигурации отсутствует токен, потребуется ввод")
        return None

    LOGGER.info("Токен бота получен из конфигурационного файла")
    return token


def _save_token_to_config(token: str) -> None:
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)

    parser = configparser.ConfigParser()
    parser["telegram"] = {"token": token}

    with CONFIG_PATH.open("w", encoding="utf-8") as config_file:
        parser.write(config_file)

    LOGGER.info("Токен сохранён в конфигурационном файле %s", CONFIG_PATH)


def _prompt_token_gui() -> str:
    if Tk is None or askstring is None:
        raise RuntimeError("GUI token prompt is not available")

    LOGGER.info("Запуск окна ввода токена")
    token: str | None = None

    while not token:
        root = Tk()
        root.withdraw()
        try:
            token = askstring("Telegram Bot", "Введите токен Telegram-бота:", show="*")
        finally:
            root.destroy()

        if token is None:
            raise RuntimeError("Token input cancelled by user")

        token = token.strip()

    LOGGER.info("Токен успешно введён через графическое окно")
    return token


def prompt_token() -> str:
    token = _load_token_from_config()
    if token:
        return token

    try:
        token = _prompt_token_gui()
    except Exception:  # noqa: BLE001 - we want to fallback to CLI input
        LOGGER.exception("Не удалось запросить токен через графический интерфейс, запрашиваем в консоли")
        token = ""
        while not token:
            token = input("Введите токен Telegram-бота: ").strip()

    _save_token_to_config(token)
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

    LOGGER.info("Запуск бота. Логи пишутся в %s", LOG_FILE)
    LOGGER.info("Starting bot polling")
    app.run_polling(close_loop=False)


if __name__ == "__main__":
    main()

