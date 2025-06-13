import json
import logging
from datetime import datetime

import jwt
import requests
from rest_framework_simplejwt.authentication import JWTAuthentication
from rest_framework_simplejwt.exceptions import InvalidToken

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
