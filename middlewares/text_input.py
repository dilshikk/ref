from aiogram import BaseMiddleware
from aiogram.types import Message
from loguru import logger

from states.flows import AdminFlow, ChannelAdminFlow, ReferralFlow, UserManagementFlow, WithdrawalFlow

# Формы, где обработчик ждёт ТОЛЬКО текст (числа, ID, ссылки, имена, реквизиты).
# Формы с медиа (поддержка, чек выплаты, контент рассылки) сюда намеренно не входят.
TEXT_ONLY_STATES: frozenset[str] = frozenset(
    s.state for s in (
        ChannelAdminFlow.waiting_for_chat_id,
        ChannelAdminFlow.waiting_for_url,
        ChannelAdminFlow.waiting_for_delete_id,
        UserManagementFlow.search_input,
        UserManagementFlow.add_balance_input,
        UserManagementFlow.send_message_input,
        ReferralFlow.CHANGE_NAME,
        WithdrawalFlow.CAPTCHA,
        WithdrawalFlow.CARD_NUMBER,
        WithdrawalFlow.CARD_HOLDER,
        AdminFlow.set_url,
        AdminFlow.set_min_balance,
        AdminFlow.set_invite_reward,
        AdminFlow.set_payment_chat_id,
        AdminFlow.set_vote_reward,
        AdminFlow.set_subcr_channel,
        AdminFlow.set_subcr_chat_id,
        AdminFlow.set_group_member_count,
        AdminFlow.set_max_withdraw,
        AdminFlow.set_antispam_limit,
        AdminFlow.set_money_admins,
        AdminFlow.ask_ad_url,
        AdminFlow.ask_ad_button_text,
        AdminFlow.ask_ad_schedule_at,
    )
)


class TextInputGuardMiddleware(BaseMiddleware):
    """
    В текстовых формах обработчики делают message.text.strip().
    Стикер/фото/голосовое давали message.text = None и роняли обработчик.
    Теперь такое сообщение не доходит до обработчика: пользователь видит подсказку,
    а форма остаётся на том же шаге.
    """

    async def __call__(self, handler, event: Message, data):
        if event.text is not None or event.chat.type != "private":
            return await handler(event, data)

        # raw_state кладёт внешний FSM-middleware aiogram
        raw_state = data.get("raw_state")
        if raw_state not in TEXT_ONLY_STATES:
            return await handler(event, data)

        user_id = event.from_user.id if event.from_user else "?"
        logger.debug(f"Нетекстовое сообщение в форме {raw_state} от {user_id}")
        await event.answer("✍️ Пожалуйста, отправьте ответ текстом.")
        return None
