import subprocess
import sys
import os

def main():
    """벡스코 챗봇 실행"""
    print("🏢 부산벡스코 챗봇을 시작합니다...")
    print("=" * 50)
    
    # 필요한 패키지 확인
    required_packages = [
        "streamlit",
        "google-generativeai",
        "pandas",
        "PyPDF2",
        "python-dotenv"
    ]
    
    print("📦 필요한 패키지를 설치합니다...")
    for package in required_packages:
        try:
            __import__(package.replace("-", "_"))
            print(f"✅ {package} - 이미 설치됨")
        except ImportError:
            print(f"📥 {package} 설치 중...")
            subprocess.check_call([sys.executable, "-m", "pip", "install", package])
            print(f"✅ {package} - 설치 완료")
    
    print("\n🚀 벡스코 챗봇을 실행합니다...")
    print("브라우저에서 자동으로 열립니다.")
    print("종료하려면 Ctrl+C를 누르세요.")
    print("=" * 50)
    
    # Streamlit 앱 실행
    try:
        subprocess.run([
            sys.executable, "-m", "streamlit", "run", "bexco_chatbot.py",
            "--server.port", "8501",
            "--server.address", "localhost"
        ])
    except KeyboardInterrupt:
        print("\n👋 벡스코 챗봇을 종료합니다.")
    except Exception as e:
        print(f"❌ 오류가 발생했습니다: {e}")

if __name__ == "__main__":
    main()
