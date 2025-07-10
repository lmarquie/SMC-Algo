import os
import logging
from typing import Dict, Optional

class EnvLoader:
    """
    Simple environment variable loader from .env files
    """
    
    def __init__(self, env_file: str = ".env"):
        self.env_file = env_file
        self.logger = logging.getLogger(__name__)
        self.loaded_vars = {}
    
    def load_env(self) -> Dict[str, str]:
        """
        Load environment variables from .env file
        Returns a dictionary of loaded variables
        """
        if not os.path.exists(self.env_file):
            self.logger.warning(f"📄 No .env file found at {self.env_file}")
            self.logger.info("   Create a .env file based on env.example for easier configuration")
            return {}
        
        self.logger.info(f"📄 Loading environment variables from {self.env_file}...")
        
        loaded_count = 0
        
        try:
            with open(self.env_file, 'r') as f:
                for line_num, line in enumerate(f, 1):
                    line = line.strip()
                    
                    # Skip empty lines and comments
                    if not line or line.startswith('#'):
                        continue
                    
                    # Parse key=value pairs
                    if '=' in line:
                        key, value = line.split('=', 1)
                        key = key.strip()
                        value = value.strip()
                        
                        # Remove quotes if present
                        if (value.startswith('"') and value.endswith('"')) or \
                           (value.startswith("'") and value.endswith("'")):
                            value = value[1:-1]
                        
                        # Set environment variable
                        os.environ[key] = value
                        self.loaded_vars[key] = value
                        loaded_count += 1
                        
                        # Log the variable (masking sensitive ones)
                        if self._is_sensitive_key(key):
                            self.logger.debug(f"  Set {key}=***")
                        else:
                            self.logger.debug(f"  Set {key}={value}")
                    else:
                        self.logger.warning(f"  Invalid line {line_num}: {line}")
            
            self.logger.info(f"✅ Loaded {loaded_count} environment variables from {self.env_file}")
            return self.loaded_vars
            
        except Exception as e:
            self.logger.error(f"❌ Error loading .env file: {e}")
            return {}
    
    def get(self, key: str, default: Optional[str] = None) -> Optional[str]:
        """
        Get an environment variable, checking loaded .env variables first
        """
        # Check if we loaded it from .env
        if key in self.loaded_vars:
            return self.loaded_vars[key]
        
        # Fall back to system environment
        return os.environ.get(key, default)
    
    def get_int(self, key: str, default: int = 0) -> int:
        """
        Get an environment variable as integer
        """
        value = self.get(key)
        if value is None:
            return default
        
        try:
            return int(value)
        except ValueError:
            self.logger.warning(f"⚠️ Could not parse {key}={value} as integer, using default {default}")
            return default
    
    def get_float(self, key: str, default: float = 0.0) -> float:
        """
        Get an environment variable as float
        """
        value = self.get(key)
        if value is None:
            return default
        
        try:
            return float(value)
        except ValueError:
            self.logger.warning(f"⚠️ Could not parse {key}={value} as float, using default {default}")
            return default
    
    def get_bool(self, key: str, default: bool = False) -> bool:
        """
        Get an environment variable as boolean
        """
        value = self.get(key)
        if value is None:
            return default
        
        return value.lower() in ('true', '1', 'yes', 'on')
    
    def require(self, key: str) -> str:
        """
        Get a required environment variable, raise error if not found
        """
        value = self.get(key)
        if value is None:
            raise ValueError(f"Required environment variable {key} not found")
        return value
    
    def _is_sensitive_key(self, key: str) -> bool:
        """
        Check if a key contains sensitive information that should be masked in logs
        """
        sensitive_patterns = ['key', 'token', 'secret', 'password', 'private']
        return any(pattern in key.lower() for pattern in sensitive_patterns)
    
    def print_summary(self):
        """
        Print a summary of loaded environment variables
        """
        print(f"\n📋 Environment Variables Summary:")
        print(f"   File: {self.env_file}")
        print(f"   Loaded: {len(self.loaded_vars)} variables")
        
        if self.loaded_vars:
            print(f"   Variables:")
            for key, value in sorted(self.loaded_vars.items()):
                if self._is_sensitive_key(key):
                    print(f"     {key}=***")
                else:
                    print(f"     {key}={value}")
        print()

# Global instance for easy access
env_loader = EnvLoader()

def load_env(env_file: str = ".env") -> Dict[str, str]:
    """
    Convenience function to load environment variables
    """
    global env_loader
    env_loader = EnvLoader(env_file)
    return env_loader.load_env()

def get_env(key: str, default: Optional[str] = None) -> Optional[str]:
    """
    Get an environment variable
    """
    return env_loader.get(key, default)

def get_env_int(key: str, default: int = 0) -> int:
    """
    Get an environment variable as integer
    """
    return env_loader.get_int(key, default)

def get_env_float(key: str, default: float = 0.0) -> float:
    """
    Get an environment variable as float
    """
    return env_loader.get_float(key, default)

def get_env_bool(key: str, default: bool = False) -> bool:
    """
    Get an environment variable as boolean
    """
    return env_loader.get_bool(key, default)

def require_env(key: str) -> str:
    """
    Get a required environment variable
    """
    return env_loader.require(key) 