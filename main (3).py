from fastapi import FastAPI, HTTPException, status
from pymongo import MongoClient
from pydantic import BaseModel, Field
from bson import ObjectId
from typing import Optional, Dict, Any
from telegram import Bot
from telegram.error import TelegramError
from datetime import datetime
from pymongo.errors import PyMongoError
from dotenv import load_dotenv
import os
from fastapi.middleware.cors import CORSMiddleware

app = FastAPI()

# Database connection
client = MongoClient("")
db = client["airdrop_db"]
users_collection = db["users"]
tasks_collection = db["tasks"]
user_activity_collection = db["user_activity"]

TELEGRAM_BOT_TOKEN = ""
telegram_bot = Bot(TELEGRAM_BOT_TOKEN)

origins = ["*"]

app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

class User(BaseModel):
    name: Optional[str] = None
    email: Optional[str] = None
    telegram_username: Optional[str] = None
    telegram_uid: Optional[str] = None
    twitter_username: Optional[str] = None
    twitter_uid: Optional[str] = None
    wallet_address: Optional[str] = None
    points: Optional[int] = None
    referral_code: Optional[str] = None
    ref_by: Optional[str] = None
    is_banned: Optional[bool] = None
    is_admin: Optional[bool] = None


class Task(BaseModel):
    task_id: Optional[str] = None
    task_type: Optional[str] = None
    description: Optional[str] = None
    reward: Optional[int] = None
    is_active: Optional[bool] = None


class UserActivity(BaseModel):
    telegram_uid: str
    task_id: str
    details: Optional[Dict[str, Any]] = None
    status: Optional[str] = None
    completed_at: Optional[datetime] = None
    
class Verifier:
    telegram_bot_token = os.getenv("TELEGRAM_BOT_TOKEN")
    telegram_bot = Bot(str(telegram_bot_token))
    telegram_chat_id = "-087"

    # Verification for task1
    async def verify_telegram_group_following(self, telegram_uid):
        try:
            member = await self.telegram_bot.get_chat_member(chat_id=self.telegram_chat_id, user_id=telegram_uid)
            if await member.status in ["member", "administrator", "creator"]:
                return True
            else:
                return False
        except TelegramError as e:
            raise HTTPException(status_code=500, detail=f"An error occurred: {str(e)}")
        
    # Verification for task2
    def verify_telegram_group_message(self, telegram_uid):
        pass

# ----- User API Endpoints -----
# if user exists, update and return it, otherwise create and return
@app.post("/profile/{telegram_uid}", status_code=status.HTTP_201_CREATED)
def create_user(user: User, telegram_uid: str):
    exist_user = users_collection.find_one({"telegram_uid": telegram_uid})
    if exist_user:
        exist_user["_id"] = str(exist_user["_id"])
        return exist_user

    new_user = user.model_dump()
    new_user["_id"] = ObjectId()
    new_user["telegram_uid"] = telegram_uid
    new_user["telegram_username"] = user.telegram_username
    new_user["ref_by"] = user.ref_by
    new_user["name"] = user.name
    users_collection.insert_one(new_user)
    new_user["_id"] = str(new_user["_id"])
    if user.ref_by and user_activity_collection.find_one({"telegram_id": telegram_uid, "task_id": "ref"}) is None and new_user["_id"] != user.ref_by:
        ref_by_user = users_collection.find_one({"_id": user.ref_by})
        if ref_by_user:
            ref_by_user["points"] = (ref_by_user.get("points") or 0) + 10000
            users_collection.update_one(
                {"_id": user.ref_by},
                {"$set": {"points": ref_by_user["points"]}},
            )
            user_activity = UserActivity(
                telegram_uid=telegram_uid,
                task_id="ref",
                details={"ref_by": user.ref_by},
                completed_at=datetime.now(),
            )
            user_activity_dict = user_activity.model_dump(exclude_unset=True)
            user_activity_dict["_id"] = ObjectId()
            user_activity_collection.insert_one(user_activity_dict)
    return new_user


# get existing user details
@app.get("/profile/{telegram_uid}")
def get_user(telegram_uid: str):
    user = users_collection.find_one({"telegram_uid": telegram_uid})
    if user:
        user["_id"] = str(user["_id"])
        return user
    else:
        raise HTTPException(status_code=404, detail="User not found")


# When any task is done, this endpoint will be called to add points to user
@app.post("/useractivity/")
def add_user_activity(user_activity: UserActivity):
    if user_activity_collection.find_one(
        {
            "telegram_uid": user_activity.telegram_uid,
            "task_id": user_activity.task_id,
        }
    ):
        return 200

    # create user activity entry in db
    user_activity.completed_at = datetime.now()
    user_activity_dict = user_activity.model_dump(exclude_unset=True)
    user_activity_dict["_id"] = ObjectId()
    user_activity_dict["_id"] = str(user_activity_dict["_id"])

    user_activity_collection.insert_one(user_activity_dict)
    return user_activity_dict
    
@app.post("/verify/")
def verify_user_activity(user_activity: UserActivity):
    if user_activity.task_id == "task1":
        # Verify if user has joined the telegram group
        verifier = Verifier()
        result = verifier.verify_telegram_group_following(user_activity.telegram_uid)
        if result:
            act = user_activity_collection.find_one(
                {
                    "telegram_uid": user_activity.telegram_uid,
                    "task_id": user_activity.task_id,
                }
            )
            if act:
                if (act["status"] == "completed"): 
                    return True
                else: 
                    user_activity_collection.update_one({ "_id": act["_id"] }, { "$set": { "status": "completed", "completed_at": datetime.now() } })
            else:
                user_activity.completed_at = datetime.now()
                user_activity.status = "completed"
                user_activity_dict = user_activity.model_dump(exclude_unset=True)
                user_activity_dict["_id"] = ObjectId()
                user_activity_dict["_id"] = str(user_activity_dict["_id"])
                user_activity_collection.insert_one(user_activity_dict)
            
            user = users_collection.find_one(
                {"telegram_uid": user_activity.telegram_uid}
            )
            task = tasks_collection.find_one({"task_id": user_activity.task_id})
            if user and task:
                user["points"] = (user.get("points") or 0) + task["reward"]
                users_collection.update_one(
                    {"telegram_uid": user_activity.telegram_uid},
                    {"$set": {"points": user["points"]}},
                )
            
            return True
        else:
            return False
        

# ----- Task API Endpoints -----
@app.post(
    "/admin/tasks/create",
    status_code=status.HTTP_201_CREATED,
    response_model=Task,
)
def add_task(task: Task):
    try:
        task_dict = task.model_dump()
        if tasks_collection.find_one({"task_id": task.task_id}):
            raise HTTPException(
                status_code=400, detail="Task with this ID already exists"
            )
        tasks_collection.insert_one(task_dict)
        return task
    except PyMongoError as e:
        raise HTTPException(
            status_code=500, detail=f"An error occurred: {str(e)}"
        )
