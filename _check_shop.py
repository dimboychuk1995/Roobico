import os
os.environ["MONGO_URI"] = "mongodb://admin:UniWays8878%24@170.9.253.3:27017/?authSource=admin"
from pymongo import MongoClient
from bson import ObjectId
client = MongoClient(os.environ["MONGO_URI"])
master = client["master_db"]
s = master.shops.find_one({"_id": ObjectId("69cb2a9df3ece6dd4f2961f1")})
print("name:", s.get("name"))
print("address:", s.get("address"))
print("phone:", s.get("phone"))
print("email:", s.get("email"))
print("billing_address:", s.get("billing_address"))
print("logo_filename:", s.get("logo_filename"))
print("updated_at:", s.get("updated_at"))
