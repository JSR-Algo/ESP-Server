import importlib
import pkgutil
from config.logger import setup_logging

TAG = __name__

logger = setup_logging()

def auto_import_modules(package_name):
    """
    Auto import all modules in specified package.

    Args:
        package_name (str): package name, e.g. 'functions'.
    """
    # Get package path
    package = importlib.import_module(package_name)
    package_path = package.__path__

    # Traverse all modules in package
    for _, module_name, _ in pkgutil.iter_modules(package_path):
        # Import Module
        full_module_name = f"{package_name}.{module_name}"
        importlib.import_module(full_module_name)
        #logger.bind(tag=TAG).info(f"Module '{full_module_name}' Loaded")