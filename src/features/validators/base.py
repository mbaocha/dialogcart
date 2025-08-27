# Base validator class defining the interface for tool argument validation

from abc import ABC, abstractmethod
from typing import Any, Dict


class BaseValidator(ABC):
    # Base class for all tool argument validators
    
    @abstractmethod
    def validate(self, args: Dict[str, Any], context: Dict[str, Any]) -> Dict[str, Any]:
        # Validate tool arguments and return validation metadata
        pass 