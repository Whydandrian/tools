# celery_app.py
from celery import Celery
import os
from dotenv import load_dotenv

load_dotenv()

def make_celery():
    # Get Redis password from environment
    redis_password = os.getenv('REDIS_PASSWORD', '')
    redis_host = os.getenv('REDIS_HOST', 'localhost')
    redis_port = os.getenv('REDIS_PORT', '6379')
    redis_db = os.getenv('REDIS_DB', '0')
    
    # Build Redis URL with password
    if redis_password:
        broker_url = f'redis://:{redis_password}@{redis_host}:{redis_port}/{redis_db}'
        backend_url = f'redis://:{redis_password}@{redis_host}:{redis_port}/{redis_db}'
    else:
        broker_url = f'redis://{redis_host}:{redis_port}/{redis_db}'
        backend_url = f'redis://{redis_host}:{redis_port}/{redis_db}'
    
    celery = Celery(
        'dokumi_ocr',
        broker=broker_url,
        backend=backend_url
    )
    
    celery.conf.update(
        task_serializer='json',
        accept_content=['json'],
        result_serializer='json',
        timezone='Asia/Jakarta',
        enable_utc=True,
        broker_connection_retry_on_startup=True,
    )
    
    return celery