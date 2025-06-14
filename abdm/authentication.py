import json
import logging
from datetime import datetime

import jwt
import requests
from django.core.cache import cache
from rest_framework.permissions import BasePermission
from rest_framework_simplejwt.authentication import JWTAuthentication
from rest_framework_simplejwt.exceptions import InvalidToken, TokenError
from rest_framework_simplejwt.tokens import AccessToken

from abdm.service.helper import cm_id, timestamp, uuid
from abdm.settings import plugin_settings as settings
from care.users.models import User

logger = logging.getLogger(__name__)

PHR_TEMP_ACCESS_TOKEN_INVALIDATION_PREFIX = "PHR_ACCESS_TOKEN_INVALIDATE:"
PHR_TEMP_REFRESH_TOKEN_INVALIDATION_PREFIX = "PHR_REFRESH_TOKEN_INVALIDATE:"


class ABDMAuthentication(JWTAuthentication):
    def open_id_authenticate(self, url, token):
        public_key = requests.get(
            url,
            headers={
                "REQUEST-ID": uuid(),
                "TIMESTAMP": timestamp(),
                "X-CM-ID": cm_id(),
            },
        )
        jwk = public_key.json()["keys"][0]
        public_key = jwt.algorithms.RSAAlgorithm.from_jwk(json.dumps(jwk))
        return jwt.decode(
            token, key=public_key, audience="account", algorithms=["RS256"]
        )

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
                user_type=User.TYPE_VALUE_MAP["Volunteer"],
                verified=True,
                date_of_birth=datetime.now().date(),
            )
            user.save()
        return user


class AbdmSessionPrincipal:
    is_authenticated = True
    is_anonymous = False

    def __init__(self, abha_address: str, record_id: int):
        if not isinstance(abha_address, str):
            raise ValueError("abha_address must be a string.")
        self.abha_address = abha_address
        self.id = record_id

    def __str__(self):
        return f"AbdmSessionPrincipal(abha_address={self.abha_address}, id={self.id})"


class PhrCustomAuthentication(JWTAuthentication):
    def get_validated_token(self, raw_token):
        raw_token_str = raw_token.decode()

        if cache.get(f"{PHR_TEMP_ACCESS_TOKEN_INVALIDATION_PREFIX}{raw_token_str}"):
            raise InvalidToken("Access token has been invalidated.")

        try:
            validated_token = AccessToken(raw_token)
            if "abha_address" not in validated_token or "id" not in validated_token:
                raise TokenError("Token is missing the required claims.")
            return validated_token

        except TokenError as e:
            raise InvalidToken(
                {
                    "detail": "Given token is not valid or is missing required information.",
                    "messages": [str(e)],
                },
            ) from e

        except Exception as e:
            raise InvalidToken(
                {
                    "detail": "An unexpected error occurred while validating the token.",
                },
            ) from e

    def get_user(self, validated_token):
        abha_address = validated_token["abha_address"]
        return AbdmSessionPrincipal(
            abha_address=abha_address, record_id=validated_token["id"]
        )


class IsPhrAuthenticated(BasePermission):
    def has_permission(self, request, view):
        return bool(request.user and isinstance(request.user, AbdmSessionPrincipal))
