"""
Касса-бот v2
- Две кассы: Фирма (только админы) / Офис (все)
- Операция: касса → приход/расход → сумма → комментарий
- Отчёты: день / неделя / месяц
- Роли: ADMIN / STAFF
"""

import os
import logging
from datetime import datetime, date, timedelta
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application, CommandHandler, CallbackQueryHandler,
    MessageHandler, ConversationHandler, filters, ContextTypes
)
from sheets import SheetsClient

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# ─── Конфиг ───────────────────────────────────────────────────────────────────
BOT_TOKEN = os.environ["BOT_TOKEN"]
SPREADSHEET_ID = os.environ["SPREADSHEET_ID"]

# Роли пользователей: Telegram ID → "admin" или "staff"
USERS = {
    355266614: "admin",
    331620668: "admin",
    6864362402: "staff",
}

CASH_FIRMA  = "Касса Фирмы"
CASH_OFFICE = "Касса Офиса"

# ─── Состояния диалога ────────────────────────────────────────────────────────
ST_CASH, ST_TYPE, ST_AMOUNT, ST_COMMENT = range(4)


# ─── Утилиты ──────────────────────────────────────────────────────────────────

def get_role(user_id: int):
    return USERS.get(user_id)

def fmt(amount: float) -> str:
    return f"{amount:,.2f} €".replace(",", " ")

def main_menu(user_id: int) -> InlineKeyboardMarkup:
    role = get_role(user_id)
    buttons = []
    if role == "admin":
        buttons.append([
            InlineKeyboardButton("➕ Приход", callback_data="op_income"),
            InlineKeyboardButton("➖ Расход", callback_data="op_expense"),
        ])
        buttons.append([InlineKeyboardButton("💰 Остатки обеих касс", callback_data="balance_all")])
        buttons.append([
            InlineKeyboardButton("📊 Отчёт: Фирма", callback_data="rep_firma"),
            InlineKeyboardButton("📊 Отчёт: Офис", callback_data="rep_office"),
        ])
    else:
        buttons.append([
            InlineKeyboardButton("➕ Приход", callback_data="op_income"),
            InlineKeyboardButton("➖ Расход", callback_data="op_expense"),
        ])
        buttons.append([InlineKeyboardButton("💰 Остаток кассы Офиса", callback_data="balance_office")])
        buttons.append([InlineKeyboardButton("📊 Отчёт: Офис", callback_data="rep_office")])
    return InlineKeyboardMarkup(buttons)

def cancel_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[InlineKeyboardButton("❌ Отмена", callback_data="cancel")]])

def back_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[InlineKeyboardButton("◀️ В меню", callback_data="menu")]])


# ─── /start и /menu ───────────────────────────────────────────────────────────

async def cmd_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    if not get_role(uid):
        await update.message.reply_text("⛔ У вас нет доступа к этому боту.")
        return
    await update.message.reply_text(
        "👋 *Касса-бот*\n\nВыберите действие:",
        parse_mode="Markdown",
        reply_markup=main_menu(uid)
    )

async def cmd_menu(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    if not get_role(uid):
        return
    await update.message.reply_text("Главное меню:", reply_markup=main_menu(uid))

async def menu_callback(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    uid = query.from_user.id
    await query.edit_message_text("Главное меню:", reply_markup=main_menu(uid))


# ─── Остаток ──────────────────────────────────────────────────────────────────

async def show_balance_all(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    sc: SheetsClient = ctx.bot_data["sheets"]
    b_firma  = sc.get_balance(CASH_FIRMA)
    b_office = sc.get_balance(CASH_OFFICE)
    await query.edit_message_text(
        f"💰 *Остатки касс:*\n\n"
        f"🏢 Касса Фирмы: `{fmt(b_firma)}`\n"
        f"🏠 Касса Офиса: `{fmt(b_office)}`",
        parse_mode="Markdown",
        reply_markup=back_kb()
    )

async def show_balance_office(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    sc: SheetsClient = ctx.bot_data["sheets"]
    b = sc.get_balance(CASH_OFFICE)
    await query.edit_message_text(
        f"💰 *Остаток кассы Офиса:*\n\n`{fmt(b)}`",
        parse_mode="Markdown",
        reply_markup=back_kb()
    )


# ─── Добавление операции ──────────────────────────────────────────────────────

async def op_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    uid = query.from_user.id
    role = get_role(uid)
    op_type = "income" if query.data == "op_income" else "expense"
    ctx.user_data["op_type"] = op_type

    if role == "admin":
        # Админ выбирает кассу
        await query.edit_message_text(
            "Выберите кассу:",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("🏢 Касса Фирмы", callback_data="cash_firma")],
                [InlineKeyboardButton("🏠 Касса Офиса", callback_data="cash_office")],
                [InlineKeyboardButton("❌ Отмена", callback_data="cancel")],
            ])
        )
        return ST_CASH
    else:
        # Сотрудник — только Офис
        ctx.user_data["cash"] = CASH_OFFICE
        return await _ask_amount(query)

async def op_cash_selected(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    ctx.user_data["cash"] = CASH_FIRMA if query.data == "cash_firma" else CASH_OFFICE
    return await _ask_amount(query)

async def _ask_amount(query):
    await query.edit_message_text(
        "Введите сумму (например: `150.50`):",
        parse_mode="Markdown",
        reply_markup=cancel_kb()
    )
    return ST_AMOUNT

async def op_amount(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip().replace(",", ".")
    try:
        amount = float(text)
        if amount <= 0:
            raise ValueError
    except ValueError:
        await update.message.reply_text("❗ Введите корректную сумму (число > 0):", reply_markup=cancel_kb())
        return ST_AMOUNT
    ctx.user_data["amount"] = amount
    op_type = ctx.user_data["op_type"]
    label = "приход" if op_type == "income" else "расход"
    await update.message.reply_text(
        f"Напишите комментарий — *с чем связан этот {label}?*",
        parse_mode="Markdown",
        reply_markup=cancel_kb()
    )
    return ST_COMMENT

async def op_comment(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    comment = update.message.text.strip()
    if len(comment) < 2:
        await update.message.reply_text("❗ Комментарий слишком короткий, напишите подробнее:")
        return ST_COMMENT

    sc: SheetsClient = ctx.bot_data["sheets"]
    ud = ctx.user_data
    user = update.effective_user.username or str(update.effective_user.id)

    sc.add_transaction(
        cash=ud["cash"],
        op_type=ud["op_type"],
        amount=ud["amount"],
        comment=comment,
        user=user,
    )
    balance = sc.get_balance(ud["cash"])
    sign  = "➕" if ud["op_type"] == "income" else "➖"
    label = "Приход" if ud["op_type"] == "income" else "Расход"
    cash_icon = "🏢" if ud["cash"] == CASH_FIRMA else "🏠"

    await update.message.reply_text(
        f"✅ *{label} записан*\n\n"
        f"{cash_icon} {ud['cash']}\n"
        f"{sign} Сумма: `{fmt(ud['amount'])}`\n"
        f"💬 {comment}\n\n"
        f"💰 Остаток: `{fmt(balance)}`",
        parse_mode="Markdown",
        reply_markup=back_kb()
    )
    ctx.user_data.clear()
    return ConversationHandler.END

async def op_cancel(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    ctx.user_data.clear()
    await query.edit_message_text("❌ Отменено.", reply_markup=back_kb())
    return ConversationHandler.END


# ─── Отчёты ───────────────────────────────────────────────────────────────────

async def show_report(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    sc: SheetsClient = ctx.bot_data["sheets"]

    cash = CASH_FIRMA if query.data == "rep_firma" else CASH_OFFICE
    cash_icon = "🏢" if cash == CASH_FIRMA else "🏠"
    today = date.today()

    await query.edit_message_text(
        f"{cash_icon} *{cash}*\n\nВыберите период:",
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("📊 Сегодня",  callback_data=f"rp_day_{cash}")],
            [InlineKeyboardButton("📅 Неделя",   callback_data=f"rp_week_{cash}")],
            [InlineKeyboardButton("🗓 Месяц",    callback_data=f"rp_month_{cash}")],
            [InlineKeyboardButton("◀️ Назад",    callback_data="menu")],
        ])
    )

async def show_report_period(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    sc: SheetsClient = ctx.bot_data["sheets"]

    _, period, *cash_parts = query.data.split("_")
    cash = "_".join(cash_parts)  # восстанавливаем название кассы
    # Определяем cash по содержимому
    cash = CASH_FIRMA if "ирм" in query.data else CASH_OFFICE
    cash_icon = "🏢" if cash == CASH_FIRMA else "🏠"

    today = date.today()
    if period == "day":
        date_from = today
        title = f"за сегодня ({today.strftime('%d.%m.%Y')})"
    elif period == "week":
        date_from = today - timedelta(days=today.weekday())
        title = f"за неделю (с {date_from.strftime('%d.%m')})"
    else:
        date_from = today.replace(day=1)
        title = f"за {today.strftime('%B %Y')}"

    rows = sc.get_transactions(cash=cash, date_from=date_from, date_to=today)

    if not rows:
        text = f"{cash_icon} *{cash}*\n_{title}_\n\n_Операций нет._"
    else:
        income  = sum(r["amount"] for r in rows if r["type"] == "income")
        expense = sum(r["amount"] for r in rows if r["type"] == "expense")
        balance = sc.get_balance(cash)

        # Последние 5 операций
        last = rows[-5:]
        ops_lines = "\n".join(
            f"{'➕' if r['type']=='income' else '➖'} `{fmt(r['amount'])}` — {r['comment'][:30]}"
            for r in reversed(last)
        )

        text = (
            f"{cash_icon} *{cash}*\n_{title}_\n\n"
            f"➕ Приходы: `{fmt(income)}`\n"
            f"➖ Расходы: `{fmt(expense)}`\n"
            f"📈 Итого: `{fmt(income - expense)}`\n"
            f"💰 Остаток: `{fmt(balance)}`\n\n"
            f"*Последние операции:*\n{ops_lines}"
        )

    await query.edit_message_text(
        text, parse_mode="Markdown",
        reply_markup=back_kb()
    )


# ─── Main ─────────────────────────────────────────────────────────────────────

def main():
    sc = SheetsClient(spreadsheet_id=SPREADSHEET_ID)
    app = Application.builder().token(BOT_TOKEN).build()
    app.bot_data["sheets"] = sc

    conv = ConversationHandler(
        entry_points=[
            CallbackQueryHandler(op_start, pattern="^op_(income|expense)$"),
        ],
        states={
            ST_CASH:    [CallbackQueryHandler(op_cash_selected, pattern="^cash_(firma|office)$")],
            ST_AMOUNT:  [MessageHandler(filters.TEXT & ~filters.COMMAND, op_amount)],
            ST_COMMENT: [MessageHandler(filters.TEXT & ~filters.COMMAND, op_comment)],
        },
        fallbacks=[CallbackQueryHandler(op_cancel, pattern="^cancel$")],
        per_user=True,
    )

    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("menu",  cmd_menu))
    app.add_handler(conv)
    app.add_handler(CallbackQueryHandler(show_balance_all,    pattern="^balance_all$"))
    app.add_handler(CallbackQueryHandler(show_balance_office, pattern="^balance_office$"))
    app.add_handler(CallbackQueryHandler(show_report,         pattern="^rep_(firma|office)$"))
    app.add_handler(CallbackQueryHandler(show_report_period,  pattern="^rp_(day|week|month)_"))
    app.add_handler(CallbackQueryHandler(menu_callback,       pattern="^menu$"))

    logger.info("Бот запущен")
    app.run_polling()

if __name__ == "__main__":
    main()
