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
        logger.info(f"Initialized Request class with base_url: {base_url}")

    def user_header(self, user_token):
        if not user_token:
            logger.debug("No user token provided, skipping user header")
            return {}
        logger.debug("User token provided, adding X-Token header")
        return {"X-Token": "Bearer " + user_token}

    def auth_header(self):
        from abdm.service.helper import cm_id, timestamp, uuid

        token = cache.get(ABDM_TOKEN_CACHE_KEY)
        if not token:
            logger.info("Missing session token, fetching new one")
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

            logger.debug(f"Fetching token from: {ABDM_TOKEN_URL}")
            response = requests.post(
                ABDM_TOKEN_URL,
                data=data,
                headers=headers,
                timeout=settings.ABDM_REQUEST_TIMEOUT,
            )

            logger.debug(f"Token fetch response status: {response.status_code}")

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
                logger.info(
                    f"Successfully fetched and cached token, expires in {expires_in} seconds"
                )
            else:
                logger.error(f"Error while fetching token: {response.text}")
                return None
        else:
            logger.debug("Using cached authentication token")

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
        logger.info(f"GET REQUEST HEADER: {headers}")

        logger.info(f"Making GET request to: {url}")
        if params:
            logger.debug(f"GET request params: {params}")

        response = requests.get(
            url, headers=headers, params=params, timeout=settings.ABDM_REQUEST_TIMEOUT
        )

        logger.debug(f"GET response status: {response.status_code}")

        if response.status_code in (400, 401) and retry_count < MAX_RETRY_COUNT + 1:
            result = response.json()
            if "code" in result and result["code"] == "900901":
                logger.warning(
                    "Received 900901 error code, invalidating token cache and retrying"
                )
                cache.delete(ABDM_TOKEN_CACHE_KEY)
                return self.get(path, params, headers, auth, retry_count + 1)

        return self._handle_response(response)

    def post(self, path, data=None, headers=None, auth=None, retry_count=0):
        url = self.url + path
        payload = json.dumps(data)
        headers = self.headers(headers, auth)

        logger.info(f"Making POST request to: {url}")
        if data:
            logger.debug(f"POST request data: {payload}")

        response = requests.post(
            url, data=payload, headers=headers, timeout=settings.ABDM_REQUEST_TIMEOUT
        )

        logger.debug(f"POST response status: {response.status_code}")

        if response.status_code in (400, 401) and retry_count < MAX_RETRY_COUNT + 1:
            result = response.json()
            if "code" in result and result["code"] == "900901":
                logger.warning(
                    "Received 900901 error code, invalidating token cache and retrying"
                )
                cache.delete(ABDM_TOKEN_CACHE_KEY)
                return self.post(path, data, headers, auth, retry_count + 1)

        return self._handle_response(response)

    def put(self, path, data=None, headers=None, auth=None):
        url = self.url + path
        payload = json.dumps(data)
        headers = self.headers(headers, auth)

        response = requests.put(
            url, data=payload, headers=headers, timeout=settings.ABDM_REQUEST_TIMEOUT
        )

        if response.status_code == 400 or response.status_code == 401:
            result = response.json()
            if "code" in result and result["code"] == "900901":
                cache.delete(ABDM_TOKEN_CACHE_KEY)
                return self.put(path, data, headers, auth)

        return self._handle_response(response)

    def _handle_response(self, response: requests.Response):
        def custom_json():
            try:
                parsed_json = json.loads(response.text)
                logger.debug("Successfully parsed JSON response")
                return parsed_json
            except json.JSONDecodeError as json_err:
                logger.error(
                    f"JSON Decode error: {json_err}, response text: {response.text}"
                )
                return {"error": response.text}
            except Exception as err:
                logger.error(
                    f"Unknown error while decoding json: {err}, response text: {response.text}"
                )
                return {}

        if response.status_code >= 400:
            logger.warning(
                f"Request failed with status {response.status_code}: {response.text}"
            )
        else:
            logger.debug(f"Request successful with status {response.status_code}")

        response.json = custom_json
        return response
