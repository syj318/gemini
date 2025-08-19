import google.generativeai as genai

# API Key 불러오기
with open('C:/Users/User/Desktop/gemini/api.txt', 'r') as f:
    GOOGLE_API_KEY = f.read().strip()

# Gemini 설정
genai.configure(api_key=GOOGLE_API_KEY)

# 모델 생성
model = genai.GenerativeModel('gemini-1.5-flash')
# 질문 보내기
response = model.generate_content('안녕?')

# 결과 출력
print(response.text)
