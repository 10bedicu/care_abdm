from pydantic import ValidationError
from rest_framework import serializers


class EMRPydanticModelField(serializers.Field):
    """
    Custom serializer field to handle EMRResource-based Pydantic models in DRF
    with both serialization and deserialization support
    """

    def __init__(self, pydantic_model, **kwargs):
        self.pydantic_model = pydantic_model
        super().__init__(**kwargs)

    def to_representation(self, value):
        if not value:
            return None

        request = self.context.get("request", None)
        user = request.user if request else None

        pydantic_instance = self.pydantic_model.serialize(value, user=user)
        return pydantic_instance.to_json()

    def to_internal_value(self, data):
        if not data:
            return None

        try:
            pydantic_instance = self.pydantic_model.model_validate(data)
            instance = self.parent.instance.patient if self.parent.instance else None

            return pydantic_instance.de_serialize(obj=instance)

        except ValidationError as e:
            raise serializers.ValidationError(str(e)) from e
        except Exception as e:
            raise serializers.ValidationError(str(e)) from e
