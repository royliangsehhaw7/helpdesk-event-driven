import sys
import logging

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler("app.log"),   # Logs to this file
        logging.StreamHandler(sys.stdout) # Logs to the console
    ]
)
# We name it 'app' so all logs are grouped under one hierarchy
logger = logging.getLogger("app")