from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any, Dict
from datetime import datetime

@dataclass
class ESIResponse:
    """Simulates an HTTP response from ESI."""
    status_code: int
    data: Any
    headers: Dict[str, str]
    timestamp: datetime

@dataclass
class ESIRequest:
    """Simulates an HTTP request to ESI."""
    url: str
    method: str = "GET"
    headers: Dict[str, str] = None
    params: Dict[str, Any] = None

class IESIClient(ABC):
    @abstractmethod
    async def request(self, request: ESIRequest) -> ESIResponse:
        pass
