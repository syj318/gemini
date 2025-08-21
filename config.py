import os
from dotenv import load_dotenv

# Load environment variables from .env file
load_dotenv()

class Config:
    PAGE_TITLE = "Gemini Chatbot"
    MODEL_NAME = "gemini-1.5-flash"  # 모델 이름은 최신 버전을 사용하셔도 좋습니다.
    API_KEY_PATH = os.getenv("GEMINI_API_PATH", "api.txt")
    SUPPORTED_EXTENSIONS = ["pdf", "csv", "txt"]
    MAX_FILE_SIZE_MB = 10
    
    # SQLite를 사용하도록 설정 (가장 간편한 방식)
    DATABASE_URL = "sqlite:///chat_history.db"
    # DATABASE_URL = os.getenv("DATABASE_URL", "mysql+mysqlconnector://user:password@localhost:3306/chat_history")
    
    TIMEZONE = os.getenv("TIMEZONE", "Asia/Seoul")
    FAQ_LIMIT = int(os.getenv("FAQ_LIMIT", "5"))