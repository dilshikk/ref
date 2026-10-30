from aiogram.fsm.state import State, StatesGroup


class AdminFlow(StatesGroup):
    are_you_sure = State()
    set_url = State()
    set_min_balance = State()
    set_invite_reward = State()
    set_payment_chat_id = State()
    set_vote_reward = State()
    set_subcr_channel = State()
    set_group_id = State()         # #116: «🆔 ID группы» (раньше GROUP_ID в .env)
    set_log_group_id = State()     # #116: «📝 Группа логов» (раньше LOG_GROUP_ID в .env)
    ask_ad_content = State()
    ask_ad_caption = State()
    ask_ad_url = State()
    ask_ad_button_text = State()
    ask_ad_schedule_at = State()
    set_group_member_count = State()
    set_max_withdraw = State()
    set_antispam_limit = State()
    set_text_template = State()
    support_reply_input = State()
    set_poll_config = State()
    payout_receipt_input = State()
    set_tariff_inline_value = State()
    set_referral_value = State()   # #85: инлайн-панель «🎁 Реферальные настройки»
    set_limit_value = State()      # #92: инлайн-панель «📊 Лимиты»
    set_payment_channel = State()  # #107: публичная ссылка «📢 Чат выплат»
    set_money_admins = State()     # #116: «👑 Денежные админы» (раньше MONEY_ADMINS в .env)


class ReferralFlow(StatesGroup):
    CARD = State()
    AMOUNT = State()
    CAPTCHA = State()
    CARD_NUMBER = State()
    CARD_HOLDER = State()
    PHONE = State()
    END = State()
    CHANGE_NAME = State()


class WithdrawalFlow(StatesGroup):
    CARD = State()
    CAPTCHA = State()
    METHOD = State()  # выбор способа вывода — только после проверки участников
    SAVED_CHOICE = State()  # «использовать сохранённые реквизиты» или «ввести новые»
    CARD_NUMBER = State()
    CARD_HOLDER = State()
    END = State()


class ChannelAdminFlow(StatesGroup):
    waiting_for_chat_id = State()
    waiting_for_url = State()
    waiting_for_delete_id = State()


class UserManagementFlow(StatesGroup):
    search_input = State()
    add_balance_input = State()
    send_message_input = State()


class SupportFlow(StatesGroup):
    waiting_message = State()


AdminState = AdminFlow
ChannelState = ChannelAdminFlow
ReferalForm = ReferralFlow
UserMgmtState = UserManagementFlow
