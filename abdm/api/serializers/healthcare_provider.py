from rest_framework import serializers
from abdm.models import HealthcareProviderRegistry


class HealthcareProviderRegistrySerializer(serializers.ModelSerializer):
    id = serializers.CharField(source="external_id", read_only=True)
    registered = serializers.BooleanField(read_only=True)
    has_existing_hpr_id = serializers.BooleanField(write_only=True, required=False, default=False)
    
    class Meta:
        model = HealthcareProviderRegistry
        exclude = ("deleted",)
        read_only_fields = ("registered", "registration_error", "is_verified")


class VerifyHPRSerializer(serializers.Serializer):
    """
    Serializer for verifying an existing HPR ID
    """
    hpr_id = serializers.CharField(max_length=50, required=True)
    name = serializers.CharField(max_length=100, required=False)
    email = serializers.EmailField(required=False)
    mobile = serializers.CharField(max_length=20, required=False)


class SearchHPRSerializer(serializers.Serializer):
    """
    Serializer for searching HPR records
    """
    name = serializers.CharField(max_length=100, required=False)
    email = serializers.EmailField(required=False)
    mobile = serializers.CharField(max_length=20, required=False)
    
    def validate(self, data):
        """
        Ensure at least one search parameter is provided
        """
        if not any(data.values()):
            raise serializers.ValidationError("At least one search parameter must be provided")
        return data


class GenerateAadhaarOtpSerializer(serializers.Serializer):
    """
    Serializer for generating OTP on Aadhaar registered mobile
    """
    aadhaar = serializers.CharField(max_length=12, required=True)
    
    def validate_aadhaar(self, value):
        """
        Validate that Aadhaar number is 12 digits
        """
        if not value.isdigit() or len(value) != 12:
            raise serializers.ValidationError("Aadhaar number must be 12 digits")
        return value


class VerifyAadhaarOtpSerializer(serializers.Serializer):
    """
    Serializer for verifying OTP sent to Aadhaar registered mobile
    """
    txnId = serializers.CharField(max_length=100, required=True)
    otp = serializers.CharField(max_length=6, required=True)
    
    def validate_otp(self, value):
        """
        Validate that OTP is 6 digits
        """
        if not value.isdigit() or len(value) != 6:
            raise serializers.ValidationError("OTP must be 6 digits")
        return value


class CheckHprIdExistSerializer(serializers.Serializer):
    """
    Serializer for checking if an HPR ID exists
    """
    txnId = serializers.CharField(max_length=100, required=True)
    preverifiedCheck = serializers.BooleanField(required=False, default=True)


class CreateHprIdSerializer(serializers.Serializer):
    """
    Serializer for creating a new HPR ID using Aadhaar verification
    """
    # Transaction ID from Aadhaar verification
    txnId = serializers.CharField(max_length=100, required=True)
    
    # Personal Information
    name = serializers.CharField(max_length=100, required=False)
    firstName = serializers.CharField(max_length=100, required=True)
    middleName = serializers.CharField(max_length=100, required=False, allow_blank=True)
    lastName = serializers.CharField(max_length=100, required=True)
    gender = serializers.CharField(max_length=10, required=True)
    yearOfBirth = serializers.CharField(max_length=4, required=True)
    monthOfBirth = serializers.CharField(max_length=2, required=True)
    dayOfBirth = serializers.CharField(max_length=2, required=True)
    email = serializers.EmailField(required=True)
    mobileNumber = serializers.CharField(max_length=20, required=True)
    
    # Professional Information
    professionalType = serializers.CharField(max_length=100, required=False)
    healthcareProfessionalType = serializers.CharField(max_length=100, required=False)
    registrationNumber = serializers.CharField(max_length=100, required=False)
    stateMedicalCouncil = serializers.CharField(max_length=100, required=False)
    stateCouncil = serializers.CharField(max_length=100, required=False)
    stateCouncilId = serializers.CharField(max_length=100, required=False)
    stateCouncilName = serializers.CharField(max_length=100, required=False)
    
    # Optional fields
    alternateEmail = serializers.EmailField(required=False)
    alternateMobile = serializers.CharField(max_length=20, required=False)
    profilePhoto = serializers.CharField(required=False, allow_blank=True)
    address = serializers.JSONField(required=False, default=dict)
    languages = serializers.ListField(required=False, default=list)
    qualifications = serializers.ListField(required=False, default=list)
    workExperiences = serializers.ListField(required=False, default=list)
    education = serializers.ListField(required=False, default=list)
    
    def validate_mobileNumber(self, value):
        """
        Validate that mobile number is in correct format (10 digits)
        """
        import re
        if not re.match(r'^\d{10}$', value):
            raise serializers.ValidationError("Mobile number must be 10 digits")
        return value
    
    def validate(self, data):
        """
        Custom validation for HPR ID creation
        """
        # If name is not provided, construct it from firstName, middleName, lastName
        if not data.get('name'):
            name_parts = [
                data.get('firstName', ''),
                data.get('middleName', ''),
                data.get('lastName', '')
            ]
            data['name'] = ' '.join([part for part in name_parts if part])
            
        return data 