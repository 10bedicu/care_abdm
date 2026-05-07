from functools import wraps

from fhir.resources.R4B.codeableconcept import CodeableConcept
from fhir.resources.R4B.coding import Coding
from fhir.resources.R4B.reference import Reference
from fhir.resources.R4B.resource import Resource

from abdm.service.helper import uuid
from care.emr.models.base import EMRBaseModel
from care.emr.resources.common.coding import Coding as CodingSpec


def cache_profiles(resource_type: str):
    def decorator(func):
        @wraps(func)
        def wrapper(self, model_instance: EMRBaseModel, *args, **kwargs):
            if not hasattr(model_instance, "external_id"):
                err = f"{model_instance.__class__.__name__} does not have 'external_id' attribute"
                raise AttributeError(err)

            cache_key_prefix = kwargs.get("cache_key_prefix", "")
            cache_key_id = str(model_instance.external_id)
            cache_key_suffix = kwargs.get("cache_key_suffix", "")
            cache_key = (
                f"{resource_type}/{cache_key_prefix}{cache_key_id}{cache_key_suffix}"
            )

            if cache_key in self._profiles:
                return self._profiles[cache_key]

            result = func(self, model_instance, *args, **kwargs)

            self._profiles[cache_key] = result
            self._resource_id_url_map[cache_key] = uuid()
            return result

        return wrapper

    return decorator


class FhirBase:
    def __init__(self):
        self._profiles = {}
        self._resource_id_url_map = {}

    def cached_profiles(self):
        return list(
            filter(lambda profile: profile is not None, self._profiles.values())
        )

    def _reference_url(self, resource: Resource = None):
        if resource is None:
            return ""

        key = f"{resource.resource_type}/{resource.id}"
        return f"urn:uuid:{self._resource_id_url_map.get(key, uuid())}"

    def _reference(self, resource: Resource = None):
        if resource is None:
            return None

        return Reference(reference=self._reference_url(resource))

    def _coding(self, coding: CodingSpec | None):
        if coding is None:
            return None

        return Coding(
            code=coding.code,
            display=coding.display,
            system=coding.system,
        )

    def _coding_to_codable_concept(self, coding: CodingSpec | None):
        if coding is None:
            return None

        fhir_coding = self._coding(coding)
        return CodeableConcept(coding=[fhir_coding], text=fhir_coding.display)

    def _concept_from_mapping(
        self, system: str, mapping: dict[str, tuple[str, str]], key: str, default: str
    ):
        coding = self._coding_from_mapping(system, mapping, key, default)
        return CodeableConcept(coding=[coding], text=coding.display)

    def _coding_from_mapping(
        self, system: str, mapping: dict[str, tuple[str, str]], key: str, default: str
    ):
        coding = mapping.get(key)
        if not mapping:
            coding = mapping.get(default)

        return Coding(
            system=system,
            code=coding[0],
            display=coding[1] or None,
        )
