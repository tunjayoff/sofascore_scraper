import logging
from rich.logging import RichHandler

# Flag to track if logging has been configured
_configured = False

def setup_logger(level: int = logging.INFO):
    """
    Configures the root logger with RichHandler.
    """
    global _configured
    if _configured:
        return
        
    logging.basicConfig(
        level=level,
        format="%(message)s",
        datefmt="[%X]",
        handlers=[RichHandler(rich_tracebacks=True, markup=True)]
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