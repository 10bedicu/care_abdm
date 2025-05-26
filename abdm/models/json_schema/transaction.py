CREATE_OR_LINK_ABHA_NUMBER = {
    "$schema": "http://json-schema.org/draft-07/schema#",
    "type": "object",
    "properties": {
        "abha_number": {"type": "string", "format": "uuid"},
        "method": {
            "type": "string",
            "enum": [
                "create_via_aadhaar_demographics",
                "create_via_aadhaar_otp",
                "link_via_otp",
                "scan_and_pull",
            ],
        },
    },
    "if": {"properties": {"method": {"const": "link_via_otp"}}},
    "then": {
        "properties": {
            "type": {
                "type": "string",
                "enum": ["aadhaar", "mobile", "abha-number", "abha-address"],
            },
            "system": {"type": "string", "enum": ["aadhaar", "abdm"]},
        },
        "required": ["type", "system"],
    },
    "required": ["abha_number", "method"],
}

CREATE_ABHA_ADDRESS = {
    "$schema": "http://json-schema.org/draft-07/schema#",
    "type": "object",
    "properties": {"abha_number": {"type": "string", "format": "uuid"}},
    "additionalProperties": False,
    "required": ["abha_number"],
}

SCAN_AND_SHARE = {
    "$schema": "http://json-schema.org/draft-07/schema#",
    "type": "object",
    "properties": {
        "abha_number": {"type": "string", "format": "uuid"},
        "is_existing_patient": {"type": "boolean"},
        "token": {"type": "string"},
    },
    "additionalProperties": False,
    "required": ["abha_number", "is_existing_patient", "token"],
}


LINK_CARE_CONTEXT = {
    "$schema": "http://json-schema.org/draft-07/schema#",
    "type": "object",
    "properties": {
        "abha_number": {"type": "string", "format": "uuid"},
        "type": {
            "type": "string",
            "enum": ["hip_initiated_linking", "patient_initiated_linking"],
        },
        "care_contexts": {
            "type": "array",
            "items": {
                "type": "string",
            },
        },
        "hf_id": {"type": "string"},
    },
    "additionalProperties": False,
    "required": ["abha_number", "care_contexts", "type"],
}


EXCHANGE_DATA = {
    "$schema": "http://json-schema.org/draft-07/schema#",
    "type": "object",
    "properties": {
        "consent_artefact": {"type": "string", "format": "uuid"},
        "is_incoming": {
            "type": "boolean"
        },  # true if receiving data, false if sending data
    },
    "additionalProperties": False,
    "required": ["consent_artefact", "is_incoming"],
}
