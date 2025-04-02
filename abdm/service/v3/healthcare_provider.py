import logging
import re
import base64
from typing import Dict, Any, Optional, Tuple, List
from Crypto.PublicKey import RSA
from Crypto.Cipher import PKCS1_v1_5
from Crypto.Hash import SHA512

from abdm.service.helper import ABDMAPIException, timestamp, uuid
from abdm.service.request import Request
from abdm.settings import plugin_settings as settings
from abdm.models import HealthcareProviderRegistry
from abdm.service.v3.types.healthcare_provider import (
    RegisterProviderBody,
    RegisterProviderResponse,
    VerifyProviderBody,
    VerifyProviderResponse,
    GetProviderDetailsBody,
    GetProviderDetailsResponse,
    CreateHprIdBody,
    CreateHprIdResponse,
    GetTokenBody,
    GetCertificateResponse,
    GatewayTokenBody,
    GatewayTokenResponse,
    GenerateAadhaarOtpBody,
    GenerateAadhaarOtpResponse,
    VerifyAadhaarOtpBody,
    VerifyAadhaarOtpResponse,
    CheckHprIdExistBody,
    CheckHprIdExistResponse,
)

logger = logging.getLogger(__name__)


class HealthcareProviderService:
    # Initialize with HPR API base URL
    request = Request(f"{getattr(settings, 'ABDM_HPR_URL', '')}/v1")
    
    # Gateway URL for authentication
    gateway_request = Request(f"{getattr(settings, 'ABDM_GATEWAY_URL', '')}")
    
    @staticmethod
    def get_gateway_token() -> Optional[GatewayTokenResponse]:
        """
        Get a token from the ABDM Gateway using client credentials
        """
        if not getattr(settings, 'ABDM_GATEWAY_URL', None):
            logger.error("ABDM Gateway URL not configured in settings")
            return None
            
        if not getattr(settings, 'ABDM_CLIENT_ID', None) or not getattr(settings, 'ABDM_CLIENT_SECRET', None):
            logger.error("ABDM Client ID or Client Secret not configured in settings")
            return None
            
        try:
            payload = {
                "clientId": settings.ABDM_CLIENT_ID,
                "clientSecret": settings.ABDM_CLIENT_SECRET,
            }
            
            path = "/v0.5/sessions"
            response = HealthcareProviderService.gateway_request.post(
                path,
                payload,
            )
            
            if response.status_code != 200:
                error_msg = f"Error getting Gateway token: {response.json()}"
                logger.error(error_msg)
                raise ABDMAPIException(detail=error_msg)
                
            return response.json()
            
        except Exception as e:
            logger.exception(f"Failed to get Gateway token: {str(e)}")
            return None
    
    @staticmethod
    def generate_aadhaar_otp(aadhaar: str, access_token: str) -> Optional[GenerateAadhaarOtpResponse]:
        """
        Generate OTP on Aadhaar registered mobile
        """
        if not getattr(settings, 'ABDM_HPR_URL', None):
            logger.error("HPR URL not configured in settings")
            return None
            
        try:
            payload = {
                "aadhaar": aadhaar
            }
            
            # Add authorization headers
            headers = {
                "Authorization": f"Bearer {access_token}"
            }
            
            path = "/registration/aadhaar/generateOtp"
            response = HealthcareProviderService.request.post(
                path,
                payload,
                headers=headers,
            )
            
            if response.status_code != 200:
                error_msg = f"Error generating Aadhaar OTP: {response.json()}"
                logger.error(error_msg)
                raise ABDMAPIException(detail=error_msg)
                
            return response.json()
            
        except ABDMAPIException as e:
            # Pass through the ABDM API exceptions
            raise e
        except Exception as e:
            logger.exception(f"Failed to generate Aadhaar OTP: {str(e)}")
            return None
    
    @staticmethod
    def verify_aadhaar_otp(
        otp: str, 
        txn_id: str, 
        access_token: str, 
        domain_name: str = "@hpr.abdm", 
        id_type: str = "hpr_id"
    ) -> Optional[VerifyAadhaarOtpResponse]:
        """
        Verify OTP sent to Aadhaar registered mobile
        """
        if not getattr(settings, 'ABDM_HPR_URL', None):
            logger.error("HPR URL not configured in settings")
            return None
            
        try:
            payload = {
                "domainName": domain_name,
                "idType": id_type,
                "otp": otp,
                "restrictions": "",
                "txnId": txn_id
            }
            
            # Add authorization headers
            headers = {
                "Authorization": f"Bearer {access_token}"
            }
            
            path = "/registration/aadhaar/verifyOTP"
            response = HealthcareProviderService.request.post(
                path,
                payload,
                headers=headers,
            )
            
            if response.status_code != 200:
                error_msg = f"Error verifying Aadhaar OTP: {response.json()}"
                logger.error(error_msg)
                raise ABDMAPIException(detail=error_msg)
                
            return response.json()
            
        except ABDMAPIException as e:
            # Pass through the ABDM API exceptions
            raise e
        except Exception as e:
            logger.exception(f"Failed to verify Aadhaar OTP: {str(e)}")
            return None
    
    @staticmethod
    def check_hpr_id_exist(txn_id: str, access_token: str, preverified_check: bool = True) -> Optional[CheckHprIdExistResponse]:
        """
        Check if an HPR ID exists for the given transaction
        """
        if not getattr(settings, 'ABDM_HPR_URL', None):
            logger.error("HPR URL not configured in settings")
            return None
            
        try:
            payload = {
                "txnId": txn_id,
                "preverifiedCheck": preverified_check
            }
            
            # Add authorization headers
            headers = {
                "Authorization": f"Bearer {access_token}"
            }
            
            path = "/v2/registration/aadhaar/checkHpIdAccountExist"
            response = HealthcareProviderService.request.post(
                path,
                payload,
                headers=headers,
            )
            
            if response.status_code != 200:
                error_msg = f"Error checking HPR ID: {response.json()}"
                logger.error(error_msg)
                raise ABDMAPIException(detail=error_msg)
                
            return response.json()
            
        except ABDMAPIException as e:
            # Pass through the ABDM API exceptions
            raise e
        except Exception as e:
            logger.exception(f"Failed to check HPR ID: {str(e)}")
            return None
    
    @staticmethod
    def register_provider(data: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """
        Register a healthcare provider in the HPR
        """
        if not getattr(settings, 'ABDM_HPR_URL', None):
            logger.error("HPR URL not configured in settings")
            return None
            
        try:
            # Prepare API payload based on HPR API documentation
            payload = {
                # Fields will be populated based on the HPR API documentation
                "name": data.get("name"),
                "email": data.get("email"),
                "mobile": data.get("mobile"),
                # Additional fields as required by the API
            }
            
            path = "/provider/register"  # Actual endpoint from HPR API docs
            response = HealthcareProviderService.request.post(
                path,
                payload,
            )
            
            if response.status_code != 200:
                error_msg = f"Error registering provider: {response.json()}"
                logger.error(error_msg)
                raise ABDMAPIException(detail=error_msg)
                
            return response.json()
            
        except Exception as e:
            logger.exception(f"Failed to register provider: {str(e)}")
            return None
    
    @staticmethod
    def verify_provider(hpr_id: str, user_data: Dict[str, Any]) -> Tuple[bool, Optional[str], Optional[Dict[str, Any]]]:
        """
        Verify a healthcare provider's HPR ID against user data
        
        Returns a tuple of:
        - bool: Whether verification was successful
        - Optional[str]: Error message if verification failed
        - Optional[Dict[str, Any]]: Provider details if verification succeeded
        """
        if not getattr(settings, 'ABDM_HPR_URL', None):
            logger.error("HPR URL not configured in settings")
            return False, "HPR URL not configured", None
            
        try:
            # First, get provider details
            provider_details = HealthcareProviderService.get_provider_details(hpr_id)
            
            if not provider_details:
                return False, "Could not retrieve provider details", None
                
            # Verify that the provided details match the HPR record
            # This is a simplified example; actual implementation would depend on the HPR API response format
            is_verified = False
            verification_errors = []
            
            # Check name (if provided)
            if user_data.get("name") and provider_details.get("name"):
                if user_data["name"].lower() != provider_details["name"].lower():
                    verification_errors.append("Name does not match HPR record")
            
            # Check email (if provided)
            if user_data.get("email") and provider_details.get("email"):
                if user_data["email"].lower() != provider_details["email"].lower():
                    verification_errors.append("Email does not match HPR record")
            
            # Check mobile (if provided)
            if user_data.get("mobile") and provider_details.get("mobile"):
                # Normalize mobile numbers before comparing
                user_mobile = re.sub(r'[^0-9]', '', user_data["mobile"])
                provider_mobile = re.sub(r'[^0-9]', '', provider_details["mobile"])
                if user_mobile != provider_mobile:
                    verification_errors.append("Mobile number does not match HPR record")
            
            is_verified = len(verification_errors) == 0
                
            if is_verified:
                return True, None, provider_details
            else:
                return False, ", ".join(verification_errors), provider_details
            
        except Exception as e:
            logger.exception(f"Failed to verify provider: {str(e)}")
            return False, f"Verification failed: {str(e)}", None
    
    @staticmethod
    def get_provider_details(hpr_id: str) -> Optional[Dict[str, Any]]:
        """
        Get details of a healthcare provider using their HPR ID
        """
        if not getattr(settings, 'ABDM_HPR_URL', None):
            logger.error("HPR URL not configured in settings")
            return None
            
        try:
            path = f"/provider/{hpr_id}"  # Actual endpoint from HPR API docs
            response = HealthcareProviderService.request.get(path)
            
            if response.status_code != 200:
                error_msg = f"Error retrieving provider details: {response.json()}"
                logger.error(error_msg)
                raise ABDMAPIException(detail=error_msg)
                
            return response.json()
            
        except Exception as e:
            logger.exception(f"Failed to retrieve provider details: {str(e)}")
            return None
    
    @staticmethod
    def search_provider(search_params: Dict[str, Any]) -> Optional[List[Dict[str, Any]]]:
        """
        Search for providers matching the given criteria
        """
        if not getattr(settings, 'ABDM_HPR_URL', None):
            logger.error("HPR URL not configured in settings")
            return None
            
        try:
            # Prepare API payload based on HPR API documentation
            payload = {
                # Fields will be populated based on the HPR API documentation
                "name": search_params.get("name"),
                "email": search_params.get("email"),
                "mobile": search_params.get("mobile"),
                # Additional search parameters as required by the API
            }
            
            path = "/provider/search"  # Actual endpoint from HPR API docs
            response = HealthcareProviderService.request.post(
                path,
                payload,
            )
            
            if response.status_code != 200:
                error_msg = f"Error searching providers: {response.json()}"
                logger.error(error_msg)
                raise ABDMAPIException(detail=error_msg)
                
            return response.json().get("providers", [])
            
        except Exception as e:
            logger.exception(f"Failed to search providers: {str(e)}")
            return None
    
    @staticmethod
    def register_provider_with_fallback(data: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """
        Register a provider with fallback mechanism for errors
        
        This method attempts to register a provider, and if specific errors occur,
        it will retry with modified parameters or provide more detailed error information.
        """
        try:
            return HealthcareProviderService.register_provider(data)
        except ABDMAPIException as e:
            # Handle specific API errors that might be recoverable
            if "provider already exists" in str(e).lower():
                # Try to retrieve the existing provider details instead
                if data.get("mobile"):
                    providers = HealthcareProviderService.search_provider({"mobile": data["mobile"]})
                    if providers and len(providers) > 0:
                        logger.info(f"Provider already exists, returning existing record")
                        return providers[0]
            
            # Re-raise the exception for non-recoverable errors
            raise
    
    @staticmethod
    def get_public_certificate() -> Optional[GetCertificateResponse]:
        """
        Get the public certificate for encrypting sensitive data
        """
        if not getattr(settings, 'ABDM_HPR_URL', None):
            logger.error("HPR URL not configured in settings")
            return None
            
        try:
            path = "/certificate"
            response = HealthcareProviderService.request.get(path)
            
            if response.status_code != 200:
                error_msg = f"Error retrieving public certificate: {response.json()}"
                logger.error(error_msg)
                raise ABDMAPIException(detail=error_msg)
                
            return response.json()
            
        except Exception as e:
            logger.exception(f"Failed to retrieve public certificate: {str(e)}")
            return None
    
    @staticmethod
    def encrypt_data(data: str, certificate: str) -> Optional[str]:
        """
        Encrypt data using RSA with provided public certificate
        """
        try:
            # Clean and format the certificate
            cert_lines = certificate.replace("-----BEGIN PUBLIC KEY-----", "")
            cert_lines = cert_lines.replace("-----END PUBLIC KEY-----", "")
            cert_lines = cert_lines.replace("\n", "").strip()
            
            # Load the public key
            public_key = RSA.importKey(base64.b64decode(cert_lines))
            
            # Create a cipher using PKCS1_v1_5
            cipher = PKCS1_v1_5.new(public_key)
            
            # Encrypt the data
            encrypted_data = cipher.encrypt(data.encode('utf-8'))
            
            # Return base64 encoded encrypted data
            return base64.b64encode(encrypted_data).decode('utf-8')
            
        except Exception as e:
            logger.exception(f"Failed to encrypt data: {str(e)}")
            return None
    
    @staticmethod
    def create_hpr_id(
        data: Dict[str, Any], 
        access_token: str,
        txn_id: str
    ) -> Optional[CreateHprIdResponse]:
        """
        Create an HPR ID for a healthcare provider
        
        Args:
            data: Dictionary containing provider details (including name, mobile, etc.)
            access_token: Gateway token for authorization
            txn_id: Transaction ID from the Aadhaar verification flow
            
        Returns:
            Response from HPR ID creation API or None if an error occurs
        """
        if not getattr(settings, 'ABDM_HPR_URL', None):
            logger.error("HPR URL not configured in settings")
            return None
            
        try:
            # First, get public certificate for encrypting sensitive data
            certificate_response = HealthcareProviderService.get_public_certificate()
            if not certificate_response or not certificate_response.get("certificate"):
                logger.error("Failed to get public certificate")
                return None
                
            # Encrypt mobile number
            encrypted_mobile = HealthcareProviderService.encrypt_data(
                data.get("mobileNumber", ""), 
                certificate_response.get("certificate", "")
            )
            
            if not encrypted_mobile:
                logger.error("Failed to encrypt mobile number")
                return None
            
            # Prepare payload for HPR ID creation
            payload = {
                "txnId": txn_id,
                "mobileNumber": encrypted_mobile,
                "name": data.get("name", ""),
                "gender": data.get("gender", ""),
                "yearOfBirth": data.get("yearOfBirth", ""),
                "monthOfBirth": data.get("monthOfBirth", ""),
                "dayOfBirth": data.get("dayOfBirth", ""),
                "firstName": data.get("firstName", ""),
                "middleName": data.get("middleName", ""),
                "lastName": data.get("lastName", ""),
                "email": data.get("email", ""),
                "profilePhoto": data.get("profilePhoto", ""),
                "healthIdNumber": data.get("healthIdNumber", ""),
                "healthId": data.get("healthId", ""),
                "hprId": data.get("hprId", ""),
                "stateCouncil": data.get("stateCouncil", ""),
                "registrationNumber": data.get("registrationNumber", ""),
                "stateMedicalCouncil": data.get("stateMedicalCouncil", ""),
                "isMCIVerified": data.get("isMCIVerified", False),
                "medicalCouncilId": data.get("medicalCouncilId", ""),
                "medicalCouncilName": data.get("medicalCouncilName", ""),
                "professionalType": data.get("professionalType", ""),
                "stateCouncilId": data.get("stateCouncilId", ""),
                "stateCouncilName": data.get("stateCouncilName", ""),
                "specialty": data.get("specialty", ""),
                "alternateEmail": data.get("alternateEmail", ""),
                "alternateMobile": data.get("alternateMobile", ""),
                "kyc": data.get("kyc", False),
                "kycVerified": data.get("kycVerified", False),
                "address": data.get("address", {}),
                "languages": data.get("languages", []),
                "qualifications": data.get("qualifications", []),
                "workExperiences": data.get("workExperiences", []),
                "education": data.get("education", []),
                "healthcareProfessionalType": data.get("healthcareProfessionalType", ""),
                "digitalSignature": data.get("digitalSignature", ""),
                "usingAadhaar": True
            }
            
            # Add authorization headers
            headers = {
                "Authorization": f"Bearer {access_token}"
            }
            
            path = "/v2/registration/aadhaar/createHpId"
            response = HealthcareProviderService.request.post(
                path,
                payload,
                headers=headers,
            )
            
            if response.status_code != 200:
                error_msg = f"Error creating HPR ID: {response.json()}"
                logger.error(error_msg)
                raise ABDMAPIException(detail=error_msg)
                
            return response.json()
            
        except ABDMAPIException as e:
            # Pass through the ABDM API exceptions
            raise e
        except Exception as e:
            logger.exception(f"Failed to create HPR ID: {str(e)}")
            return None
    
    @staticmethod
    def handle_error(error: Dict[str, Any]) -> str:
        """
        Handle error responses from HPR API
        """
        if "error" in error:
            if isinstance(error["error"], dict):
                return error["error"].get("message", "Unknown error")
            return str(error["error"])
        return "Unknown error" 