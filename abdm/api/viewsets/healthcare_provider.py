import logging
from celery import shared_task
from drf_spectacular.utils import extend_schema
from rest_framework.decorators import action
from rest_framework.mixins import (
    CreateModelMixin,
    ListModelMixin,
    RetrieveModelMixin,
    UpdateModelMixin,
)
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.viewsets import GenericViewSet
from rest_framework import status
from datetime import datetime

from abdm.api.serializers.healthcare_provider import (
    HealthcareProviderRegistrySerializer,
    VerifyHPRSerializer,
    SearchHPRSerializer,
    CreateHprIdSerializer,
    GenerateAadhaarOtpSerializer,
    VerifyAadhaarOtpSerializer,
    CheckHprIdExistSerializer,
)
from abdm.models import HealthcareProviderRegistry
from abdm.service.v3.healthcare_provider import HealthcareProviderService
from abdm.service.helper import uuid, ABDMAPIException
from abdm.settings import plugin_settings as settings

logger = logging.getLogger(__name__)


@shared_task(bind=True, max_retries=3)
def register_healthcare_provider(self, hpr_registry_id):
    """
    Celery task to register a healthcare provider with retry logic
    """
    try:
        registry_entry = HealthcareProviderRegistry.objects.filter(id=hpr_registry_id).first()
        
        if not registry_entry:
            return [False, "Healthcare Provider Registry entry not found"]
            
        if registry_entry.registered:
            return [True, None]
            
        # Get user details for registration
        user = registry_entry.user
        
        # Prepare registration data
        registration_data = {
            "name": registry_entry.name or f"{user.first_name} {user.last_name}".strip() or user.username,
            "email": registry_entry.email or user.email,
            "mobile": registry_entry.mobile or user.phone_number,
            # Additional data as needed
        }
        
        response = HealthcareProviderService.register_provider(registration_data)
        
        if response and "hpr_id" in response:
            registry_entry.hpr_id = response["hpr_id"]
            registry_entry.registered = True
            registry_entry.is_verified = True
            registry_entry.registration_error = None
            registry_entry.verification_method = 'AUTO'
            registry_entry.verification_time = datetime.now()
            registry_entry.save()
            return [True, None]
        else:
            error_message = "Unknown error during HPR registration"
            if response and "error" in response:
                error_message = str(response["error"])
            
            registry_entry.registration_error = error_message
            registry_entry.save()
            return [False, error_message]
    
    except Exception as e:
        # Log the error
        logger.exception(f"Failed to register HPR (attempt {self.request.retries + 1}): {str(e)}")
        
        # Update the record with the error
        registry_entry = HealthcareProviderRegistry.objects.filter(id=hpr_registry_id).first()
        if registry_entry:
            registry_entry.registration_error = f"Attempt {self.request.retries + 1}: {str(e)}"
            registry_entry.save()
        
        # Retry with exponential backoff
        if self.request.retries < self.max_retries:
            # Retry after 60s, 120s, 240s, etc.
            retry_delay = 60 * (2 ** self.request.retries)
            self.retry(countdown=retry_delay)
            
        return [False, str(e)]


@extend_schema(tags=["ABDM: Healthcare Provider Registry"])
class HealthcareProviderRegistryViewSet(
    GenericViewSet,
    CreateModelMixin,
    ListModelMixin,
    RetrieveModelMixin,
    UpdateModelMixin,
):
    serializer_class = HealthcareProviderRegistrySerializer
    model = HealthcareProviderRegistry
    queryset = HealthcareProviderRegistry.objects.all()
    permission_classes = (IsAuthenticated,)
    
    def get_queryset(self):
        return self.queryset.filter(user=self.request.user)
    
    def create(self, request, *args, **kwargs):
        """
        Create a new HPR record. Handles both new registrations and existing HPR IDs.
        """
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        
        # Check if the user is entering an existing HPR ID
        has_existing = serializer.validated_data.pop('has_existing_hpr_id', False)
        
        if has_existing:
            # If the user says they have an existing HPR ID, it must be provided
            if not serializer.validated_data.get('hpr_id'):
                return Response(
                    {"hpr_id": ["HPR ID is required when has_existing_hpr_id is true"]},
                    status=status.HTTP_400_BAD_REQUEST
                )
                
            # Verify that the HPR ID belongs to the user
            hpr_id = serializer.validated_data.get('hpr_id')
            user_data = {
                "name": serializer.validated_data.get('name', ''),
                "email": serializer.validated_data.get('email', ''),
                "mobile": serializer.validated_data.get('mobile', ''),
            }
            
            verified, error, details = HealthcareProviderService.verify_provider(hpr_id, user_data)
            
            if not verified:
                return Response(
                    {"verification_error": error},
                    status=status.HTTP_400_BAD_REQUEST
                )
                
            # Update with verified data from provider record if available
            if details:
                for field in ['name', 'email', 'mobile']:
                    if details.get(field) and not serializer.validated_data.get(field):
                        serializer.validated_data[field] = details.get(field)
            
            # Mark as verified
            serializer.validated_data['is_verified'] = True
            serializer.validated_data['registered'] = True
            serializer.validated_data['verification_method'] = 'API'
            serializer.validated_data['verification_time'] = datetime.now()
        
        # Add the user to the serializer data
        serializer.validated_data['user'] = request.user
        instance = serializer.save()
        
        # If it's a new registration, trigger the registration process
        if not has_existing and not instance.registered:
            register_healthcare_provider.delay(instance.id)
        
        headers = self.get_success_headers(serializer.data)
        return Response(
            self.get_serializer(instance).data, 
            status=status.HTTP_201_CREATED, 
            headers=headers
        )
    
    @action(detail=True, methods=["POST"])
    def register(self, request, pk=None):
        """
        Endpoint to manually trigger HPR registration
        """
        instance = self.get_object()
        
        # Don't try to register if already registered
        if instance.registered:
            return Response({"registered": True})
            
        # Don't proceed if user indicates they have an existing ID but haven't entered it
        if instance.hpr_id is None:
            return Response(
                {"detail": "HPR ID is missing. Please update the record with your HPR ID."},
                status=status.HTTP_400_BAD_REQUEST
            )
        
        [registered, error] = register_healthcare_provider(instance.id)
        
        if error:
            return Response({"detail": error}, status=status.HTTP_400_BAD_REQUEST)
            
        return Response({"registered": registered})
    
    @action(detail=False, methods=["POST"])
    def verify(self, request):
        """
        Verify an existing HPR ID
        """
        serializer = VerifyHPRSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        
        hpr_id = serializer.validated_data['hpr_id']
        user_data = {
            "name": serializer.validated_data.get('name', ''),
            "email": serializer.validated_data.get('email', ''),
            "mobile": serializer.validated_data.get('mobile', ''),
        }
        
        verified, error, details = HealthcareProviderService.verify_provider(hpr_id, user_data)
        
        if not verified:
            return Response(
                {"verification_error": error},
                status=status.HTTP_400_BAD_REQUEST
            )
            
        # Return the verified details
        return Response({
            "verified": True,
            "details": details
        })
    
    @action(detail=False, methods=["POST"])
    def search(self, request):
        """
        Search for HPR records matching the criteria
        """
        serializer = SearchHPRSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        
        results = HealthcareProviderService.search_provider(serializer.validated_data)
        
        return Response({
            "count": len(results),
            "results": results
        })
    
    @action(detail=False, methods=["POST"])
    def generate_aadhaar_otp(self, request):
        """
        Generate OTP on Aadhaar registered mobile
        
        This is the first step in the Aadhaar-based verification flow for HPR ID creation.
        """
        serializer = GenerateAadhaarOtpSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        
        try:
            # Get gateway token first
            gateway_token_response = HealthcareProviderService.get_gateway_token()
            
            if not gateway_token_response or not gateway_token_response.get("accessToken"):
                return Response(
                    {"detail": "Failed to get gateway token"},
                    status=status.HTTP_500_INTERNAL_SERVER_ERROR
                )
                
            access_token = gateway_token_response.get("accessToken")
            
            # Call the service to generate OTP
            result = HealthcareProviderService.generate_aadhaar_otp(
                serializer.validated_data['aadhaar'],
                access_token
            )
            
            if not result:
                return Response(
                    {"detail": "Failed to generate OTP. Please try again."},
                    status=status.HTTP_500_INTERNAL_SERVER_ERROR
                )
                
            # Return transaction ID for next steps
            return Response({
                "txnId": result.get("txnId", ""),
                "message": "OTP sent to registered mobile number"
            })
                
        except ABDMAPIException as e:
            return Response(
                {"detail": str(e)},
                status=status.HTTP_400_BAD_REQUEST
            )
        except Exception as e:
            logger.exception(f"Failed to generate Aadhaar OTP: {str(e)}")
            return Response(
                {"detail": str(e)},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )
    
    @action(detail=False, methods=["POST"])
    def verify_aadhaar_otp(self, request):
        """
        Verify OTP sent to Aadhaar registered mobile
        
        This is the second step in the Aadhaar-based verification flow for HPR ID creation.
        """
        serializer = VerifyAadhaarOtpSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        
        try:
            # Get gateway token first
            gateway_token_response = HealthcareProviderService.get_gateway_token()
            
            if not gateway_token_response or not gateway_token_response.get("accessToken"):
                return Response(
                    {"detail": "Failed to get gateway token"},
                    status=status.HTTP_500_INTERNAL_SERVER_ERROR
                )
                
            access_token = gateway_token_response.get("accessToken")
            
            # Call the service to verify OTP
            result = HealthcareProviderService.verify_aadhaar_otp(
                serializer.validated_data['otp'],
                serializer.validated_data['txnId'],
                access_token
            )
            
            if not result:
                return Response(
                    {"detail": "Failed to verify OTP. Please try again."},
                    status=status.HTTP_500_INTERNAL_SERVER_ERROR
                )
                
            # Return transaction ID and token for next steps
            return Response({
                "txnId": result.get("txnId", ""),
                "token": access_token,
                "message": "OTP verified successfully"
            })
                
        except ABDMAPIException as e:
            return Response(
                {"detail": str(e)},
                status=status.HTTP_400_BAD_REQUEST
            )
        except Exception as e:
            logger.exception(f"Failed to verify Aadhaar OTP: {str(e)}")
            return Response(
                {"detail": str(e)},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )
    
    @action(detail=False, methods=["POST"])
    def check_hpr_id_exist(self, request):
        """
        Check if an HPR ID exists for the given transaction
        
        This is an optional step after Aadhaar verification to check if an HPR ID already exists.
        """
        serializer = CheckHprIdExistSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        
        try:
            # Get gateway token first
            gateway_token_response = HealthcareProviderService.get_gateway_token()
            
            if not gateway_token_response or not gateway_token_response.get("accessToken"):
                return Response(
                    {"detail": "Failed to get gateway token"},
                    status=status.HTTP_500_INTERNAL_SERVER_ERROR
                )
                
            access_token = gateway_token_response.get("accessToken")
            
            # Call the service to check if HPR ID exists
            result = HealthcareProviderService.check_hpr_id_exist(
                serializer.validated_data['txnId'],
                access_token,
                serializer.validated_data.get('preverifiedCheck', True)
            )
            
            if not result:
                return Response(
                    {"detail": "Failed to check HPR ID existence. Please try again."},
                    status=status.HTTP_500_INTERNAL_SERVER_ERROR
                )
                
            # Return the result with existing HPR ID if available
            exists = result.get("exist", False)
            response_data = {
                "exists": exists,
                "txnId": serializer.validated_data['txnId'],
                "token": access_token,
                "message": "HPR ID check completed"
            }
            
            if exists and result.get("hprId"):
                response_data["hprId"] = result.get("hprId")
                
                # Save the HPR ID in our database if it exists
                if request.user.is_authenticated:
                    hpr_record, created = HealthcareProviderRegistry.objects.get_or_create(
                        user=request.user,
                        defaults={
                            'hpr_id': result.get("hprId"),
                            'is_verified': True,
                            'registered': True,
                            'verification_method': 'AADHAAR',
                            'verification_time': datetime.now(),
                        }
                    )
                    
                    # If not created, update the record
                    if not created:
                        hpr_record.hpr_id = result.get("hprId")
                        hpr_record.is_verified = True
                        hpr_record.registered = True
                        hpr_record.verification_method = 'AADHAAR'
                        hpr_record.verification_time = datetime.now()
                        hpr_record.registration_error = None
                        hpr_record.save()
            
            return Response(response_data)
                
        except ABDMAPIException as e:
            return Response(
                {"detail": str(e)},
                status=status.HTTP_400_BAD_REQUEST
            )
        except Exception as e:
            logger.exception(f"Failed to check HPR ID existence: {str(e)}")
            return Response(
                {"detail": str(e)},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )
    
    @action(detail=False, methods=["POST"])
    def create_hpr_id(self, request):
        """
        Create a new HPR ID using the HPR API with Aadhaar verification
        
        This endpoint implements the Aadhaar-based HPR ID creation process.
        It requires the transaction ID from the OTP verification step.
        """
        serializer = CreateHprIdSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        
        try:
            # Get gateway token first
            gateway_token_response = HealthcareProviderService.get_gateway_token()
            
            if not gateway_token_response or not gateway_token_response.get("accessToken"):
                return Response(
                    {"detail": "Failed to get gateway token"},
                    status=status.HTTP_500_INTERNAL_SERVER_ERROR
                )
                
            access_token = gateway_token_response.get("accessToken")
            txn_id = serializer.validated_data['txnId']
            
            # Call the service to create the HPR ID
            result = HealthcareProviderService.create_hpr_id(
                serializer.validated_data, 
                access_token,
                txn_id
            )
            
            if not result:
                return Response(
                    {"detail": "Failed to create HPR ID. Please try again."},
                    status=status.HTTP_500_INTERNAL_SERVER_ERROR
                )
                
            # If successful, create a local record for the user
            if result.get('status') == "SUCCESS" and result.get('hprId'):
                hpr_id = result['hprId']
                
                # Check if a record already exists for this user
                hpr_record, created = HealthcareProviderRegistry.objects.get_or_create(
                    user=request.user,
                    defaults={
                        'hpr_id': hpr_id,
                        'name': serializer.validated_data.get('name', ''),
                        'email': serializer.validated_data.get('email', ''),
                        'mobile': serializer.validated_data.get('mobileNumber', ''),
                        'is_verified': True,
                        'registered': True,
                        'verification_method': 'AADHAAR',
                        'verification_time': datetime.now(),
                    }
                )
                
                # If not created, update the record
                if not created:
                    hpr_record.hpr_id = hpr_id
                    hpr_record.name = serializer.validated_data.get('name', hpr_record.name)
                    hpr_record.email = serializer.validated_data.get('email', hpr_record.email)
                    hpr_record.mobile = serializer.validated_data.get('mobileNumber', hpr_record.mobile)
                    hpr_record.is_verified = True
                    hpr_record.registered = True
                    hpr_record.verification_method = 'AADHAAR'
                    hpr_record.verification_time = datetime.now()
                    hpr_record.registration_error = None
                    hpr_record.save()
                
                # Return success response with HPR ID
                return Response({
                    "hprId": hpr_id,
                    "status": result.get('status', 'SUCCESS'),
                    "message": "Successfully created HPR ID"
                })
            else:
                # Return error response
                return Response(
                    {"detail": result.get('message', 'Failed to create HPR ID')},
                    status=status.HTTP_400_BAD_REQUEST
                )
                
        except ABDMAPIException as e:
            return Response(
                {"detail": str(e)},
                status=status.HTTP_400_BAD_REQUEST
            )
        except Exception as e:
            logger.exception(f"Failed to create HPR ID: {str(e)}")
            return Response(
                {"detail": str(e)},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )
    
    def perform_update(self, serializer):
        instance = serializer.instance
        has_hpr_id_changed = 'hpr_id' in serializer.validated_data and instance.hpr_id != serializer.validated_data['hpr_id']
        
        # If the HPR ID is being changed, verify it
        if has_hpr_id_changed:
            hpr_id = serializer.validated_data['hpr_id']
            user_data = {
                "name": serializer.validated_data.get('name', instance.name),
                "email": serializer.validated_data.get('email', instance.email),
                "mobile": serializer.validated_data.get('mobile', instance.mobile),
            }
            
            verified, error, details = HealthcareProviderService.verify_provider(hpr_id, user_data)
            
            if verified:
                # Mark as verified when HPR ID is successfully verified
                serializer.validated_data['is_verified'] = True
                serializer.validated_data['registered'] = True
                serializer.validated_data['verification_method'] = 'API'
                serializer.validated_data['verification_time'] = datetime.now()
                
                # Update with verified data if available
                if details:
                    for field in ['name', 'email', 'mobile']:
                        if details.get(field) and not serializer.validated_data.get(field):
                            serializer.validated_data[field] = details.get(field)
            else:
                # Don't save invalid HPR IDs
                raise serializers.ValidationError({"hpr_id": [error]})
        
        # If we're creating a new HPR ID, mark as unregistered to trigger registration
        elif 'hpr_id' not in serializer.validated_data and not instance.registered:
            serializer.validated_data['registered'] = False
            
        # Save the instance
        instance = serializer.save()
        
        # Trigger registration if needed
        if not instance.registered and not instance.hpr_id:
            register_healthcare_provider.delay(instance.id) 