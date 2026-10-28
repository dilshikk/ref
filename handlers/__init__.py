from aiogram import Router


def setup_routers() -> Router:
    from .groups import member_events
    from .private import (
        admin_panel,
        payout_guard,
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

    router.include_routers(
        # payout_guard стоит ПЕРЕД admin_panel: его исправленные обработчики
        # одобрения/отклонения выплат перехватывают старые из admin_panel
        payout_guard.router,
        admin_panel.router,
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
        error_router.router,
        onboarding.group_router,
        fallback.router,
    )
    return router
