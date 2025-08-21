import mysql.connector
from datetime import datetime

DB_CONFIG = {
    "host": "localhost",
    "user": "user",
    "password": "wawa5930",
    "database": "chat_history"
}

def add_monthly_partition():
    today = datetime.now()
    this_month = datetime(today.year, today.month, 1)

    if today.month == 12:
        next_month = datetime(today.year + 1, 1, 1)
    else:
        next_month = datetime(today.year, today.month + 1, 1)

    # 새 파티션 이름 (예: p202508)
    part_name = f"p{this_month.strftime('%Y%m')}"
    next_month_str = next_month.strftime("%Y-%m-%d")

    alter_sql = f"""
    ALTER TABLE messages
    ADD PARTITION (
        PARTITION {part_name} VALUES LESS THAN (TO_DAYS('{next_month_str}'))
    );
    """

    try:
        conn = mysql.connector.connect(**DB_CONFIG)
        cursor = conn.cursor()
        cursor.execute(alter_sql)
        conn.commit()
        print(f"✅ 새 파티션 추가 완료: {part_name} (기준일 {next_month_str})")
    except mysql.connector.Error as e:
        if "Duplicate partition name" in str(e):
            print(f"⚠️ 이미 파티션 {part_name} 이 존재합니다.")
        else:
            print(f"❌ 오류 발생: {e}")
    finally:
        if cursor: cursor.close()
        if conn: conn.close()

if __name__ == "__main__":
    add_monthly_partition()
