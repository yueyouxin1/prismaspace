# app/services/exceptions.py

class ServiceException(Exception):
    """Base exception for all service layer errors."""
    def __init__(self, message: str):
        self.message = message
        super().__init__(self.message)

class UserNotFound(ServiceException):
    """Raised when a user is not found in the database."""
    pass

class EmailAlreadyExistsError(ServiceException):
    """Raised when trying to register with an email that already exists."""
    pass

class PhoneNumberExistsError(ServiceException):
    """Raised when trying to register with a phone number that already exists."""
    pass

class InvalidCredentialsError(ServiceException):
    """Raised during authentication if credentials are invalid."""
    pass

class ConfigurationError(ServiceException):
    """Raised if a required system configuration (e.g., a product) is missing."""
    pass

class NotFoundError(ServiceException):
    """Raised if a required system configuration (e.g., a product) is missing."""
    pass

class PermissionDeniedError(ServiceException):
    """Raised if a required system configuration (e.g., a product) is missing."""
    pass