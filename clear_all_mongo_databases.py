from pymongo import MongoClient

# Настройки подключения (по умолчанию)
MONGO_URI = "mongodb://127.0.0.1:27017"

client = MongoClient(MONGO_URI)

db_names = client.list_database_names()

# Системные базы, которые не трогаем
system_dbs = {"admin", "local", "config"}

for db_name in db_names:
    if db_name not in system_dbs:
        print(f"Удаляю базу данных: {db_name}")
        client.drop_database(db_name)

print("Готово. Все пользовательские базы данных удалены.")
