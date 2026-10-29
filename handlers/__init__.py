from aiogram import Router


def setup_routers() -> Router:
    from .groups import member_events
    from .private import (
        admin,
        broadcasts,
        payout_guard,
        referral_holds,
        onboarding,
        faq,
        subscriptions,
        referrals,
        user_management,
        wallet,
        withdrawals,
        payout_history,
        profile_settings,
        support,
        fallback,
    )
    from .errors import error_router

    router = Router(name="handlers.root")

    # Обработчик ошибок вешаем на КОРНЕВОЙ роутер. В aiogram 3 ошибки поднимаются
    # только к родителям, поэтому в соседнем роутере error_router он не видел ошибок
    # других обработчиков — админ о них не узнавал.
    router.errors.register(error_router.MyErrorHandler)

    router.include_routers(
        # payout_guard — единственное место с обработчиками выплат (#35);
        # стоит первым, чтобы money_guard отвечал раньше остальных роутеров
        payout_guard.router,
        referral_holds.router,
        # broadcasts тоже ПЕРЕД админкой: заменяет старые обработчики рассылок (#19)
        broadcasts.router,
        broadcasts.member_router,
        # админ-панель по разделам (#37); формы внутри пакета подключены последними
        admin.router,
        onboarding.private_router,
        faq.router,
        subscriptions.router,
        referrals.router,
        user_management.router,
        wallet.router,
        withdrawals.router,
        payout_history.router,
        profile_settings.router,
        support.router,
        member_events.router,
        fallback.router,
    )
    return router
