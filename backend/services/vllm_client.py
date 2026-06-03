"""
vLLM Client for LLM inference.
Provides a unified interface for interacting with vLLM server.
Based on Langfuse integration specifications for model latency tracking.
Supports various vLLM API implementations.
"""
import requests
import json
from typing import Optional, List, Dict, Any
import time


class VLLMClient:
    """Client for vLLM API with Langfuse compatibility."""
    
    def __init__(self, base_url="http://localhost:8000", model="qwen2.5:3b"):
        """
        Initialize vLLM client.
        
        Args:
            base_url: vLLM server URL (default: http://localhost:8000)
            model: Model name to use (default: qwen2.5:3b)
        """
        self.base_url = base_url
        self.model = model
        self.api_version = self._detect_api_version()
    
    def _detect_api_version(self):
        """
        Detect which API endpoints are available on the vLLM server.
        """
        # Check for OpenAI-compatible API
        try:
            response = requests.get(f"{self.base_url}/health", timeout=5)
            if response.status_code == 200:
                # Try to access models endpoint to confirm OpenAI compatibility
                try:
                    models_response = requests.get(f"{self.base_url}/v1/models", timeout=5)
                    if models_response.status_code == 200:
                        print("vLLM OpenAI API detected")
                        return "openai"
                except:
                    pass
            
            print("Basic vLLM API detected")
            return "basic"
        except:
            print("Could not detect vLLM API version, assuming basic")
            return "basic"
    
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
        Automatically selects the appropriate API based on server capabilities.
        
        Args:
            prompt: User prompt
            system_prompt: Optional system prompt
            temperature: Sampling temperature (0.0-1.0)
            max_tokens: Maximum tokens to generate
            json_mode: If True, request JSON output
            
        Returns:
            Dictionary with 'response' and 'latency' keys
        """
        if self.api_version == "openai":
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
            # Use basic vLLM API - this would be for older vLLM versions
            return self._basic_generate(
                prompt=prompt,
                system_prompt=system_prompt,
                temperature=temperature,
                max_tokens=max_tokens
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
        if self.api_version == "openai":
            return self._openai_chat_completion(
                messages=messages,
                temperature=temperature,
                max_tokens=max_tokens,
                json_mode=json_mode
            )
        else:
            # Convert chat messages to a single prompt for basic API
            prompt = self._convert_messages_to_prompt(messages)
            return self._basic_generate(
                prompt=prompt,
                temperature=temperature,
                max_tokens=max_tokens
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
            response = requests.post(url, json=payload, timeout=120)
            
            if response.status_code == 404:
                # If chat completion is not available, try completions
                print("Chat completions not available, trying completions API")
                # Convert messages to a simple prompt
                prompt = self._convert_messages_to_prompt(messages)
                return self._openai_completions(
                    prompt=prompt,
                    temperature=temperature,
                    max_tokens=max_tokens
                )
            else:
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
    
    def _openai_completions(
        self,
        prompt: str,
        temperature: float = 0.7,
        max_tokens: int = 2048
    ) -> Dict[str, Any]:
        """
        Use OpenAI-compatible completions API.
        """
        url = f"{self.base_url}/v1/completions"
        
        payload = {
            "model": self.model,
            "prompt": prompt,
            "temperature": temperature,
            "max_tokens": max_tokens
        }
        
        start_time = time.time()  # For latency tracking
        
        try:
            response = requests.post(url, json=payload, timeout=120)
            
            if response.status_code == 404:
                # If completions API is not available either, raise an error
                raise Exception("Neither chat nor completions API is available")
            else:
                response.raise_for_status()
                
                result = response.json()
                content = result.get("choices", [{}])[0].get("text", "").strip()
                
                end_time = time.time()
                latency_ms = (end_time - start_time) * 1000  # Convert to milliseconds
                
                print(f"vLLM completions completed with latency: {latency_ms:.2f}ms for model: {self.model}")
                
                return {
                    "response": content,
                    "latency": latency_ms,
                    "model": self.model
                }
            
        except requests.exceptions.RequestException as e:
            raise Exception(f"vLLM API error: {str(e)}")
    
    def _basic_generate(
        self,
        prompt: str,
        system_prompt: Optional[str] = None,
        temperature: float = 0.7,
        max_tokens: int = 2048
    ) -> Dict[str, Any]:
        """
        Use basic vLLM generate API (for older versions or different implementations).
        """
        # Combine system prompt with user prompt if provided
        full_prompt = prompt
        if system_prompt:
            full_prompt = f"<|system|>{system_prompt}<|user|>{prompt}<|assistant|>"
        
        url = f"{self.base_url}/generate"  # Basic generate endpoint
        
        payload = {
            "prompt": full_prompt,
            "temperature": temperature,
            "max_tokens": max_tokens,
            "model": self.model,
            "stream": False
        }
        
        start_time = time.time()  # For latency tracking
        
        try:
            response = requests.post(url, json=payload, timeout=120)
            
            if response.status_code == 404:
                # If basic generate is not available, try other endpoints
                raise Exception("Basic generate API not available, server might not be vLLM with OpenAI compatibility")
            else:
                response.raise_for_status()
                
                result = response.json()
                response_text = result.get("outputs", {}).get("text", "") if "outputs" in result else result.get("text", "")
                
                end_time = time.time()
                latency_ms = (end_time - start_time) * 1000  # Convert to milliseconds
                
                print(f"vLLM basic generate completed with latency: {latency_ms:.2f}ms for model: {self.model}")
                
                return {
                    "response": response_text.strip(),
                    "latency": latency_ms,
                    "model": self.model
                }
            
        except requests.exceptions.RequestException as e:
            raise Exception(f"vLLM API error: {str(e)}")
    
    def _convert_messages_to_prompt(self, messages: List[Dict[str, str]]) -> str:
        """
        Convert chat messages to a single prompt string.
        """
        prompt_parts = []
        for msg in messages:
            role = msg.get("role", "")
            content = msg.get("content", "")
            if role == "system":
                prompt_parts.append(f"System: {content}")
            elif role == "user":
                prompt_parts.append(f"User: {content}")
            elif role == "assistant":
                prompt_parts.append(f"Assistant: {content}")
        
        return "\n".join(prompt_parts)
    
    def check_health(self) -> bool:
        """Check if vLLM server is reachable."""
        try:
            response = requests.get(f"{self.base_url}/health", timeout=5)
            return response.status_code == 200
        except:
            return False
    
    def list_models(self) -> List[str]:
        """List available models in vLLM."""
        try:
            response = requests.get(f"{self.base_url}/v1/models", timeout=5)
            if response.status_code == 200:
                data = response.json()
                return [model["id"] for model in data.get("data", [])]
            else:
                # If OpenAI endpoint not available, try basic approach
                print("Models endpoint not available")
                return []
        except Exception as e:
            print(f"Warning: Could not list models: {e}")
            return []


# Singleton instance for easy import
_vllm_client = None

def get_vllm_client(model: str = "qwen2.5:3b") -> VLLMClient:
    """Get or create vLLM client singleton."""
    global _vllm_client
    if _vllm_client is None or _vllm_client.model != model:
        _vllm_client = VLLMClient(model=model)
    return _vllm_client