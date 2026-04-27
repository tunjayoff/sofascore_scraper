import logging
import os
from rich.logging import RichHandler

# Flag to track if logging has been configured
_configured = False

def setup_logger(level: int = None):
    """
    Configures the root logger with RichHandler.
    """
    global _configured
    if _configured:
        return
        
    if level is None:
        log_level_str = os.getenv("LOG_LEVEL", "INFO").upper()
        # Override if DEBUG env variable is truthy
        if os.getenv("DEBUG", "").lower() in ("true", "1", "yes", "t", "y"):
            log_level_str = "DEBUG"
            
        try:
            level = getattr(logging, log_level_str)
            if not isinstance(level, int):
                raise ValueError
        except (AttributeError, ValueError):
            level = logging.INFO
            print(f"Uyarı: Geçersiz LOG_LEVEL '{log_level_str}'. INFO olarak ayarlanıyor.")
            
    # USE_COLOR kontrolü
    use_color = os.getenv("USE_COLOR", "true").lower() == "true"
    if not use_color:
        os.environ["NO_COLOR"] = "1"
        
    logging.basicConfig(
        level=level,
        format="%(message)s",
        datefmt="[%X]",
        handlers=[RichHandler(rich_tracebacks=True, markup=use_color)]
    )
    _configured = True

def get_logger(name: str) -> logging.Logger:
    """
    Returns a named logger instance. 
    Ensures that the logging system is configured.
    """
    if not _configured:
        setup_logger()
    return logging.getLogger(name)

# Initialize on import to ensure all loggers benefit from Rich
setup_logger()