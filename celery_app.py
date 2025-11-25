from celery import Celery
import os
from dotenv import load_dotenv

load_dotenv()

def make_celery(flask_app=None):
    broker_url = os.getenv("CELERY_BROKER_URL")
    result_backend = os.getenv("CELERY_RESULT_BACKEND")

    celery = Celery(
        flask_app.import_name if flask_app else __name__,
        broker=broker_url,
        backend=result_backend
    )

    celery.conf.update(
        task_serializer="json",
        accept_content=["json"],
        result_serializer="json",
        timezone="Asia/Jakarta",
        enable_utc=True,
    )

    # Integrasi context Flask (jika dipakai dari tasks.py)
    if flask_app is not None:
        class ContextTask(celery.Task):
            def __call__(self, *args, **kwargs):
                with flask_app.app_context():
                    return self.run(*args, **kwargs)

        celery.Task = ContextTask

    return celery
