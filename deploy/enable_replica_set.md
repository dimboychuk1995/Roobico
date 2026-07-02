# Включение транзакций MongoDB (single-node replica set)

Транзакции Mongo работают только на replica set. Приложение уже готово:
`app/utils/mongo_tx.py` определяет поддержку автоматически — после этой
процедуры денежные операции (платёж + статус work order) станут атомарными
без изменений кода. Пока сервер standalone, они выполняются последовательно.

Процедура на сервере (даунтайм ~10 секунд, делать в тихое время):

```bash
# 1. Бэкап на всякий случай
mongodump --uri "mongodb://admin:<pass>@127.0.0.1:27017" --out /root/mongo_backup_$(date +%F)

# 2. В /etc/mongod.conf добавить:
#    replication:
#      replSetName: rs0

# 3. Перезапустить mongod
systemctl restart mongod

# 4. Инициализировать replica set (один раз)
mongosh -u admin -p '<pass>' --eval 'rs.initiate({_id: "rs0", members: [{_id: 0, host: "127.0.0.1:27017"}]})'

# 5. Проверить
mongosh -u admin -p '<pass>' --eval 'rs.status().ok'
```

После этого в `MONGO_URI` в `.env` добавить `replicaSet=rs0`:

```
MONGO_URI=mongodb://admin:...@198.199.122.49:27017/?authSource=admin&replicaSet=rs0&directConnection=true
```

`directConnection=true` обязателен: replica set объявляет себя по host
`127.0.0.1`, и без него драйвер, подключающийся извне по публичному IP,
не сможет найти primary.

Перезапустить приложение и проверить в логах строку:
`Mongo transactions: enabled (replica set)`.
