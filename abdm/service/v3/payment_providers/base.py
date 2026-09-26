from abc import ABC, abstractmethod

from django.core.exceptions import ImproperlyConfigured

_PROVIDERS: dict[str, "PaymentProvider"] = {}


class PaymentProvider(ABC):
    """
    Interface a scan-and-pay payment provider must implement to be usable by ABDM.

    Providers are registered via :func:`register_provider` (typically from a
    plug's ``AppConfig.ready``) and selected at runtime through the
    ``ABDM_SCAN_AND_PAY_PROVIDER`` setting.
    """

    #: Unique key used to select this provider (e.g. "razorpay", "sbi_epay").
    name: str = ""

    @abstractmethod
    def create_payment_link(self, invoice) -> dict:
        """
        Create a payment link for the given invoice.

        Must return a dict with the keys ``order_number``, ``payment_link_id``
        and ``payment_url``.
        """

    def reconcile_order(self, order) -> None:  # noqa: B027
        """
        Poll the provider for the order's status and reconcile it if paid.

        Optional; defaults to a no-op for providers that only reconcile via
        webhooks.
        """


def register_provider(provider_cls: type[PaymentProvider]) -> type[PaymentProvider]:
    """Instantiate and register a payment provider. Usable as a decorator."""
    provider = provider_cls()
    if not provider.name:
        msg = (
            f"Payment provider {provider_cls.__name__} must define a non-empty 'name'."
        )
        raise ImproperlyConfigured(msg)
    _PROVIDERS[provider.name] = provider
    return provider_cls


def unregister_provider(name: str) -> None:
    """Remove a registered provider. Safe to call for unknown names."""
    _PROVIDERS.pop(name, None)


def get_provider(name: str) -> PaymentProvider:
    try:
        return _PROVIDERS[name]
    except KeyError:
        msg = (
            f"No payment provider registered for '{name}'. "
            f"Available providers: {available_providers()}"
        )
        raise ImproperlyConfigured(msg) from None


def available_providers() -> list[str]:
    return sorted(_PROVIDERS)
