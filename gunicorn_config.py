import multiprocessing
import os

# Create audio directory
os.makedirs('audio', exist_ok=True)

# Binding
bind = "0.0.0.0:8000"  # Change port if needed

# Worker processes
workers = 1  # Start with just one worker to debug issues
worker_class = "sync"
timeout = 120  # Increased for longer audio generation tasks

# Logging
accesslog = "./access.log"
errorlog = "./error.log"
loglevel = "info"  # More verbose logging for troubleshooting

# Restart workers when code changes (dev only)
reload = False  # Set to True for development

# Process naming
proc_name = "notebooklm_server"

# Additional configurations
preload_app = True  # Load application code before the worker processes are forked
capture_output = True  # Redirect stdout/stderr to the specified error log file

# Server hooks
def on_starting(server):
    print("Starting NotebookLM Server")
