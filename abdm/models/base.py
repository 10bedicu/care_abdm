from django.db import models


class Status(models.TextChoices):
    """
    Enum representing the various statuses of a consent request.

    - REQUESTED: The consent request has been made but not yet responded to.
    - GRANTED: The patient has granted consent to access their health information.
    - DENIED: The patient has denied the consent request.
    - EXPIRED: The consent request has expired and is no longer valid.
    - REVOKED: The patient has revoked previously granted consent.
    """
    REQUESTED = "REQUESTED"
    GRANTED = "GRANTED"
    DENIED = "DENIED"
    EXPIRED = "EXPIRED"
    REVOKED = "REVOKED"


class Purpose(models.TextChoices):
    """
    Enum representing the purpose for accessing the patient's health records.

    - CARE_MANAGEMENT (CAREMGT): Access for managing patient care.
    - BREAK_THE_GLASS (BTG): Emergency access, typically when normal consent cannot be obtained.
    - PUBLIC_HEALTH (PUBHLTH): Access for public health activities and purposes.
    - HEALTHCARE_PAYMENT (HPAYMT): Access for healthcare payment activities.
    - DISEASE_SPECIFIC_HEALTHCARE_RESEARCH (DSRCH): Access for research related to specific diseases.
    - SELF_REQUESTED (PATRQT): Access requested by the patient themselves.

    For more detailed descriptions of each purpose, please refer to the HL7 ValueSet documentation:
    http://terminology.hl7.org/ValueSet/v3-PurposeOfUse
    """
    CARE_MANAGEMENT = "CAREMGT"
    BREAK_THE_GLASS = "BTG"
    PUBLIC_HEALTH = "PUBHLTH"
    HEALTHCARE_PAYMENT = "HPAYMT"
    DISEASE_SPECIFIC_HEALTHCARE_RESEARCH = "DSRCH"
    SELF_REQUESTED = "PATRQT"


class HealthInformationType(models.TextChoices):
    """
    Enum representing the types of health information that can be accessed.

    - PRESCRIPTION: The Clinical Artifact represents the medication advice to the patient in compliance with the Pharmacy Council of India (PCI) guidelines, which can be shared across the health ecosystem.
    - DIAGNOSTIC_REPORT: The Clinical Artifact represents diagnostic reports including Radiology and Laboratory reports that can be shared across the health ecosystem.
    - OP_CONSULTATION: The Clinical Artifact represents the outpatient visit consultation note which may include clinical information on any OP examinations, procedures along with medication administered, and advice that can be shared across the health ecosystem.
    - DISCHARGE_SUMMARY: Clinical document used to represent the discharge summary record for ABDM HDE data set.
    - IMMUNIZATION_RECORD: The Clinical Artifact represents the Immunization records with any additional documents such as vaccine certificate, the next immunization recommendations, etc. This can be further shared across the health ecosystem.
    - RECORD_ARTIFACT: The Clinical Artifact represents the unstructured historical health records as a single of multiple Health Record Documents generally uploaded by the patients through the Health Locker and can be shared across the health ecosystem.
    - WELLNESS_RECORD: The Clinical Artifact represents regular wellness information of patients typically through the Patient Health Record (PHR) application covering clinical information such as vitals, physical examination, general wellness, women wellness, etc., that can be shared across the health ecosystem.

    For more information on each type, refer to the official NDHM FHIR documentation:
    https://www.nrces.in/ndhm/fhir/r4/index.html
    """

    PRESCRIPTION = "Prescription"
    DIAGNOSTIC_REPORT = "DiagnosticReport"
    OP_CONSULTATION = "OPConsultation"
    DISCHARGE_SUMMARY = "DischargeSummary"
    IMMUNIZATION_RECORD = "ImmunizationRecord"
    RECORD_ARTIFACT = "HealthDocumentRecord"
    WELLNESS_RECORD = "WellnessRecord"
    INVOICE = "Invoice"


class AccessMode(models.TextChoices):
    """
    Enum representing the type of permission to access health information.

    - VIEW: Permission to view the health information.
    - STORE: Permission to store the health information.
    - QUERY: Permission to query or retrieve health information.
    - STREAM: Permission to stream the health information.
    """
    VIEW = "VIEW"
    STORE = "STORE"
    QUERY = "QUERY"
    STREAM = "STREAM"


class FrequencyUnit(models.TextChoices):
    """
    Enum representing the unit of time used to throttle the frequency of data requests.

    - HOUR: Unit used to specify frequency in hours.
    - DAY: Unit used to specify frequency in days.
    - WEEK: Unit used to specify frequency in weeks.
    - MONTH: Unit used to specify frequency in months.
    - YEAR: Unit used to specify frequency in years.

    This unit is used by the Consent Manager (CM) to validate that the number of data requests made against a consent artifact (granted after a consent request) falls within the allowed frequency.

    Example: If a consent artifact is granted with a frequency of {“unit”: “HOUR”, “value”: 24, “repeats”: 2}, the CM will allow only 2 data requests within 24 hours.
    """
    HOUR = "HOUR"
    DAY = "DAY"
    WEEK = "WEEK"
    MONTH = "MONTH"
    YEAR = "YEAR"
