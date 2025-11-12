from rest_framework.serializers import CharField, FloatField, Serializer


class PhrGatewayPatientShareSerializer(Serializer):
    hip_id = CharField(max_length=50, required=True)
    context = CharField(max_length=50, required=True)
    hpr_id = CharField(max_length=50, required=False, allow_null=True)
    latitude = FloatField(required=False, allow_null=True)
    longitude = FloatField(required=False, allow_null=True)
