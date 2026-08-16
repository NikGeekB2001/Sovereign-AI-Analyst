"""
Ollama Client for LLM inference.
Provides a unified interface for interacting with local Ollama server.
"""
import os
import requests
import json
from typing import Optional, List, Dict, Any


class OllamaClient:
    """Client for Ollama API."""
    
    def __init__(self, base_url=None, model=None):
        """
        Initialize Ollama client.
        
        Args:
            base_url: Ollama server URL (default: http://localhost:11434)
            model: Model name to use (default: qwen2.5:3b)
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
    ) -> str:
        """
        Generate text using Ollama.
        
        Args:
            prompt: User prompt
            system_prompt: Optional system prompt
            temperature: Sampling temperature (0.0-1.0)
            max_tokens: Maximum tokens to generate
            json_mode: If True, request JSON output
            
        Returns:
            Generated text response
        """
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
            response = requests.post(url, json=payload, timeout=60)
            response.raise_for_status()
            
            result = response.json()
            return result.get("response", "").strip()
            
        except requests.exceptions.ConnectionError:
            raise ConnectionError(
                f"Cannot connect to Ollama at {self.base_url}. "
                "Make sure Ollama is running (ollama serve)."
            )
        except requests.exceptions.Timeout:
            raise TimeoutError(f"Request to Ollama timed out after 60 seconds")
        except Exception as e:
            raise RuntimeError(f"Ollama API error: {str(e)}")
    
    def chat(
        self,
        messages: List[Dict[str, str]],
        temperature: float = 0.7,
        max_tokens: int = 2048,
        json_mode: bool = False
    ) -> str:
        """
        Chat completion using Ollama chat API.
        
        Args:
            messages: List of message dicts with 'role' and 'content'
            temperature: Sampling temperature
            max_tokens: Maximum tokens to generate
            json_mode: If True, request JSON output
            
        Returns:
            Generated response
        """
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
            response = requests.post(url, json=payload, timeout=60)
            response.raise_for_status()
            
            result = response.json()
            return result.get("message", {}).get("content", "").strip()
            
        except requests.exceptions.ConnectionError:
            raise ConnectionError(
                f"Cannot connect to Ollama at {self.base_url}. "
                "Make sure Ollama is running (ollama serve)."
            )
        except Exception as e:
            raise RuntimeError(f"Ollama API error: {str(e)}")
    
    def check_health(self) -> bool:
        """Check if Ollama server is reachable."""
        try:
            response = requests.get(f"{self.base_url}/api/tags", timeout=5)
            return response.status_code == 200
        except:
            return False
    
    def list_models(self) -> List[str]:
        """List available models in Ollama."""
        try:
            response = requests.get(f"{self.base_url}/api/tags", timeout=5)
            response.raise_for_status()
            data = response.json()
            return [model["name"] for model in data.get("models", [])]
        except Exception as e:
            print(f"Warning: Could not list models: {e}")
            return []


# Singleton instance for easy import
_ollama_client = None

def get_ollama_client(model: str = None) -> OllamaClient:
    """Get or create Ollama client singleton."""
    global _ollama_client
    if _ollama_client is None or _ollama_client.model != model:
        _ollama_client = OllamaClient(model=model or os.getenv("LLM_MODEL", "qwen2.5:3b"))
    return _ollama_client
