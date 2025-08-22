import os
from dotenv import load_dotenv

# Load environment variables from .env file
load_dotenv()

class Config:
    PAGE_TITLE = "Gemini Chatbot"
    MODEL_NAME = "gemini-1.5-flash"  
    API_KEY_PATH = os.getenv("GEMINI_API_PATH", "api.txt")
    SUPPORTED_EXTENSIONS = ["pdf", "csv", "txt"]
    MAX_FILE_SIZE_MB = 10
    
    DATABASE_URL = "mysql+mysqlconnector://user:wawa5930@localhost:3306/chat_history"

    
    TIMEZONE = os.getenv("TIMEZONE", "Asia/Seoul")
    FAQ_LIMIT = int(os.getenv("FAQ_LIMIT", "5"))