import os
from config.config_loader import read_config, get_project_dir, load_config


default_config_file = "config.yaml"
config_file_valid = False


def check_config_file():
    global config_file_valid
    if config_file_valid:
        return
    """
    Simplified config check, only prompts user about config file usage
    """
    custom_config_file = get_project_dir() + "data/." + default_config_file
    if not os.path.exists(custom_config_file):
        raise FileNotFoundError(
            "data/.config.yaml file not found, follow tutorial to confirm whether config file exists"
        )

    # Check WhetherRead config from API
    config = load_config()
    if config.get("read_config_from_api", False):
        print("Read config from API")
        old_config_origin = read_config(custom_config_file)
        if old_config_origin.get("selected_module") is not None:
            error_msg = "Your config file seems to contain both console config and local config:\n"
            error_msg += "\nSuggestion:\n"
            error_msg += "1. Copy config_from_api.yaml in root directory to data, rename to .config.yaml\n"
            error_msg += "2. Configure interface address and key per tutorial\n"
            raise ValueError(error_msg)
    config_file_valid = True
