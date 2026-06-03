"""
GUI Interface for managing vLLM service with RTX 3070 optimizations.
Provides controls for starting/stopping vLLM, model selection, and configuration.
"""
import tkinter as tk
from tkinter import ttk, scrolledtext, messagebox
import threading
import subprocess
import sys
import os
import requests
import time
import json
from dotenv import load_dotenv


class VLLMGUIManager:
    def __init__(self, root):
        self.root = root
        self.root.title("vLLM Manager - RTX 3070 Optimized")
        self.root.geometry("800x600")
        
        # Load environment variables
        load_dotenv()
        
        self.vllm_process = None
        self.is_running = False
        
        self.setup_ui()
        
    def setup_ui(self):
        # Main frame
        main_frame = ttk.Frame(self.root, padding="10")
        main_frame.grid(row=0, column=0, sticky=(tk.W, tk.E, tk.N, tk.S))
        
        # Configuration frame
        config_frame = ttk.LabelFrame(main_frame, text="Configuration", padding="10")
        config_frame.grid(row=0, column=0, columnspan=2, sticky=(tk.W, tk.E), pady=(0, 10))
        
        # Model selection
        ttk.Label(config_frame, text="Model:").grid(row=0, column=0, sticky=tk.W, padx=(0, 5))
        self.model_var = tk.StringVar(value="Qwen/Qwen2-7B-Instruct")
        model_entry = ttk.Entry(config_frame, textvariable=self.model_var, width=30)
        model_entry.grid(row=0, column=1, sticky=(tk.W, tk.E), padx=(0, 5))
        
        # Port selection
        ttk.Label(config_frame, text="Port:").grid(row=0, column=2, sticky=tk.W, padx=(10, 5))
        self.port_var = tk.StringVar(value="8000")
        port_entry = ttk.Entry(config_frame, textvariable=self.port_var, width=10)
        port_entry.grid(row=0, column=3, sticky=(tk.W, tk.E))
        
        # Tensor parallel size (for RTX 3070)
        ttk.Label(config_frame, text="Tensor Parallel Size:").grid(row=1, column=0, sticky=tk.W, padx=(0, 5), pady=(5, 0))
        self.tensor_parallel_var = tk.StringVar(value="1")
        tp_spinbox = ttk.Spinbox(config_frame, from_=1, to=8, textvariable=self.tensor_parallel_var, width=8)
        tp_spinbox.grid(row=1, column=1, sticky=(tk.W, tk.E), padx=(0, 5), pady=(5, 0))
        
        # GPU memory utilization
        ttk.Label(config_frame, text="GPU Memory Utilization:").grid(row=1, column=2, sticky=tk.W, padx=(10, 5), pady=(5, 0))
        self.gpu_mem_util_var = tk.StringVar(value="0.7")
        gpu_mem_slider = ttk.Scale(config_frame, from_=0.1, to=1.0, variable=self.gpu_mem_util_var, orient=tk.HORIZONTAL)
        gpu_mem_slider.grid(row=1, column=3, sticky=(tk.W, tk.E), padx=(0, 5), pady=(5, 0))
        
        # Dtype selection
        ttk.Label(config_frame, text="Data Type:").grid(row=2, column=0, sticky=tk.W, padx=(0, 5), pady=(5, 0))
        self.dtype_var = tk.StringVar(value="float16")
        dtype_combo = ttk.Combobox(config_frame, textvariable=self.dtype_var, values=["float16", "float32"], state="readonly", width=15)
        dtype_combo.grid(row=2, column=1, sticky=(tk.W, tk.E), padx=(0, 5), pady=(5, 0))
        
        # Max model length
        ttk.Label(config_frame, text="Max Model Length:").grid(row=2, column=2, sticky=tk.W, padx=(10, 5), pady=(5, 0))
        self.max_len_var = tk.StringVar(value="4096")
        max_len_entry = ttk.Entry(config_frame, textvariable=self.max_len_var, width=10)
        max_len_entry.grid(row=2, column=3, sticky=(tk.W, tk.E), pady=(5, 0))
        
        # Control buttons
        button_frame = ttk.Frame(main_frame)
        button_frame.grid(row=1, column=0, columnspan=2, pady=(0, 10))
        
        self.start_button = ttk.Button(button_frame, text="Start vLLM", command=self.start_vllm)
        self.start_button.grid(row=0, column=0, padx=(0, 5))
        
        self.stop_button = ttk.Button(button_frame, text="Stop vLLM", command=self.stop_vllm, state=tk.DISABLED)
        self.stop_button.grid(row=0, column=1, padx=(0, 5))
        
        self.status_button = ttk.Button(button_frame, text="Check Status", command=self.check_status)
        self.status_button.grid(row=0, column=2, padx=(0, 5))
        
        self.test_button = ttk.Button(button_frame, text="Test Connection", command=self.test_connection)
        self.test_button.grid(row=0, column=3)
        
        # Log display
        log_frame = ttk.LabelFrame(main_frame, text="Log Output", padding="5")
        log_frame.grid(row=2, column=0, columnspan=2, sticky=(tk.W, tk.E, tk.N, tk.S), pady=(0, 10))
        
        self.log_text = scrolledtext.ScrolledText(log_frame, height=20, width=90)
        self.log_text.grid(row=0, column=0, sticky=(tk.W, tk.E, tk.N, tk.S))
        
        # Progress bar
        self.progress = ttk.Progressbar(main_frame, mode='indeterminate')
        self.progress.grid(row=3, column=0, columnspan=2, sticky=(tk.W, tk.E), pady=(0, 10))
        
        # Configure grid weights
        self.root.columnconfigure(0, weight=1)
        self.root.rowconfigure(0, weight=1)
        main_frame.columnconfigure(0, weight=1)
        main_frame.rowconfigure(2, weight=1)
        log_frame.columnconfigure(0, weight=1)
        log_frame.rowconfigure(0, weight=1)
        
        # Add log message
        self.log_message("vLLM Manager initialized. Ready to start service.")
        self.log_message(f"Detected GPU: NVIDIA GeForce RTX 3070 with optimizations enabled")
        
    def log_message(self, message):
        """Add message to log display."""
        self.log_text.insert(tk.END, f"[{time.strftime('%H:%M:%S')}] {message}\n")
        self.log_text.see(tk.END)
        self.root.update_idletasks()
        
    def start_vllm(self):
        """Start vLLM service with RTX 3070 optimizations."""
        if self.is_running:
            messagebox.showwarning("Warning", "vLLM is already running!")
            return
            
        try:
            model = self.model_var.get()
            port = self.port_var.get()
            tensor_parallel = self.tensor_parallel_var.get()
            gpu_mem_util = self.gpu_mem_util_var.get()
            dtype = self.dtype_var.get()
            max_len = self.max_len_var.get()
            
            # Validate inputs
            if not model:
                messagebox.showerror("Error", "Please specify a model")
                return
                
            # Build Docker command for RTX 3070 optimizations
            docker_cmd = [
                "docker", "run", "-d",
                "--gpus", "all",
                "--shm-size=1g",
                f"-p{port}:8000",
                "-e", f"CUDA_VISIBLE_DEVICES=0",
                "-e", f"VLLM_TENSOR_PARALLEL_SIZE={tensor_parallel}",
                "-e", f"VLLM_GPU_MEMORY_UTILIZATION={gpu_mem_util}",
                "-e", f"VLLM_DTYPE={dtype}",
                "vllm/vllm-openai:latest",
                "--model", model,
                f"--tensor-parallel-size", tensor_parallel,
                f"--gpu-memory-utilization", gpu_mem_util,
                f"--dtype", dtype,
                f"--max-model-len", max_len,
                f"--max-num-batched-tokens", str(int(max_len) * 2)  # For RTX 3070 optimization
            ]
            
            self.log_message(f"Starting vLLM with model: {model}")
            self.log_message(f"Docker command: {' '.join(docker_cmd[6:])}")  # Don't log the full command for security
            
            # Show progress
            self.progress.start()
            self.start_button.config(state=tk.DISABLED)
            
            # Start vLLM in a separate thread
            self.vllm_thread = threading.Thread(target=self._run_vllm_process, args=(docker_cmd,))
            self.vllm_thread.daemon = True
            self.vllm_thread.start()
            
        except Exception as e:
            self.log_message(f"Error starting vLLM: {str(e)}")
            self.start_button.config(state=tk.NORMAL)
            self.progress.stop()
            
    def _run_vllm_process(self, docker_cmd):
        """Run the vLLM process in a separate thread."""
        try:
            # Execute Docker command
            result = subprocess.run(
                ["docker", "run", "-d", "--gpus", "all", "--shm-size=1g"] +
                [f"-p{self.port_var.get()}:8000", "-e", "CUDA_VISIBLE_DEVICES=0", 
                 "-e", f"VLLM_TENSOR_PARALLEL_SIZE={self.tensor_parallel_var.get()}",
                 "-e", f"VLLM_GPU_MEMORY_UTILIZATION={self.gpu_mem_util_var.get()}",
                 "-e", f"VLLM_DTYPE={self.dtype_var.get()}",
                 "vllm/vllm-openai:latest"] +
                ["--model", self.model_var.get(),
                 "--tensor-parallel-size", self.tensor_parallel_var.get(),
                 "--gpu-memory-utilization", self.gpu_mem_util_var.get(),
                 "--dtype", self.dtype_var.get(),
                 "--max-model-len", self.max_len_var.get(),
                 "--max-num-batched-tokens", str(int(self.max_len_var.get()) * 2)],
                capture_output=True, text=True, check=True
            )
            
            container_id = result.stdout.strip()
            self.container_id = container_id
            
            self.log_message(f"vLLM started successfully with container ID: {container_id[:12]}")
            self.is_running = True
            
            # Enable stop button and disable start button
            self.root.after(0, lambda: self.stop_button.config(state=tk.NORMAL))
            self.root.after(0, lambda: self.start_button.config(state=tk.DISABLED))
            self.root.after(0, lambda: self.progress.stop())
            
            # Wait for service to be ready
            self.wait_for_service()
            
        except subprocess.CalledProcessError as e:
            self.log_message(f"Failed to start vLLM: {e.stderr}")
            self.root.after(0, lambda: self.start_button.config(state=tk.NORMAL))
            self.root.after(0, lambda: self.progress.stop())
        except Exception as e:
            self.log_message(f"Error in vLLM thread: {str(e)}")
            self.root.after(0, lambda: self.start_button.config(state=tk.NORMAL))
            self.root.after(0, lambda: self.progress.stop())
    
    def wait_for_service(self):
        """Wait for vLLM service to be ready."""
        max_attempts = 60  # Wait up to 3 minutes
        attempt = 0
        
        while attempt < max_attempts and self.is_running:
            try:
                response = requests.get(f"http://localhost:{self.port_var.get()}/health", timeout=5)
                if response.status_code == 200:
                    self.log_message("vLLM service is ready and healthy!")
                    break
            except requests.exceptions.RequestException:
                pass
            
            time.sleep(5)
            attempt += 1
        
        if attempt >= max_attempts:
            self.log_message("WARNING: vLLM service may not be responding properly")
    
    def stop_vllm(self):
        """Stop vLLM service."""
        if not self.is_running:
            messagebox.showwarning("Warning", "vLLM is not running!")
            return
            
        try:
            # Stop the Docker container
            subprocess.run(["docker", "stop", self.container_id], check=True, capture_output=True)
            subprocess.run(["docker", "rm", self.container_id], check=True, capture_output=True)
            
            self.log_message(f"vLLM stopped and container removed: {self.container_id[:12]}")
            self.is_running = False
            
            # Update UI
            self.stop_button.config(state=tk.DISABLED)
            self.start_button.config(state=tk.NORMAL)
            
        except subprocess.CalledProcessError as e:
            self.log_message(f"Error stopping vLLM: {e}")
        except Exception as e:
            self.log_message(f"Error in stop process: {str(e)}")
    
    def check_status(self):
        """Check vLLM service status."""
        try:
            response = requests.get(f"http://localhost:{self.port_var.get()}/health", timeout=5)
            if response.status_code == 200:
                self.log_message("✓ vLLM service is running and healthy")
                
                # Get model info
                models_response = requests.get(f"http://localhost:{self.port_var.get()}/v1/models", timeout=5)
                if models_response.status_code == 200:
                    models = models_response.json()
                    if models.get('data'):
                        model_name = models['data'][0].get('id', 'Unknown')
                        self.log_message(f"✓ Active model: {model_name}")
            else:
                self.log_message(f"✗ vLLM service responded with status: {response.status_code}")
        except requests.exceptions.RequestException:
            self.log_message("✗ vLLM service is not responding")
    
    def test_connection(self):
        """Test connection with a simple generation request."""
        try:
            response = requests.post(
                f"http://localhost:{self.port_var.get()}/v1/chat/completions",
                json={
                    "model": self.model_var.get().split('/')[-1] if '/' in self.model_var.get() else self.model_var.get(),
                    "messages": [{"role": "user", "content": "Hello, are you working?"}],
                    "temperature": 0.7,
                    "max_tokens": 50
                },
                timeout=30
            )
            
            if response.status_code == 200:
                result = response.json()
                if 'choices' in result and len(result['choices']) > 0:
                    content = result['choices'][0]['message']['content']
                    self.log_message(f"✓ Test successful: {content[:50]}...")
                else:
                    self.log_message("✗ Test failed: No choices in response")
            else:
                self.log_message(f"✗ Test failed with status: {response.status_code}, {response.text}")
        except requests.exceptions.RequestException as e:
            self.log_message(f"✗ Test connection failed: {str(e)}")
        except Exception as e:
            self.log_message(f"✗ Test error: {str(e)}")


def main():
    root = tk.Tk()
    app = VLLMGUIManager(root)
    
    # Handle window closing
    def on_closing():
        if app.is_running:
            if messagebox.askokcancel("Quit", "vLLM is running. Do you want to quit and stop vLLM?"):
                try:
                    if hasattr(app, 'container_id'):
                        subprocess.run(["docker", "stop", app.container_id], capture_output=True)
                        subprocess.run(["docker", "rm", app.container_id], capture_output=True)
                except:
                    pass
                root.destroy()
        else:
            root.destroy()
    
    root.protocol("WM_DELETE_WINDOW", on_closing)
    root.mainloop()


if __name__ == "__main__":
    main()