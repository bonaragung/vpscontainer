from fastapi import FastAPI, Request, Form, Depends, HTTPException, status
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from starlette.middleware.sessions import SessionMiddleware
import subprocess
import re
import json
from passlib.context import CryptContext

app = FastAPI()
templates = Jinja2Templates(directory="templates")

app.add_middleware(SessionMiddleware, secret_key="super-secret-key-yang-sangat-rahasia-dan-panjang") # Ganti secret key!

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

USERS = {
    "admin": pwd_context.hash("admin123"), # Ganti 'admin123' dengan password Anda
}

security = HTTPBasic()

DEFAULT_RAM = "512m"
DEFAULT_CPU = "0.5"
DEFAULT_STORAGE = "10G"
AVAILABLE_IMAGES = [
    "vps-image",
    "vps-ubuntu",
    "vps-debian",
    "vps-fedora",
    "vps-kali"
]
DEFAULT_IMAGE = AVAILABLE_IMAGES[0]

DESCRIPTION_LABEL = "com.myvpsapp.description"
IMAGE_LABEL = "com.myvpsapp.image"

# --- Fungsi Utility ---
def verify_password(plain_password, hashed_password):
    return pwd_context.verify(plain_password, hashed_password)

def get_available_ports():
    result = subprocess.run(
        ["docker", "ps", "--format", "{{.Ports}}"],
        capture_output=True, text=True
    )
    used_ports = set()
    for line in result.stdout.splitlines():
        matches_ssh = re.findall(r'(\d+)->22/tcp', line)
        matches_web = re.findall(r'(\d+)->80/tcp', line)
        used_ports.update(int(p) for p in matches_ssh)
        used_ports.update(int(p) for p in matches_web)
    return used_ports

def find_next_port(used_ports, start=20000):
    port = start
    while port in used_ports:
        port += 1
    return port

def is_storage_opt_supported():
    try:
        result = subprocess.run(["docker", "info"], capture_output=True, text=True)
        info = result.stdout.lower()
        return 'overlay2' in info and 'xfs' in info and 'pquota' in info
    except:
        return False

def list_vps():
    result = subprocess.run(
        ["docker", "ps", "-a", "--format", "{{.ID}}|{{.Names}}|{{.Status}}|{{.Image}}"],
        capture_output=True, text=True
    )

    vps_list = []
    app_related_images = set(AVAILABLE_IMAGES)

    for line in result.stdout.strip().splitlines():
        parts = line.split("|")
        if len(parts) == 4:
            container_id, name, status, image_used_by_docker = parts
            
            if image_used_by_docker in app_related_images:
                ssh_port = "N/A"
                web_port = "N/A"
                hostname = ""
                ram_usage = "N/A"
                cpu_usage = "N/A"
                description = ""
                selected_image = image_used_by_docker

                try:
                    inspect_result = subprocess.run(
                        ["docker", "inspect", container_id],
                        capture_output=True, text=True, check=True
                    )
                    inspect_data = json.loads(inspect_result.stdout)[0]

                    hostname = inspect_data.get('Config', {}).get('Hostname', name)

                    ram_usage = inspect_data.get('HostConfig', {}).get('Memory', 0)
                    if ram_usage > 0:
                        if ram_usage >= 1024**3:
                            ram_usage = f"{ram_usage / (1024**3):.0f}G"
                        elif ram_usage >= 1024**2:
                            ram_usage = f"{ram_usage / (1024**2):.0f}m"
                        else:
                            ram_usage = f"{ram_usage}B"
                    else:
                        ram_usage = "N/A"

                    cpu_nano = inspect_data.get('HostConfig', {}).get('NanoCpus', 0)
                    if cpu_nano > 0:
                        cpu_usage = f"{cpu_nano / 1_000_000_000:.1f}"
                    else:
                        cpu_usage = "N/A"

                    labels = inspect_data.get('Config', {}).get('Labels', {})
                    description = labels.get(DESCRIPTION_LABEL, "")
                    selected_image = labels.get(IMAGE_LABEL, image_used_by_docker)

                    ports = inspect_data.get('NetworkSettings', {}).get('Ports', {})
                    for container_port, host_details in ports.items():
                        if host_details:
                            for detail in host_details:
                                host_ip = detail.get('HostIp', '0.0.0.0')
                                host_port = detail.get('HostPort', 'N/A')

                                if container_port == "22/tcp":
                                    ssh_port = f"{host_ip}:{host_port}" if host_ip != "0.0.0.0" else host_port
                                elif container_port == "80/tcp":
                                    web_port = f"{host_ip}:{host_port}" if host_ip != "0.0.0.0" else host_port

                except Exception as e:
                    print(f"Error inspecting container {name}: {e}")

                vps_list.append({
                    "id": container_id,
                    "name": name,
                    "status": status,
                    "ssh_port": ssh_port,
                    "web_port": web_port,
                    "hostname": hostname,
                    "ram": ram_usage,
                    "cpu": cpu_usage,
                    "description": description,
                    "image": selected_image
                })
    return vps_list

def get_docker_stats():
    """Mengambil statistik Docker dari semua kontainer yang berjalan."""
    stats_list = []
    try:
        # Jalankan docker stats --no-stream --format json
        # Ini akan memberikan satu snapshot data dalam format JSON
        cmd = ["docker", "stats", "--no-stream", "--format", "{{json .}}"]
        process = subprocess.run(cmd, capture_output=True, text=True, check=True)
        
        # Setiap baris output adalah objek JSON terpisah
        for line in process.stdout.strip().split('\n'):
            if line:
                try:
                    stats_data = json.loads(line)
                    stats_list.append(stats_data)
                except json.JSONDecodeError as e:
                    print(f"Error decoding JSON line: {e} - Line: {line}")
    except subprocess.CalledProcessError as e:
        print(f"Error running docker stats: {e.stderr}")
    return stats_list


# --- Authentication Logic ---
def get_current_user(request: Request):
    if "username" not in request.session:
        raise HTTPException(
            status_code=status.HTTP_303_SEE_OTHER,
            detail="Not authenticated",
            headers={"Location": "/login"}
        )
    return request.session["username"]

# --- Endpoints ---

@app.get("/login", response_class=HTMLResponse)
async def login_get(request: Request):
    if "username" in request.session:
        return RedirectResponse("/", status_code=303)
    return templates.TemplateResponse("login.html", {"request": request, "error_message": None})

@app.post("/login", response_class=HTMLResponse)
async def login_post(request: Request, username: str = Form(...), password: str = Form(...)):
    if username not in USERS or not verify_password(password, USERS[username]):
        return templates.TemplateResponse("login.html", {"request": request, "error_message": "Username atau password salah."})
    
    request.session["username"] = username
    return RedirectResponse("/", status_code=303)

@app.post("/logout")
async def logout(request: Request):
    request.session.clear()
    return RedirectResponse("/login", status_code=303)

@app.get("/", response_class=HTMLResponse)
async def form_get(request: Request, current_user: str = Depends(get_current_user)):
    return templates.TemplateResponse("form.html", {
        "request": request,
        "current_user": current_user,
        "available_images": AVAILABLE_IMAGES,
        "default_image": DEFAULT_IMAGE
    })

@app.post("/", response_class=HTMLResponse)
async def form_post(
    request: Request,
    name: str = Form(...),
    hostname: str = Form(""),
    ram: str = Form(DEFAULT_RAM),
    cpu: str = Form(DEFAULT_CPU),
    storage: str = Form(DEFAULT_STORAGE),
    image: str = Form(DEFAULT_IMAGE),
    description: str = Form(""),
    current_user: str = Depends(get_current_user)
):
    if image not in AVAILABLE_IMAGES:
        return templates.TemplateResponse("form.html", {
            "request": request,
            "error": True,
            "stderr": f"Image '{image}' tidak valid.",
            "current_user": current_user,
            "available_images": AVAILABLE_IMAGES,
            "default_image": DEFAULT_IMAGE
        })

    hostname = hostname or name
    used_ports = get_available_ports()
    ssh_port = find_next_port(used_ports)
    used_ports.add(ssh_port)
    web_port = find_next_port(used_ports)
    
    cmd = [
        "docker", "run", "-d",
        "--name", name,
        "--hostname", hostname,
        "-p", f"{ssh_port}:22",
        "-p", f"{web_port}:80",
        "--memory", ram,
        "--cpus", cpu
    ]
    
    if description:
        cmd.extend(["--label", f"{DESCRIPTION_LABEL}={description}"])
    
    cmd.extend(["--label", f"{IMAGE_LABEL}={image}"])

    if is_storage_opt_supported():
        cmd += ["--storage-opt", f"size={storage}"]
    else:
        storage = "Tidak didukung (dilewati)"

    cmd.append(image)

    try:
        result = subprocess.run(cmd, capture_output=True, text=True, check=True)
        container_id = result.stdout.strip()
        return templates.TemplateResponse("form.html", {
            "request": request,
            "success": True,
            "name": name,
            "hostname": hostname,
            "ssh_port": ssh_port,
            "web_port": web_port,
            "ram": ram,
            "cpu": cpu,
            "storage": storage,
            "container_id": container_id,
            "description": description,
            "image": image,
            "current_user": current_user,
            "available_images": AVAILABLE_IMAGES,
            "default_image": DEFAULT_IMAGE
        })
    except subprocess.CalledProcessError as e:
        return templates.TemplateResponse("form.html", {
            "request": request,
            "error": True,
            "stderr": e.stderr,
            "current_user": current_user,
            "available_images": AVAILABLE_IMAGES,
            "default_image": DEFAULT_IMAGE
        })

@app.get("/manage", response_class=HTMLResponse)
async def manage_vps(request: Request, current_user: str = Depends(get_current_user)):
    vps_list = list_vps()
    return templates.TemplateResponse("manage.html", {"request": request, "vps_list": vps_list, "current_user": current_user})

# Endpoint baru untuk Monitoring
@app.get("/monitor", response_class=HTMLResponse)
async def monitor_vps(request: Request, current_user: str = Depends(get_current_user)):
    stats_data = get_docker_stats()
    # Filter stats data agar hanya menampilkan kontainer yang kita kelola
    # Yaitu, kontainer yang imagenya ada di AVAILABLE_IMAGES
    filtered_stats = []
    for stat in stats_data:
        # Coba ambil image yang digunakan dari 'Image' field di docker stats
        # Jika tidak ada, fallback ke IMAGE_LABEL jika kita bisa melakukan inspect dari sini
        # Untuk kesederhanaan, kita bisa mengecek Image ID atau Name jika itu cocok dengan image yang kita kelola
        # Namun, cara paling akurat adalah dengan membandingkan nama image yang digunakan di docker stats
        # dengan daftar AVAILABLE_IMAGES.
        # Catatan: docker stats --format json tidak selalu memberikan 'Image' dengan nama lengkap
        # Terkadang hanya ID atau SHA. Perlu disesuaikan jika ingin lebih akurat.
        # Untuk demo ini, kita akan asumsikan 'Image' field cukup akurat.
        image_name_from_stat = stat.get('Image', '')
        if any(img_prefix in image_name_from_stat for img_prefix in AVAILABLE_IMAGES):
            # Coba ambil nama kontainer dari `Name` atau `ID`
            container_name = stat.get('Name', stat.get('ID', 'N/A'))
            # Cek apakah nama kontainer ini ada di daftar VPS yang kita kelola (melalui list_vps)
            # Ini mungkin terlalu kompleks, jadi kita akan tampilkan semua kontainer yang di-stats oleh Docker.
            # Jika Anda ingin hanya yang dikelola aplikasi ini, Anda perlu mendapatkan daftar nama kontainer dari list_vps
            # terlebih dahulu dan memfilter berdasarkan nama.
            filtered_stats.append(stat)

    # Sebagai alternatif yang lebih sederhana, kita bisa mendapatkan semua nama VPS yang dikelola,
    # lalu memfilter docker stats berdasarkan nama tersebut.
    managed_vps_names = {vps['name'] for vps in list_vps()}
    
    final_stats = []
    for stat in stats_data:
        if stat.get('Name') in managed_vps_names:
            final_stats.append(stat)

    return templates.TemplateResponse("monitor.html", {
        "request": request,
        "stats_list": final_stats, # Kirim data stats ke template
        "current_user": current_user
    })


@app.post("/toggle")
async def toggle_vps(name: str = Form(...), current_user: str = Depends(get_current_user)):
    info = subprocess.run(["docker", "inspect", "-f", "{{.State.Running}}", name], capture_output=True, text=True)
    if info.stdout.strip() == "true":
        subprocess.run(["docker", "stop", name])
    else:
        subprocess.run(["docker", "start", name])
    return RedirectResponse("/manage", status_code=303)

@app.post("/delete")
async def delete_vps(name: str = Form(...), current_user: str = Depends(get_current_user)):
    subprocess.run(["docker", "rm", "-f", name])
    return RedirectResponse("/manage", status_code=303)

@app.post("/edit")
async def edit_vps(
    name: str = Form(...),
    hostname: str = Form(...),
    ram: str = Form(...),
    cpu: str = Form(...),
    description: str = Form(""),
    current_user: str = Depends(get_current_user)
):
    current_ssh_port = "N/A"
    current_web_port = "N/A"
    current_ram = DEFAULT_RAM
    current_cpu = DEFAULT_CPU
    current_description = ""
    current_image = DEFAULT_IMAGE

    try:
        inspect_result = subprocess.run(
            ["docker", "inspect", name],
            capture_output=True, text=True, check=False
        )
        if inspect_result.returncode == 0:
            inspect_data = json.loads(inspect_result.stdout)[0]
            
            ports = inspect_data.get('NetworkSettings', {}).get('Ports', {})
            for container_port, host_details in ports.items():
                if host_details:
                    for detail in host_details:
                        if container_port == "22/tcp":
                            current_ssh_port = detail.get('HostPort', 'N/A')
                        elif container_port == "80/tcp":
                            current_web_port = detail.get('HostPort', 'N/A')

            mem_bytes = inspect_data.get('HostConfig', {}).get('Memory', 0)
            if mem_bytes > 0:
                if mem_bytes >= 1024**3:
                    current_ram = f"{mem_bytes / (1024**3):.0f}G"
                elif mem_bytes >= 1024**2:
                    current_ram = f"{mem_bytes / (1024**2):.0f}m"
                else:
                    current_ram = f"{mem_bytes}B"
            
            cpu_nano = inspect_data.get('HostConfig', {}).get('NanoCpus', 0)
            if cpu_nano > 0:
                current_cpu = f"{cpu_nano / 1_000_000_000:.1f}"
            
            labels = inspect_data.get('Config', {}).get('Labels', {})
            current_description = labels.get(DESCRIPTION_LABEL, "")
            current_image = labels.get(IMAGE_LABEL, inspect_data.get('Config', {}).get('Image', DEFAULT_IMAGE))

    except Exception as e:
        print(f"Error getting current details for {name}: {e}")

    subprocess.run(["docker", "rm", "-f", name])

    used_ports = get_available_ports()
    
    if current_ssh_port != "N/A" and int(current_ssh_port) not in used_ports:
        ssh_port = int(current_ssh_port)
    else:
        ssh_port = find_next_port(used_ports)
    used_ports.add(ssh_port)

    if current_web_port != "N/A" and int(current_web_port) not in used_ports:
        web_port = int(current_web_port)
    else:
        web_port = find_next_port(used_ports)
    
    while web_port == ssh_port:
        web_port = find_next_port(used_ports, start=web_port + 1)
    
    cmd = [
        "docker", "run", "-d",
        "--name", name,
        "--hostname", hostname,
        "-p", f"{ssh_port}:22",
        "-p", f"{web_port}:80",
        "--memory", ram,
        "--cpus", cpu
    ]
    
    if description:
        cmd.extend(["--label", f"{DESCRIPTION_LABEL}={description}"])
    
    cmd.extend(["--label", f"{IMAGE_LABEL}={current_image}"])


    cmd.append(current_image)

    subprocess.run(cmd)
    return RedirectResponse("/manage", status_code=303)