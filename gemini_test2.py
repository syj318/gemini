import google.generativeai as genai

# API Key 불러오기
with open('C:/Users/User/Desktop/gemini/api.txt', 'r') as f:
    GOOGLE_API_KEY = f.read().strip()

# Gemini 설정
genai.configure(api_key=GOOGLE_API_KEY)

# 모델 생성
model = genai.GenerativeModel('gemini-1.5-flash')

# 채팅 세션 시작, history: 내가 이전에 무슨 질문을 했는지 알수있음.
chat = model.start_chat(history=[])

while True:
    message = input("질문: ")

    # '종료' 입력 시 루프 종료
    if(message == "종료"):
        break
    
    response = chat.send_message(message)

    print("답변: ", response.text)
    print("\n")