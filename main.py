import os
from dotenv import load_dotenv
from data_layer.staticUpdater import update_main_from_following

if __name__ == "__main__":
    load_dotenv()

    api_key = os.getenv("POLYGON_API_KEY")
    if not api_key:
        raise ValueError("❌ Missing POLYGON_API_KEY in .env file")

    update_main_from_following()
