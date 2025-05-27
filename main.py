from fastapi import FastAPI, Request, Form
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
import subprocess
import re
import json

app = FastAPI()
templates = Jinja2Templates(directory="templates")

DEFAULT_RAM = "512m"
DEFAULT_CPU = "0.5"
DEFAULT_STORAGE = "10G"
IMAGE_NAME = "vps-image"
DESCRIPTION_LABEL = "com.myvpsapp.description" # Label Docker untuk menyimpan keterangan

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

# Fungsi list_vps dengan penambahan RAM, CPU, dan Keterangan
def list_vps():
    result = subprocess.run(
        ["docker", "ps", "-a", "--format", "{{.ID}}|{{.Names}}|{{.Status}}|{{.Image}}"],
        capture_output=True, text=True
    )

    vps_list = []
    for line in result.stdout.strip().splitlines():
        parts = line.split("|")
        if len(parts) == 4:
            container_id, name, status, image = parts
            if image == IMAGE_NAME:
                ssh_port = "N/A"
                web_port = "N/A"
                hostname = ""
                ram_usage = "N/A"
                cpu_usage = "N/A"
                description = "" # Default keterangan

                try:
                    inspect_result = subprocess.run(
                        ["docker", "inspect", container_id],
                        capture_output=True, text=True, check=True
                    )
                    inspect_data = json.loads(inspect_result.stdout)[0]

                    # Get Hostname
                    hostname = inspect_data.get('Config', {}).get('Hostname', name)

                    # Get RAM and CPU from Config
                    ram_usage = inspect_data.get('HostConfig', {}).get('Memory', 0)
                    if ram_usage > 0: # Konversi byte ke format yang mudah dibaca (misal: "512m")
                        if ram_usage >= 1024**3: # GB
                            ram_usage = f"{ram_usage / (1024**3):.0f}G"
                        elif ram_usage >= 1024**2: # MB
                            ram_usage = f"{ram_usage / (1024**2):.0f}m"
                        else: # KB atau byte
                            ram_usage = f"{ram_usage}B"
                    else:
                        ram_usage = "N/A" # Jika tidak ada memori yang diset
                    
                    cpu_nano = inspect_data.get('HostConfig', {}).get('NanoCpus', 0)
                    if cpu_nano > 0: # Konversi NanoCpus ke format float (misal: "0.5")
                        cpu_usage = f"{cpu_nano / 1_000_000_000:.1f}" # 1 core = 1,000,000,000 NanoCpus
                    else:
                        cpu_usage = "N/A" # Jika tidak ada CPU yang diset

                    # Get Description from Labels
                    labels = inspect_data.get('Config', {}).get('Labels', {})
                    description = labels.get(DESCRIPTION_LABEL, "")

                    # Get ports
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
                    "ram": ram_usage, # Tambahkan ram
                    "cpu": cpu_usage, # Tambahkan cpu
                    "description": description # Tambahkan keterangan
                })
    return vps_list


@app.get("/", response_class=HTMLResponse)
async def form_get(request: Request):
    return templates.TemplateResponse("form.html", {"request": request})

@app.post("/", response_class=HTMLResponse)
async def form_post(
    request: Request,
    name: str = Form(...),
    hostname: str = Form(""),
    ram: str = Form(DEFAULT_RAM),
    cpu: str = Form(DEFAULT_CPU),
    storage: str = Form(DEFAULT_STORAGE),
    description: str = Form("") # Tambahkan field description
):
    hostname = hostname or name
    used_ports = get_available_ports()
    ssh_port = find_next_port(used_ports)
    # Tambahkan ssh_port ke used_ports agar web_port tidak sama
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
    
    # Tambahkan label untuk keterangan jika ada
    if description:
        cmd.extend(["--label", f"{DESCRIPTION_LABEL}={description}"])

    if is_storage_opt_supported():
        cmd += ["--storage-opt", f"size={storage}"]
    else:
        storage = "Tidak didukung (dilewati)"

    cmd.append(IMAGE_NAME)

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
            "description": description # Kirim keterangan kembali ke template
        })
    except subprocess.CalledProcessError as e:
        return templates.TemplateResponse("form.html", {
            "request": request,
            "error": True,
            "stderr": e.stderr
        })

@app.get("/manage", response_class=HTMLResponse)
async def manage_vps(request: Request):
    vps_list = list_vps()
    return templates.TemplateResponse("manage.html", {"request": request, "vps_list": vps_list})

@app.post("/toggle")
async def toggle_vps(name: str = Form(...)):
    info = subprocess.run(["docker", "inspect", "-f", "{{.State.Running}}", name], capture_output=True, text=True)
    if info.stdout.strip() == "true":
        subprocess.run(["docker", "stop", name])
    else:
        subprocess.run(["docker", "start", name])
    return RedirectResponse("/manage", status_code=303)

@app.post("/delete")
async def delete_vps(name: str = Form(...)):
    subprocess.run(["docker", "rm", "-f", name])
    return RedirectResponse("/manage", status_code=303)

@app.post("/edit")
async def edit_vps(
    name: str = Form(...),
    hostname: str = Form(...),
    ram: str = Form(...),
    cpu: str = Form(...),
    description: str = Form("") # Tambahkan field description yang diterima dari form
):
    # Dapatkan port, ram, cpu, dan description yang sedang digunakan sebelum menghapus
    current_ssh_port = "N/A"
    current_web_port = "N/A"
    current_ram = DEFAULT_RAM
    current_cpu = DEFAULT_CPU
    current_description = ""

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

            # Dapatkan RAM dan CPU dari HostConfig
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
            
            # Dapatkan Keterangan dari Labels
            labels = inspect_data.get('Config', {}).get('Labels', {})
            current_description = labels.get(DESCRIPTION_LABEL, "")

    except Exception as e:
        print(f"Error getting current details for {name}: {e}")

    # Hapus kontainer lama
    subprocess.run(["docker", "rm", "-f", name])

    # Gunakan kembali port yang sama jika masih tersedia, atau cari yang baru
    used_ports = get_available_ports()
    
    # Coba gunakan kembali current_ssh_port
    if current_ssh_port != "N/A" and int(current_ssh_port) not in used_ports:
        ssh_port = int(current_ssh_port)
    else:
        ssh_port = find_next_port(used_ports)
    used_ports.add(ssh_port) # Tambahkan port yang baru saja kita pilih agar tidak terpakai lagi

    # Coba gunakan kembali current_web_port
    if current_web_port != "N/A" and int(current_web_port) not in used_ports:
        web_port = int(current_web_port)
    else:
        web_port = find_next_port(used_ports)
    
    # Pastikan web_port tidak sama dengan ssh_port
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
    
    # Tambahkan label keterangan baru
    if description:
        cmd.extend(["--label", f"{DESCRIPTION_LABEL}={description}"])

    cmd.append(IMAGE_NAME)
    subprocess.run(cmd)
    return RedirectResponse("/manage", status_code=303)