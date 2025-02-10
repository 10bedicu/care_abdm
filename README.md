# Care Abdm

[![Release Status](https://img.shields.io/pypi/v/care_abdm.svg)](https://pypi.python.org/pypi/care_abdm)
[![Build Status](https://github.com/ohcnetwork/care_abdm/actions/workflows/build.yaml/badge.svg)](https://github.com/ohcnetwork/care_abdm/actions/workflows/build.yaml)

Care ABDM is a plugin for care to integrate ABDM (of India's Ayushman Bharat Digital Mission) specifications. By integrating ABDM with CARE, it creates a seamless, interconnected network where patient data is readily accessible accross various health facilities.

## Features

- **Digital Health ID:** Every citizen is assigned a unique digital health identifier, ensuring that their health records are accurately and securely linked across the healthcare system.
- **Electronic Health Records (EHR):** Comprehensive and interoperable health records allow for seamless sharing of patient data among hospitals, clinics, and other healthcare providers, facilitating better-coordinated care.
- **Interoperability:** Standardized protocols and a unified framework enable various health systems and platforms to exchange information smoothly, enhancing communication and reducing duplication of efforts.

## Installation

https://care-be-docs.ohc.network/pluggable-apps/configuration.html

https://github.com/ohcnetwork/care/blob/develop/plug_config.py

To install care abdm, you can add the plugin config in [care/plug_config.py](https://github.com/ohcnetwork/care/blob/develop/plug_config.py) as follows:

```python
...

abdm_plug = Plug(
    name="abdm",
    package_name="git+https://github.com/ohcnetwork/care_abdm.git",
    version="@master",
    configs={
        "ABDM_CLIENT_ID": "abdm_client_id",
        "ABDM_CLIENT_SECRET": "abdm_client_secret",
        "ABDM_GATEWAY_URL": "",
        "ABDM_ABHA_URL": "",
        "ABDM_FACILITY_URL": "",
        "ABDM_HIP_NAME_PREFIX": "",
        "ABDM_HIP_NAME_SUFFIX": "",
        "ABDM_USERNAME": "",
        "ABDM_CM_ID": "",
        "AUTH_USER_MODEL": "users.User"
    },
)
plugs = [abdm_plug]
...
```

## Configuration

The following configurations variables are available for Care Abdm:

- `ABDM_CLIENT_ID`: The client id for the ABDM service.
- `ABDM_CLIENT_SECRET`: The client secret for the ABDM service.
- `ABDM_GATEWAY_URL`: The URL for the ABDM service APIs.
- `ABDM_ABHA_URL`: The URL for the health service APIs.
- `ABDM_FACILITY_URL`: The URL for the ABDM facility APIs.
- `ABDM_HIP_NAME_PREFIX`: The prefix for the HIP name. Used to avoid conflicts while registering a facility as ABDM health facility.
- `ABDM_HIP_NAME_SUFFIX`: The suffix for the HIP name. Used to avoid conflicts while registering a facility as ABDM health facility.
- `ABDM_USERNAME`: The internal username for the ABDM service. Intended to track the records created via ABDM.
- `ABDM_CM_ID`: The X-CM-ID header value for the ABDM service.
- `AUTH_USER_MODEL`: The user model to use for the ABDM service.

The plugin will try to find the API key from the config first and then from the environment variable.

## License

This project is licensed under the terms of the [MIT license](LICENSE).

---

This plugin was created with [Cookiecutter](https://github.com/audreyr/cookiecutter) using the [ohcnetwork/care-plugin-cookiecutter](https://github.com/ohcnetwork/care-plugin-cookiecutter).
