import json
import logging
from datetime import datetime

import jwt
import requests
from django.core.cache import cache
from rest_framework_simplejwt.authentication import JWTAuthentication
from rest_framework_simplejwt.exceptions import InvalidToken

from abdm.service.helper import cm_id, timestamp, uuid
from abdm.settings import plugin_settings as settings
from care.users.models import User

logger = logging.getLogger(__name__)

ABDM_JWKS_CACHE_KEY_PREFIX = "abdm_jwks"
ABDM_JWKS_CACHE_TTL = 60 * 60 * 24  # 24 hours


def _jwks_headers():
    return {
        "REQUEST-ID": uuid(),
        "TIMESTAMP": timestamp(),
        "X-CM-ID": cm_id(),
    }


def _fetch_gateway_jwk(url, *, use_cache=True):
    cache_key = f"{ABDM_JWKS_CACHE_KEY_PREFIX}__{url}"
    if use_cache:
        jwk = cache.get(cache_key)
        if jwk is not None:
            return jwk

    try:
        response = requests.get(
            url,
            headers=_jwks_headers(),
            timeout=settings.ABDM_REQUEST_TIMEOUT,
        )
        response.raise_for_status()
        jwk = response.json()["keys"][0]
    except (requests.RequestException, KeyError, IndexError, ValueError) as e:
        logger.error("ABDM JWKS fetch failed for %s: %s", url, e)
        raise ValueError(f"ABDM JWKS fetch failed: {e}") from e

    cache.set(cache_key, jwk, ABDM_JWKS_CACHE_TTL)
    return jwk


def _decode_with_jwk(jwk, token):
    public_key = jwt.algorithms.RSAAlgorithm.from_jwk(json.dumps(jwk))
    return jwt.decode(
        token, key=public_key, audience="account", algorithms=["RS256"]
    )


class ABDMAuthentication(JWTAuthentication):
    def open_id_authenticate(self, url, token):
        jwk = _fetch_gateway_jwk(url)
        try:
            return _decode_with_jwk(jwk, token)
        except jwt.InvalidSignatureError:
            logger.debug(
                "ABDM JWKS signature verification failed; bypassing cache to refresh key"
            )
            fresh_jwk = _fetch_gateway_jwk(url, use_cache=False)
            if fresh_jwk == jwk:
                raise
            return _decode_with_jwk(fresh_jwk, token)

    def authenticate_header(self, request):
        return "Bearer"

    def authenticate(self, request):
        jwt_token = request.META.get("HTTP_AUTHORIZATION")
        if jwt_token is None:
            return None
        jwt_token = self.get_jwt_token(jwt_token)

        abdm_cert_url = f"{settings.ABDM_GATEWAY_URL}/gateway/v3/certs"
        validated_token = self.get_validated_token(abdm_cert_url, jwt_token)

        return self.get_user(validated_token), validated_token

    def get_jwt_token(self, token):
        return token.replace("Bearer", "").replace(" ", "")

    def get_validated_token(self, url, token):
        try:
            return self.open_id_authenticate(url, token)
        except Exception as e:
            logger.error(f"Error validating ABDM authorization token: {e}")
            raise InvalidToken({"detail": f"Invalid Authorization token: {e}"})

    def get_user(self, validated_token):
        user = User.objects.filter(username=settings.ABDM_USERNAME).first()
        if not user:
            password = User.objects.make_random_password()
            user = User(
                username=settings.ABDM_USERNAME,
                email="abdm@ohc.network",
                password=f"{password}123",
                gender=3,
                phone_number="917777777777",
                verified=True,
                date_of_birth=datetime.now().date(),
            )
            user.save()
        return user
