import os
from dotenv import load_dotenv
load_dotenv()
from pymongo import MongoClient
from bson import ObjectId

c = MongoClient(os.environ["MONGO_URI"])
db = c["shop_lts-repair_lts-repair_d2063f"]

event_id = ObjectId("69d821dde97fdc94d955b311")
preset_data = [{"id": "69d821d3e97fdc94d955b30f", "name": "Oil change"}]

r = db.calendar_events.update_one(
    {"_id": event_id},
    {"$set": {"presets": preset_data}}
)
print("modified:", r.modified_count)

doc = db.calendar_events.find_one({"_id": event_id}, {"presets": 1})
print("presets now:", doc.get("presets"))
