"""The encounter extension this plugin registers with CARE.

``extensions["abdm"]`` on an encounter holds the patient's choice to keep that
visit out of ABDM: ``{"link_records": false}``. The choice is made when the
encounter is created and cannot be changed afterwards, because a care context
that has been linked cannot be unlinked.
"""

from django.utils import timezone
from jsonschema import ValidationError as SchemaValidationError
from jsonschema import validate

from care.emr.extensions.base import ExtensionResource, PlugExtension
from care.emr.registries.extensions.registry import ExtensionRegistry

EXTENSION_NAME = "abdm"
LINK_RECORDS = "link_records"
RECORDED_BY = "recorded_by"
RECORDED_AT = "recorded_at"

# set by the plugin, never by the client
READ_ONLY_KEYS = (RECORDED_BY, RECORDED_AT)

LINK_RECORDS_SCHEMA = {
    "type": "boolean",
    "title": "Link records to ABHA",
    "description": (
        "False keeps the records of this encounter out of ABDM. "
        "Read only when the encounter is created."
    ),
}


class EncounterLinkingExtension(PlugExtension):
    resource_type = ExtensionResource.encounter
    extension_name = EXTENSION_NAME

    write_schema = {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "type": "object",
        "properties": {LINK_RECORDS: LINK_RECORDS_SCHEMA},
        "additionalProperties": False,
    }
    read_schema = {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "type": "object",
        "properties": {
            LINK_RECORDS: LINK_RECORDS_SCHEMA,
            RECORDED_BY: {
                "type": ["string", "null"],
                "title": "Recorded by",
                "description": "Id of the user who created the encounter",
                "readOnly": True,
            },
            RECORDED_AT: {
                "type": "string",
                "format": "date-time",
                "title": "Recorded at",
                "readOnly": True,
            },
        },
        "additionalProperties": False,
    }
    retrieve_schema = read_schema

    def _writable(self, data: dict) -> dict:
        # a client that sends back what it read is not refused for the stamp
        return {key: value for key, value in data.items() if key not in READ_ONLY_KEYS}

    def validate(self, data, resource=None):
        if not isinstance(data, dict):
            raise ValueError("The abdm extension must be an object")

        try:
            validate(self._writable(data), self.write_schema)
        except SchemaValidationError as e:
            raise ValueError("Invalid abdm extension") from e
        return data

    def serialize_extensions(self, data, resource=None):
        return self._writable(data)


ExtensionRegistry.register(EncounterLinkingExtension())


def linking_opted_out(extensions) -> bool:
    """Whether the extensions of an encounter keep its records out of ABDM."""
    value = extensions.get(EXTENSION_NAME) if isinstance(extensions, dict) else None
    return isinstance(value, dict) and value.get(LINK_RECORDS) is False


def stamp_opt_out(encounter):
    """Record who opted a new encounter out of linking, and when."""
    if not linking_opted_out(encounter.extensions):
        return

    creator = encounter.created_by if encounter.created_by_id else None
    encounter.extensions = {
        **encounter.extensions,
        EXTENSION_NAME: {
            LINK_RECORDS: False,
            RECORDED_BY: str(creator.external_id) if creator else None,
            RECORDED_AT: timezone.now().isoformat(),
        },
    }


def keep_recorded_choice(encounter, stored_extensions):
    """Put back the ``abdm`` extension an existing encounter is stored with.

    CARE replaces the whole of ``extensions`` on an update that sends it, and its
    validator cannot tell an update from a create, so the rule that the choice is
    only made at creation is kept here.
    """
    stored = stored_extensions if isinstance(stored_extensions, dict) else {}
    extensions = encounter.extensions if isinstance(encounter.extensions, dict) else {}

    if EXTENSION_NAME in stored:
        if extensions.get(EXTENSION_NAME) != stored[EXTENSION_NAME]:
            encounter.extensions = {**extensions, EXTENSION_NAME: stored[EXTENSION_NAME]}
    elif EXTENSION_NAME in extensions:
        encounter.extensions = {
            key: value for key, value in extensions.items() if key != EXTENSION_NAME
        }
