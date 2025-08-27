from typing import Any, Optional, Dict
import decimal

def json_safe(obj):
    if isinstance(obj, dict):
        return {k: json_safe(v) for k, v in obj.items()}
    elif isinstance(obj, list):
        return [json_safe(v) for v in obj]
    elif isinstance(obj, decimal.Decimal):
        return float(obj)
    else:
        return obj

def standard_response(
    success: bool,
    data: Any = None,
    error: Optional[str] = None
) -> Dict[str, Any]:
    return {
        "success": success,
        "data": json_safe(data),
        "error": error
    }


