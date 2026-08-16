"""
Unified LLM Client for LLM inference.
Provides a unified interface for interacting with both Ollama and vLLM.
Compliant with Langfuse integration specs for model latency tracking.
Includes fallback mechanisms when vLLM is unavailable.
"""
import os
from typing import Optional, List, Dict, Any
import time


class OllamaClient:
    """Client for Ollama API."""
    
    def __init__(self, base_url=None, model=None):
        """
        Initialize Ollama client.
        
        Args:
            base_url: Ollama server URL (default from OLLAMA_BASE_URL env)
            model: Model name to use (default from LLM_MODEL env)
        """
        self.base_url = base_url or os.getenv("OLLAMA_BASE_URL", "http://localhost:11434")
        self.model = model or os.getenv("LLM_MODEL", "qwen2.5:3b")
    
    def generate(
        self, 
        prompt: str, 
        system_prompt: Optional[str] = None,
        temperature: float = 0.7,
        max_tokens: int = 2048,
        json_mode: bool = False
    ) -> Dict[str, Any]:
        """
        Generate text using Ollama with latency tracking.
        
        Args:
            prompt: User prompt
            system_prompt: Optional system prompt
            temperature: Sampling temperature (0.0-1.0)
            max_tokens: Maximum tokens to generate
            json_mode: If True, request JSON output
            
        Returns:
            Dictionary with 'response' and 'latency' keys
        """
        start_time = time.time()  # Track latency
        
        url = f"{self.base_url}/api/generate"
        
        payload = {
            "model": self.model,
            "prompt": prompt,
            "temperature": temperature,
            "num_predict": max_tokens,
            "stream": False
        }
        
        if system_prompt:
            payload["system"] = system_prompt
        
        if json_mode:
            payload["format"] = "json"
        
        try:
            import requests
            response = requests.post(url, json=payload, timeout=int(os.getenv("LLM_TIMEOUT", "60")))
            response.raise_for_status()
            
            result = response.json()
            response_text = result.get("response", "").strip()
            
            end_time = time.time()
            latency_ms = (end_time - start_time) * 1000  # Convert to milliseconds
            
            return {
                "response": response_text,
                "latency": latency_ms,
                "tokens": result.get("stats", {}).get("total_tokens", 0)
            }
            
        except requests.exceptions.ConnectionError:
            raise Exception(
                f"Cannot connect to Ollama at {self.base_url}. "
                "Make sure Ollama is running (ollama serve)."
            )
        except requests.exceptions.Timeout:
            raise Exception(f"Request to Ollama timed out after 60 seconds")
        except Exception as e:
            raise Exception(f"Ollama API error: {str(e)}")
    
    def chat(
        self,
        messages: List[Dict[str, str]],
        temperature: float = 0.7,
        max_tokens: int = 2048,
        json_mode: bool = False
    ) -> Dict[str, Any]:
        """
        Chat completion using Ollama chat API with latency tracking.
        
        Args:
            messages: List of message dicts with 'role' and 'content'
            temperature: Sampling temperature
            max_tokens: Maximum tokens to generate
            json_mode: If True, request JSON output
            
        Returns:
            Dictionary with 'response' and 'latency' keys
        """
        start_time = time.time()  # Track latency
        
        url = f"{self.base_url}/api/chat"
        
        payload = {
            "model": self.model,
            "messages": messages,
            "temperature": temperature,
            "num_predict": max_tokens,
            "stream": False
        }
        
        if json_mode:
            payload["format"] = "json"
        
        try:
            import requests
            response = requests.post(url, json=payload, timeout=int(os.getenv("LLM_TIMEOUT", "60")))
            response.raise_for_status()
            
            result = response.json()
            response_text = result.get("message", {}).get("content", "").strip()
            
            end_time = time.time()
            latency_ms = (end_time - start_time) * 1000  # Convert to milliseconds
            
            return {
                "response": response_text,
                "latency": latency_ms,
                "tokens": result.get("message", {}).get("tokens", 0)
            }
            
        except requests.exceptions.ConnectionError:
            raise Exception(
                f"Cannot connect to Ollama at {self.base_url}. "
                "Make sure Ollama is running (ollama serve)."
            )
        except Exception as e:
            raise Exception(f"Ollama API error: {str(e)}")
    
    def check_health(self) -> bool:
        """Check if Ollama server is reachable."""
        try:
            import requests
            response = requests.get(f"{self.base_url}/api/tags", timeout=5)
            return response.status_code == 200
        except:
            return False
    
    def list_models(self) -> List[str]:
        """List available models in Ollama."""
        try:
            import requests
            response = requests.get(f"{self.base_url}/api/tags", timeout=5)
            response.raise_for_status()
            data = response.json()
            return [model["name"] for model in data.get("models", [])]
        except Exception as e:
            print(f"Warning: Could not list models: {e}")
            return []


class VLLMClient:
    """Client for vLLM API."""
    
    def __init__(self, base_url=None, model=None):
        """
        Initialize vLLM client.
        
        Args:
            base_url: vLLM server URL (default from VLLM_BASE_URL env)
            model: Model name to use (default from LLM_MODEL env)
        """
        self.base_url = base_url or os.getenv("VLLM_BASE_URL", "http://localhost:8000")
        self.model = model or os.getenv("LLM_MODEL", "qwen2.5:3b")
        # Attempt to detect API compatibility
        self.supports_openai_api = self._check_openai_api_compatibility()
    
    def _check_openai_api_compatibility(self) -> bool:
        """
        Check if the vLLM server supports OpenAI-compatible API.
        """
        try:
            import requests
            # Check if the health endpoint exists
            health_resp = requests.get(f"{self.base_url}/health", timeout=5)
            
            if health_resp.status_code == 200:
                # Try to access models endpoint to confirm OpenAI compatibility
                models_resp = requests.get(f"{self.base_url}/v1/models", timeout=5)
                return models_resp.status_code == 200
            return False
        except:
            return False
    
    def generate(
        self, 
        prompt: str, 
        system_prompt: Optional[str] = None,
        temperature: float = 0.7,
        max_tokens: int = 2048,
        json_mode: bool = False
    ) -> Dict[str, Any]:
        """
        Generate text using vLLM with latency tracking.
        Automatically adapts to available API endpoints.
        
        Args:
            prompt: User prompt
            system_prompt: Optional system prompt
            temperature: Sampling temperature (0.0-1.0)
            max_tokens: Maximum tokens to generate
            json_mode: If True, request JSON output
            
        Returns:
            Dictionary with 'response' and 'latency' keys
        """
        if self.supports_openai_api:
            # Use OpenAI-compatible chat API
            messages = []
            if system_prompt:
                messages.append({"role": "system", "content": system_prompt})
            messages.append({"role": "user", "content": prompt})
            
            return self._openai_chat_completion(
                messages=messages,
                temperature=temperature,
                max_tokens=max_tokens,
                json_mode=json_mode
            )
        else:
            # vLLM server doesn't support OpenAI API - return an error
            raise Exception(
                f"vLLM server at {self.base_url} does not support OpenAI API. "
                "Make sure vLLM is started with --served-model-name parameter "
                "and OpenAI API compatibility enabled."
            )
    
    def chat(
        self,
        messages: List[Dict[str, str]],
        temperature: float = 0.7,
        max_tokens: int = 2048,
        json_mode: bool = False
    ) -> Dict[str, Any]:
        """
        Chat completion using vLLM chat API with latency tracking.
        
        Args:
            messages: List of message dicts with 'role' and 'content'
            temperature: Sampling temperature
            max_tokens: Maximum tokens to generate
            json_mode: If True, request JSON output
            
        Returns:
            Dictionary with 'response' and 'latency' keys
        """
        if self.supports_openai_api:
            return self._openai_chat_completion(
                messages=messages,
                temperature=temperature,
                max_tokens=max_tokens,
                json_mode=json_mode
            )
        else:
            # vLLM server doesn't support OpenAI API - return an error
            raise Exception(
                f"vLLM server at {self.base_url} does not support OpenAI API. "
                "Make sure vLLM is started with --served-model-name parameter "
                "and OpenAI API compatibility enabled."
            )
    
    def _openai_chat_completion(
        self,
        messages: List[Dict[str, str]],
        temperature: float = 0.7,
        max_tokens: int = 2048,
        json_mode: bool = False
    ) -> Dict[str, Any]:
        """
        Use OpenAI-compatible chat completion API.
        """
        url = f"{self.base_url}/v1/chat/completions"
        
        payload = {
            "model": self.model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens
        }
        
        if json_mode:
            payload["response_format"] = {"type": "json_object"}
        
        start_time = time.time()  # For latency tracking
        
        try:
            import requests
            response = requests.post(url, json=payload, timeout=120)
            response.raise_for_status()
            
            result = response.json()
            content = result.get("choices", [{}])[0].get("message", {}).get("content", "").strip()
            
            end_time = time.time()
            latency_ms = (end_time - start_time) * 1000  # Convert to milliseconds
            
            # Log for Langfuse - include latency information and model type
            print(f"vLLM chat completed with latency: {latency_ms:.2f}ms for model: {self.model}")
            
            return {
                "response": content,
                "latency": latency_ms,
                "model": self.model
            }
            
        except requests.exceptions.RequestException as e:
            raise Exception(f"vLLM API error: {str(e)}")
    
    def check_health(self) -> bool:
        """Check if vLLM server is reachable."""
        try:
            import requests
            response = requests.get(f"{self.base_url}/health", timeout=5)
            return response.status_code == 200
        except:
            return False
    
    def list_models(self) -> List[str]:
        """List available models in vLLM."""
        if not self.supports_openai_api:
            return []
        
        try:
            import requests
            response = requests.get(f"{self.base_url}/v1/models", timeout=5)
            response.raise_for_status()
            data = response.json()
            return [model["id"] for model in data.get("data", [])]
        except Exception as e:
            print(f"Warning: Could not list models: {e}")
            return []


class UnifiedLLMClient:
    """Unified client for both Ollama and vLLM APIs."""
    
    def __init__(self, backend: str = "ollama", model: str = "qwen2.5:3b"):
        """
        Initialize unified LLM client.
        
        Args:
            backend: Either 'ollama' or 'vllm' (default: 'ollama')
            model: Model name to use (default: 'qwen2.5:3b')
        """
        self.backend = backend.lower()
        self.model = model
        
        # Ensure we don't break anything - if vllm is selected but not available, fall back to ollama
        if self.backend == "vllm":
            try:
                self.client = VLLMClient(model=model)
                # Test if vLLM is actually functional
                if not self.client.check_health():
                    print(f"Warning: vLLM server at {self.client.base_url} is not responding, falling back to Ollama")
                    self.client = OllamaClient(model=model)
                    self.backend = "ollama"
            except Exception as e:
                print(f"Warning: Could not initialize vLLM client ({e}), falling back to Ollama")
                self.client = OllamaClient(model=model)
                self.backend = "ollama"
        else:  # default to ollama
            self.client = OllamaClient(model=model)
    
    def generate(
        self, 
        prompt: str, 
        system_prompt: Optional[str] = None,
        temperature: float = 0.7,
        max_tokens: int = 2048,
        json_mode: bool = False
    ) -> Dict[str, Any]:
        """
        Generate text using the configured backend with latency tracking.
        
        Args:
            prompt: User prompt
            system_prompt: Optional system prompt
            temperature: Sampling temperature (0.0-1.0)
            max_tokens: Maximum tokens to generate
            json_mode: If True, request JSON output
            
        Returns:
            Dictionary with 'response' and 'latency' keys
        """
        return self.client.generate(
            prompt=prompt,
            system_prompt=system_prompt,
            temperature=temperature,
            max_tokens=max_tokens,
            json_mode=json_mode
        )
    
    def chat(
        self,
        messages: List[Dict[str, str]],
        temperature: float = 0.7,
        max_tokens: int = 2048,
        json_mode: bool = False
    ) -> Dict[str, Any]:
        """
        Chat completion using the configured backend with latency tracking.
        
        Args:
            messages: List of message dicts with 'role' and 'content'
            temperature: Sampling temperature
            max_tokens: Maximum tokens to generate
            json_mode: If True, request JSON output
            
        Returns:
            Dictionary with 'response' and 'latency' keys
        """
        return self.client.chat(
            messages=messages,
            temperature=temperature,
            max_tokens=max_tokens,
            json_mode=json_mode
        )
    
    def check_health(self) -> bool:
        """Check if the backend server is reachable."""
        return self.client.check_health()
    
    def list_models(self) -> List[str]:
        """List available models in the backend."""
        return self.client.list_models()


# Singleton instance for easy import
_unified_client = None

def get_unified_client(backend: str = None, model: str = None) -> UnifiedLLMClient:
    """Get or create unified LLM client singleton."""
    global _unified_client
    
    # Get backend from environment variable if not provided
    if backend is None:
        backend = os.getenv("LLM_BACKEND", "ollama")
    if model is None:
        model = os.getenv("LLM_MODEL", "qwen2.5:3b")
    
    if _unified_client is None or _unified_client.backend != backend or _unified_client.model != model:
        _unified_client = UnifiedLLMClient(backend=backend, model=model)
    
    return _unified_client