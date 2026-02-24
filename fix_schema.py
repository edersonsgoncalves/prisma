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
        print("Alterando coluna conta_moeda para VARCHAR(10)...")
        cursor.execute("ALTER TABLE contas_bancarias MODIFY COLUMN conta_moeda VARCHAR(10)")
        connection.commit()
        print("Sucesso!")
finally:
    connection.close()
