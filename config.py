import os

# Base directory of the application
BASE_DIR = os.path.abspath(os.path.dirname(__file__))

class Config:
    SECRET_KEY = os.environ.get('SECRET_KEY', 'factory_absenteeism_secret_key_2026')
    DB_PATH = os.path.join(BASE_DIR, 'absenteeism.db')
    SQLALCHEMY_DATABASE_URI = os.environ.get('DATABASE_URL', f'sqlite:///{DB_PATH}')
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    
    TOLERANCE_MINUTES = 5
    EARLY_EXIT_THRESHOLD_MINUTES = 60
    
    SHIFTS = {
        1: {
            "start": "05:00",
            "end": "14:48",
            "break_minutes": 60,
            "net_work_minutes": 488
        },
        2: {
            "start": "14:48",
            "end": "00:16",
            "break_minutes": 60,
            "net_work_minutes": 478
        },
        3: {
            "start": "00:16",
            "end": "05:00",
            "break_minutes": 30,
            "net_work_minutes": 254
        },
        4: {
            "start": "08:00",
            "end": "17:00",
            "break_minutes": 60,
            "net_work_minutes": 480
        }
    }
