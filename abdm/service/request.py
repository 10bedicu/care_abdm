import json
import logging

import requests
from django.core.cache import cache

from abdm.settings import plugin_settings as settings

ABDM_TOKEN_URL = settings.ABDM_AUTH_URL or (
    settings.ABDM_GATEWAY_URL + "/gateway/v3/sessions"
)
ABDM_TOKEN_CACHE_KEY = "abdm_token"
MAX_RETRY_COUNT = 1

logger = logging.getLogger(__name__)


class Request:
    def __init__(self, base_url):
        self.url = base_url

    def user_header(self, user_token):
        if not user_token:
            return {}
        return {"X-Token": "Bearer " + user_token}

    def auth_header(self):
        from abdm.service.helper import cm_id, timestamp, uuid

        token = cache.get(ABDM_TOKEN_CACHE_KEY)
        if not token:
            data = json.dumps(
                {
                    "clientId": settings.ABDM_CLIENT_ID,
                    "clientSecret": settings.ABDM_CLIENT_SECRET,
                    "grantType": "client_credentials",
                }
            )
            headers = {
                "Content-Type": "application/json",
                "Accept": "application/json",
                "REQUEST-ID": uuid(),
                "TIMESTAMP": timestamp(),
                "X-CM-ID": cm_id(),
            }

            response = requests.post(
                ABDM_TOKEN_URL, data=data, headers=headers, timeout=settings.ABDM_REQUEST_TIMEOUT
            )

            if response.status_code < 300:
                if response.headers["Content-Type"] != "application/json":
                    logger.error(
                        f"Invalid content type: {response.headers['Content-Type']}"
                    )
                    return None
                data = response.json()
                token = data["accessToken"]
                expires_in = data["expiresIn"]

                cache.set(ABDM_TOKEN_CACHE_KEY, token, expires_in)
            else:
                logger.error(
                    "Error while fetching token status=%s",
                    response.status_code,
                )
                return None
        return {"Authorization": f"Bearer {token}"}

    def headers(self, additional_headers=None, auth=None):
        return {
            "Content-Type": "application/json",
            "Accept": "*/*",
            **(additional_headers or {}),
            **(self.user_header(auth) or {}),
            **(self.auth_header() or {}),
        }

    def get(self, path, params=None, headers=None, auth=None, retry_count=0):
        url = self.url + path
        headers = self.headers(headers, auth)

        response = requests.get(url, headers=headers, params=params, timeout=settings.ABDM_REQUEST_TIMEOUT)

        if response.status_code in (400, 401) and retry_count < MAX_RETRY_COUNT + 1:
            result = response.json()
            if "code" in result and result["code"] == "900901":
                logger.warning(
                    "Received 900901 error code, invalidating token cache and retrying"
                )
                cache.delete(ABDM_TOKEN_CACHE_KEY)
                return self.get(path, params, headers, auth, retry_count + 1)

        return self._handle_response(response, path)

    def post(self, path, data=None, headers=None, auth=None, retry_count=0):
        url = self.url + path
        payload = json.dumps(data)
        headers = self.headers(headers, auth)

        response = requests.post(url, data=payload, headers=headers, timeout=settings.ABDM_REQUEST_TIMEOUT)

        if response.status_code in (400, 401) and retry_count < MAX_RETRY_COUNT + 1:
            result = response.json()
            if "code" in result and result["code"] == "900901":
                logger.warning(
                    "Received 900901 error code, invalidating token cache and retrying"
                )
                cache.delete(ABDM_TOKEN_CACHE_KEY)
                return self.post(path, data, headers, auth, retry_count + 1)

        return self._handle_response(response, path)

    def _extract_abdm_error_fields(self, response: requests.Response):
        code = None
        message = None
        try:
            body = json.loads(response.text)
            if isinstance(body, dict):
                code = body.get("code")
                message = body.get("message")
                error = body.get("error")
                if isinstance(error, dict):
                    code = code or error.get("code")
                    message = message or error.get("message")
        except (json.JSONDecodeError, ValueError):
            pass
        return code, message

    def _log_request_failure(self, path, response: requests.Response):
        code, message = self._extract_abdm_error_fields(response)
        logger.warning(
            "ABDM request failed path=%s status=%s code=%s message=%s",
            path,
            response.status_code,
            code,
            message,
        )

    def _handle_response(self, response: requests.Response, path: str):
        def custom_json():
            try:
                return json.loads(response.text)
            except json.JSONDecodeError:
                logger.error(
                    "ABDM response decode failed path=%s status=%s error_type=%s",
                    path,
                    response.status_code,
                    "JSONDecodeError",
                )
                return {"message": "Invalid JSON response from ABDM"}
            except Exception as err:
                logger.error(
                    "ABDM response decode failed path=%s status=%s error_type=%s",
                    path,
                    response.status_code,
                    type(err).__name__,
                    exc_info=True,
                )
                return {}

        if response.status_code >= 400:
            self._log_request_failure(path, response)

        response.json = custom_json
        return response
