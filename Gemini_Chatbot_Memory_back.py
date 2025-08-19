import streamlit as st
import google.generativeai as genai
import PyPDF2
import pandas as pd
import io
import os
from typing import Dict, Tuple, Optional
import hashlib

# db 연동
import uuid
from datetime import datetime
from zoneinfo import ZoneInfo
from typing import List, Tuple
from sqlalchemy import (
    create_engine, String, Integer, Text, DateTime, func, select, Column, Index
)
from sqlalchemy.orm import declarative_base, sessionmaker
from sqlalchemy.sql import over
from dotenv import load_dotenv

load_dotenv()
DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///chat_history.db")
TIMEZONE = os.getenv("TIMEZONE", "Asia/Seoul")
FAQ_LIMIT = int(os.getenv("FAQ_LIMIT", "5"))

Base = declarative_base()

class Message(Base):
    __tablename__ = "messages"
    id = Column(Integer, primary_key=True, autoincrement=True)
    session_id = Column(String(64), index=True, nullable=False)
    user_text = Column(Text, nullable=False)
    bot_text  = Column(Text, nullable=False)
    created_at = Column(DateTime, nullable=False, index=True, default=func.now())

Index("ix_messages_user_text", Message.user_text, mysql_length=255)

class DBManager:
    engine = create_engine(
        DATABASE_URL,
        pool_size=5,
        max_overflow=10,
        pool_pre_ping=True,
        future=True,
    )
    SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)

    @staticmethod
    def init_db():
        Base.metadata.create_all(DBManager.engine)

    @staticmethod
    def now_local_naive():
        # 타임존 포함 시간을 naive로 변환해 DB 저장 시 표준화
        return datetime.now(ZoneInfo(TIMEZONE)).replace(tzinfo=None)

    @staticmethod
    def save_qa(session_id: str, user_text: str, bot_text: str):
        with DBManager.SessionLocal() as s:
            s.add(Message(
                session_id=session_id,
                user_text=user_text,
                bot_text=bot_text,
                created_at=DBManager.now_local_naive()
            ))
            s.commit()

    @staticmethod
    def get_top_faqs(limit: int = 5) -> List[Tuple[str, str, int]]:
        """
        동일 질문(소문자+TRIM)으로 그룹화하여 가장 자주 나온 상위 N개 반환.
        반환: [(question, sample_answer, count), ...]
        """
        qnorm = func.lower(func.trim(Message.user_text)).label("qnorm")
        rn = over(func.row_number(), partition_by=qnorm, order_by=Message.created_at.desc()).label("rn")

        with DBManager.SessionLocal() as s:
            sub = select(
                qnorm,
                Message.user_text.label("raw_q"),
                Message.bot_text.label("a"),
                Message.created_at,
                rn
            ).subquery()

            # 각 qnorm 그룹에서 최신 레코드(rn=1)
            latest = select(sub.c.qnorm, sub.c.raw_q, sub.c.a).where(sub.c.rn == 1).subquery()

            # 그룹 카운트 + 최신시각
            counts = (
                select(
                    qnorm.label("qnorm"),
                    func.count().label("cnt"),
                    func.max(Message.created_at).label("last_at")
                )
                .group_by(qnorm)
                .subquery()
            )

            q = (
                select(latest.c.raw_q, latest.c.a, counts.c.cnt)
                .join(counts, counts.c.qnorm == latest.c.qnorm)
                .order_by(counts.c.cnt.desc(), counts.c.last_at.desc())
                .limit(limit)
            )
            rows = s.execute(q).all()

        return [(r[0], r[1], r[2]) for r in rows]
    @staticmethod
    def get_all_messages(limit: int = 10):
        with DBManager.SessionLocal() as s:
            rows = s.query(Message).order_by(Message.created_at.desc()).limit(limit).all()
            return rows


# ---------------------- 설정 클래스 ----------------------
class Config:
    PAGE_TITLE = "Gemini Chatbot"
    MODEL_NAME = 'gemini-1.5-flash-8b'
    API_KEY_PATH = 'C:/Users/User/Desktop/gemini/api.txt'
    SUPPORTED_EXTENSIONS = ['pdf', 'csv', 'txt']
    MAX_FILE_SIZE_MB = 10

# ---------------------- 파일 처리 클래스 ----------------------
class FileProcessor:
    @staticmethod
    def get_file_hash(file_content: bytes) -> str:
        """파일 해시 생성으로 중복 업로드 방지"""
        return hashlib.md5(file_content).hexdigest()

    @staticmethod
    def process_pdf(file) -> str:
        """PDF 파일 처리"""
        try:
            pdf_reader = PyPDF2.PdfReader(file)
            content = []
            for page_num, page in enumerate(pdf_reader.pages, 1):
                text = page.extract_text() or ""
                if text.strip():
                    content.append(f"[Page {page_num}]\n{text}")
            return "\n\n".join(content)
        except Exception as e:
            return f"PDF 처리 오류: {str(e)}"

    @staticmethod
    def process_csv(file) -> str:
        """CSV 파일 처리 - 더 나은 포맷팅"""
        try:
            # 여러 인코딩 시도
            encodings = ['utf-8', 'cp949', 'euc-kr', 'latin-1']
            df = None

            for encoding in encodings:
                try:
                    file.seek(0)
                    df = pd.read_csv(file, encoding=encoding)
                    break
                except UnicodeDecodeError:
                    continue

            if df is None:
                return "CSV 파일 인코딩을 읽을 수 없습니다."

            # 데이터 요약 정보 포함
            summary = (
                f"CSV 파일 정보:\n"
                f"- 행 수: {len(df)}\n"
                f"- 열 수: {len(df.columns)}\n"
                f"- 컬럼명: {', '.join(map(str, df.columns))}\n\n"
            )

            # 처음 5행과 마지막 5행만 표시 (큰 파일 대응)
            if len(df) > 10:
                content = df.head().to_string() + "\n...\n" + df.tail().to_string()
            else:
                content = df.to_string()

            return summary + content
        except Exception as e:
            return f"CSV 처리 오류: {str(e)}"

    @staticmethod
    def process_txt(file) -> str:
        """텍스트 파일 처리"""
        encodings = ['utf-8', 'cp949', 'euc-kr', 'latin-1']

        for encoding in encodings:
            try:
                file.seek(0)
                content = file.read().decode(encoding)
                return content
            except UnicodeDecodeError:
                continue

        return "텍스트 파일을 읽을 수 없습니다."

    @classmethod
    def process_file(cls, uploaded_file) -> Tuple[str, str]:
        """파일 처리 통합 메서드"""
        file_name = uploaded_file.name
        file_ext = file_name.lower().split('.')[-1]

        # 파일 크기 체크
        file_size_mb = uploaded_file.size / (1024 * 1024)
        if file_size_mb > Config.MAX_FILE_SIZE_MB:
            return f"파일 크기가 {Config.MAX_FILE_SIZE_MB}MB를 초과합니다.", ""

        # 파일 타입별 처리
        processors = {
            'pdf': cls.process_pdf,
            'csv': cls.process_csv,
            'txt': cls.process_txt
        }

        processor = processors.get(file_ext)
        if processor:
            content = processor(uploaded_file)
            return file_name, content
        else:
            return file_name, f"지원하지 않는 파일 형식입니다: {file_ext}"

# ---------------------- UI 관리 클래스 ----------------------
class UIManager:
    @staticmethod
    def load_css():
        """CSS 스타일 로드"""
        st.markdown("""
        <style>
        .main-container {
            display: flex;
            flex-direction: column;
            height: calc(100vh - 100px);
        }
        .chat-area {
            flex: 1;
            overflow-y: auto;
            padding-bottom: 20px;
        }
        .fixed-bottom {
            position: fixed;
            bottom: 120px;
            left: 20px;
            right: 20px;
            background-color: transparent;
            padding: 10px;
            z-index: 100;
        }
        .chat-container {
            display: flex;
            flex-direction: column;
            margin: 5px 0;
        }
        .user-bubble {
            background-color: #9ED2FF;
            color: black;
            padding: 12px 18px;
            border-radius: 16px;
            margin: 5px;
            max-width: 60%;
            word-wrap: break-word;
            font-size: 16px;
            align-self: flex-end;
        }
        .bot-bubble {
            background-color: #EAEAEA;
            color: black;
            padding: 12px 18px;
            border-radius: 16px;
            margin: 5px;
            max-width: 60%;
            word-wrap: break-word;
            font-size: 16px;
            align-self: flex-start;
        }
        .file-info {
            background-color: #f0f2f6;
            padding: 8px;
            border-radius: 8px;
            margin: 5px 0;
            font-size: 14px;
            position: sticky;
            top: 0;
            z-index: 100;
        }
        /* 입력창 아래 여백 */
        .stApp > .main .block-container {
            padding-bottom: 20px !important;
        }
        </style>
        """, unsafe_allow_html=True)

    @staticmethod
    def display_message(sender: str, message: str):
        """메시지 표시"""
        css_class = "user-bubble" if sender == "user" else "bot-bubble"
        st.markdown(f"<div class='chat-container'><div class='{css_class}'>{message}</div></div>", unsafe_allow_html=True)

    @staticmethod
    def display_file_info(files: Dict[str, str]):
        """업로드된 파일 정보 표시"""
        if files:
            file_list = ", ".join(files.keys())
            st.markdown(f"<div class='file-info'><span style='color: #333333;'>📎 업로드된 파일: {file_list}</span></div>", unsafe_allow_html=True)

    @staticmethod
    def display_faq_bubbles(faqs):
        """FAQ를 가로 한 줄(5개 고정) 버튼으로 표시"""
        if not faqs:
            return

        # 상위 5개만 사용
        faqs = faqs[:5]

        st.markdown("#### 🔥 자주 묻는 질문")

        # 5개의 고정 컬럼을 만들어 한 줄에 배치
        cols = st.columns(5, gap="small")

        for i in range(5):
            with cols[i]:
                if i < len(faqs):
                    q, a, _ = faqs[i]  # cnt는 무시
                    question_display = (q[:25] + "…") if len(q) > 25 else q

                    if st.button(
                        f"🔍 {question_display}",
                        key=f"faq_{i}",
                        use_container_width=True,
                        help=f"질문: {q}\n답변: {a[:100]}..."
                    ):
                        # FAQ 클릭 시 대화에 추가 및 DB 저장
                        st.session_state.messages.append(("user", q))
                        st.session_state.messages.append(("bot", a))
                        DBManager.save_qa(st.session_state["session_id"], q, a)
                        st.rerun()
                else:
                    # FAQ가 5개 미만이면 빈 자리 유지
                    st.write("")


# ---------------------- 챗봇 관리 클래스 ----------------------
class ChatbotManager:
    def __init__(self):
        self.model = self.load_model()
        self.initialize_session()

    @staticmethod
    @st.cache_resource
    def load_model():
        """모델 로드 (캐시됨)"""
        try:
            with open(Config.API_KEY_PATH, 'r', encoding='utf-8') as f:
                api_key = f.read().strip()
            genai.configure(api_key=api_key)
            return genai.GenerativeModel(Config.MODEL_NAME)
        except Exception as e:
            st.error(f"모델 로드 실패: {e}")
            st.stop()

    @staticmethod
    def initialize_session():
        """세션 상태 초기화"""
        # DB 테이블 생성(최초 1회)
        DBManager.init_db()

        # 접속 사용자별 세션 ID (쿠키 대신 세션 상태로 관리)
        if "session_id" not in st.session_state:
            st.session_state["session_id"] = str(uuid.uuid4())

        if "chat_session" not in st.session_state:
            st.session_state["chat_session"] = ChatbotManager.load_model().start_chat(history=[])
        if "messages" not in st.session_state:
            st.session_state["messages"] = []
        if "uploaded_files_text" not in st.session_state:
            st.session_state["uploaded_files_text"] = {}
        if "file_hashes" not in st.session_state:
            st.session_state["file_hashes"] = set()

        # ▶ 사이드바 "DB 최근 대화 확인" 토글 상태
        if "show_db_recent" not in st.session_state:
            st.session_state["show_db_recent"] = False

    def create_context_prompt(self, user_prompt: str) -> str:
        """파일 내용과 사용자 질문을 결합한 프롬프트 생성"""
        file_contents = st.session_state.uploaded_files_text

        if not file_contents:
            return user_prompt

        context_parts = []
        for filename, content in file_contents.items():
            if content.strip():
                context_parts.append(
                    f"=== {filename} ===\n"
                    f"{content[:2000]}{'...(내용 생략)' if len(content) > 2000 else ''}"
                )

        if context_parts:
            context = "\n\n".join(context_parts)
            return f"다음은 업로드된 파일의 내용입니다:\n\n{context}\n\n사용자 질문: {user_prompt}"

        return user_prompt

    def generate_response(self, prompt: str) -> str:
        """AI 응답 생성"""
        try:
            full_prompt = self.create_context_prompt(prompt)
            response = st.session_state.chat_session.send_message(full_prompt, stream=True)

            response_text = ""
            for chunk in response:
                if hasattr(chunk, 'text'):
                    response_text += chunk.text

            return response_text
        except Exception as e:
            return f"응답 생성 중 오류가 발생했습니다: {str(e)}"

# ---------------------- 메인 애플리케이션 ----------------------
def main():
    # 페이지 설정
    st.set_page_config(
        page_title=Config.PAGE_TITLE,
        layout="wide",
        initial_sidebar_state="expanded"
    )

    # 타이틀과 설명
    st.title(Config.PAGE_TITLE)

    # CSS 로드
    UIManager.load_css()

    # 챗봇 매니저 초기화
    chatbot = ChatbotManager()

    # 사이드바 - 파일 업로드 섹션
    with st.sidebar:
        st.header("📎 파일 업로드")

        # 파일 업로더
        uploaded_file = st.file_uploader(
            "파일 선택",
            type=Config.SUPPORTED_EXTENSIONS,
            help=f"지원 형식: {', '.join(Config.SUPPORTED_EXTENSIONS)} (최대 {Config.MAX_FILE_SIZE_MB}MB)"
        )

        # 현재 업로드된 파일 표시
        if st.session_state.uploaded_files_text:
            st.markdown("<h3 style='color: #FFFFFF;'>📁 업로드된 파일</h3>", unsafe_allow_html=True)
            for filename in list(st.session_state.uploaded_files_text.keys()):
                col1, col2 = st.columns([4, 1])
                with col1:
                    st.markdown(f"<span style='color: #FFFFFF;'>📄 {filename}</span>", unsafe_allow_html=True)
                with col2:
                    if st.button("❌", key=f"remove_{filename}", help="파일 삭제"):
                        del st.session_state.uploaded_files_text[filename]
                        st.rerun()

    # 메인 채팅 영역
    chat_container = st.container()
    with chat_container:
        # 업로드된 파일 정보 표시
        UIManager.display_file_info(st.session_state.uploaded_files_text)

        # 기존 대화 표시
        for sender, message in st.session_state.messages:
            UIManager.display_message(sender, message)

    # 파일 처리
    if uploaded_file is not None:
        # 중복 체크
        uploaded_file.seek(0)
        file_content = uploaded_file.read()
        file_hash = FileProcessor.get_file_hash(file_content)

        if file_hash not in st.session_state.file_hashes:
            uploaded_file.seek(0)  # 파일 포인터 리셋
            filename, content = FileProcessor.process_file(uploaded_file)

            if content and not content.startswith("파일 크기가"):
                st.session_state.uploaded_files_text[filename] = content
                st.session_state.file_hashes.add(file_hash)
                st.success(f"✅ 업로드 완료: {filename}")
                st.rerun()
            else:
                st.error(content)

    # ====== FAQ(자주 묻는 질문) 영역: 입력창 바로 위 ======
    faqs = DBManager.get_top_faqs(limit=FAQ_LIMIT)
    UIManager.display_faq_bubbles(faqs)

    # 채팅 입력 (하단에 고정)
    if prompt := st.chat_input("메시지를 입력하세요..."):
        # 사용자 메시지 추가
        st.session_state.messages.append(("user", prompt))

        # AI 응답 생성
        with st.spinner("💭 답변을 생성하고 있습니다..."):
            response = chatbot.generate_response(prompt)

        # 봇 응답 추가
        st.session_state.messages.append(("bot", response))

        # ✅ DB 저장 (여러 사용자 공용 DB에 기록)
        DBManager.save_qa(st.session_state["session_id"], prompt, response)

        # 페이지 새로고침
        st.rerun()

    # === 사이드바 버튼: "DB 최근 대화 확인" <-> "닫기" 토글 ===
    btn_label = "닫기" if st.session_state.get("show_db_recent", False) else "DB 최근 대화 확인"
    if st.sidebar.button(btn_label, key="db_recent_toggle"):
        st.session_state["show_db_recent"] = not st.session_state.get("show_db_recent", False)
        st.rerun()

    # === 기존과 동일한 형태/위치로 목록 렌더 (열림 상태일 때만) ===
    if st.session_state.get("show_db_recent", False):
        messages = DBManager.get_all_messages()
        for m in messages:
            st.write(f"[{m.created_at}] ({m.session_id})")
            st.write(f"🙋 {m.user_text}")
            st.write(f"🤖 {m.bot_text}")
            st.markdown("---")


if __name__ == "__main__":
    main()
