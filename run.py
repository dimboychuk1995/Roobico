from dotenv import load_dotenv
load_dotenv()

from app import create_app

app = create_app()

if __name__ == "__main__":
    import os

    # 0.0.0.0 — чтобы мобильное приложение (Expo Go) в той же Wi-Fi сети
    # могло достучаться до API. Переопределяется: HOST=127.0.0.1 python run.py
    app.run(debug=True, host=os.environ.get("HOST", "0.0.0.0"), port=int(os.environ.get("PORT", "5000")))