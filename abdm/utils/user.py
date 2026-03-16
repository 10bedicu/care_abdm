from abdm.settings import plugin_settings as settings
from care.users.models import User

ABDM_USER = None


def get_or_create_abdm_user():
    global ABDM_USER
    if ABDM_USER:
        return ABDM_USER

    user, _ = User.objects.get_or_create(
        username=settings.ABDM_USERNAME,
        defaults={
            "email": "abdm@ohc.network",
            "phone_number": "917777777777",
            "verified": True,
        },
    )
    ABDM_USER = user

    return user
