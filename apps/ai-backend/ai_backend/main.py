from dotenv import load_dotenv

from .app import create_app
from .config import Settings

load_dotenv()
app = create_app(Settings.from_env())
