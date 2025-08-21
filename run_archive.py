from utils import DB
import sys

def run_archiving_process():
    """
    데이터 아카이빙 프로세스를 실행하고 결과를 출력합니다.
    """
    print("==================================================")
    print("=         데이터베이스 아카이빙 스크립트         =")
    print("==================================================")
    print("이 스크립트는 설정된 기간(기본 6개월)이 지난")
    print("오래된 대화 기록을 CSV 파일로 백업하고")
    print("데이터베이스에서 삭제하여 용량을 최적화합니다.")
    print("-" * 50)
    
    # DB 초기화 (테이블 구조 확인 및 연결 설정)
    DB.init()
    
    # 아카이빙 함수 호출 (6개월 이전 데이터 대상)
    result_message = DB.archive_old_messages(months_ago=6) # 6개월 기준으로
    
    print("-" * 50)
    print(f"작업 결과: {result_message}")
    print("==================================================")

if __name__ == "__main__":
    # 사용자가 스크립트 실행을 확인하도록 간단한 입력 절차 추가
    if len(sys.argv) > 1 and sys.argv[1] == '--force':
         run_archiving_process()
    else:
        print("경고: 이 스크립트는 데이터베이스에서 데이터를 영구적으로 삭제합니다.")
        print("삭제된 데이터는 생성되는 CSV 백업 파일을 통해서만 접근할 수 있습니다.")
        confirm = input("정말로 아카이빙을 진행하시겠습니까? (yes/no): ")
        if confirm.lower() == 'yes':
            run_archiving_process()
        else:
            print("작업이 사용자에 의해 취소되었습니다.")

     