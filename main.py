import os
import io
from typing import Dict, List, Tuple

import streamlit as st
import google.generativeai as genai

from config import Config
from utils import DB, FileProcessor, UI

# ======================= 챗봇 =======================
class Chatbot:
    @staticmethod
    @st.cache_resource
    def load_model():
        try:
            with open(Config.API_KEY_PATH, "r", encoding="utf-8") as f:
                api_key = f.read().strip()
            genai.configure(api_key=api_key)
            return genai.GenerativeModel(Config.MODEL_NAME)
        except Exception as e:
            st.error(f"모델 로드 실패: {e}")
            st.stop()

    def __init__(self):
        self.model = Chatbot.load_model()
        self._ensure_session()

    @staticmethod
    def _ensure_session():
        DB.init()
        ss = st.session_state
        ss.setdefault("session_id", os.urandom(8).hex())
        ss.setdefault("chat_session", Chatbot.load_model().start_chat(history=[]))
        ss.setdefault("messages", [])
        ss.setdefault("uploaded_files_text", {})
        ss.setdefault("file_hashes", set())
        ss.setdefault("show_db_recent", False)

    @staticmethod
    def _prompt_with_files(user_prompt: str) -> str:
        files = st.session_state.uploaded_files_text
        if not files:
            return user_prompt
        parts = []
        for fname, content in files.items():
            snippet = content[:2000]
            more = "...(내용 생략)" if len(content) > 2000 else ""
            parts.append(f"=== {fname} ===\n{snippet}{more}")
        return f"다음은 업로드된 파일의 내용입니다:\n\n" + "\n\n".join(parts) + f"\n\n사용자 질문: {user_prompt}"

    def reply(self, user_prompt: str) -> str:
        try:
            full = self._prompt_with_files(user_prompt)
            stream = st.session_state.chat_session.send_message(full, stream=True)
            text = ""
            for chunk in stream:
                if hasattr(chunk, "text"):
                    text += chunk.text
            return text
        except Exception as e:
            return f"응답 생성 중 오류가 발생했습니다: {e}"

# ======================= 메인 =======================
def main():
    st.set_page_config(page_title=Config.PAGE_TITLE, layout="wide", initial_sidebar_state="expanded")
    st.title(Config.PAGE_TITLE)
    UI.css()

    bot = Chatbot()

    # ---------- 사이드바 파일 업로드 ----------
    with st.sidebar:
        st.header("📎 파일 업로드")
        uploaded = st.file_uploader(
            "파일 선택",
            type=Config.SUPPORTED_EXTENSIONS,
            help=f"지원 형식: {', '.join(Config.SUPPORTED_EXTENSIONS)} (최대 {Config.MAX_FILE_SIZE_MB}MB)"
        )

        # 업로드된 파일 목록/삭제
        if st.session_state.uploaded_files_text:
            st.markdown("<h3 style='color:#FFFFFF;'>📁 업로드된 파일</h3>", unsafe_allow_html=True)
            for fname in list(st.session_state.uploaded_files_text.keys()):
                c1, c2 = st.columns([4, 1])
                with c1:
                    st.markdown(f"<span style='color:#FFFFFF;'>📄 {fname}</span>", unsafe_allow_html=True)
                with c2:
                    if st.button("❌", key=f"remove_{fname}", help="파일 삭제"):
                        del st.session_state.uploaded_files_text[fname]
                        st.rerun()

    # ---------- 채팅 영역 ----------
    UI.file_info(st.session_state.uploaded_files_text)
    for sender, msg in st.session_state.messages:
        UI.bubble(sender, msg)

    # ---------- 파일 처리 ----------
    if uploaded is not None:
        uploaded.seek(0)
        raw = uploaded.read()
        file_hash = FileProcessor.md5(raw)

        if file_hash not in st.session_state.file_hashes:
            uploaded.seek(0)
            fname, content = FileProcessor.process_file(uploaded)
            if content and not content.startswith("파일 크기가"):
                st.session_state.uploaded_files_text[fname] = content
                st.session_state.file_hashes.add(file_hash)
                st.success(f"✅ 업로드 완료: {fname}")
                st.rerun()
            else:
                st.error(content)

    # ---------- FAQ (입력창 위) ----------
    UI.faq_bubbles(DB.get_top_faqs(limit=Config.FAQ_LIMIT))

    # ---------- 입력 ----------
    if prompt := st.chat_input("메시지를 입력하세요..."):
        st.session_state.messages.append(("user", prompt))
        with st.spinner("💭 답변을 생성하고 있습니다..."):
            reply = bot.reply(prompt)
        st.session_state.messages.append(("bot", reply))
        DB.save_qa(st.session_state.session_id, prompt, reply)
        st.rerun()

    # ---------- 사이드바 토글 ----------
    btn_label = "닫기" if st.session_state.show_db_recent else "DB 최근 대화 확인"
    if st.sidebar.button(btn_label, key="db_recent_toggle"):
        st.session_state.show_db_recent = not st.session_state.show_db_recent
        st.rerun()

    if st.session_state.show_db_recent:
        for m in DB.get_recent_messages():
            st.write(f"[{m.created_at}] ({m.session_id})")
            st.write(f"🙋 {m.user_text}")
            st.write(f"🤖 {m.bot_text}")
            st.markdown("---")

if __name__ == "__main__":
    main()