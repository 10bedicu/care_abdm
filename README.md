# Care Abdm

[![Release Status](https://img.shields.io/pypi/v/care_abdm.svg)](https://pypi.python.org/pypi/care_abdm)
[![Build Status](https://github.com/ohcnetwork/care_abdm/actions/workflows/build.yaml/badge.svg)](https://github.com/ohcnetwork/care_abdm/actions/workflows/build.yaml)

Care ABDM is a plugin for care to integrate ABDM (of India's Ayushman Bharat Digital Mission) specifications. By integrating ABDM with CARE, it creates a seamless, interconnected network where patient data is readily accessible accross various health facilities.

## Features

- **Digital Health ID:** Every citizen is assigned a unique digital health identifier, ensuring that their health records are accurately and securely linked across the healthcare system.
- **Electronic Health Records (EHR):** Comprehensive and interoperable health records allow for seamless sharing of patient data among hospitals, clinics, and other healthcare providers, facilitating better-coordinated care.
- **Interoperability:** Standardized protocols and a unified framework enable various health systems and platforms to exchange information smoothly, enhancing communication and reducing duplication of efforts.

## Local Development

To develop the plug in local environment along with care, follow the steps below:

1. Go to the care root directory and clone the plugin repository:

```bash
cd care
git clone git@github.com:ohcnetwork/care_abdm.git
```

2. Add the plugin config in plug_config.py

```python
...

abdm_plugin = Plug(
    name="abdm",
    package_name="/app/care_abdm",
    version="",
    configs={},
)
plugs = [abdm_plug]

...
```

3. Tweak the code in plugs/manager.py, install the plugin in editable mode

```python
...

subprocess.check_call(
    [sys.executable, "-m", "pip", "install", "-e", *packages] # add -e flag to install in editable mode
)

...
```

4. Rebuild the docker image and run the server

```bash
make re-build
make up
```

5. Setup the enviroment values:

- ABDM_AUTH_URL: "https://abdm-auth.coolify.ohc.network"
- Use the default value for the others

> [!IMPORTANT]
> Do not push these changes in a PR. These changes are only for local development.

## Production Setup

To install care hello, you can add the plugin config in [care/plug_config.py](https://github.com/ohcnetwork/care/blob/develop/plug_config.py) as follows:

```python
...

abdm_plug = Plug(
    name="abdm",
    package_name="git+https://github.com/ohcnetwork/care_abdm.git",
    version="@master",
    configs={},
)
plugs = [abdm_plug]
...
```

[Extended Docs on Plug Installation](https://care-be-docs.ohc.network/pluggable-apps/configuration.html)

## Configuration

The following configurations variables are available for Care Abdm:

- `ABDM_CLIENT_ID`: The client id for the ABDM service.
- `ABDM_CLIENT_SECRET`: The client secret for the ABDM service.
- `ABDM_AUTH_URL`: This is a proxy URL used only for local development to get the auth token.
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
