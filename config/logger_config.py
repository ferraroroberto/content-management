import logging
import sys
import os

def setup_logger(name, level=logging.INFO, file_logging=True):
    """
    Set up and configure a logger with the given name and level.
    
    Args:
        name (str): The name of the logger
        level (int): The logging level (default: logging.INFO)
        file_logging (bool): Whether to log to file (default: True)
    
    Returns:
        logging.Logger: The configured logger instance
    """
    # Create logger
    logger = logging.getLogger(name)
    logger.setLevel(level)
    
    # Clear any existing handlers (to avoid duplicates)
    if logger.hasHandlers():
        logger.handlers.clear()
    
    # Create console handler
    c_handler = logging.StreamHandler(sys.stdout)
    c_handler.setLevel(level)
    
    # Create formatters and add to handlers
    log_format = '%(asctime)s - %(name)s - %(levelname)s - %(message)s'
    c_formatter = logging.Formatter(log_format)
    c_handler.setFormatter(c_formatter)
    
    # Add console handler to the logger
    logger.addHandler(c_handler)
    
    # Add file handler if file_logging is True
    if file_logging:
        # Create logs directory if it doesn't exist
        logs_dir = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'logs')
        os.makedirs(logs_dir, exist_ok=True)
        
        # Create file handler with current date in filename
        log_file = os.path.join(logs_dir, f"{name}.log")
        f_handler = logging.FileHandler(log_file, encoding='utf-8')
        f_handler.setLevel(level)
        f_formatter = logging.Formatter(log_format)
        f_handler.setFormatter(f_formatter)
        logger.addHandler(f_handler)
    
    return logger
