"""
Structured JSON Logging Configuration for Luma API

Provides consistent, parseable logging for development and production.
Logs can be viewed with jq for easy filtering and analysis.
"""
import json
import logging
import os
import sys
import time
import uuid
from datetime import datetime
from typing import Any, Dict, Optional


class JSONFormatter(logging.Formatter):
    """
    Custom JSON formatter for structured logging.
    
    Outputs logs as single-line JSON objects that are:
    - Machine-parseable (CloudWatch, Elasticsearch, etc.)
    - Human-readable with jq
    - Consistent across environments
    """
    
    
    def format(self, record: logging.LogRecord) -> str:
        """Format log record as JSON."""
        log_data: Dict[str, Any] = {
            'timestamp': datetime.utcnow().isoformat() + 'Z',
            'level': record.levelname,
            'logger': record.name,
            'message': record.getMessage(),
        }
        
        # Add optional fields if present
        optional_fields = [
            'request_id', 'method', 'path', 'status_code', 
            'duration_ms', 'text_length', 'groups_count', 
            'route', 'processing_time_ms', 'error_type',
            'stage', 'input', 'output', 'notes', 'trace', 'final_response', 'sentence_trace'
        ]
        
        for field in optional_fields:
            if hasattr(record, field):
                value = getattr(record, field)
                # Include all values, including empty dicts/lists (they're valid data)
                log_data[field] = value
        
        # Add exception info if present
        if record.exc_info:
            log_data['exception'] = self.formatException(record.exc_info)
        
        # Add extra fields from extra parameter
        if hasattr(record, 'extra_data'):
            log_data.update(record.extra_data)
        
        # Include all non-standard attributes from extra parameter
        # This catches any fields passed via extra={} that aren't in optional_fields
        standard_attrs = {
            'name', 'msg', 'args', 'created', 'filename', 'funcName',
            'levelname', 'levelno', 'lineno', 'module', 'msecs',
            'message', 'pathname', 'process', 'processName', 'relativeCreated',
            'thread', 'threadName', 'exc_info', 'exc_text', 'stack_info',
            'extra_data', 'getMessage'
        }
        # Check record.__dict__ for attributes set via extra={}
        if hasattr(record, '__dict__'):
            for attr_name, attr_value in record.__dict__.items():
                if attr_name not in standard_attrs and attr_name not in log_data:
                    # Only include serializable types
                    if isinstance(attr_value, (str, int, float, bool, type(None), dict, list)):
                        log_data[attr_name] = attr_value
        
        return json.dumps(log_data, ensure_ascii=False)


class PrettyJSONFormatter(logging.Formatter):
    """
    Pretty-printed JSON formatter for development.
    
    Makes logs easier to read in console while maintaining
    the same structure as production logs.
    """
    
    # ANSI color codes
    COLORS = {
        'DEBUG': '\033[36m',      # Cyan
        'INFO': '\033[32m',       # Green
        'WARNING': '\033[33m',    # Yellow
        'ERROR': '\033[31m',      # Red
        'CRITICAL': '\033[35m',   # Magenta
        'RESET': '\033[0m',       # Reset
    }
    
    def format(self, record: logging.LogRecord) -> str:
        """Format log record as pretty JSON."""
        log_data: Dict[str, Any] = {
            'timestamp': datetime.utcnow().strftime('%H:%M:%S.%f')[:-3],
            'level': record.levelname,
            'logger': record.name,
            'message': record.getMessage(),
        }
        
        # Add optional fields
        optional_fields = [
            'request_id', 'method', 'path', 'status_code', 
            'duration_ms', 'text_length', 'groups_count', 
            'route', 'processing_time_ms'
        ]
        
        for field in optional_fields:
            if hasattr(record, field):
                value = getattr(record, field)
                if value is not None:
                    log_data[field] = value
        
        # Add exception if present
        if record.exc_info:
            log_data['exception'] = self.formatException(record.exc_info)
        
        # Color the level
        color = self.COLORS.get(record.levelname, self.COLORS['RESET'])
        reset = self.COLORS['RESET']
        
        # Format as readable string
        parts = [
            f"{color}[{log_data['level']}]{reset}",
            f"{log_data['timestamp']}",
            f"{log_data['logger']}:",
            log_data['message']
        ]
        
        # Add extra details
        extra_parts = []
        if 'request_id' in log_data:
            extra_parts.append(f"req_id={log_data['request_id']}")
        if 'method' in log_data and 'path' in log_data:
            extra_parts.append(f"{log_data['method']} {log_data['path']}")
        if 'status_code' in log_data:
            extra_parts.append(f"status={log_data['status_code']}")
        if 'duration_ms' in log_data:
            extra_parts.append(f"duration={log_data['duration_ms']}ms")
        
        if extra_parts:
            parts.append(f"({', '.join(extra_parts)})")
        
        result = ' '.join(parts)
        
        # Add exception on new line if present
        if 'exception' in log_data:
            result += '\n' + log_data['exception']
        
        return result


def setup_logging(
    app_name: str = 'luma-api',
    log_level: str = 'INFO',
    log_format: str = 'json',
    log_file: Optional[str] = None
) -> logging.Logger:
    """
    Configure structured logging for the application.
    
    Args:
        app_name: Name of the application logger
        log_level: Logging level (DEBUG, INFO, WARNING, ERROR, CRITICAL)
        log_format: Format type ('json' or 'pretty')
        log_file: Optional file path for file-based logging
    
    Returns:
        Configured logger instance
    
    Example:
        >>> logger = setup_logging('luma-api', 'INFO', 'json')
        >>> logger.info('Server started', extra={'port': 9001})
    """
    # Get log level
    numeric_level = getattr(logging, log_level.upper(), logging.INFO)
    
    # Create logger
    logger = logging.getLogger(app_name)
    logger.setLevel(numeric_level)
    logger.handlers = []  # Clear any existing handlers
    
    # Choose formatter
    if log_format == 'pretty':
        formatter = PrettyJSONFormatter()
    else:
        formatter = JSONFormatter()
    
    # Console handler (stdout)
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setLevel(numeric_level)
    console_handler.setFormatter(formatter)
    logger.addHandler(console_handler)
    
    # File handler (optional)
    if log_file:
        try:
            # Create directory if it doesn't exist
            os.makedirs(os.path.dirname(log_file), exist_ok=True)
            
            file_handler = logging.FileHandler(log_file)
            file_handler.setLevel(numeric_level)
            # Always use JSON for file logs
            file_handler.setFormatter(JSONFormatter())
            logger.addHandler(file_handler)
        except Exception as e:
            logger.warning(f"Failed to setup file logging: {e}")
    
    # Don't propagate to root logger
    logger.propagate = False
    
    return logger


def generate_request_id() -> str:
    """
    Generate a short request ID for tracing.
    
    Returns:
        8-character unique identifier
    """
    return str(uuid.uuid4())[:8]


def log_with_context(
    logger: logging.Logger,
    level: int,
    message: str,
    request_id: Optional[str] = None,
    **kwargs
):
    """
    Log with additional context fields.
    
    Args:
        logger: Logger instance
        level: Logging level (logging.INFO, etc.)
        message: Log message
        request_id: Optional request ID for tracing
        **kwargs: Additional context fields
    
    Example:
        >>> log_with_context(
        ...     logger, logging.INFO, "Processing request",
        ...     request_id="abc123", text_length=42, route="rule"
        ... )
    """
    extra = {}
    if request_id:
        extra['request_id'] = request_id
    extra.update(kwargs)
    
    # Create a custom log record
    record = logger.makeRecord(
        logger.name,
        level,
        '',
        0,
        message,
        (),
        None
    )
    
    # Add extra fields
    for key, value in extra.items():
        setattr(record, key, value)
    
    logger.handle(record)


# ============================================================================
# Function Call Logging Decorator
# ============================================================================

def log_function_call(
    level: str = 'INFO',
    log_args: bool = True,
    log_result: bool = True,
    log_time: bool = True,
    truncate_at: int = 500
):
    """
    Decorator to automatically log function input, output, and timing.
    
    Provides clean, consistent logging for functions without boilerplate.
    Automatically logs summaries at specified level and full details at DEBUG.
    
    Args:
        level: Log level for summaries ('INFO' or 'DEBUG')
        log_args: Whether to log function arguments
        log_result: Whether to log function result
        log_time: Whether to log execution time
        truncate_at: Truncate long strings at this length
    
    Example:
        >>> @log_function_call()
        ... def extract(self, sentence: str, force_llm: bool = False):
        ...     return result
        
        Produces logs:
        INFO: extract() called (sentence_length=25, force_llm=false)
        INFO: extract() completed (duration_ms=45.2, status=success)
    """
    import functools
    import inspect
    import time
    from typing import Any, Callable
    
    def decorator(func: Callable) -> Callable:
        logger = logging.getLogger(func.__module__)
        log_level = getattr(logging, level.upper(), logging.INFO)
        
        @functools.wraps(func)
        def wrapper(*args, **kwargs) -> Any:
            func_name = func.__qualname__
            
            # Prepare input summary
            call_info = _prepare_args_summary(func, args, kwargs, truncate_at)
            
            # Log function call
            if log_level <= logger.getEffectiveLevel():
                logger.log(log_level, f"{func_name}() called", extra=call_info)
            
            # Log full input at DEBUG
            if log_args and logger.isEnabledFor(logging.DEBUG):
                debug_info = _prepare_args_debug(func, args, kwargs, truncate_at)
                logger.debug(f"{func_name}() input", extra=debug_info)
            
            # Execute function with timing
            start_time = time.perf_counter()
            try:
                result = func(*args, **kwargs)
                duration = (time.perf_counter() - start_time) * 1000
                
                # Prepare result summary
                result_info = _prepare_result_summary(result, truncate_at)
                if log_time:
                    result_info['duration_ms'] = round(duration, 2)
                
                # Log completion
                if log_level <= logger.getEffectiveLevel():
                    logger.log(log_level, f"{func_name}() completed", extra=result_info)
                
                # Log full result at DEBUG
                if log_result and logger.isEnabledFor(logging.DEBUG):
                    debug_result = _prepare_result_debug(result, truncate_at)
                    logger.debug(f"{func_name}() output", extra=debug_result)
                
                return result
                
            except Exception as e:
                duration = (time.perf_counter() - start_time) * 1000
                logger.error(
                    f"{func_name}() failed after {round(duration, 2)}ms",
                    extra={
                        'error_type': type(e).__name__,
                        'error_message': str(e),
                        'duration_ms': round(duration, 2)
                    },
                    exc_info=True
                )
                raise
        
        return wrapper
    return decorator


def _prepare_args_summary(func, args, kwargs, truncate_at):
    """Prepare argument summary for INFO logging."""
    import inspect
    
    info = {}
    
    try:
        # Get function signature
        sig = inspect.signature(func)
        bound_args = sig.bind_partial(*args, **kwargs)
        
        # Add summaries of arguments
        for param_name, param_value in bound_args.arguments.items():
            if param_name == 'self':
                continue
            
            # Add type-specific summaries
            if isinstance(param_value, str):
                info[f'{param_name}_length'] = len(param_value)
            elif isinstance(param_value, (list, tuple, dict)):
                info[f'{param_name}_count'] = len(param_value)
            elif isinstance(param_value, (int, float, bool)):
                info[param_name] = param_value
            elif param_value is None:
                info[param_name] = None
    except Exception:
        # If we can't inspect, just skip
        pass
    
    return info


def _prepare_args_debug(func, args, kwargs, truncate_at):
    """Prepare full arguments for DEBUG logging."""
    import inspect
    
    try:
        sig = inspect.signature(func)
        bound_args = sig.bind_partial(*args, **kwargs)
        
        debug_info = {}
        for param_name, param_value in bound_args.arguments.items():
            if param_name == 'self':
                continue
            
            # Truncate long strings
            if isinstance(param_value, str) and len(param_value) > truncate_at:
                debug_info[param_name] = param_value[:truncate_at] + '...'
            elif isinstance(param_value, (list, tuple)) and len(param_value) > 10:
                # Truncate long lists
                debug_info[param_name] = list(param_value[:10]) + ['...']
            else:
                debug_info[param_name] = param_value
        
        return debug_info
    except Exception:
        return {}


def _prepare_result_summary(result, truncate_at):
    """Prepare result summary for INFO logging."""
    info = {}
    
    try:
        # Handle common result types
        if result is None:
            info['result'] = 'None'
        elif hasattr(result, 'status'):
            # Has status attribute (like ExtractionResult)
            status_val = result.status
            if hasattr(status_val, 'value'):
                info['status'] = status_val.value
            else:
                info['status'] = str(status_val)
        
        # Extract additional useful info
        if hasattr(result, 'groups'):
            groups = result.groups
            info['groups_count'] = len(groups) if groups else 0
        
        if hasattr(result, 'route'):
            info['route'] = result.route
        elif hasattr(result, 'grouping_result') and hasattr(result.grouping_result, 'route'):
            info['route'] = result.grouping_result.route
        
        # Handle dict results
        if isinstance(result, dict):
            if 'status' in result:
                info['status'] = result['status']
            if 'groups' in result:
                info['groups_count'] = len(result['groups']) if result['groups'] else 0
            if 'route' in result:
                info['route'] = result['route']
        
        # Handle list/tuple results
        if isinstance(result, (list, tuple)):
            info['result_count'] = len(result)
    except Exception:
        # If we can't extract info, just skip
        pass
    
    return info


def _prepare_result_debug(result, truncate_at):
    """Prepare full result for DEBUG logging."""
    try:
        if result is None:
            return {'result': None}
        
        # Try to convert to dict
        if hasattr(result, 'to_dict'):
            return {'result': result.to_dict()}
        elif hasattr(result, '__dict__'):
            result_dict = {}
            for k, v in result.__dict__.items():
                if not k.startswith('_'):
                    # Truncate long strings in result
                    if isinstance(v, str) and len(v) > truncate_at:
                        result_dict[k] = v[:truncate_at] + '...'
                    else:
                        result_dict[k] = v
            return {'result': result_dict}
        elif isinstance(result, (dict, list, tuple, str, int, float, bool)):
            return {'result': result}
        else:
            return {'result': str(result)[:truncate_at]}
    except Exception:
        return {'result': '<unable to serialize>'}


