import os

CURRENT_DIR = os.path.dirname(os.path.abspath(__file__))
ROOT_DIR = os.path.dirname(os.path.dirname(CURRENT_DIR))
DATA_DIR = os.path.join(ROOT_DIR, "qq_forwarder_data")

os.makedirs(DATA_DIR, exist_ok=True)
