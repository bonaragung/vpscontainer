# VPS Web

Project ini adalah aplikasi FastAPI sederhana untuk mengelola VPS melalui web.


## Persyaratan Sebelum Memulai

Pastikan perangkat Anda sudah memiliki:

- Python 3
- Image Docker `vps-image` (buat custom image terlebih dahulu)
- Docker
- Install dependency Python:

```bash
pip install fastapi uvicorn jinja2
pip install python-multipart

Cara Menjalankan Aplikasi

uvicorn main:app --reload

Buka browser dan akses http://localhost:8000

## Struktur Direktori

vpscontainer/
├── main.py           ← FastAPI app
|── Dockerfile
|── supervisord.conf
├── templates/
│   ├── form.html     ← Form input VPS
│   └── manage.html   ← Form control VPS
│   └── monitor.html  ← Form monitoring VPS

