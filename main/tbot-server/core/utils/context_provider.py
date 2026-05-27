import httpx
from typing import Dict, Any, List
from config.logger import setup_logging

TAG = __name__

class ContextDataProvider:
    """Data context filling, responsible for getting data from configured API"""
    
    def __init__(self, config: Dict[str, Any], logger=None):
        self.config = config
        self.logger = logger or setup_logging()
        self.context_data = ""

    def fetch_all(self, device_id: str) -> str:
        """Get all configured context data"""
        context_providers = self.config.get("context_providers", [])
        if not context_providers:
            return ""

        formatted_lines = []
        for provider in context_providers:
            url = provider.get("url")
            headers = provider.get("headers", {})

            if not url:
                continue

            try:
                headers = headers.copy() if isinstance(headers, dict) else {}
                # will device_id Add toRequest header
                headers["device-id"] = device_id
                
                # Send Request
                response = httpx.get(url, headers=headers, timeout=3)
                
                if response.status_code == 200:
                    result = response.json()
                    if isinstance(result, dict):
                        if result.get("code") == 0:
                            data = result.get("data")
                            # Format data
                            if isinstance(data, dict):
                                for k, v in data.items():
                                    formatted_lines.append(f"- **{k}:** {v}")
                            elif isinstance(data, list):
                                for item in data:
                                    formatted_lines.append(f"- {item}")
                            else:
                                formatted_lines.append(f"- {data}")
                        else:
                            self.logger.bind(tag=TAG).warning(f"API {url} ReturnErrorcode: {result.get('msg')}")
                    else:
                        self.logger.bind(tag=TAG).warning(f"API {url} Returned is notJSONDictionary")
                else:
                    self.logger.bind(tag=TAG).warning(f"API {url} Request failed: {response.status_code}")
            except Exception as e:
                self.logger.bind(tag=TAG).error(f"Get context data {url} Fail: {e}")
        
        # Join all formatted lines into one string
        self.context_data = "\n".join(formatted_lines)
        if self.context_data:
            self.logger.bind(tag=TAG).debug(f"Dynamic context data injected:\n{self.context_data}")
        return self.context_data
