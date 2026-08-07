import razorpay

from abdm.settings import plugin_settings as settings


def get_razorpay_client():
    return razorpay.Client(
        auth=(
            settings.ABDM_RAZORPAY_KEY_ID,
            settings.ABDM_RAZORPAY_KEY_SECRET,
        )
    )
