import re
import logging
from typing import Dict, Any, Optional

from abdm.service.request import Request
from abdm.service.helper import uuid, timestamp
from abdm.settings import plugin_settings as settings
from abdm.service.helper import ABDMAPIException

logger = logging.getLogger(__name__)

class HPRService:
    request = Request(f"{settings.ABDM_FACILITY_URL}/v1")
    
    @staticmethod
    def handle_error(response_data):
        if isinstance(response_data, dict):
            if "error" in response_data:
                return response_data["error"].get("message", "Unknown error")
            
            if "details" in response_data:
                return response_data["details"]
        
        return "Unknown error from HPR API"

    @staticmethod
    def register_health_professional(
        data: Dict[str, Any]
    ) -> Dict[str, Any]:
        """
        Register a health professional with the HPR registry
        
        Args:
            data: Dictionary containing the health professional data
                - name: Full name of the health professional
                - email: Email of the health professional
                - mobile: Mobile number of the health professional
                - council_name: Name of the professional council (e.g., Medical Council of India)
                - council_registration_number: Registration number with the council
                - professional_type: Type of health professional (e.g., Doctor, Nurse)
                - qualification: Professional qualification
        
        Returns:
            The API response with the HPR ID
        """
        user = data.get("user")
        
        if not user:
            raise ABDMAPIException(detail="User is required to register health professional")
        
        try:
            payload = {
                "requestId": uuid(),
                "timestamp": timestamp(),
                "professionalDetails": {
                    "name": data.get("name", f"{user.first_name} {user.last_name}").strip(),
                    "email": data.get("email", user.email),
                    "mobile": data.get("mobile", getattr(user, "phone_number", "")),
                    "identifiers": [
                        {
                            "type": "MCI",  # Default to Medical Council of India
                            "value": data.get("council_registration_number", "")
                        }
                    ],
                    "languages": data.get("languages", ["en"]),  # Default to English
                    "professionalType": data.get("professional_type", "Doctor"),  # Default to Doctor
                    "councilName": data.get("council_name", "Medical Council of India"),
                    "councilRegistrationNumber": data.get("council_registration_number", ""),
                    "qualification": data.get("qualification", "")
                }
            }
            
            path = "/professional/registration"
            response = HPRService.request.post(
                path,
                payload,
                headers={
                    "REQUEST-ID": uuid(),
                    "TIMESTAMP": timestamp(),
                }
            )
            
            if response.status_code != 200:
                error_message = HPRService.handle_error(response.json())
                logger.error(f"Error registering health professional: {error_message}")
                raise ABDMAPIException(detail=f"Failed to register health professional: {error_message}")
            
            response_data = response.json()
            return response_data
        
        except Exception as e:
            logger.exception(f"Exception while registering health professional: {str(e)}")
            raise ABDMAPIException(detail=f"Failed to register health professional: {str(e)}")
    
    @staticmethod
    def get_health_professional(
        hpr_id: str
    ) -> Optional[Dict[str, Any]]:
        """
        Get health professional details by HPR ID
        
        Args:
            hpr_id: The HPR ID of the health professional
            
        Returns:
            The health professional details or None if not found
        """
        if not hpr_id:
            raise ABDMAPIException(detail="HPR ID is required to fetch health professional details")
        
        try:
            path = f"/professional/{hpr_id}"
            response = HPRService.request.get(
                path,
                headers={
                    "REQUEST-ID": uuid(),
                    "TIMESTAMP": timestamp(),
                }
            )
            
            if response.status_code == 404:
                return None
                
            if response.status_code != 200:
                error_message = HPRService.handle_error(response.json())
                logger.error(f"Error fetching health professional: {error_message}")
                raise ABDMAPIException(detail=f"Failed to fetch health professional: {error_message}")
            
            response_data = response.json()
            return response_data
        
        except Exception as e:
            logger.exception(f"Exception while fetching health professional: {str(e)}")
            raise ABDMAPIException(detail=f"Failed to fetch health professional: {str(e)}")
    
    @staticmethod
    def update_health_professional(
        data: Dict[str, Any]
    ) -> Dict[str, Any]:
        """
        Update a health professional's details
        
        Args:
            data: Dictionary containing the health professional data
                - hpr_id: The HPR ID of the health professional (required)
                - name: Full name of the health professional
                - email: Email of the health professional
                - mobile: Mobile number of the health professional
                - council_name: Name of the professional council
                - council_registration_number: Registration number with the council
                - professional_type: Type of health professional
                - qualification: Professional qualification
                
        Returns:
            The API response with the updated details
        """
        hpr_id = data.get("hpr_id")
        
        if not hpr_id:
            raise ABDMAPIException(detail="HPR ID is required to update health professional")
        
        try:
            user = data.get("user")
            
            payload = {
                "requestId": uuid(),
                "timestamp": timestamp(),
                "professionalDetails": {
                    "hprId": hpr_id
                }
            }
            
            # Only include fields that are being updated
            professional_details = payload["professionalDetails"]
            
            if "name" in data or user:
                professional_details["name"] = data.get(
                    "name", 
                    f"{user.first_name} {user.last_name}" if user else ""
                ).strip()
                
            if "email" in data or (user and user.email):
                professional_details["email"] = data.get("email", user.email if user else "")
                
            if "mobile" in data or (user and hasattr(user, "phone_number")):
                professional_details["mobile"] = data.get(
                    "mobile", 
                    getattr(user, "phone_number", "") if user else ""
                )
                
            if "languages" in data:
                professional_details["languages"] = data.get("languages")
                
            if "professional_type" in data:
                professional_details["professionalType"] = data.get("professional_type")
                
            if "council_name" in data:
                professional_details["councilName"] = data.get("council_name")
                
            if "council_registration_number" in data:
                professional_details["councilRegistrationNumber"] = data.get("council_registration_number")
                professional_details["identifiers"] = [
                    {
                        "type": "MCI",  # Default to Medical Council of India
                        "value": data.get("council_registration_number")
                    }
                ]
                
            if "qualification" in data:
                professional_details["qualification"] = data.get("qualification")
            
            path = "/professional/update"
            response = HPRService.request.post(
                path,
                payload,
                headers={
                    "REQUEST-ID": uuid(),
                    "TIMESTAMP": timestamp(),
                }
            )
            
            if response.status_code != 200:
                error_message = HPRService.handle_error(response.json())
                logger.error(f"Error updating health professional: {error_message}")
                raise ABDMAPIException(detail=f"Failed to update health professional: {error_message}")
            
            response_data = response.json()
            return response_data
        
        except Exception as e:
            logger.exception(f"Exception while updating health professional: {str(e)}")
            raise ABDMAPIException(detail=f"Failed to update health professional: {str(e)}") 