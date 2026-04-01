import os
import pymysql
from dotenv import load_dotenv

load_dotenv()

connection = pymysql.connect(
    host=os.getenv('DB_HOST'),
    user=os.getenv('DB_USER'),
    password=os.getenv('DB_PASS'),
    database=os.getenv('DB_NAME'),
    port=int(os.getenv('DB_PORT', 3306))
)

try:
    with connection.cursor() as cursor:
        cursor.execute("DESCRIBE contas_bancarias")
        for row in cursor.fetchall():
            print(row)
finally:
    connection.close()
