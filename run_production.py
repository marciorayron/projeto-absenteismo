"""Production entrypoint using Waitress WSGI server.

   Usage:
       python run_production.py
       # or: waitress-serve --host=0.0.0.0 --port=5000 --threads=16 app:create_app()
"""
from waitress import serve
from app import create_app

app = create_app()

if __name__ == '__main__':
    serve(app, host='0.0.0.0', port=5000, threads=16)