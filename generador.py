import sys, requests, base64, time, json, os, re, subprocess, signal, random, shutil, mimetypes, gc, ctypes

# ---------------------------------------------------------------------------
# OPTIMIZACIÓN EXTREMA DE CPU / GPU EN APPLE SILICON (MAC MINI M4)
# 1. Limita todos los motores matemáticos a 1 solo hilo.
# 2. Reduce la prioridad Unix al mínimo (nice 19).
# 3. Activa PRIO_DARWIN_BG (Background QoS de macOS) para confinar este proceso
#    y todos sus hijos (SDXL, FFMPEG, MoviePy) a los núcleos de Eficiencia (E-cores),
#    dejando los núcleos de Rendimiento (P-cores) 100% libres para el sistema y el usuario.
# ---------------------------------------------------------------------------
os.environ["OMP_NUM_THREADS"] = "1"
os.environ["MKL_NUM_THREADS"] = "1"
os.environ["OPENBLAS_NUM_THREADS"] = "1"
os.environ["VECLIB_MAXIMUM_THREADS"] = "1"
os.environ["NUMEXPR_NUM_THREADS"] = "1"
os.environ["TOKENIZERS_PARALLELISM"] = "false"
os.environ["OBJC_DISABLE_INITIALIZE_FORK_SAFETY"] = "YES"
try:
    os.nice(19)
except Exception:
    pass
try:
    # PRIO_DARWIN_PROCESS = 4, PRIO_DARWIN_BG = 0x1000 (ejecución en E-cores sin bloquear UI)
    ctypes.CDLL("/usr/lib/libc.dylib").setpriority(4, 0, 0x1000)
except Exception:
    pass

from PIL import Image, ImageDraw, ImageFont, ImageEnhance, ImageOps, ImageFilter, ImageColor
import json_repair

rutas_mac = ['/opt/homebrew/bin/magick', '/usr/local/bin/magick']
for ruta in rutas_mac:
    if os.path.exists(ruta):
        os.environ['IMAGEMAGICK_BINARY'] = ruta
        break

if not hasattr(Image, 'ANTIALIAS'): Image.ANTIALIAS = Image.LANCZOS

try:
    from moviepy.editor import ImageClip, AudioFileClip, concatenate_videoclips, TextClip, CompositeVideoClip
except ImportError:
    print("⚠ Faltan librerías: pip install moviepy==1.0.3 Pillow numpy json-repair")

# ---------------------------------------------------------------------------
# CONFIGURACIÓN (las claves se leen de .env, NO se escriben en el código)
# ---------------------------------------------------------------------------
def cargar_env(ruta=".env"):
    if not os.path.exists(ruta): return
    for linea in open(ruta, encoding="utf-8"):
        linea = linea.strip()
        if not linea or linea.startswith("#") or "=" not in linea: continue
        k, v = linea.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))

cargar_env()
API_KEY_ELEVENLABS = os.environ.get("API_KEY_ELEVENLABS", "")
API_KEY_ANTHROPIC = os.environ.get("API_KEY_ANTHROPIC", "")
VOICE_ID_ELEVENLABS = os.environ.get("VOICE_ID_ELEVENLABS", "")

URL_SD_BASE = "http://127.0.0.1:7860/sdapi/v1"
URL_API_SD = f"{URL_SD_BASE}/txt2img"
RUTA_SD = "/Users/franciscojavierminajascarbajal/narrativas_automatizadas/stable-diffusion-webui"
MODELO_SD = "realvisxlV50_v50LightningBakedvae_full_fp16.safetensors"

PROMPT_BASE = "photorealistic, highly detailed, sharp focus"
NEGATIVO_BASE = "watermark, deformed, borders, letterbox"
FUENTE_IMPACT = "/System/Library/Fonts/Supplemental/Impact.ttf"
DURACION_PORTADA = 0.35  # segundos que la portada aparece como primer frame del video (0 = desactivar)
MAX_DURACION_S = 120  # límite de locución: arriba de esto Shorts penaliza (ideal 85-115 s)

# Subida a TikTok paramétrica (0 = desactivada por defecto para no bloquear el flujo por API;
# prepara el .mp4, metadata y portadas en la Mac mini y copia copy al portapapeles)
SUBIR_TIKTOK_AUTOMATICO = os.environ.get("SUBIR_TIKTOK_AUTOMATICO", "0").lower() in ("1", "true", "yes")

def actualizar_variable_env(clave, valor, ruta=".env"):
    """Actualiza o inserta una variable en el archivo .env y en os.environ."""
    lineas = []
    encontrada = False
    if os.path.exists(ruta):
        with open(ruta, "r", encoding="utf-8") as f:
            for l in f:
                if l.strip().startswith(f"{clave}=") or l.strip().startswith(f"{clave} ="):
                    lineas.append(f"{clave}=\"{valor}\"\n")
                    encontrada = True
                else:
                    lineas.append(l)
    if not encontrada:
        lineas.append(f"{clave}=\"{valor}\"\n")
    with open(ruta, "w", encoding="utf-8") as f:
        f.writelines(lineas)
    os.environ[clave] = str(valor)

TEMAS_AZAR = [
    # Ponderados según rendimiento real: arquitectura/folclore (YouTube+IG), morbo (TikTok), absurdo (Facebook)
    "casa, mansión o edificio histórico de México con pasado macabro", "prisión, hospital o manicomio abandonado de Latinoamérica",
    "leyenda urbana mexicana (La Llorona, La Vampira, brujas, nahuales)", "entidad folclórica latinoamericana en ciudad moderna",
    "pueblo fantasma o hacienda maldita de México", "arquitectura maldita de principios del siglo XX",
    "procedimiento médico o castigo histórico macabro (morbo médico)", "hecho histórico absurdo, bizarro e indignante que parece mentira",
    "ritual folclórico que aún se practica en secreto", "caso de supervivencia extrema olvidado"]

# Ubicación dual del historial: en el repo (versionado) y en el Home del usuario (a prueba de borrados de carpetas)
ARCHIVO_HISTORIAS_LOCAL = os.path.abspath("historias_usadas.json")
ARCHIVO_HISTORIAS_HOME = os.path.expanduser("~/.narrativas_historias_usadas.json")

def obtener_historias_previas():
    """Recopila todas las historias/casos ya generados para que Claude jamás los repita,
    incluso si el usuario borra las carpetas de videos para liberar espacio en disco."""
    historias = set()

    def _leer_archivo(ruta):
        if not os.path.exists(ruta): return
        try:
            with open(ruta, encoding="utf-8") as f:
                datos = json.load(f)
                if isinstance(datos, list):
                    for item in datos:
                        if isinstance(item, dict) and item.get("titulo"):
                            historias.add(item["titulo"].strip())
                        elif isinstance(item, str) and item.strip():
                            historias.add(item.strip())
        except Exception: pass

    # 1. Leer del archivo local y del archivo en el Home del sistema (respaldo seguro)
    _leer_archivo(ARCHIVO_HISTORIAS_LOCAL)
    _leer_archivo(ARCHIVO_HISTORIAS_HOME)

    # 2. Escaneo de carpetas de días (viernes, sabado, etc.) si aún existen en disco
    for r, _, archivos in os.walk("."):
        if any(p in r for p in ["venv", ".git", "stable-diffusion-webui", "__pycache__"]): continue
        if "metadata.md" in archivos:
            try:
                with open(os.path.join(r, "metadata.md"), encoding="utf-8", errors="ignore") as f:
                    for linea in f:
                        if linea.startswith("# "):
                            tit = re.sub(r"^#\s*[🎬📸]?\s*", "", linea).strip()
                            if tit: historias.add(tit)
                            break
            except Exception: pass

    return sorted(list(historias))

def registrar_historia_usada(titulo, tipo="video", detalles=None):
    """Guarda una historia en el historial permanente con bitácora detallada de iteración y commit en Git."""
    if not titulo: return
    titulo_limpio = str(titulo).strip()
    detalles = detalles or {}
    
    # Cargar registros existentes
    registros = {}
    for ruta in [ARCHIVO_HISTORIAS_HOME, ARCHIVO_HISTORIAS_LOCAL]:
        if os.path.exists(ruta):
            try:
                with open(ruta, encoding="utf-8") as f:
                    datos = json.load(f)
                    if isinstance(datos, list):
                        for item in datos:
                            if isinstance(item, dict) and item.get("titulo"):
                                registros[item["titulo"].strip()] = item
                            elif isinstance(item, str) and item.strip():
                                t = item.strip()
                                registros[t] = {"titulo": t, "fecha": "", "slug": slug(t, 40)}
            except Exception: pass

    # Actualizar o insertar registro con bitácora enriquecida
    import datetime
    fecha_hoy = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    previo = registros.get(titulo_limpio, {})
    
    nuevo_registro = {
        "titulo": titulo_limpio,
        "fecha": previo.get("fecha") or fecha_hoy,
        "ultima_actualizacion": fecha_hoy,
        "tipo": tipo,
        "slug": slug(titulo_limpio, 40),
        "estado": detalles.get("estado", previo.get("estado", "guion_listo")),
        "tier": detalles.get("tier", previo.get("tier")),
        "duracion_audio_s": detalles.get("duracion_audio_s", previo.get("duracion_audio_s")),
        "num_imagenes": detalles.get("num_imagenes", previo.get("num_imagenes")),
        "carpeta": detalles.get("carpeta", previo.get("carpeta")),
        "archivo_salida": detalles.get("archivo_salida", previo.get("archivo_salida")),
        "momento_dia": detalles.get("momento_dia", previo.get("momento_dia"))
    }
    # Limpiar valores nulos para un JSON limpio y legible
    registros[titulo_limpio] = {k: v for k, v in nuevo_registro.items() if v is not None}

    lista_ordenada = sorted(list(registros.values()), key=lambda x: x["titulo"])

    # Escribir en ambas ubicaciones confiables
    for ruta in [ARCHIVO_HISTORIAS_LOCAL, ARCHIVO_HISTORIAS_HOME]:
        try:
            with open(ruta, "w", encoding="utf-8") as f:
                json.dump(lista_ordenada, f, ensure_ascii=False, indent=2)
        except Exception: pass

    # Versionar en Git automáticamente cada historia procesada
    try:
        estado_label = nuevo_registro.get("estado", "procesada")
        msg_commit = f"historia({tipo}): {estado_label} '{titulo_limpio}'"
        subprocess.run(["git", "add", "historias_usadas.json"], cwd=os.path.dirname(ARCHIVO_HISTORIAS_LOCAL) or ".", capture_output=True, text=True, check=False)
        res_commit = subprocess.run(["git", "commit", "-m", msg_commit], cwd=os.path.dirname(ARCHIVO_HISTORIAS_LOCAL) or ".", capture_output=True, text=True, check=False)
        if res_commit.returncode == 0:
            print(f"   🔖 [Bitácora Git] Commit: {msg_commit}")
            # Intentar push en segundo plano sin bloquear si hay conexión
            subprocess.Popen(["git", "push", "origin", "main"], cwd=os.path.dirname(ARCHIVO_HISTORIAS_LOCAL) or ".", stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except Exception as e:
        pass

# ---------------------------------------------------------------------------
# INTEGRACIÓN GOOGLE DRIVE (API AUTÓNOMA)
# ---------------------------------------------------------------------------
GDRIVE_FOLDER_ID = os.environ.get("GDRIVE_FOLDER_ID", "1kLN0dbdv2ZgL0V96JjHquBx0RhLtcMwS")
SCOPES_DRIVE = ['https://www.googleapis.com/auth/drive.file', 'https://www.googleapis.com/auth/drive']

def obtener_servicio_drive():
    """Obtiene el cliente autenticado de Google Drive usando OAuth2 (credentials.json / token.json) o Cuenta de Servicio."""
    try:
        from google.oauth2.credentials import Credentials
        from google_auth_oauthlib.flow import InstalledAppFlow
        from google.auth.transport.requests import Request
        from googleapiclient.discovery import build
        from google.oauth2 import service_account
    except ImportError:
        print("⚠ Faltan librerías de Google Drive: ./venv/bin/pip install google-api-python-client google-auth-httplib2 google-auth-oauthlib")
        return None

    # 1. Opción Cuenta de Servicio (service_account.json) si existe
    if os.path.exists("service_account.json"):
        try:
            creds = service_account.Credentials.from_service_account_file("service_account.json", scopes=SCOPES_DRIVE)
            return build('drive', 'v3', credentials=creds)
        except Exception as e:
            print(f"⚠ Error con service_account.json: {e}")

    # 2. Opción OAuth2 usuario (credentials.json -> token.json)
    creds = None
    if os.path.exists('token.json'):
        try:
            creds = Credentials.from_authorized_user_file('token.json', SCOPES_DRIVE)
        except Exception:
            creds = None

    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            try:
                creds.refresh(Request())
                with open('token.json', 'w') as token_file:
                    token_file.write(creds.to_json())
            except Exception:
                creds = None

        if not creds:
            # Buscar archivo de credenciales de cliente OAuth
            arch_cred = "credentials.json"
            if not os.path.exists(arch_cred):
                cands = [f for f in os.listdir(".") if f.startswith("client_secret") and f.endswith(".json")]
                if cands: arch_cred = cands[0]

            if not os.path.exists(arch_cred):
                return None  # No hay credenciales configuradas todavía

            try:
                print("\n🔑 Iniciando autenticación autónoma con Google Drive...")
                flow = InstalledAppFlow.from_client_secrets_file(arch_cred, SCOPES_DRIVE)
                creds = flow.run_local_server(port=0)
                with open('token.json', 'w') as token_file:
                    token_file.write(creds.to_json())
                print("✔ Autenticación con Google Drive completada y guardada en token.json.")
            except Exception as e:
                print(f"⚠ Error autenticando con Google Drive: {e}")
                return None

    try:
        return build('drive', 'v3', credentials=creds)
    except Exception as e:
        print(f"⚠ Error conectando con servicio Google Drive: {e}")
        return None

def es_archivo_portada(nombre):
    """Identifica imágenes de portada que deben conservarse en la Mac para YouTube, TikTok y Bilibili."""
    n = nombre.lower()
    return "portada" in n and n.endswith(".png")

def purgar_archivos_intermedios_local(ruta_carpeta):
    """
    Optimiza el almacenamiento local de la Mac mini tras confirmar el respaldo 100% en Google Drive.
    Regla estricta:
    - CONSERVA ÚNICAMENTE en la Mac mini:
      1) El video final (.mp4) para subir a TikTok y Bilibili.
      2) La metadata completa ('metadata.md') con títulos, hashtags y horarios.
      3) Las imágenes de portada ('00_portada*.png' o '*portada*.png').
    - ELIMINA del disco local los archivos intermedios pesados:
      - Todas las imágenes de escenas intermedias ('01_*.png', '02_*.png', '*_base.png', etc.)
      - El archivo de locución ('00_locucion.mp3')
      - Cualquier video temporal o parcial ('*.parcial.mp4', 'temp*.mp4')
    """
    if not os.path.exists(ruta_carpeta):
        return

    nombre_c = os.path.basename(ruta_carpeta.rstrip("/\\"))
    es_carrusel = "historia_foto" in nombre_c

    eliminados = 0
    bytes_liberados = 0
    conservados = []

    for nom in os.listdir(ruta_carpeta):
        ruta_arch = os.path.join(ruta_carpeta, nom)
        if not os.path.isfile(ruta_arch):
            continue

        n_low = nom.lower()

        # En carruseles de fotos (FB/IG/YT): conservar diapositivas finales rotuladas y metadata
        if es_carrusel:
            if n_low == "metadata.md" or (n_low.endswith(".png") and not "_base" in n_low):
                conservados.append(nom)
                continue
            try:
                tam = os.path.getsize(ruta_arch)
                os.remove(ruta_arch)
                eliminados += 1
                bytes_liberados += tam
            except Exception:
                pass
            continue

        # En videos verticales:
        # 1. Conservar video .mp4 final
        if n_low.endswith(".mp4") and not n_low.endswith(".parcial.mp4") and not n_low.startswith("temp"):
            conservados.append(nom)
            continue

        # 2. Conservar metadata.md
        if n_low == "metadata.md":
            conservados.append(nom)
            continue

        # 3. Conservar imágenes de portada (00_portada.png, 00_portada_tiktok.png, 00_portada_bilibili.png, etc.)
        if es_archivo_portada(nom):
            conservados.append(nom)
            continue

        # El resto son escenas intermedias (01-26_*.png), bases y locución mp3
        try:
            tam = os.path.getsize(ruta_arch)
            os.remove(ruta_arch)
            eliminados += 1
            bytes_liberados += tam
        except Exception as e:
            print(f"   ⚠ No se pudo eliminar temporal local '{nom}': {e}")

    mb_lib = bytes_liberados / (1024 * 1024)
    print(f"\n🧹 [DISCO MAC MINI OPTIMIZADO - {nombre_c}]:")
    print(f"   ✔ Se eliminaron {eliminados} archivos intermedios pesados ({mb_lib:.1f} MB liberados tras respaldo en Google Drive).")
    print(f"   📁 Conservados localmente en la Mac para subir a TikTok y Bilibili:")
    for c_nom in sorted(conservados):
        print(f"      • {c_nom}")

def subir_carpeta_a_gdrive_y_liberar(ruta_carpeta, parent_id=GDRIVE_FOLDER_ID, conservar_local=False, redes_pendientes=None):
    """Sube recursivamente una carpeta y todos sus archivos a Google Drive como respaldo.
    Se ejecuta DESPUÉS de publicar en las 4 redes sociales.
    Al confirmar el respaldo 100% en la nube:
    - Purgará los archivos intermedios pesados (escenas y audios) para liberar disco local.
    - Conservará permanentemente en la Mac mini el video .mp4, metadata.md y portadas para TikTok y Bilibili."""
    if not os.path.exists(ruta_carpeta):
        return False

    service = obtener_servicio_drive()
    if not service:
        print(f"ℹ No se pudo conectar a Google Drive (coloca 'credentials.json' para activar la subida automática).")
        print(f"  La carpeta local '{ruta_carpeta}' se conserva en el disco.")
        return False

    from googleapiclient.http import MediaFileUpload

    nombre_carpeta = os.path.basename(ruta_carpeta.rstrip("/\\"))
    print(f"\n☁ Subiendo carpeta '{nombre_carpeta}' a Google Drive (ID: {parent_id})...")

    try:
        # 1. Crear la carpeta en Google Drive dentro del parent_id
        meta_carpeta = {
            'name': nombre_carpeta,
            'mimeType': 'application/vnd.google-apps.folder',
            'parents': [parent_id]
        }
        folder_drive = service.files().create(body=meta_carpeta, fields='id').execute()
        folder_id = folder_drive.get('id')

        # 2. Subir todos los archivos de la carpeta
        archivos_locales = [f for f in os.listdir(ruta_carpeta) if os.path.isfile(os.path.join(ruta_carpeta, f)) and not f.startswith(".")]
        subidos = 0

        for idx, nom_arch in enumerate(archivos_locales, 1):
            ruta_f = os.path.join(ruta_carpeta, nom_arch)
            tipo_mime, _ = mimetypes.guess_type(ruta_f)
            if not tipo_mime:
                if nom_arch.endswith(".mp4"): tipo_mime = "video/mp4"
                elif nom_arch.endswith(".mp3"): tipo_mime = "audio/mpeg"
                elif nom_arch.endswith(".png"): tipo_mime = "image/png"
                elif nom_arch.endswith(".md"): tipo_mime = "text/markdown"
                else: tipo_mime = "application/octet-stream"

            tam_mb = os.path.getsize(ruta_f) / (1024 * 1024)
            print(f"   [{idx}/{len(archivos_locales)}] Subiendo {nom_arch} ({tam_mb:.1f} MB)...", end="", flush=True)

            file_metadata = {'name': nom_arch, 'parents': [folder_id]}
            media = MediaFileUpload(ruta_f, mimetype=tipo_mime, resumable=True)
            request = service.files().create(body=file_metadata, media_body=media, fields='id')
            response = None
            while response is None:
                status, response = request.next_chunk()

            if response and response.get('id'):
                subidos += 1
                print(" ✔")
            else:
                print(" ✖")

        # 3. Validar integridad de subida antes de optimizar la Mac local
        if subidos == len(archivos_locales):
            print(f"✅ Carpeta '{nombre_carpeta}' respaldada completa en Google Drive ({subidos}/{len(archivos_locales)} archivos).")
            print(f"🔒 [Regla Google Drive]: Solo agregar contenido nuevo; PROHIBIDO eliminar nada de la nube.")

            # Purgar archivos intermedios pesados de la Mac conservando .mp4, metadata y portadas
            purgar_archivos_intermedios_local(ruta_carpeta)

            if conservar_local:
                detalle_redes = ", ".join(redes_pendientes) if redes_pendientes else "alguna red social"
                print(f"📌 Se conservan el video .mp4, metadata y portadas en la Mac porque quedó pendiente: [{detalle_redes}].")
                print(f"   💡 Puedes reintentar subir solo a la red pendiente desde el menú Opción 5 (Panel de Redes).")
            else:
                print(f"✔ Publicación automatizada procesada al 100%. Video y metadata listos en la Mac para TikTok y Bilibili.")
            return True
        else:
            print(f"⚠ Solo se subieron {subidos} de {len(archivos_locales)} archivos a Drive. Se conservan todos los archivos en la Mac por seguridad.")
            return False

    except Exception as e:
        print(f"✖ Error subiendo '{nombre_carpeta}' a Google Drive: {e}")
        return False

# ---------------------------------------------------------------------------
# INTEGRACIÓN YOUTUBE SHORTS (API AUTÓNOMA OFICIAL)
# ---------------------------------------------------------------------------
SCOPES_YOUTUBE = ['https://www.googleapis.com/auth/youtube.upload', 'https://www.googleapis.com/auth/youtube']

def obtener_servicio_youtube():
    """Obtiene el cliente autenticado de YouTube Data API v3 usando credentials.json -> token_youtube.json."""
    try:
        from google.oauth2.credentials import Credentials
        from google_auth_oauthlib.flow import InstalledAppFlow
        from google.auth.transport.requests import Request
        from googleapiclient.discovery import build
    except ImportError:
        print("⚠ Faltan librerías de Google: ./venv/bin/pip install google-api-python-client google-auth-httplib2 google-auth-oauthlib")
        return None

    creds = None
    if os.path.exists('token_youtube.json'):
        try:
            creds = Credentials.from_authorized_user_file('token_youtube.json', SCOPES_YOUTUBE)
        except Exception:
            creds = None

    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            try:
                creds.refresh(Request())
                with open('token_youtube.json', 'w') as token_file:
                    token_file.write(creds.to_json())
            except Exception:
                creds = None

        if not creds:
            arch_cred = "credentials.json"
            if not os.path.exists(arch_cred):
                cands = [f for f in os.listdir(".") if f.startswith("client_secret") and f.endswith(".json")]
                if cands: arch_cred = cands[0]

            if not os.path.exists(arch_cred):
                return None

            try:
                print("\n🔴 Iniciando autenticación autónoma con YouTube Data API...")
                flow = InstalledAppFlow.from_client_secrets_file(arch_cred, SCOPES_YOUTUBE)
                creds = flow.run_local_server(port=0)
                with open('token_youtube.json', 'w') as token_file:
                    token_file.write(creds.to_json())
                print("✔ Autenticación con YouTube completada y guardada en token_youtube.json.")
            except Exception as e:
                print(f"⚠ Error autenticando con YouTube: {e}")
                return None

    try:
        return build('youtube', 'v3', credentials=creds)
    except Exception as e:
        print(f"⚠ Error conectando con servicio YouTube: {e}")
        return None

def resolver_fecha_dia(texto_dia=None):
    """Convierte una entrada como 'hoy', 'mañana', 'viernes', 'sábado', '2026-10-15' o '15/10/2026'
    en una fecha 'YYYY-MM-DD' en zona horaria CST (México, UTC-6)."""
    from datetime import datetime, timedelta, timezone
    tz_cst = timezone(timedelta(hours=-6))
    ahora_cst = datetime.now(tz_cst)
    if not texto_dia or not texto_dia.strip():
        return (ahora_cst + timedelta(days=1)).strftime("%Y-%m-%d")

    t = texto_dia.strip().lower()
    # Normalizar acentos comunes
    reemplazos = {"á": "a", "é": "e", "í": "i", "ó": "o", "ú": "u"}
    for k, v in reemplazos.items():
        t = t.replace(k, v)

    if t in ("hoy", "today"):
        return ahora_cst.strftime("%Y-%m-%d")
    if t in ("manana", "mañana", "tomorrow"):
        return (ahora_cst + timedelta(days=1)).strftime("%Y-%m-%d")
    if t in ("pasado manana", "pasado mañana"):
        return (ahora_cst + timedelta(days=2)).strftime("%Y-%m-%d")

    dias_semana = {
        "lunes": 0, "martes": 1, "miercoles": 2, "jueves": 3,
        "viernes": 4, "sabado": 5, "domingo": 6
    }
    for nombre_dia, num_dia in dias_semana.items():
        if nombre_dia in t:
            dias_faltantes = (num_dia - ahora_cst.weekday()) % 7
            if dias_faltantes == 0:
                dias_faltantes = 7
            return (ahora_cst + timedelta(days=dias_faltantes)).strftime("%Y-%m-%d")

    # Intentar formatos YYYY-MM-DD o DD/MM/YYYY
    for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y"):
        try:
            dt = datetime.strptime(t, fmt)
            return dt.strftime("%Y-%m-%d")
        except ValueError:
            pass

    return (ahora_cst + timedelta(days=1)).strftime("%Y-%m-%d")

def formatear_hora_cst(publish_at_iso):
    """Convierte un timestamp ISO UTC ('YYYY-MM-DDTHH:MM:SZ') a texto legible en hora CDMX (CST, UTC-6)."""
    if not publish_at_iso:
        return "Inmediato (Público ahora)"
    try:
        from datetime import datetime, timedelta, timezone
        tz_cst = timezone(timedelta(hours=-6))
        dt_utc = datetime.strptime(publish_at_iso, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
        dt_cst = dt_utc.astimezone(tz_cst)
        return f"{dt_cst.strftime('%Y-%m-%d %H:%M')} CST ({publish_at_iso})"
    except Exception:
        return publish_at_iso

def calcular_fecha_programacion(momento_dia=None, hora_especifica=None, fecha_base=None, forzar_programacion=True):
    """Calcula fecha/hora en formato ISO 8601 UTC (RFC 3339) para programar en las redes sociales.
    Zona horaria de referencia: CST (México, UTC-6).
    Soporta entradas naturales como 'mañana', 'hoy', 'viernes', 'mañana 21:00', '01:30', '2026-10-15 20:00'.
    Cuando forzar_programacion=True, NUNCA devuelve None (garantiza que jamás se libere de inmediato por error)."""
    from datetime import datetime, timedelta, timezone
    tz_cst = timezone(timedelta(hours=-6))
    ahora_cst = datetime.now(tz_cst)

    horas_franja = {
        "manana": (8, 0),
        "mediodia": (13, 0),
        "tarde": (17, 0),
        "noche": (21, 0),
        "madrugada": (1, 30),
        "multi": (20, 30),
        "jornada_completa": (20, 30),
    }

    target_h_def, target_m_def = horas_franja.get(momento_dia or "multi", (20, 30))

    try:
        base_dt = ahora_cst
        tiene_fecha_explicita = False
        if fecha_base and str(fecha_base).strip():
            fecha_norm = resolver_fecha_dia(str(fecha_base))
            y, m, d = map(int, fecha_norm.split("-"))
            base_dt = ahora_cst.replace(year=y, month=m, day=d, second=0, microsecond=0)
            tiene_fecha_explicita = True

        if hora_especifica and str(hora_especifica).strip():
            he = str(hora_especifica).strip().lower()
            # 1. Buscar si incluye una hora tipo HH:MM o H:MM (con o sin am/pm)
            m_hora = re.search(r'\b(\d{1,2}):(\d{2})\s*(am|pm)?\b', he)
            # Limpiar la parte de la hora para ver si además escribieron un día ('mañana', 'viernes', '2026-10-15', etc.)
            resto_dia = re.sub(r'\b\d{1,2}:\d{2}\s*(am|pm)?\b', '', he).replace("a las", "").replace("cst", "").strip()

            if resto_dia:
                fecha_norm = resolver_fecha_dia(resto_dia)
                y, m, d = map(int, fecha_norm.split("-"))
                base_dt = ahora_cst.replace(year=y, month=m, day=d, second=0, microsecond=0)
                tiene_fecha_explicita = True

            if m_hora:
                hh = int(m_hora.group(1))
                mm = int(m_hora.group(2))
                ampm = m_hora.group(3)
                if ampm == "pm" and hh < 12:
                    hh += 12
                elif ampm == "am" and hh == 12:
                    hh = 0
                dt = base_dt.replace(hour=min(23, max(0, hh)), minute=min(59, max(0, mm)), second=0, microsecond=0)
            else:
                # El usuario escribió solo un día como 'mañana', 'hoy', 'viernes' sin HH:MM -> usar la hora de la franja elegida
                dt = base_dt.replace(hour=target_h_def, minute=target_m_def, second=0, microsecond=0)

            while dt <= ahora_cst + timedelta(minutes=20):
                dt += timedelta(days=1)
            return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

        # Si no hay hora_especifica, usar momento_dia (o 'multi' -> 20:30 CST por defecto)
        dt = base_dt.replace(hour=target_h_def, minute=target_m_def, second=0, microsecond=0)
        if tiene_fecha_explicita and momento_dia == "madrugada":
            # Si el usuario eligió por ejemplo 'hoy' a las 22:00 CST y franja 'madrugada' (01:30 AM), es la madrugada entrante
            if dt <= ahora_cst + timedelta(minutes=20):
                dt += timedelta(days=1)
        while dt <= ahora_cst + timedelta(minutes=20):
            dt += timedelta(days=1)
        return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    except Exception as e:
        print(f"   ⚠ Aviso interpretando fecha/hora ('{e}'); aplicando respaldo automático de programación.")
        if forzar_programacion:
            dt_fallback = (ahora_cst + timedelta(days=1)).replace(hour=target_h_def, minute=target_m_def, second=0, microsecond=0)
            if dt_fallback <= ahora_cst + timedelta(minutes=20):
                dt_fallback = ahora_cst + timedelta(hours=2)
            return dt_fallback.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    return None

# ---------------------------------------------------------------------------
# CALENDARIO Y PROGRAMACIÓN DE SEMANA COMPLETA (LUNES A DOMINGO)
# ---------------------------------------------------------------------------
CALENDARIO_SEMANA_COMPLETA = [
    {
        "dia": "Lunes",
        "num_dia": 0,
        "hora_cst": (20, 0),
        "momento": "noche",
        "enfoque": "Comienzo de semana: misterio histórico profundo y atmósfera intrigante",
        "horario_str": "20:00 CST"
    },
    {
        "dia": "Martes",
        "num_dia": 1,
        "hora_cst": (20, 30),
        "momento": "noche",
        "enfoque": "Pico nocturno: leyendas urbanas oscuras y giros inesperados",
        "horario_str": "20:30 CST"
    },
    {
        "dia": "Miércoles",
        "num_dia": 2,
        "hora_cst": (20, 0),
        "momento": "noche",
        "enfoque": "Mitad de semana: arquitectura maldita y lugares prohibidos",
        "horario_str": "20:00 CST"
    },
    {
        "dia": "Jueves",
        "num_dia": 3,
        "hora_cst": (20, 30),
        "momento": "noche",
        "enfoque": "Noche Prime: folclore y enigmas no resueltos de alta retención",
        "horario_str": "20:30 CST"
    },
    {
        "dia": "Viernes",
        "num_dia": 4,
        "hora_cst": (19, 30),
        "momento": "tarde",
        "enfoque": "Viernes de terror y desconexión laboral: shock visual y tensión",
        "horario_str": "19:30 CST"
    },
    {
        "dia": "Sábado",
        "num_dia": 5,
        "hora_cst": (19, 0),
        "momento": "tarde",
        "enfoque": "Fin de semana ocio: misterio cautivador y debate comunitario",
        "horario_str": "19:00 CST"
    },
    {
        "dia": "Domingo",
        "num_dia": 6,
        "hora_cst": (21, 0),
        "momento": "noche",
        "enfoque": "Domingo Prime: máxima audiencia en cama, introspección y leyendas extremas",
        "horario_str": "21:00 CST"
    }
]

def resolver_fechas_semana_completa(referencia_semana=None):
    """Calcula las fechas exactas y horas óptimas de Lunes a Domingo.
    Devuelve lista de 7 dicts con día, fecha, horario_str, momento, enfoque, fecha_prog_iso y dt_cst."""
    from datetime import datetime, timedelta, timezone
    tz_cst = timezone(timedelta(hours=-6))
    ahora_cst = datetime.now(tz_cst)

    ref_str = str(referencia_semana or "").strip().lower()
    if not ref_str or ref_str in ("proxima", "próxima", "siguiente"):
        dias_para_lunes = (0 - ahora_cst.weekday()) % 7
        if dias_para_lunes == 0:
            dias_para_lunes = 7  # Próximo lunes
        lunes_dt = (ahora_cst + timedelta(days=dias_para_lunes)).date()
    elif ref_str in ("actual", "esta", "esta semana", "hoy"):
        dias_desde_lunes = ahora_cst.weekday()
        lunes_dt = (ahora_cst - timedelta(days=dias_desde_lunes)).date()
    else:
        ref_norm = resolver_fecha_dia(ref_str)
        try:
            ref_dt = datetime.strptime(ref_norm, "%Y-%m-%d").date()
            lunes_dt = ref_dt - timedelta(days=ref_dt.weekday())
        except Exception:
            dias_para_lunes = (0 - ahora_cst.weekday()) % 7
            if dias_para_lunes == 0: dias_para_lunes = 7
            lunes_dt = (ahora_cst + timedelta(days=dias_para_lunes)).date()

    res = []
    for item in CALENDARIO_SEMANA_COMPLETA:
        idx_dia = item["num_dia"]
        fecha_dia = lunes_dt + timedelta(days=idx_dia)
        fecha_str = fecha_dia.strftime("%Y-%m-%d")
        h, m = item["hora_cst"]

        dt_cst = datetime(fecha_dia.year, fecha_dia.month, fecha_dia.day, h, m, 0, tzinfo=tz_cst)
        # Si la fecha/hora ya quedó en el pasado respecto a ahora, avanzar 1 semana para asegurar programación futura
        if dt_cst <= ahora_cst + timedelta(minutes=20):
            dt_cst += timedelta(days=7)
            fecha_str = dt_cst.strftime("%Y-%m-%d")

        iso_utc = dt_cst.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

        res.append({
            "dia": item["dia"],
            "fecha": fecha_str,
            "horario_str": item["horario_str"],
            "momento": item["momento"],
            "enfoque": item["enfoque"],
            "fecha_prog_iso": iso_utc,
            "hora_cst": f"{h:02d}:{m:02d}"
        })
    return res

def subir_y_programar_youtube_short(ruta_video, titulo, descripcion, tags=None, publish_at_iso=None, primer_comentario=None):
    """Sube un video vertical a YouTube Shorts y lo deja programado (privado hasta publishAt) o público."""
    if not os.path.exists(ruta_video):
        return None

    service = obtener_servicio_youtube()
    if not service:
        print("ℹ No se pudo conectar a YouTube Data API (se conserva sin subir).")
        return None

    from googleapiclient.http import MediaFileUpload
    from datetime import datetime, timedelta, timezone

    # Garantizar que si se pidió programación, publish_at_iso esté al menos 20 minutos en el futuro
    if publish_at_iso:
        try:
            dt_utc = datetime.strptime(publish_at_iso, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
            min_utc = datetime.now(timezone.utc) + timedelta(minutes=20)
            if dt_utc < min_utc:
                dt_utc = min_utc
                publish_at_iso = dt_utc.strftime("%Y-%m-%dT%H:%M:%SZ")
        except Exception:
            dt_utc = datetime.now(timezone.utc) + timedelta(hours=2)
            publish_at_iso = dt_utc.strftime("%Y-%m-%dT%H:%M:%SZ")

    modo_str = f"PROGRAMADO ({formatear_hora_cst(publish_at_iso)})" if publish_at_iso else "PÚBLICO INMEDIATO"
    print(f"\n🔴 Subiendo a YouTube Shorts [{modo_str}]: '{titulo[:50]}'...")
    if tags is None:
        tags = ["Shorts", "NarrativasSumergidas", "Misterio", "LeyendasUrbanas"]
    elif isinstance(tags, str):
        tags = [t.strip().lstrip("#") for t in tags.split() if t.strip()]

    # Si hay fecha y hora de publicación programada, el estado DEBE ser 'private' con publishAt
    status_body = {
        'privacyStatus': 'private' if publish_at_iso else 'public',
        'selfDeclaredMadeForKids': False
    }
    if publish_at_iso:
        status_body['publishAt'] = publish_at_iso

    body = {
        'snippet': {
            'title': titulo[:100],
            'description': descripcion[:5000],
            'tags': tags[:15],
            'categoryId': '24'  # Entretenimiento
        },
        'status': status_body
    }

    try:
        media = MediaFileUpload(ruta_video, mimetype='video/mp4', chunksize=1024*1024*5, resumable=True)
        request = service.videos().insert(part=','.join(body.keys()), body=body, media_body=media)

        response = None
        while response is None:
            status, response = request.next_chunk()
            if status:
                print(f"   Progreso YouTube: {int(status.progress() * 100)}%...", end="\r", flush=True)

        video_id = response.get('id')
        if video_id:
            url_short = f"https://youtube.com/shorts/{video_id}"
            st_resp = response.get('status', {})
            if publish_at_iso:
                print(f"✅ ¡YouTube Short subido y PROGRAMADO con éxito (NO es público todavía)!")
                print(f"   🔒 Estado actual en YouTube: {st_resp.get('privacyStatus', 'private').upper()}")
                print(f"   🕒 Se liberará automáticamente el: {formatear_hora_cst(publish_at_iso)}")
            else:
                print(f"✅ ¡YouTube Short subido y PUBLICADO exitosamente!")
            print(f"   🔗 Enlace: {url_short}")

            # Publicar primer comentario fijado si el video ya es público (en videos privados/programados YouTube bloquea comentarios hasta su estreno)
            if primer_comentario and primer_comentario.strip() and not publish_at_iso:
                try:
                    service.commentThreads().insert(
                        part="snippet",
                        body={
                            "snippet": {
                                "videoId": video_id,
                                "topLevelComment": {
                                    "snippet": {
                                        "textOriginal": primer_comentario.strip()
                                    }
                                }
                            }
                        }
                    ).execute()
                    print(f"   💬 Primer comentario fijado publicado: \"{primer_comentario.strip()[:60]}...\"")
                except Exception as e_comm:
                    print(f"   ℹ No se pudo insertar el primer comentario automático: {e_comm}")
            elif primer_comentario and primer_comentario.strip() and publish_at_iso:
                print(f"   💬 Comentario fijado listo en metadata.md para cuando se estrene: \"{primer_comentario.strip()[:60]}...\"")

            return video_id
        return None
    except Exception as e:
        print(f"✖ Error subiendo a YouTube Shorts: {e}")
        return None

def subir_video_tiktok(ruta_video, titulo=None, descripcion=None, hashtags=None):
    """Sube y publica un video vertical en TikTok vía API oficial.
    Si el token cuenta con el permiso 'video.publish' (Direct Post), lo publica directamente en el perfil
    con su copy y hashtags. De lo contrario, lo envía a los Borradores de la app (inbox)."""
    if not os.path.exists(ruta_video):
        return None

    archivo_token = "token_tiktok.json"
    if not os.path.exists(archivo_token):
        return None

    try:
        with open(archivo_token, "r", encoding="utf-8") as f:
            d = json.load(f)
            token = d.get("access_token") or d.get("data", {}).get("access_token")
            scopes_str = d.get("scope", "") or d.get("data", {}).get("scope", "")
        if not token:
            return None

        tam_archivo = os.path.getsize(ruta_video)
        print(f"\n🎵 Subiendo video a TikTok: '{os.path.basename(ruta_video)}' ({tam_archivo / (1024*1024):.1f} MB)...")

        headers_init = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json; charset=UTF-8"
        }

        partes_texto = []
        if titulo:
            partes_texto.append(titulo.strip())
        if descripcion:
            partes_texto.append(descripcion.strip())
        if hashtags:
            partes_texto.append(hashtags.strip())
        caption_tt = " ".join(partes_texto).strip()[:2100] or os.path.splitext(os.path.basename(ruta_video))[0]

        res_init = None
        modo_directo = False

        # Calcular chunks respetando el límite estricto de TikTok (máx 64 MB por chunk)
        max_chunk = 50 * 1024 * 1024  # 50 MB
        if tam_archivo <= max_chunk:
            chunk_size = tam_archivo
            total_chunks = 1
        else:
            chunk_size = 20 * 1024 * 1024  # 20 MB por bloque
            total_chunks = tam_archivo // chunk_size

        # Usar Inbox Draft (/v2/post/publish/inbox/video/init/) por defecto para dejar el borrador
        # listo en la app de TikTok (permite programar o publicar en público sin requerir auditoría de app).
        usar_direct_post = os.getenv("TIKTOK_DIRECT_POST", "0") == "1"
        if usar_direct_post and "video.publish" in scopes_str:
            privacidad = "PUBLIC_TO_EVERYONE"
            body_direct = {
                "post_info": {
                    "title": caption_tt,
                    "privacy_level": privacidad,
                    "disable_duet": False,
                    "disable_comment": False,
                    "disable_stitch": False
                },
                "source_info": {
                    "source": "FILE_UPLOAD",
                    "video_size": tam_archivo,
                    "chunk_size": chunk_size,
                    "total_chunk_count": total_chunks
                }
            }
            r_dir = requests.post(
                "https://open.tiktokapis.com/v2/post/publish/video/init/",
                headers=headers_init,
                json=body_direct,
                timeout=20
            )
            rj = r_dir.json()
            if "data" in rj and "upload_url" in rj["data"]:
                res_init = rj
                modo_directo = True
                print(f"   🚀 Modo Direct Post activado (privacidad: {privacidad})")

        # Subida a Bandeja de Entrada / Borradores de la app (video.upload)
        if not res_init:
            url_init = "https://open.tiktokapis.com/v2/post/publish/inbox/video/init/"
            body_init = {
                "source_info": {
                    "source": "FILE_UPLOAD",
                    "video_size": tam_archivo,
                    "chunk_size": chunk_size,
                    "total_chunk_count": total_chunks
                }
            }
            r_init = requests.post(url_init, headers=headers_init, json=body_init, timeout=20)
            res_init = r_init.json()
            if "data" not in res_init or "upload_url" not in res_init["data"]:
                print(f"✖ Error iniciando subida a TikTok: {res_init}")
                return None

        upload_url = res_init["data"]["upload_url"]
        publish_id = res_init["data"]["publish_id"]

        # 2. Upload video bytes (soporta 1 o múltiples chunks)
        with open(ruta_video, "rb") as f_vid:
            for idx_c in range(total_chunks):
                inicio_b = idx_c * chunk_size
                if idx_c == total_chunks - 1:
                    fin_b = tam_archivo - 1
                else:
                    fin_b = inicio_b + chunk_size - 1
                bloque = f_vid.read(fin_b - inicio_b + 1)
                headers_upload = {
                    "Content-Range": f"bytes {inicio_b}-{fin_b}/{tam_archivo}",
                    "Content-Length": str(len(bloque)),
                    "Content-Type": "video/mp4"
                }
                requests.put(upload_url, data=bloque, headers=headers_upload, timeout=240)

        # 3. Check status
        url_status = "https://open.tiktokapis.com/v2/post/publish/status/fetch/"
        for _ in range(18):
            time.sleep(4)
            r_stat = requests.post(url_status, headers=headers_init, json={"publish_id": publish_id}, timeout=15)
            txt = r_stat.text
            if "PUBLISH_COMPLETE" in txt or "SEND_TO_USER_INBOX" in txt or "SUCCESS" in txt:
                if modo_directo or "PUBLISH_COMPLETE" in txt:
                    print(f"✅ ¡Video PUBLICADO directamente en tu perfil de TikTok con su copy y hashtags! (ID: {publish_id})")
                else:
                    print(f"✅ ¡Video enviado exitosamente a tus Borradores de TikTok! (ID: {publish_id})")
                    print("   📲 Abre la Bandeja de entrada en tu app de TikTok para publicarlo o programarlo.")
                    if caption_tt:
                        try:
                            subprocess.run(["pbcopy"], input=caption_tt.encode("utf-8"), check=True)
                            print("   📋 Copy copiado automáticamente a tu Portapapeles (Universal Clipboard Mac/iPhone):")
                        except Exception:
                            print("   📋 Copy listo para pegar en TikTok:")
                        print(f"      {caption_tt}")
                return publish_id
            elif "FAILED" in txt:
                print(f"✖ TikTok reportó fallo al procesar el video: {txt}")
                return None

        print(f"ℹ Video transferido a tus Borradores de TikTok (ID: {publish_id}).")
        if caption_tt:
            try:
                subprocess.run(["pbcopy"], input=caption_tt.encode("utf-8"), check=True)
                print("   📋 Copy copiado automáticamente a tu Portapapeles (Universal Clipboard Mac/iPhone):")
            except Exception:
                print("   📋 Copy listo para pegar en TikTok:")
            print(f"      {caption_tt}")
        return publish_id
    except Exception as e:
        print(f"✖ Error subiendo a TikTok: {e}")
        return None

def subir_video_facebook(ruta_video, titulo=None, descripcion=None, publish_at_iso=None):
    """Sube y publica (o programa) un video directamente en la Página de Facebook vía Graph API."""
    if not os.path.exists(ruta_video):
        return None

    archivo_token = "token_meta.json"
    if not os.path.exists(archivo_token):
        return None

    try:
        with open(archivo_token, "r", encoding="utf-8") as f:
            d = json.load(f)
            token = d.get("facebook", {}).get("access_token") or d.get("access_token")
            page_id = d.get("facebook", {}).get("page_id") or d.get("page_id", "1158791430643856")

        if not token or not page_id:
            return None

        tam_mb = os.path.getsize(ruta_video) / (1024 * 1024)
        modo_str = f"PROGRAMADO ({formatear_hora_cst(publish_at_iso)})" if publish_at_iso else "PÚBLICO INMEDIATO"
        print(f"\n📘 Subiendo video a Facebook Page [{modo_str}]: '{os.path.basename(ruta_video)}' ({tam_mb:.1f} MB)...")

        url = f"https://graph-video.facebook.com/v19.0/{page_id}/videos"
        payload = {
            "access_token": token,
            "title": titulo or os.path.splitext(os.path.basename(ruta_video))[0],
            "description": descripcion or ""
        }

        programado_fb = False
        if publish_at_iso:
            from datetime import datetime, timezone
            try:
                dt_utc = datetime.strptime(publish_at_iso, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
                unix_ts = int(dt_utc.timestamp())
            except Exception:
                unix_ts = int(time.time()) + 3600
            # Meta Graph API requiere que scheduled_publish_time esté entre 10 min y 6 meses en el futuro.
            # Si faltan menos de 12 minutos, lo ajustamos a +15 minutos para NUNCA liberar de inmediato.
            if unix_ts < int(time.time()) + 720:
                unix_ts = int(time.time()) + 900
            payload["published"] = "false"
            payload["scheduled_publish_time"] = str(unix_ts)
            programado_fb = True

        with open(ruta_video, "rb") as f_vid:
            files = {"source": f_vid}
            r = requests.post(url, data=payload, files=files, timeout=300)

        if r.ok:
            res = r.json()
            vid_id = res.get("id")
            if programado_fb:
                print(f"✅ ¡Video subido y PROGRAMADO exitosamente en Facebook (NO es público todavía)! (ID: {vid_id})")
                print(f"   🕒 Se publicará automáticamente el: {formatear_hora_cst(publish_at_iso)}")
            else:
                print(f"✅ ¡Video publicado exitosamente en Facebook! (ID: {vid_id})")
            return vid_id
        else:
            print(f"✖ Error subiendo a Facebook ({r.status_code}): {r.text}")
            return None
    except Exception as e:
        print(f"✖ Error subiendo a Facebook: {e}")
        return None

def subir_video_instagram(ruta_video, titulo=None, descripcion=None, hashtags=None, publish_at_iso=None):
    """Sube y publica (o programa) un Reel en la cuenta de Instagram (@narrativasumergidas) vía Instagram Graph API.
    Si se especifica publish_at_iso en el futuro, deja el contenedor preparado y lanza un programador en segundo plano
    para que NO se libere de inmediato al público sino exactamente en la fecha/hora definida."""
    if not os.path.exists(ruta_video):
        return None

    archivo_token = "token_meta.json"
    if not os.path.exists(archivo_token):
        return None

    try:
        with open(archivo_token, "r", encoding="utf-8") as f:
            d = json.load(f)
            ig_cfg = d.get("instagram", {})
            token = ig_cfg.get("access_token") or os.getenv("IG_ACCESS_TOKEN")
            ig_id = ig_cfg.get("account_id") or os.getenv("META_IG_ACCOUNT_ID", "28838223979153102")

        if not token or not ig_id:
            return None

        tam_mb = os.path.getsize(ruta_video) / (1024 * 1024)
        nombre_base_mp4 = os.path.splitext(os.path.basename(ruta_video))[0]
        nombre_unico_remoto = f"{nombre_base_mp4}_{int(time.time())}.mp4"
        modo_str = f"PROGRAMADO ({formatear_hora_cst(publish_at_iso)})" if publish_at_iso else "PÚBLICO INMEDIATO"
        print(f"\n📸 Subiendo Reel a Instagram (@narrativasumergidas) [{modo_str}]: '{os.path.basename(ruta_video)}' ({tam_mb:.1f} MB)...")

        partes_caption = []
        if titulo:
            partes_caption.append(titulo.strip())
        if descripcion:
            partes_caption.append(descripcion.strip())
        if hashtags:
            partes_caption.append(hashtags.strip())
        caption = "\n\n".join(partes_caption) or nombre_base_mp4

        # Verificar si es publicación programada a futuro
        espera_segundos = 0
        if publish_at_iso:
            try:
                from datetime import datetime, timezone
                dt_utc = datetime.strptime(publish_at_iso, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
                espera_segundos = int(dt_utc.timestamp() - time.time())
            except Exception:
                espera_segundos = 3600

        # Si faltan más de 20 horas para la hora programada, guardar copia en .cola_programada_ig/ y programar subida completa
        if publish_at_iso and espera_segundos > 300:
            os.makedirs(".cola_programada_ig", exist_ok=True)
            ruta_cola_mp4 = os.path.abspath(os.path.join(".cola_programada_ig", nombre_unico_remoto))
            shutil.copy2(ruta_video, ruta_cola_mp4)

            # Guardar registro en cola_instagram_programada.json
            ruta_json_cola = "cola_instagram_programada.json"
            try:
                cola_actual = json.load(open(ruta_json_cola, encoding="utf-8")) if os.path.exists(ruta_json_cola) else []
            except Exception:
                cola_actual = []
            job_id = f"ig_sched_{int(time.time())}"
            cola_actual.append({
                "job_id": job_id,
                "archivo_mp4": ruta_cola_mp4,
                "titulo": titulo or "",
                "descripcion": descripcion or "",
                "hashtags": hashtags or "",
                "publish_at_iso": publish_at_iso,
                "estado": "programado"
            })
            with open(ruta_json_cola, "w", encoding="utf-8") as fc:
                json.dump(cola_actual, fc, ensure_ascii=False, indent=2)

            # Lanzar demonio ligero en segundo plano (en núcleos de eficiencia) que espera hasta publish_at_iso y publica
            script_bg = (
                f"import time, os, json; "
                f"time.sleep({max(60, espera_segundos)}); "
                f"from generador import subir_video_instagram; "
                f"res = subir_video_instagram({repr(ruta_cola_mp4)}, titulo={repr(titulo)}, descripcion={repr(descripcion)}, hashtags={repr(hashtags)}, publish_at_iso=None); "
                f"os.remove({repr(ruta_cola_mp4)}) if res and os.path.exists({repr(ruta_cola_mp4)}) else None"
            )
            cmd_bg = ["/usr/sbin/taskpolicy", "-b", "nice", "-n", "19", sys.executable, "-c", script_bg] if os.path.exists("/usr/sbin/taskpolicy") else [sys.executable, "-c", script_bg]
            subprocess.Popen(cmd_bg, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
            print(f"✅ ¡Reel de Instagram PROGRAMADO con éxito (NO se liberó al público ahora)! (Job: {job_id})")
            print(f"   🕒 Se publicará automáticamente en @narrativasumergidas el: {formatear_hora_cst(publish_at_iso)}")
            return job_id

        # 1. Generar URL directa temporal con nombre único para que Instagram nunca use caché de otro video
        with open(ruta_video, "rb") as vf:
            r_up = requests.post(
                "https://litterbox.catbox.moe/resources/internals/api.php",
                headers={"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)"},
                data={"reqtype": "fileupload", "time": "1h"},
                files={"fileToUpload": (nombre_unico_remoto, vf, "video/mp4")},
                timeout=180
            )
        video_url = r_up.text.strip()
        if not video_url.startswith("https://"):
            print(f"✖ No se pudo generar enlace temporal para Instagram: {video_url[:120]}")
            return None

        # 2. Crear contenedor de Reel en Instagram
        r_cont = requests.post(
            f"https://graph.instagram.com/v21.0/{ig_id}/media",
            data={
                "media_type": "REELS",
                "video_url": video_url,
                "caption": caption,
                "access_token": token
            },
            timeout=30
        )
        if not r_cont.ok:
            print(f"✖ Error creando contenedor en Instagram ({r_cont.status_code}): {r_cont.text}")
            return None

        cid = r_cont.json().get("id")
        print(f"   ⏳ Procesando Reel en Instagram (Container ID: {cid})...")
        for _ in range(30):
            time.sleep(5)
            r_st = requests.get(
                f"https://graph.instagram.com/v21.0/{cid}?fields=status_code,status&access_token={token}",
                timeout=15
            )
            st_data = r_st.json()
            sc = st_data.get("status_code")
            if sc == "FINISHED":
                r_pub = requests.post(
                    f"https://graph.instagram.com/v21.0/{ig_id}/media_publish",
                    data={"creation_id": cid, "access_token": token},
                    timeout=30
                )
                if r_pub.ok:
                    mid = r_pub.json().get("id")
                    r_link = requests.get(
                        f"https://graph.instagram.com/v21.0/{mid}?fields=id,permalink&access_token={token}",
                        timeout=15
                    )
                    permalink = r_link.json().get("permalink", "") if r_link.ok else ""
                    print(f"✅ ¡Reel publicado exitosamente en Instagram! (ID: {mid}) {permalink}")
                    return mid
                else:
                    print(f"✖ Error publicando Reel en Instagram ({r_pub.status_code}): {r_pub.text}")
                    return None
            elif sc == "ERROR":
                print(f"✖ Error transcodificando Reel en Instagram: {st_data}")
                return None

        print("⚠ Tiempo de espera agotado procesando el Reel en Instagram.")
        return None
    except Exception as e:
        print(f"✖ Error subiendo a Instagram: {e}")
        return None

def obtener_metricas_meta():
    """Consulta métricas de Facebook e Instagram (vistas, alcance, guardados, compartidos, likes, comentarios)
    si existen credenciales en token_meta.json o .env."""
    token_fb = os.getenv("META_ACCESS_TOKEN")
    token_ig = os.getenv("INSTAGRAM_ACCESS_TOKEN")
    page_id = os.getenv("META_PAGE_ID", "")
    ig_id = os.getenv("META_IG_ACCOUNT_ID", "")
    archivo_token = "token_meta.json"
    if os.path.exists(archivo_token):
        try:
            with open(archivo_token, "r", encoding="utf-8") as f:
                d = json.load(f)
                if not token_fb:
                    token_fb = d.get("facebook", {}).get("access_token") or d.get("access_token")
                if not token_ig:
                    token_ig = d.get("instagram", {}).get("access_token")
                if not page_id:
                    page_id = d.get("facebook", {}).get("page_id") or d.get("page_id", "")
                if not ig_id:
                    ig_id = d.get("instagram", {}).get("account_id") or d.get("ig_account_id", "")
        except Exception:
            pass

    if not token_fb and not token_ig:
        return None

    # Cargar caché previo de Facebook/Instagram por si un token temporal caducó
    cache_fb = []
    cache_ig = []
    if os.path.exists("metricas_canal.json"):
        try:
            with open("metricas_canal.json", "r", encoding="utf-8") as f:
                prev_meta = json.load(f).get("multiplataforma", {}).get("meta", {})
                if isinstance(prev_meta, dict):
                    cache_fb = prev_meta.get("facebook", [])
                    cache_ig = prev_meta.get("instagram", [])
        except Exception:
            pass

    reporte_meta = {"facebook": [], "instagram": []}

    try:
        # 1. Facebook Page Posts & Videos Insights
        if page_id and token_fb:
            vistas_videos_fb = {}
            try:
                r_vids = requests.get(
                    f"https://graph.facebook.com/v19.0/{page_id}/videos",
                    params={"access_token": token_fb, "fields": "id,title,description,views,created_time", "limit": 15},
                    timeout=15
                )
                if r_vids.ok:
                    for v in r_vids.json().get("data", []):
                        desc_k = (v.get("description") or v.get("title") or "")[:40].replace("\n", " ").strip()
                        if desc_k:
                            vistas_videos_fb[desc_k] = v.get("views", 0)
            except Exception:
                pass

            url_fb = f"https://graph.facebook.com/v19.0/{page_id}/published_posts"
            params = {
                "access_token": token_fb,
                "fields": "id,message,created_time,shares",
                "limit": 15
            }
            res = requests.get(url_fb, params=params, timeout=15)
            if res.ok:
                posts = res.json().get("data", [])
                for p in posts:
                    msg_limpio = (p.get("message") or "")[:60].replace("\n", " ")
                    clave_v = msg_limpio[:40].strip()
                    reporte_meta["facebook"].append({
                        "id": p.get("id"),
                        "texto": msg_limpio,
                        "fecha": p.get("created_time", "")[:10],
                        "compartidos": p.get("shares", {}).get("count", 0) if p.get("shares") else 0,
                        "vistas": vistas_videos_fb.get(clave_v, 0)
                    })
                reporte_meta["facebook"].sort(key=lambda x: (x.get("compartidos", 0), x.get("vistas", 0)), reverse=True)

        if not reporte_meta["facebook"] and cache_fb:
            reporte_meta["facebook"] = cache_fb

        # 2. Instagram Media + Insights (views, reach, saved, shares, total_interactions)
        if token_ig:
            url_ig = "https://graph.instagram.com/v21.0/me/media"
            params = {
                "access_token": token_ig,
                "fields": "id,caption,media_type,like_count,comments_count,timestamp,permalink",
                "limit": 15
            }
            res = requests.get(url_ig, params=params, timeout=15)
            if res.ok:
                media = res.json().get("data", [])
                for m in media:
                    mid = m.get("id")
                    ins_map = {}
                    try:
                        r_ins = requests.get(
                            f"https://graph.instagram.com/v21.0/{mid}/insights",
                            params={
                                "metric": "views,reach,saved,shares,total_interactions",
                                "access_token": token_ig
                            },
                            timeout=10
                        )
                        if r_ins.ok:
                            for met in r_ins.json().get("data", []):
                                ins_map[met.get("name")] = met.get("values", [{}])[0].get("value", 0)
                    except Exception:
                        pass

                    reporte_meta["instagram"].append({
                        "id": mid,
                        "caption": (m.get("caption") or "")[:60].replace("\n", " "),
                        "vistas": ins_map.get("views", 0),
                        "alcance": ins_map.get("reach", 0),
                        "likes": m.get("like_count", 0),
                        "comentarios": m.get("comments_count", 0),
                        "guardados": ins_map.get("saved", 0),
                        "compartidos": ins_map.get("shares", 0),
                        "interacciones": ins_map.get("total_interactions", m.get("like_count", 0) + m.get("comments_count", 0)),
                        "fecha": m.get("timestamp", "")[:10]
                    })
                reporte_meta["instagram"].sort(
                    key=lambda x: (x.get("vistas", 0), x.get("guardados", 0), x.get("likes", 0)),
                    reverse=True
                )
        elif ig_id and token_fb:
            url_ig = f"https://graph.facebook.com/v19.0/{ig_id}/media"
            params = {
                "access_token": token_fb,
                "fields": "id,caption,media_type,like_count,comments_count,timestamp",
                "limit": 10
            }
            res = requests.get(url_ig, params=params, timeout=15)
            if res.ok:
                media = res.json().get("data", [])
                for m in media:
                    reporte_meta["instagram"].append({
                        "id": m.get("id"),
                        "caption": (m.get("caption") or "")[:60].replace("\n", " "),
                        "vistas": 0,
                        "alcance": 0,
                        "likes": m.get("like_count", 0),
                        "comentarios": m.get("comments_count", 0),
                        "guardados": 0,
                        "compartidos": 0,
                        "fecha": m.get("timestamp", "")[:10]
                    })

        if not reporte_meta["instagram"] and cache_ig:
            reporte_meta["instagram"] = cache_ig

        return reporte_meta
    except Exception as e:
        print(f"   ℹ Meta API no disponible o expiró el token: {e}")
        if cache_fb or cache_ig:
            return {"facebook": cache_fb, "instagram": cache_ig}
        return None

def obtener_metricas_tiktok():
    """Consulta métricas de TikTok si existe token en token_tiktok.json o .env."""
    token_tiktok = os.getenv("TIKTOK_ACCESS_TOKEN")
    archivo_token = "token_tiktok.json"
    if os.path.exists(archivo_token) and not token_tiktok:
        try:
            with open(archivo_token, "r", encoding="utf-8") as f:
                d = json.load(f)
                token_tiktok = d.get("access_token")
        except Exception:
            pass

    if not token_tiktok:
        return None

    try:
        # TikTok Display / Creator API
        url = "https://open.tiktokapis.com/v2/video/list/?fields=id,title,video_description,like_count,comment_count,share_count,view_count"
        headers = {
            "Authorization": f"Bearer {token_tiktok}",
            "Content-Type": "application/json"
        }
        body = {"max_count": 15}
        res = requests.post(url, headers=headers, json=body, timeout=15)
        if res.ok:
            data = res.json().get("data", {}).get("videos", [])
            reporte_tt = []
            for v in data:
                tit = v.get("title") or v.get("video_description") or ""
                reporte_tt.append({
                    "id": v.get("id"),
                    "titulo": tit[:50],
                    "vistas": v.get("view_count", 0),
                    "likes": v.get("like_count", 0),
                    "comentarios": v.get("comment_count", 0),
                    "compartidos": v.get("share_count", 0)
                })
            reporte_tt.sort(key=lambda x: x["vistas"], reverse=True)
            return reporte_tt
    except Exception as e:
        print(f"   ℹ TikTok API no disponible o expiró el token: {e}")
    return None

def analizar_rendimiento_canal(max_videos=15):
    """Analiza las métricas reales de los videos de YouTube Shorts, TikTok, Instagram y Facebook,
    calcula patrones ganadores y actualiza el contexto para retroalimentar a Claude."""
    service = obtener_servicio_youtube()
    if not service:
        print("✖ No se pudo conectar a YouTube para extraer métricas.")
        return None

    print(f"\n📊 Analizando métricas de las 4 redes sociales de Narrativas Sumergidas...")
    try:
        res = service.search().list(channelId='UCATQH4An6atNNAtZYXMNkoQ', part='snippet',
                                    maxResults=max_videos, order='date', type='video').execute()
        items = res.get('items', [])
        if not items:
            print("No se encontraron videos en el canal.")
            return None

        vids = [it['id']['videoId'] for it in items]
        detalles = service.videos().list(id=','.join(vids), part='snippet,statistics,contentDetails').execute().get('items', [])

        reporte = []
        for v in detalles:
            stats = v.get('statistics', {})
            snip = v.get('snippet', {})
            vistas = int(stats.get('viewCount', 0))
            likes = int(stats.get('likeCount', 0))
            comentarios = int(stats.get('commentCount', 0))
            reporte.append({
                "id": v['id'],
                "titulo": snip.get('title', ''),
                "vistas": vistas,
                "likes": likes,
                "comentarios": comentarios,
                "ratio_engagement": round((likes + comentarios) / max(1, vistas) * 100, 2),
                "publicado": snip.get('publishedAt', '')[:10]
            })

        reporte.sort(key=lambda x: x["vistas"], reverse=True)

        print("\n" + "═" * 70)
        print("📈 1) REPORTE DE RENDIMIENTO EN YOUTUBE SHORTS")
        print("═" * 70)
        print(f"{'Vistas':<8} | {'Likes':<6} | {'Engage%':<8} | Título")
        print("─" * 70)
        for r in reporte:
            print(f"{r['vistas']:<8} | {r['likes']:<6} | {r['ratio_engagement']:<7}% | {r['titulo'][:45]}")
        print("═" * 70)

        tops = [r for r in reporte if r["vistas"] >= 500]
        medios = [r for r in reporte if 100 <= r["vistas"] < 500]
        bajos = [r for r in reporte if r["vistas"] < 100]

        print(f"\n💡 CONCLUSIONES ALGORÍTMICAS (YOUTUBE):")
        print(f"   🔥 Éxitos virales (≥500 vistas): {len(tops)} videos")
        for t in tops: print(f"      ✔ '{t['titulo']}' ({t['vistas']} vistas)")

        print(f"   ⚡ Rendimiento medio (100-500 vistas): {len(medios)} videos")
        print(f"   ⚠️ Bajo rendimiento (<100 vistas): {len(bajos)} videos")
        for b in bajos: print(f"      ✖ '{b['titulo']}' ({b['vistas']} vistas)")

        # Chequeo multi-plataforma (Meta & TikTok)
        meta_data = obtener_metricas_meta()
        tt_data = obtener_metricas_tiktok()

        if tt_data:
            print("\n" + "═" * 78)
            print("🎵 2) REPORTE DE RENDIMIENTO EN TIKTOK")
            print("═" * 78)
            print(f"{'Vistas':<9} | {'Likes':<6} | {'Shares':<7} | {'Coment':<6} | Título")
            print("─" * 78)
            for v in tt_data[:8]:
                print(f"{v['vistas']:<9} | {v['likes']:<6} | {v.get('compartidos', 0):<7} | {v['comentarios']:<6} | {v['titulo'][:40]}")
            print("═" * 78)

        if meta_data and meta_data.get("instagram"):
            print("\n" + "═" * 82)
            print("📸 3) REPORTE DE RENDIMIENTO EN INSTAGRAM (@narrativasumergidas)")
            print("═" * 82)
            print(f"{'Vistas':<7} | {'Alcance':<7} | {'Likes':<5} | {'Guard':<5} | {'Shares':<6} | Publicación")
            print("─" * 82)
            for ig_it in meta_data["instagram"][:10]:
                print(f"{ig_it.get('vistas', 0):<7} | {ig_it.get('alcance', 0):<7} | {ig_it.get('likes', 0):<5} | {ig_it.get('guardados', 0):<5} | {ig_it.get('compartidos', 0):<6} | {ig_it.get('caption', '')[:38]}")
            print("═" * 82)

        if meta_data and meta_data.get("facebook"):
            print("\n" + "═" * 78)
            print("📘 4) REPORTE DE RENDIMIENTO EN FACEBOOK (Narrativas Sumergidas)")
            print("═" * 78)
            print(f"{'Shares':<8} | {'Fecha':<10} | Publicación")
            print("─" * 78)
            for fb_it in meta_data["facebook"][:8]:
                print(f"{fb_it.get('compartidos', 0):<8} | {fb_it.get('fecha', ''):<10} | {fb_it.get('texto', '')[:50]}")
            print("═" * 78)

        print("\n🌐 ESTADO DE CONEXIÓN MULTIPLATAFORMA:")
        if meta_data:
            print(f"   ✔ Meta conectado: FB ({len(meta_data.get('facebook', []))} posts), IG ({len(meta_data.get('instagram', []))} media con insights)")
        else:
            print("   ℹ Meta (Facebook / Instagram): Sin token configurado")

        if tt_data:
            print(f"   ✔ TikTok conectado: {len(tt_data)} videos analizados")
        else:
            print("   ℹ TikTok: Sin token configurado")

        resumen_analisis = {
            "fecha_analisis": time.strftime("%Y-%m-%d %H:%M:%S"),
            "total_videos_analizados": len(reporte),
            "promedio_vistas": round(sum(r["vistas"] for r in reporte) / max(1, len(reporte)), 1),
            "todos_youtube": reporte,
            "top_videos": tops,
            "videos_medios": medios,
            "videos_bajos": bajos,
            "multiplataforma": {
                "meta": meta_data if meta_data else "no_configurado",
                "tiktok": tt_data if tt_data else "no_configurado"
            }
        }

        # Optimizar los 3 prompts maestros usando Claude con las métricas reales de las 4 redes
        optimizacion_ia = optimizar_prompts_con_ia(resumen_analisis)
        if optimizacion_ia:
            resumen_analisis["optimizacion_prompts_ia"] = optimizacion_ia

        with open("metricas_canal.json", "w", encoding="utf-8") as f:
            json.dump(resumen_analisis, f, ensure_ascii=False, indent=2)
        print("\n✔ Archivo 'metricas_canal.json' y Prompts Maestros actualizados con las estadísticas de las 4 redes.")
        return resumen_analisis
    except Exception as e:
        print(f"✖ Error analizando el canal: {e}")
        return None

def _inyectar_bloque_calibracion(ruta_prompt, titulo_bloque, lineas_reglas, resumen_stats):
    """Actualiza o añade un bloque de calibración dinámica basado en métricas reales al final de un archivo prompt_*.md."""
    if not os.path.exists(ruta_prompt):
        return False
    try:
        with open(ruta_prompt, "r", encoding="utf-8") as f:
            contenido = f.read()

        marca_ini = "═══ 📊 CALIBRACIÓN DINÁMICA MULTIRRED (AUTO-OPTIMIZADA POR IA) ═══"
        marca_fin = "═══ FIN CALIBRACIÓN DINÁMICA MULTIRRED ═══"

        bloque = f"\n{marca_ini}\n"
        bloque += f"Última actualización de métricas reales (YT + TikTok + IG + FB): { time.strftime('%Y-%m-%d %H:%M') }\n"
        if resumen_stats:
            bloque += f"{resumen_stats}\n"
        bloque += f"\n🎯 DIRECTRICES DE MEJORA CONTINUA ({titulo_bloque}):\n"
        for i, reg in enumerate(lineas_reglas, 1):
            bloque += f"{i}. {reg}\n"
        bloque += f"{marca_fin}\n"

        if marca_ini in contenido and marca_fin in contenido:
            patron = re.compile(re.escape(marca_ini) + r".*?" + re.escape(marca_fin) + r"\n?", re.DOTALL)
            nuevo_contenido = patron.sub(bloque.lstrip("\n"), contenido)
        else:
            # Insertar justo antes de '═══ SALIDA JSON' o 'Salida JSON estricta:' para no romper el cierre JSON
            for ancla in ("═══ SALIDA JSON ESTRICTA ═══", "Salida JSON estricta:"):
                if ancla in contenido:
                    nuevo_contenido = contenido.replace(ancla, bloque.lstrip("\n") + "\n" + ancla, 1)
                    break
            else:
                nuevo_contenido = contenido.rstrip() + "\n" + bloque

        with open(ruta_prompt, "w", encoding="utf-8") as f:
            f.write(nuevo_contenido)
        return True
    except Exception as e:
        print(f"   ⚠ No se pudo actualizar {ruta_prompt}: {e}")
        return False

def optimizar_prompts_con_ia(resumen_analisis):
    """Usa Claude para analizar las métricas reales de las 4 redes (YouTube, TikTok, Instagram, Facebook)
    y mejora automáticamente prompt_guion.md, prompt_escenas.md y prompt_historia_foto.md."""
    print("\n🧠 Analizando métricas de las 4 redes con Claude para optimizar los Prompts y mejorar la calidad de los videos...")
    try:
        yt_top = resumen_analisis.get("top_videos", [])[:5]
        yt_bajos = resumen_analisis.get("videos_bajos", [])[:4]
        multi = resumen_analisis.get("multiplataforma", {})
        tt_list = (multi.get("tiktok") or [])[:6] if isinstance(multi.get("tiktok"), list) else []
        meta = multi.get("meta") if isinstance(multi.get("meta"), dict) else {}
        ig_list = (meta.get("instagram") or [])[:6]
        fb_list = (meta.get("facebook") or [])[:6]

        resumen_stats_lineas = []
        if yt_top:
            resumen_stats_lineas.append(
                "- YouTube Top: " + " | ".join(f"'{v['titulo'][:35]}' ({v['vistas']} vistas, {v['ratio_engagement']}% eng)" for v in yt_top[:3])
            )
        if tt_list:
            resumen_stats_lineas.append(
                "- TikTok Top: " + " | ".join(f"'{v['titulo'][:35]}' ({v['vistas']} vistas, {v['likes']} likes, {v.get('compartidos',0)} shares)" for v in tt_list[:3])
            )
        if ig_list:
            resumen_stats_lineas.append(
                "- Instagram Top: " + " | ".join(f"'{v.get('caption','')[:30]}' ({v.get('vistas',0)} vistas, {v.get('guardados',0)} guardados, {v.get('compartidos',0)} shares)" for v in ig_list[:3])
            )
        if fb_list:
            resumen_stats_lineas.append(
                "- Facebook Top: " + " | ".join(f"'{v.get('texto','')[:30]}' ({v.get('compartidos',0)} compartidos)" for v in fb_list[:3])
            )
        resumen_stats_txt = "\n".join(resumen_stats_lineas)

        prompt_sys = (
            "Eres el Director de Estrategia Algorítmica y Calidad Cinematográfica de 'Narrativas Sumergidas'. "
            "Analiza las estadísticas reales de las 4 redes sociales del canal (YouTube Shorts, TikTok, Instagram Reels y Facebook Reels) "
            "y genera reglas ultra-específicas, accionables y medibles para mejorar la calidad de los futuros videos en 3 áreas: "
            "1) Guion, retención hablada y metadata por red, 2) Dirección de arte visual SDXL, iluminación, movimientos de cámara y portadas, "
            "y 3) Historias fotográficas (carruseles).\n"
            "REGLAS INQUEBRANTABLES DEL FORMATO DEL CANAL (NO CONTRADECIR NUNCA):\n"
            "- Los videos son minidocumentales inmersivos de 85 a 115 segundos (215-280 palabras; los mayores éxitos del canal como La Casa de las Brujas [113s], Lecumberri [86s], Winchester [87s] y La Vampira están en ese rango).\n"
            "- La cuota visual es de 18 a 25 escenas (~4.5 segundos por imagen).\n"
            "- En SDXL: 'no humans, empty scene' para arquitectura/objetos, 'negative space at top' cuando lleva texto, desgaste contextual de época en retratos, y cero estilos en los prompts individuales (solo en estilo_visual_global).\n"
            "Responde ÚNICAMENTE con JSON válido."
        )
        mensaje_usr = (
            f"ESTADÍSTICAS REALES DE LAS 4 REDES:\n"
            f"- YOUTUBE SHORTS TOP: {json.dumps(yt_top, ensure_ascii=False)}\n"
            f"- YOUTUBE SHORTS BAJOS: {json.dumps(yt_bajos, ensure_ascii=False)}\n"
            f"- TIKTOK (@narrativasumergidas): {json.dumps(tt_list, ensure_ascii=False)}\n"
            f"- INSTAGRAM (@narrativasumergidas): {json.dumps(ig_list, ensure_ascii=False)}\n"
            f"- FACEBOOK (Narrativas Sumergidas): {json.dumps(fb_list, ensure_ascii=False)}\n\n"
            "Devuelve exactamente este formato JSON:\n"
            "{\n"
            '  "diagnostico_global": "Resumen ejecutivo de 2 líneas sobre qué está funcionando mejor en las 4 redes",\n'
            '  "reglas_guion": [\n'
            '    "Regla 1 para mejorar el gancho 0-3s y el loop abierto basada en los videos más vistos (La Vampira 31.2k en TikTok / 1.2k en YT, Casa de las Brujas 1k en YT)",\n'
            '    "Regla 2 sobre temáticas ganadoras, lugares físicos reales en México y mantenimiento del ritmo en los 85-115s (215-280 palabras) con re-gancho a mitad",\n'
            '    "Regla 3 sobre estructura de revelaciones cada ~5s y cierre con pregunta dicotómica A/B para comentarios",\n'
            '    "Regla 4 sobre optimización de títulos y copys para YT (SEO documental), TikTok (shock/curiosidad), IG (dato guardable) y FB (ancla histórica + debate moral para compartidos)"\n'
            "  ],\n"
            '  "reglas_escenas": [\n'
            '    "Regla 1 para la Escena 1 (01_gancho.png) y retención visual en los primeros 3 segundos",\n'
            '    "Regla 2 sobre estética arquitectónica/folclórica e iluminación cinematográfica que maximiza guardados en IG y vistas en YT (respetando reglas SDXL)",\n'
            '    "Regla 3 sobre alternancia de planos, movimientos de cámara (zoom_in, tilt_up, pan_left/right, slow_drift) y transiciones (crossfade, fade_black, cut) a lo largo de las 18-25 escenas (~4.5s/escena)",\n'
            '    "Regla 4 sobre diseño de Portada A (YT/IG arquitectura) y Portada B (TikTok/FB rostro desgastado o detalle perturbador) para elevar el CTR"\n'
            "  ],\n"
            '  "reglas_foto": [\n'
            '    "Regla 1 para la Diapositiva 1 y retención de deslizamiento en IG y FB",\n'
            '    "Regla 2 para maximizar Guardados en Instagram y Compartidos en Facebook",\n'
            '    "Regla 3 sobre dilemas morales y encuestas A/B para comentarios"\n'
            "  ]\n"
            "}"
        )

        resultado = llamar_claude(prompt_sys, mensaje_usr, max_tokens=16000, reintentos=2)
        if not resultado or not isinstance(resultado, dict):
            return None

        reglas_guion = resultado.get("reglas_guion", [])
        reglas_escenas = resultado.get("reglas_escenas", [])
        reglas_foto = resultado.get("reglas_foto", [])

        if reglas_guion:
            _inyectar_bloque_calibracion("prompt_guion.md", "GUION, RETENCIÓN Y METADATA 4 REDES", reglas_guion, resumen_stats_txt)
            print("   ✔ 'prompt_guion.md' mejorado con las estadísticas de las 4 redes.")
        if reglas_escenas:
            _inyectar_bloque_calibracion("prompt_escenas.md", "DIRECCIÓN DE ARTE, ESCENAS SDXL Y PORTADAS", reglas_escenas, resumen_stats_txt)
            print("   ✔ 'prompt_escenas.md' mejorado con directrices visuales basadas en retención real.")
        if reglas_foto:
            _inyectar_bloque_calibracion("prompt_historia_foto.md", "CARRUSELES E HISTORIAS FOTOGRÁFICAS", reglas_foto, resumen_stats_txt)
            print("   ✔ 'prompt_historia_foto.md' mejorado con métricas de guardados y compartidos.")

        if resultado.get("diagnostico_global"):
            print(f"\n🎯 Diagnóstico IA Multiplataforma:\n   {resultado['diagnostico_global']}")

        return resultado
    except Exception as e:
        print(f"   ⚠ Error en optimización automática de prompts con IA: {e}")
        return None

def obtener_feedback_metricas(modo="general"):
    """Lee metricas_canal.json si existe y construye directrices para Claude
    basadas en las métricas reales de las 4 redes: YouTube Shorts, TikTok, Instagram y Facebook."""
    if not os.path.exists("metricas_canal.json"):
        return ""
    try:
        with open("metricas_canal.json", "r", encoding="utf-8") as f:
            data = json.load(f)
        top = data.get("top_videos", [])
        bajos = data.get("videos_bajos", [])
        multi = data.get("multiplataforma", {})
        tt = multi.get("tiktok", []) if isinstance(multi, dict) else []
        meta = multi.get("meta", {}) if isinstance(multi, dict) else {}
        ig_list = meta.get("instagram", []) if isinstance(meta, dict) else []
        fb_list = meta.get("facebook", []) if isinstance(meta, dict) else []
        opt_ia = data.get("optimizacion_prompts_ia", {})

        if not top and not bajos and not tt and not ig_list and not fb_list:
            return ""

        feedback = "\n\n📊 RETROALIMENTACIÓN DE AUDIENCIA REAL (MÉTRICAS EN VIVO DE LAS 4 REDES):\n"
        feedback += "El sistema analizó el rendimiento real de los contenidos publicados en YouTube Shorts, TikTok, Instagram y Facebook:\n"

        if top:
            feedback += "\n1️⃣ YOUTUBE SHORTS — PATRONES GANADORES (≥500-1,250 vistas):\n"
            for t in top[:4]:
                feedback += f"  - '{t.get('titulo')}' ({t.get('vistas')} vistas, {t.get('ratio_engagement')}% engagement)\n"
            feedback += "  -> Clave YouTube: Ángulo documental/histórico riguroso, arquitectura maldita reconocible (Roma, Lecumberri, Ciudad Madero, Winchester) y misterios mexicanos.\n"

        if bajos:
            feedback += "⚠️ YOUTUBE SHORTS — QUÉ EVITAR (<100 vistas):\n"
            for b in bajos[:3]:
                feedback += f"  - '{b.get('titulo')}' ({b.get('vistas')} vistas)\n"
            feedback += "  -> Clave a evitar: Desastres técnicos extranjeros sin elemento sobrenatural o títulos genéricos sin curiosidad inmediata.\n"

        if isinstance(tt, list) and tt:
            feedback += "\n2️⃣ TIKTOK — HITS VIRALES Y RETENCIÓN:\n"
            for t_item in tt[:4]:
                feedback += f"  - '{t_item.get('titulo')}' ({t_item.get('vistas'):,} vistas, {t_item.get('likes'):,} likes, {t_item.get('compartidos', 0):,} compartidos, {t_item.get('comentarios', 0)} comentarios)\n"
            feedback += "  -> Clave TikTok: Gancho de shock visual en los primeros 2 segundos, leyendas locales con nombre de iglesia/colonia real y llamada a compartir.\n"

        if isinstance(ig_list, list) and ig_list:
            feedback += "\n3️⃣ INSTAGRAM (@narrativasumergidas) — ALCANCE, GUARDADOS Y COMPARTIDOS:\n"
            ig_sorted = sorted(
                ig_list,
                key=lambda x: (x.get("vistas", 0), x.get("guardados", 0), x.get("likes", 0)),
                reverse=True
            )
            for ig_item in ig_sorted[:4]:
                feedback += (
                    f"  - '{ig_item.get('caption')}' ({ig_item.get('vistas', 0)} reproducciones, "
                    f"alcance {ig_item.get('alcance', 0)}, {ig_item.get('likes', 0)} likes, "
                    f"{ig_item.get('guardados', 0)} guardados, {ig_item.get('compartidos', 0)} compartidos)\n"
                )
            feedback += "  -> Clave Instagram: Estética gótica/arquitectónica ('La Casa de las Brujas', 'Maximiliano', 'La Vampira', 'Lecumberri') y enfoque de 'dato guardable para visitar' que maximiza los Guardados (Saves) y Compartidos por DM.\n"

        if isinstance(fb_list, list) and fb_list:
            feedback += "\n4️⃣ FACEBOOK REELS & POSTS (Narrativas Sumergidas) — VIRALIDAD POR COMPARTIDOS:\n"
            fb_sorted = sorted(
                fb_list,
                key=lambda x: (x.get("compartidos", 0), x.get("vistas", 0)),
                reverse=True
            )
            for fb_item in fb_sorted[:4]:
                feedback += f"  - '{fb_item.get('texto')}' ({fb_item.get('compartidos', 0)} compartidos, fecha {fb_item.get('fecha', '')})\n"
            feedback += "  -> Clave Facebook: Datos históricos insólitos o escandalosos, leyendas urbanas con dilema moral y preguntas dicotómicas A/B que provoquen debate en comentarios y compartidos masivos.\n"

        if isinstance(opt_ia, dict) and opt_ia:
            if modo == "escenas" and opt_ia.get("reglas_escenas"):
                feedback += "\n🎨 REGLAS DE DIRECCIÓN DE ARTE APRENDIDAS DE LAS MÉTRICAS:\n"
                for r in opt_ia["reglas_escenas"]:
                    feedback += f"  - {r}\n"
            elif modo == "foto" and opt_ia.get("reglas_foto"):
                feedback += "\n📸 REGLAS DE CARRUSELES APRENDIDAS DE LAS MÉTRICAS:\n"
                for r in opt_ia["reglas_foto"]:
                    feedback += f"  - {r}\n"
            else:
                if opt_ia.get("diagnostico_global"):
                    feedback += f"\n🧠 DIAGNÓSTICO IA MULTIRRED: {opt_ia['diagnostico_global']}\n"
                if opt_ia.get("reglas_guion"):
                    feedback += "✍️ REGLAS DE GUION APRENDIDAS DE LAS MÉTRICAS:\n"
                    for r in opt_ia["reglas_guion"]:
                        feedback += f"  - {r}\n"
                if opt_ia.get("reglas_escenas") and modo == "general":
                    feedback += "🎨 REGLAS VISUALES APRENDIDAS DE LAS MÉTRICAS:\n"
                    for r in opt_ia["reglas_escenas"]:
                        feedback += f"  - {r}\n"

        feedback += "\n👉 INSTRUCCIÓN OBLIGATORIA: Aplica estas métricas reales de las 4 plataformas para elevar la calidad narrativa y visual del video.\n"
        return feedback
    except Exception:
        return ""

def mostrar_estado_conexiones():
    """Muestra el estado detallado de conexión con cada plataforma."""
    print("\n" + "═" * 65)
    print("🌐 ESTADO DE CONEXIONES Y CUENTAS OFICIALES")
    print("═" * 65)

    # YouTube
    if os.path.exists("token_youtube.json"):
        try:
            srv = obtener_servicio_youtube()
            if srv:
                ch = srv.channels().list(mine=True, part='snippet,statistics').execute().get('items', [])
                if ch:
                    tit = ch[0]['snippet']['title']
                    subs = ch[0]['statistics'].get('subscriberCount', '0')
                    vids = ch[0]['statistics'].get('videoCount', '0')
                    print(f"  ✅ YouTube Shorts: CONECTADO")
                    print(f"     Canal: '{tit}' | {subs} suscriptores | {vids} videos")
                else:
                    print(f"  ✅ YouTube Shorts: CONECTADO (token_youtube.json)")
            else:
                print(f"  ⚠️ YouTube Shorts: token_youtube.json existe pero falló la conexión")
        except Exception as e:
            print(f"  ⚠️ YouTube Shorts: error verificando token ({e})")
    else:
        print("  ✖ YouTube Shorts: No conectado (falta token_youtube.json)")

    # Google Drive
    if os.path.exists("token.json"):
        print("  ✅ Google Drive: CONECTADO (token.json activo, subida y respaldo autónomo)")
    else:
        print("  ✖ Google Drive: No conectado")

    # TikTok
    token_tt = os.getenv("TIKTOK_ACCESS_TOKEN")
    if os.path.exists("token_tiktok.json"):
        try:
            with open("token_tiktok.json", encoding="utf-8") as f:
                d = json.load(f)
                token_tt = d.get("access_token") or d.get("data", {}).get("access_token", token_tt)
        except: pass

    if token_tt:
        try:
            r_u = requests.get("https://open.tiktokapis.com/v2/user/info/?fields=avatar_url,display_name",
                               headers={"Authorization": f"Bearer {token_tt}"}, timeout=8)
            nom = r_u.json().get("data", {}).get("user", {}).get("display_name") if r_u.ok else None
            if nom:
                print(f"  ✅ TikTok: CONECTADO (Cuenta Oficial: '{nom}')")
            else:
                print(f"  ✅ TikTok: CONECTADO (token_tiktok.json activo)")
        except:
            print(f"  ✅ TikTok: CONECTADO (token_tiktok.json activo)")
    else:
        print("  ⏳ TikTok: Pendiente de conectar (Opción 4 en este menú)")

    # Meta (FB / IG)
    token_m = os.getenv("META_ACCESS_TOKEN")
    token_ig = os.getenv("IG_ACCESS_TOKEN")
    page_nombre = None
    page_seguidores = 0
    ig_username = None
    ig_media_count = 0
    if os.path.exists("token_meta.json"):
        try:
            with open("token_meta.json", encoding="utf-8") as f:
                d = json.load(f)
                token_m = d.get("facebook", {}).get("access_token") or d.get("access_token", token_m)
                p_id = d.get("facebook", {}).get("page_id") or d.get("page_id", "1158791430643856")
                token_ig = d.get("instagram", {}).get("access_token") or token_ig
                if token_m and p_id:
                    r_p = requests.get(f"https://graph.facebook.com/v19.0/{p_id}?fields=name,followers_count&access_token={token_m}", timeout=6)
                    if r_p.ok:
                        dt = r_p.json()
                        page_nombre = dt.get("name")
                        page_seguidores = dt.get("followers_count", 0)
                if token_ig:
                    r_ig = requests.get(f"https://graph.instagram.com/v19.0/me?fields=username,media_count&access_token={token_ig}", timeout=6)
                    if r_ig.ok:
                        dt_ig = r_ig.json()
                        ig_username = dt_ig.get("username")
                        ig_media_count = dt_ig.get("media_count", 0)
        except: pass

    if page_nombre:
        print(f"  ✅ Facebook: CONECTADO (Página: '{page_nombre}' | {page_seguidores} seguidores)")
    elif token_m:
        print(f"  ✅ Facebook: CONECTADO (Token configurado)")
    else:
        print("  ⏳ Facebook: Pendiente de conectar (Opción 6 en este menú)")

    if ig_username:
        print(f"  ✅ Instagram: CONECTADO (@{ig_username} | {ig_media_count} publicaciones)")
    elif token_ig:
        print(f"  ✅ Instagram: CONECTADO (Token configurado)")
    else:
        print("  ⏳ Instagram: Pendiente de conectar (Opción 6 en este menú)")
    print("═" * 65)

def mostrar_patrones_ia():
    """Muestra qué patrones de audiencia está aplicando Claude actualmente."""
    fb = obtener_feedback_metricas()
    if fb:
        print("\n" + "═" * 70)
        print("🧠 CONOCIMIENTO ACTUAL APRENDIDO POR LA IA:")
        print(fb)
        print("═" * 70)
    else:
        print("\nℹ Aún no hay métricas guardadas. Ejecuta la opción 1 para analizar el canal.")

def conectar_o_probar_tiktok():
    """Asistente interactivo para conectar o probar la API de TikTok."""
    print("\n" + "═" * 65)
    print("🎵 ASISTENTE DE CONEXIÓN A TIKTOK")
    print("═" * 65)
    token_actual = os.getenv("TIKTOK_ACCESS_TOKEN")
    archivo_token = "token_tiktok.json"
    if os.path.exists(archivo_token):
        try:
            with open(archivo_token, encoding="utf-8") as f:
                d = json.load(f)
                token_actual = d.get("access_token") or d.get("data", {}).get("access_token")
        except: pass

    if token_actual:
        print(f"✔ Token actual detectado: {token_actual[:12]}...{token_actual[-6:]}")
        print("\n¿Qué deseas hacer?")
        print("  1) Probar conexión actual con la API de TikTok")
        print("  2) Re-autenticar con tu cuenta (Iniciar sesión en TikTok)")
        print("  3) Pegar un nuevo Access Token manualmente")
        print("  0) Volver")
        op = input("Opción [1]: ").strip() or "1"
        if op == "0": return
        if op == "1":
            print("\n🔄 Probando conexión a TikTok API...")
            try:
                url = "https://open.tiktokapis.com/v2/user/info/?fields=avatar_url,display_name"
                h = {"Authorization": f"Bearer {token_actual}"}
                r = requests.get(url, headers=h, timeout=10)
                if r.ok:
                    data = r.json().get("data", {}).get("user", {})
                    print(f"✅ ¡Conexión exitosa con TikTok!")
                    print(f"   Usuario: {data.get('display_name', 'Creador')}")
                else:
                    print(f"✖ TikTok respondió con error ({r.status_code}): {r.text[:200]}")
            except Exception as e:
                print(f"✖ Error conectando a TikTok: {e}")
            return
        elif op == "3":
            nuevo_token = input("\nPega aquí tu Access Token de TikTok (o ENTER para cancelar): ").strip()
            if nuevo_token:
                with open("token_tiktok.json", "w", encoding="utf-8") as f:
                    json.dump({"access_token": nuevo_token}, f, indent=2)
                print("✔ Guardado en 'token_tiktok.json'.")
            return

    # Flujo OAuth con PKCE (code_challenge requerido por TikTok API v2)
    import hashlib
    client_key = os.getenv("TIKTOK_CLIENT_KEY", "sbawaqizbhzrfa2vz5")
    client_secret = os.getenv("TIKTOK_CLIENT_SECRET", "OvSs9yhXEfTVFVj9yCoRNntCC06NO9Rk")
    redirect_uri = os.getenv("TIKTOK_REDIRECT_URI", "https://localhost:8080/callback")
    scopes = "user.info.basic,user.info.stats,video.list,video.upload,video.publish"
    state = str(int(time.time()))

    verifier = "narrativas_sumergidas_tiktok_oauth_verification_token_m4_2026"
    h = hashlib.sha256(verifier.encode("utf-8")).digest()
    code_challenge = h.hex()

    auth_url = (
        f"https://www.tiktok.com/v2/auth/authorize/"
        f"?client_key={client_key}"
        f"&scope={scopes}"
        f"&response_type=code"
        f"&redirect_uri={redirect_uri}"
        f"&state={state}"
        f"&code_challenge={code_challenge}"
        f"&code_challenge_method=S256"
    )

    print("\n🔗 PASO 1: Abre el siguiente enlace en tu navegador para autorizar la app:")
    print("─" * 65)
    print(auth_url)
    print("─" * 65)
    print("\nPASO 2: Inicia sesión con la cuenta de Narrativas Sumergidas y haz clic en 'Autorizar'.")
    print("El navegador se redirigirá a una dirección que empieza con: https://localhost:8080/callback?code=...")
    print("(Aunque la página diga 'No se puede acceder al sitio', copia toda la URL de la barra del navegador).")

    resp_url = input("\nPega aquí la URL completa a la que te redirigió (o el código): ").strip()
    if not resp_url:
        print("Operación cancelada.")
        return

    codigo = resp_url
    if "code=" in resp_url:
        m = re.search(r"code=([^&]+)", resp_url)
        if m: codigo = m.group(1)

    print(f"\n🔄 Canjeando código de autorización por Token de acceso...")
    try:
        url_token = "https://open.tiktokapis.com/v2/oauth/token/"
        headers = {"Content-Type": "application/x-www-form-urlencoded"}
        datos = {
            "client_key": client_key,
            "client_secret": client_secret,
            "code": codigo,
            "grant_type": "authorization_code",
            "redirect_uri": redirect_uri,
            "code_verifier": verifier
        }
        res = requests.post(url_token, data=datos, headers=headers, timeout=20)
        cuerpo = res.json()
        if res.ok and "data" in cuerpo and "access_token" in cuerpo["data"]:
            with open("token_tiktok.json", "w", encoding="utf-8") as f:
                json.dump(cuerpo["data"], f, indent=2)
            print("✅ ¡Autenticación con TikTok exitosa!")
            print(f"✔ Token guardado en 'token_tiktok.json'.")
            acc_tok = cuerpo["data"]["access_token"]
            r_user = requests.get("https://open.tiktokapis.com/v2/user/info/?fields=avatar_url,display_name",
                                  headers={"Authorization": f"Bearer {acc_tok}"}, timeout=10)
            if r_user.ok:
                u_info = r_user.json().get("data", {}).get("user", {})
                print(f"   Cuenta conectada: {u_info.get('display_name', 'TikTok User')}")
        else:
            print(f"✖ TikTok devolvió un error al canjear el token: {res.text}")
    except Exception as e:
        print(f"✖ Error en la solicitud de token: {e}")

def conectar_o_probar_meta():
    """Asistente interactivo para conectar Meta (Facebook e Instagram)."""
    print("\n" + "═" * 65)
    print("📘 ASISTENTE DE CONEXIÓN A META (FACEBOOK & INSTAGRAM)")
    print("═" * 65)
    token_fb = os.getenv("META_ACCESS_TOKEN")
    token_ig = os.getenv("IG_ACCESS_TOKEN")
    p_id = "1158791430643856"
    archivo_token = "token_meta.json"
    if os.path.exists(archivo_token):
        try:
            with open(archivo_token, encoding="utf-8") as f:
                d = json.load(f)
                token_fb = d.get("facebook", {}).get("access_token") or d.get("access_token", token_fb)
                p_id = d.get("facebook", {}).get("page_id") or d.get("page_id", p_id)
                token_ig = d.get("instagram", {}).get("access_token") or token_ig
        except: pass

    if token_fb or token_ig:
        if token_fb:
            print(f"✔ Token Facebook detectado: {token_fb[:12]}...{token_fb[-6:]}")
        if token_ig:
            print(f"✔ Token Instagram detectado: {token_ig[:12]}...{token_ig[-6:]}")
        print("\n¿Qué deseas hacer?")
        print("  1) Probar conexión actual con Facebook e Instagram")
        print("  2) Actualizar / Cambiar Access Token de Facebook o Instagram")
        print("  0) Volver")
        op = input("Opción [1]: ").strip() or "1"
        if op == "0": return
        if op == "1":
            if token_fb:
                print("\n🔄 Probando conexión a Facebook Graph API...")
                try:
                    r_p = requests.get(f"https://graph.facebook.com/v19.0/{p_id}?fields=id,name,fan_count,followers_count&access_token={token_fb}", timeout=10)
                    if r_p.ok:
                        data = r_p.json()
                        print(f"✅ ¡Conexión exitosa con Facebook!")
                        print(f"   Página: '{data.get('name', '')}' (ID: {data.get('id', '')})")
                        print(f"   Seguidores: {data.get('followers_count', 0)}")
                        r_posts = requests.get(f"https://graph.facebook.com/v19.0/{p_id}/published_posts?fields=id,message,created_time,shares&limit=5&access_token={token_fb}", timeout=10)
                        if r_posts.ok:
                            posts = r_posts.json().get("data", [])
                            print(f"   Posts recientes detectados: {len(posts)}")
                            for p in posts[:3]:
                                msg = (p.get("message") or "")[:50].replace("\n", " ")
                                sh = p.get("shares", {}).get("count", 0) if p.get("shares") else 0
                                print(f"   - [{p.get('created_time', '')[:10]}] \"{msg}...\" ({sh} compartidos)")
                    else:
                        print(f"✖ Facebook respondió con error ({r_p.status_code}): {r_p.text[:200]}")
                except Exception as e:
                    print(f"✖ Error conectando a Facebook: {e}")

            if token_ig:
                print("\n🔄 Probando conexión a Instagram API...")
                try:
                    r_ig = requests.get(f"https://graph.instagram.com/v19.0/me?fields=id,username,account_type,media_count&access_token={token_ig}", timeout=10)
                    if r_ig.ok:
                        data_ig = r_ig.json()
                        print(f"✅ ¡Conexión exitosa con Instagram!")
                        print(f"   Cuenta: @{data_ig.get('username', '')} (ID: {data_ig.get('id', '')})")
                        print(f"   Tipo: {data_ig.get('account_type', '')} | Publicaciones: {data_ig.get('media_count', 0)}")
                        r_m = requests.get(f"https://graph.instagram.com/v19.0/me/media?fields=id,caption,like_count,comments_count,timestamp&limit=5&access_token={token_ig}", timeout=10)
                        if r_m.ok:
                            medias = r_m.json().get("data", [])
                            print(f"   Posts recientes detectados: {len(medias)}")
                            for m in medias[:3]:
                                cap = (m.get("caption") or "")[:50].replace("\n", " ")
                                print(f"   - [{m.get('timestamp', '')[:10]}] \"{cap}...\" (❤️ {m.get('like_count', 0)} | 💬 {m.get('comments_count', 0)})")
                    else:
                        print(f"✖ Instagram respondió con error ({r_ig.status_code}): {r_ig.text[:200]}")
                except Exception as e:
                    print(f"✖ Error conectando a Instagram: {e}")
            return

    print("\nPara actualizar tokens de Facebook o Instagram:")
    print("💡 TIP: Si pegas un token temporal de 1 hora de Graph API Explorer junto con tu App ID y App Secret,")
    print("   el sistema lo convertirá automáticamente en un Token de Página PERMANENTE (que nunca caduca).")
    nuevo_fb = input("Pega el Token de Facebook (de 1h o permanente, o ENTER para conservar el actual): ").strip()
    nuevo_ig = input("Pega el nuevo Token de Instagram (o ENTER para conservar el actual): ").strip()
    if nuevo_fb or nuevo_ig:
        d = {}
        if os.path.exists("token_meta.json"):
            try:
                with open("token_meta.json", encoding="utf-8") as f:
                    d = json.load(f)
            except Exception:
                pass
        if nuevo_fb:
            app_id = d.get("app_id") or os.getenv("META_APP_ID", "")
            app_secret = d.get("app_secret") or os.getenv("META_APP_SECRET", "")
            in_id = input(f"App ID de Meta (ENTER para usar '{app_id}' o saltar canje): ").strip() or app_id
            in_sec = input("App Secret de Meta (ENTER para usar el guardado o saltar canje): ").strip() or app_secret
            if in_id and in_sec:
                d["app_id"] = in_id
                d["app_secret"] = in_sec
                tok_perm = canjear_token_permanente_facebook(nuevo_fb, in_id, in_sec, p_id)
                if tok_perm:
                    nuevo_fb = tok_perm
            d.setdefault("facebook", {})["access_token"] = nuevo_fb
            d.setdefault("facebook", {})["page_id"] = p_id
            d["access_token"] = nuevo_fb
        if nuevo_ig:
            d.setdefault("instagram", {})["access_token"] = nuevo_ig
        with open("token_meta.json", "w", encoding="utf-8") as f:
            json.dump(d, f, indent=2)
        print("✔ Guardado en 'token_meta.json'.")

def canjear_token_permanente_facebook(token_corto, app_id, app_secret, page_id="1158791430643856"):
    """Convierte un token corto (1h) de Meta en un Token de Usuario de 60 días y luego extrae
    el Token de Página PERMANENTE (sin fecha de expiración) para Narrativas Sumergidas."""
    try:
        print("\n🔄 Canjeando token temporal por Token de Largo Plazo (60 días) / Permanente de Página...")
        r_ll = requests.get(
            "https://graph.facebook.com/v19.0/oauth/access_token",
            params={
                "grant_type": "fb_exchange_token",
                "client_id": app_id.strip(),
                "client_secret": app_secret.strip(),
                "fb_exchange_token": token_corto.strip()
            },
            timeout=15
        )
        if not r_ll.ok:
            print(f"   ⚠ No se pudo canjear fb_exchange_token ({r_ll.status_code}): {r_ll.text[:200]}")
            return None
        ll_token = r_ll.json().get("access_token")
        if not ll_token:
            return None
        print("   ✔ Token de 60 días obtenido.")

        # Intentar obtener el Page Access Token Permanente desde /me/accounts o /{page_id}?fields=access_token
        r_pg = requests.get(
            f"https://graph.facebook.com/v19.0/{page_id}",
            params={"fields": "access_token,name", "access_token": ll_token},
            timeout=15
        )
        if r_pg.ok and r_pg.json().get("access_token"):
            pg_tok = r_pg.json()["access_token"]
            print(f"   ✅ ¡Token de Página PERMANENTE (Nunca caduca) generado para '{r_pg.json().get('name', page_id)}'!")
            return pg_tok

        r_acc = requests.get(
            "https://graph.facebook.com/v19.0/me/accounts",
            params={"access_token": ll_token},
            timeout=15
        )
        if r_acc.ok:
            for pg in r_acc.json().get("data", []):
                if pg.get("id") == str(page_id) and pg.get("access_token"):
                    print(f"   ✅ ¡Token de Página PERMANENTE (Nunca caduca) generado para '{pg.get('name')}'!")
                    return pg["access_token"]

        print("   ✔ Se usará el Token extendido de 60 días.")
        return ll_token
    except Exception as e:
        print(f"   ⚠ Error canjeando token permanente: {e}")
        return None

def mostrar_metricas_tiktok():
    """Muestra una tabla con las estadísticas reales de los videos en TikTok."""
    datos = obtener_metricas_tiktok()
    if not datos:
        print("✖ No se pudieron extraer estadísticas de TikTok (verifica token_tiktok.json).")
        return
    print("\n" + "═" * 78)
    print("📈 RANKING DE RENDIMIENTO EN TIKTOK (NARRATIVAS SUMERGIDAS)")
    print("═" * 78)
    print(f"{'Vistas':<9} | {'Likes':<6} | {'Shares':<7} | {'Coment':<6} | Título / Publicación")
    print("─" * 78)
    for v in datos:
        print(f"{v['vistas']:<9} | {v['likes']:<6} | {v.get('compartidos', 0):<7} | {v['comentarios']:<6} | {v['titulo'][:38]}")
    print("═" * 78)
    total_v = sum(v["vistas"] for v in datos)
    total_l = sum(v["likes"] for v in datos)
    total_s = sum(v.get("compartidos", 0) for v in datos)
    print(f"📊 Totales analizados: {len(datos)} videos | {total_v:,} vistas | {total_l:,} likes | {total_s:,} compartidos\n")

def toggle_tiktok_automatico():
    global SUBIR_TIKTOK_AUTOMATICO
    print("\n" + "═" * 65)
    print("⚙️ CONFIGURACIÓN PARAMÉTRICA DE SUBIDA A TIKTOK")
    print("═" * 65)
    est = "ACTIVADA (Intentar envío por API)" if SUBIR_TIKTOK_AUTOMATICO else "DESACTIVADA (Preparar en local para Mac y copiar portapapeles)"
    print(f"Estado actual: {est}")
    print("\nOpciones:")
    print("  1) DESACTIVADA (Recomendado): Conserva el .mp4, metadata y portadas en la Mac mini,")
    print("     y copia automáticamente el copy + hashtags al portapapeles para subir sin errores ni bloqueos de API.")
    print("  2) ACTIVADA: Intentar subida automática vía API oficial (requiere token_tiktok.json activo).")
    print("  0) Cancelar sin cambios")
    op = input("\nElige una opción [1]: ").strip() or "1"
    if op == "0":
        return
    nuevo_val = (op == "2")
    actualizar_variable_env("SUBIR_TIKTOK_AUTOMATICO", "1" if nuevo_val else "0")
    SUBIR_TIKTOK_AUTOMATICO = nuevo_val
    print(f"✔ Configuración actualizada: {'ACTIVADA (API)' if SUBIR_TIKTOK_AUTOMATICO else 'DESACTIVADA (Preparar en Mac mini)'}")

def menu_rendimiento_redes():
    """Menú interactivo para gestionar métricas, conexiones sociales y feedback algorítmico."""
    while True:
        est_tk = "ACTIVADA (API)" if SUBIR_TIKTOK_AUTOMATICO else "DESACTIVADA (Preparar en Mac)"
        print("\n" + "═" * 65)
        print("📊 PANEL DE RENDIMIENTO Y REDES SOCIALES")
        print("═" * 65)
        print("  1) Analizar las 4 redes (YT + TikTok + IG + FB) y optimizar Prompts IA")
        print("  2) Ver ranking y estadísticas de videos en TikTok")
        print("  3) Ver estado de conexiones (YouTube, TikTok, Meta / FB / IG)")
        print("  4) Ver patrones ganadores y mejoras aplicadas en los Prompts IA")
        print("  5) Conectar / Re-autenticar TikTok")
        print("  6) Conectar / Probar Meta (Facebook e Instagram)")
        print("  7) Probar subir un video a TikTok (enviar a borradores)")
        print("  8) Probar subir un video a Facebook Page (publicar video)")
        print("  9) Probar subir un video a Instagram (@narrativasumergidas Reel)")
        print(f" 10) Configurar subida a TikTok por API [Estado: {est_tk}]")
        print("  0) Volver al menú principal")
        op = input("Elige una opción: ").strip()
        if op == "1":
            analizar_rendimiento_canal()
        elif op == "2":
            mostrar_metricas_tiktok()
        elif op == "3":
            mostrar_estado_conexiones()
        elif op == "4":
            mostrar_patrones_ia()
        elif op == "5":
            conectar_o_probar_tiktok()
        elif op == "6":
            conectar_o_probar_meta()
        elif op == "7":
            ruta_v = input("Ruta del archivo .mp4 a subir a TikTok: ").strip().strip('"').strip("'")
            if os.path.exists(ruta_v):
                subir_video_tiktok(ruta_v)
            else:
                print(f"✖ El archivo '{ruta_v}' no existe.")
        elif op == "8":
            ruta_v = input("Ruta del archivo .mp4 a subir a Facebook: ").strip().strip('"').strip("'")
            if os.path.exists(ruta_v):
                subir_video_facebook(ruta_v)
            else:
                print(f"✖ El archivo '{ruta_v}' no existe.")
        elif op == "9":
            ruta_v = input("Ruta del archivo .mp4 a subir a Instagram: ").strip().strip('"').strip("'")
            if os.path.exists(ruta_v):
                subir_video_instagram(ruta_v)
            else:
                print(f"✖ El archivo '{ruta_v}' no existe.")
        elif op == "10":
            toggle_tiktok_automatico()
        elif op == "0":
            break


# ---------------------------------------------------------------------------
# UTILIDADES
# ---------------------------------------------------------------------------
def slug(texto, n=40):
    return re.sub(r'[^\w\s-]', '', texto.lower()).strip().replace(' ', '_')[:n] or "sin_titulo"

def pedir_int(msg, defecto=1, minimo=1, maximo=50):
    v = input(f"{msg} [{defecto}]: ").strip()
    try: return max(minimo, min(maximo, int(v)))
    except ValueError: return defecto

def pedir_multilinea(msg):
    print(f"{msg} (termina con una línea que diga FIN):")
    lineas = []
    while True:
        l = input()
        if l.strip().upper() == "FIN": break
        lineas.append(l)
    return "\n".join(lineas).strip()

def pedir_config_semana_completa():
    """Configura el lote semanal de 7 videos (Lunes a Domingo) con programación automática y horarios óptimos."""
    print("\n" + "═" * 70)
    print("📅 GENERACIÓN DE SEMANA COMPLETA (7 VIDEOS: LUNES A DOMINGO)")
    print("═" * 70)
    print("El sistema programará 1 video diario con horario prime-time calibrado:")
    print("  • Lunes:     20:00 CST | Comienzo de semana, misterio histórico profundo")
    print("  • Martes:    20:30 CST | Pico nocturno, leyendas urbanas oscuras")
    print("  • Miércoles: 20:00 CST | Mitad de semana, arquitectura maldita")
    print("  • Jueves:    20:30 CST | Noche prime previo a fin de semana, enigmas")
    print("  • Viernes:   19:30 CST | Viernes de terror y desconexión laboral")
    print("  • Sábado:    19:00 CST | Fin de semana ocio, debate comunitario")
    print("  • Domingo:   21:00 CST | Domingo prime, máxima retención en cama")
    print("═" * 70)

    print("\n¿Para qué semana deseas programar?")
    print("  1) Próxima semana entrante (Recomendado)")
    print("  2) Semana actual (días restantes / en curso)")
    print("  3) Especificar fecha de inicio o semana (ej. '2026-10-19', 'próximo lunes')")
    op_sem = input("Opción [1]: ").strip() or "1"
    ref_sem = "proxima"
    if op_sem == "2":
        ref_sem = "actual"
    elif op_sem == "3":
        ref_sem = input("Fecha o referencia de inicio [próxima]: ").strip() or "proxima"

    fechas_semana = resolver_fechas_semana_completa(ref_sem)
    print(f"\n🗓️ Calendario semanal confirmado ({fechas_semana[0]['fecha']} al {fechas_semana[-1]['fecha']}):")
    for f in fechas_semana:
        print(f"   • {f['dia']:<9} ({f['fecha']}): {f['horario_str']} -> {f['enfoque']}")

    print("\n¿Cómo deseas definir los 7 temas de la semana?")
    print("  1) 100% Automático / Tendencias (Claude selecciona 7 misterios virales)")
    print("  2) Ingresar los 7 temas (uno por día o separados por '|')")
    op_temas = input("Opción [1]: ").strip() or "1"

    temas_usuario = []
    if op_temas == "2":
        entrada = input("\nIngresa los 7 temas separados por '|' (o presiona ENTER para pedir día a día):\n").strip()
        if entrada and "|" in entrada:
            temas_usuario = [t.strip() for t in entrada.split("|") if t.strip()]
        if len(temas_usuario) < 7:
            print("Ingresa el tema para cada día (presiona ENTER para dejar que Claude elija al azar):")
            for f in fechas_semana[len(temas_usuario):]:
                t = input(f"   • Tema para {f['dia']} ({f['fecha']}): ").strip()
                temas_usuario.append(t if t else None)

    peticiones = []
    for idx, f in enumerate(fechas_semana):
        tema_txt = temas_usuario[idx] if idx < len(temas_usuario) else None
        peticiones.append({
            "tipo": "tema",
            "texto": tema_txt,
            "dia_semana": f["dia"],
            "fecha_liberacion": f["fecha"],
            "hora_programacion": f["horario_str"],
            "momento_dia": f["momento"],
            "enfoque_dia": f["enfoque"],
            "fecha_prog_iso": f["fecha_prog_iso"]
        })

    config = {
        "momento_dia": "semana_completa",
        "modo_programacion": "semana_completa",
        "fecha_liberacion": f"{fechas_semana[0]['fecha']} al {fechas_semana[-1]['fecha']}",
        "fechas_semana": fechas_semana
    }
    return peticiones, config

def pedir_modo(etiqueta):
    """Devuelve la lista de 'peticiones'. Opción 2 admite varios temas y N por tema (o N sobre un solo tema)."""
    print(f"\n¿Cómo quieres definir {etiqueta}?")
    print("  1) Aleatorio")
    print("  2) Yo doy el TEMA (uno o varios separados por |; puedo pedir N por tema)")
    print("  3) Yo doy la HISTORIA completa (se adapta tal cual)")
    print("  4) Semana Completa (7 videos: Lunes a Domingo con programación automática)")
    modo = input("Opción [1]: ").strip() or "1"
    if modo == "4":
        return pedir_config_semana_completa()
    if modo == "3":
        return [{"tipo": "historia", "texto": pedir_multilinea("Pega tu historia")}]
    if modo == "2":
        temas = [t.strip() for t in input("Tema(s): ").split("|") if t.strip()]
        if temas:
            pregunta = f"¿Cuántos {etiqueta} por cada tema?" if len(temas) > 1 else f"¿Cuántos {etiqueta} sobre este tema?"
            m = pedir_int(pregunta, 1)
            print(f"   → {len(temas)} tema(s) × {m} = {len(temas) * m} {etiqueta}" + (" (cada uno con un caso o ángulo distinto)" if m > 1 else ""))
            return [{"tipo": "tema", "texto": t, "variante": j, "de": m} for t in temas for j in range(1, m + 1)]
    n = pedir_int(f"¿Cuántos {etiqueta} generar?", 1)
    return [{"tipo": "tema", "texto": None} for _ in range(n)]

FRANJAS_DIA = {
    "1": ("Cualquier momento / Multi-horario (estrategia 24h completa en metadata)", "multi"),
    "2": ("Mañana (07:00 - 10:00 CST: despertar, transporte, datos insólitos)", "manana"),
    "3": ("Mediodía (12:00 - 15:00 CST: break de almuerzo, misterio y arquitectura)", "mediodia"),
    "4": ("Tarde (16:00 - 19:00 CST: salida laboral/escolar, debate en Facebook)", "tarde"),
    "5": ("Noche Prime (20:00 - 23:00 CST: máxima oscuridad, terror inmersivo)", "noche"),
    "6": ("Madrugada / Insomnio (00:00 - 04:00 CST: paranoia y misterio para desvelados)", "madrugada"),
    "7": ("Jornada 24H Completa (1 por franja: Mañana, Mediodía, Tarde, Noche y Madrugada)", "jornada_completa"),
    "8": ("Semana Completa (Lunes a Domingo: 1 video diario con horario prime-time calibrado)", "semana_completa"),
}

ORDEN_5_FRANJAS = ["manana", "mediodia", "tarde", "noche", "madrugada"]

def pedir_config_videos(num_peticiones=1):
    print("\n¿Para qué momento del día deseas enfocar la historia / publicación?")
    for k, (nombre, _) in FRANJAS_DIA.items():
        print(f"  {k}) {nombre}")
    # Si se solicitaron 5 entregas, sugerir opción 7 (Jornada 24H) por defecto
    defecto = "7" if num_peticiones == 5 else "1"
    op = input(f"Opción [{defecto}]: ").strip() or defecto
    _, clave = FRANJAS_DIA.get(op, FRANJAS_DIA["1"])

    fecha_liberacion = None
    if num_peticiones == 5 or clave == "jornada_completa":
        print("\n📅 ¿En qué DÍA buscas liberar este lote de 5 videos (Jornada 24H Completa)?")
        print("   (Puedes escribir: 'mañana', 'hoy', un día como 'viernes', 'sábado', 'domingo' o fecha 'YYYY-MM-DD')")
        dia_in = input("Día de liberación del lote [mañana]: ").strip() or "mañana"
        fecha_liberacion = resolver_fecha_dia(dia_in)
        print(f"   ✔ Lote programado para la jornada del día: {fecha_liberacion}")
        print(f"     • Video 1 (Mañana):      {fecha_liberacion} 08:00 CST")
        print(f"     • Video 2 (Mediodía):    {fecha_liberacion} 13:00 CST")
        print(f"     • Video 3 (Tarde):       {fecha_liberacion} 17:00 CST")
        print(f"     • Video 4 (Noche Prime): {fecha_liberacion} 21:00 CST")
        print(f"     • Video 5 (Madrugada):   01:30 CST (cierre de madrugada de la jornada)")

    print("\n¿Deseas programar la publicación automática en las redes (SIN liberar de inmediato al público)?")
    print("  1) Publicar de inmediato al terminar el ensamblaje")
    print("  2) (Recomendado) Programar automáticamente cada video en su hora óptima del día elegido (08:00, 13:00, 17:00, 21:00, 01:30 CST)")
    print("  3) Especificar fecha y/o hora personalizada (ej. 'mañana', 'mañana 21:00', 'viernes 19:30' o '2026-10-15 20:30')")
    defecto_prog = "2"
    op_prog = input(f"Opción [{defecto_prog}]: ").strip() or defecto_prog
    modo_prog = "inmediato"
    hora_pers = None
    if op_prog == "2":
        modo_prog = "auto_franja"
        if not fecha_liberacion:
            dia_in = input("📅 ¿Para qué DÍA deseas programar la publicación? ('hoy', 'mañana', 'sábado', 'YYYY-MM-DD') [mañana]: ").strip() or "mañana"
            fecha_liberacion = resolver_fecha_dia(dia_in)
        iso_prev = calcular_fecha_programacion(momento_dia=clave, fecha_base=fecha_liberacion, forzar_programacion=True)
        print(f"   🔒 HORARIO DE PROGRAMACIÓN CONFIRMADO: {formatear_hora_cst(iso_prev)} (NO se liberará de inmediato)")
    elif op_prog == "3":
        modo_prog = "personalizada"
        hora_pers = input("Ingresa día y/o hora (ej. 'mañana 01:30', 'mañana', 'hoy 21:00', 'YYYY-MM-DD HH:MM') [mañana]: ").strip() or "mañana"
        iso_prev = calcular_fecha_programacion(momento_dia=clave, hora_especifica=hora_pers, fecha_base=fecha_liberacion, forzar_programacion=True)
        print(f"   🔒 HORARIO DE PROGRAMACIÓN CONFIRMADO: {formatear_hora_cst(iso_prev)} (NO se liberará de inmediato)")
    else:
        print("   ⚡ Modo seleccionado: Publicación inmediata al terminar el ensamblaje.")

    return {
        "momento_dia": clave,
        "modo_programacion": modo_prog,
        "hora_personalizada": hora_pers,
        "fecha_liberacion": fecha_liberacion
    }

def construir_mensaje(peticion, evitar, prefijo="Historia sobre"):
    if peticion["tipo"] == "historia":
        return f"Adapta ESTA historia proporcionada por el creador (no inventes otra):\n\n{peticion['texto']}"
    tema = peticion["texto"] or f"Evento real o leyenda urbana oscura sobre: {random.choice(TEMAS_AZAR)}"
    msg = f"{prefijo}: {tema}"
    if peticion.get("de", 1) > 1:
        v = peticion['variante']
        total = peticion['de']
        msg += (f"\n\nESTRATEGIA EN SERIE (Parte {v} de {total}):\n"
                f"- Este video es la PARTE {v} de una serie de {total} entregas sobre este gran caso.\n"
                f"- Duración estricta de la locución: 85 a 115 segundos (≈215-280 palabras).\n")
        if v < total:
            msg += (f"- Como es la Parte {v}, termina con un CLIFFHANGER / LOOP ABIERTO brutal y un gancho claro hacia la Parte {v+1} ('En la siguiente parte verás...').\n"
                    f"- En 'metadata.calendario_publicacion', define los días específicos de la semana recomendados para publicar la Parte {v} y la Parte {v+1} (ej. Parte 1 el Viernes para captar tráfico, Parte 2 el Domingo para cerrar).\n")
        else:
            msg += (f"- Como es la Parte final ({v} de {total}), da la resolución y clímax definitivo del misterio.\n"
                    f"- En 'metadata.calendario_publicacion', sugiere el día óptimo para el desenlace de la saga.\n")
        msg += f"- Rellena los campos 'serie', 'parte' ('Parte {v} de {total}'), y 'gancho_siguiente_parte'.\n"
    else:
        msg += ("\n\nCALENDARIO DE PUBLICACIÓN:\n"
                "- En 'metadata.calendario_publicacion', indica qué día de la semana y qué ventana de horario son ideales para este contenido según su tier.\n"
                "- Si consideras que el caso es tan amplio que debería convertirse en serie de 2 o 3 partes para no saturar al espectador, señálalo en 'calendario_publicacion.estrategia_escalonada'.\n")

    dia_sem = peticion.get("dia_semana")
    hora_prog = peticion.get("hora_programacion")
    enfoque_d = peticion.get("enfoque_dia")
    if dia_sem and fecha_lib:
        msg += (f"\n\n📅 CALENDARIO SEMANAL CALIBRADO ({dia_sem.upper()} {fecha_lib} a las {hora_prog}):\n"
                f"- Este video se publicará el día {dia_sem} {fecha_lib} a las {hora_prog}.\n"
                f"- Enfoque psicológico del día: {enfoque_d or 'Máximo enganche y misterio'}.\n"
                f"- Adapta el ritmo, el gancho inicial y la iluminación de las escenas para la audiencia de {dia_sem}.\n"
                f"- OBLIGATORIO: En la metadata de CADA red social, el campo 'hora_publicacion' DEBE colocar: '{dia_sem} {fecha_lib} a las {hora_prog}'.\n"
                f"- En 'metadata.calendario_publicacion', fija la fecha '{fecha_lib}' en la ventana de '{hora_prog}'.\n")
    elif momento and momento != "multi":
        descripciones = {
            "manana": "MAÑANA (07:00-10:00 CST: camino al trabajo/estudio, tono intrigante y dato insólito matutino)",
            "mediodia": "MEDIODÍA (12:00-15:00 CST: break de comida/almuerzo, misterio envolvente)",
            "tarde": "TARDE (16:00-19:00 CST: salida de actividades, debate moral y tensión)",
            "noche": "NOCHE PRIME (20:00-23:00 CST: máxima oscuridad, terror inmersivo)",
            "madrugada": "MADRUGADA / INSOMNIO (00:00-04:00 CST: atmósfera de paranoia para los que no pueden dormir)",
        }
        msg += (f"\n\nMOMENTO DEL DÍA OBJETIVO{txt_dia_lib}: Diseña el gancho, el tono y la ambientación lumínica específicamente para la franja de {descripciones.get(momento, momento.upper())}.\n"
                f"- OBLIGATORIO: En la metadata de CADA red social, el campo 'hora_publicacion' DEBE colocar la fecha/día{txt_dia_lib} y la hora recomendada de publicación dentro de esta franja de {descripciones.get(momento, momento.upper())}.")
    else:
        msg += ("\n\nMOMENTO DEL DÍA: Flexible / Cualquier momento.\n"
                "- OBLIGATORIO: En la metadata de CADA red social, incluye en 'hora_publicacion' la mejor ventana horaria y día óptimo para esa plataforma según su tier.\n"
                "- Incluye además en la metadata la tabla 'estrategia_horaria' adaptada para mañana, mediodía, tarde, noche y madrugada.")

    if evitar:
        # Tomar los últimos 40 para no saturar el prompt pero garantizar cobertura absoluta
        lista_evitar = evitar[-40:] if len(evitar) > 40 else evitar
        msg += ("\n\n⛔ REGLA ESTRICTA DE NO REPETICIÓN: El canal YA produjo contenido sobre los siguientes casos/títulos. "
                "Está TERMINANTEMENTE PROHIBIDO repetir cualquiera de estos casos, eventos, leyendas o títulos; "
                "debes elegir un caso, lugar o suceso completamente NUEVO y no explorado:\n- " + "\n- ".join(lista_evitar))

    # Inyección inteligente de patrones basados en métricas reales de la audiencia
    feedback_met = obtener_feedback_metricas()
    if feedback_met:
        msg += feedback_met

    return msg

# ---------------------------------------------------------------------------
# CLAUDE / ELEVENLABS
# ---------------------------------------------------------------------------
def llamar_claude(prompt_sistema, mensaje, max_tokens=16000, reintentos=2):
    for intento in range(1, reintentos + 1):
        try:
            res = requests.post("https://api.anthropic.com/v1/messages",
                                json={"model": "claude-sonnet-5-5", "max_tokens": max_tokens, "system": prompt_sistema,
                                      "messages": [{"role": "user", "content": mensaje}]},
                                headers={"x-api-key": API_KEY_ANTHROPIC, "anthropic-version": "2023-06-01"}, timeout=600)
            res.raise_for_status()
            cuerpo = res.json()
            txt = next((b.get('text') for b in cuerpo.get('content', []) if b.get('type') == 'text'), "").strip()
            if cuerpo.get("stop_reason") == "max_tokens":
                print("⚠ Respuesta de Claude truncada por límite de tokens; se intenta reparar el JSON.")
            inicio = txt.find("{")
            if inicio == -1:
                print(f"⚠ Claude no devolvió JSON (intento {intento}/{reintentos}). Respuesta: {txt[:300]!r}")
                continue
            datos = json.loads(json_repair.repair_json(txt[inicio:]))
            if isinstance(datos, dict) and datos: return datos
            print(f"⚠ JSON inválido o vacío (intento {intento}/{reintentos}).")
        except Exception as e:
            print(f"Error en API de Claude (intento {intento}/{reintentos}): {e}")
    return None

def generar_audio(datos, ruta):
    try:
        voz = VOICE_ID_ELEVENLABS or datos.get('voice_id', '6bNjXphfWPUDHuFkgDt3')
        res = requests.post(f"https://api.elevenlabs.io/v1/text-to-speech/{voz}",
                            json={"text": re.sub(r'\[.*?\]\s*', '', datos.get("texto_completo", "")), "model_id": "eleven_multilingual_v2"},
                            headers={"xi-api-key": API_KEY_ELEVENLABS})
        res.raise_for_status()
        with open(ruta, 'wb') as f: f.write(res.content)
    except Exception as e: print(e)

# ---------------------------------------------------------------------------
# STABLE DIFFUSION
# ---------------------------------------------------------------------------
def _aplicar_qos_fondo_pid(pid):
    """Aplica prioridad nice 19 y Background QoS (E-cores) a un PID en macOS."""
    try:
        subprocess.run(["/usr/bin/renice", "+19", "-p", str(pid)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except Exception:
        pass
    try:
        if os.path.exists("/usr/sbin/taskpolicy"):
            subprocess.run(["/usr/sbin/taskpolicy", "-b", "-p", str(pid)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except Exception:
        pass

def iniciar_sd():
    try:
        if requests.get(f"{URL_SD_BASE}/sd-models", timeout=2).status_code == 200:
            # Si ya estaba corriendo en el puerto 7860, aplicarle limitación de CPU/QoS de fondo
            try:
                out = subprocess.check_output(["lsof", "-ti:7860"], text=True).strip()
                for pid_s in out.splitlines():
                    if pid_s.strip().isdigit():
                        _aplicar_qos_fondo_pid(int(pid_s.strip()))
            except Exception:
                pass
            return None
    except Exception:
        pass
    env = os.environ.copy()
    env.pop("VIRTUAL_ENV", None)
    # Control estricto de uso de CPU/GPU: 1 solo hilo y Background QoS (E-cores) en Mac mini M4
    env["OMP_NUM_THREADS"] = "1"
    env["MKL_NUM_THREADS"] = "1"
    env["OPENBLAS_NUM_THREADS"] = "1"
    env["VECLIB_MAXIMUM_THREADS"] = "1"
    env["NUMEXPR_NUM_THREADS"] = "1"
    env["TOKENIZERS_PARALLELISM"] = "false"
    comandos = ["nice", "-n", "19", "./webui.sh", "--api", "--nowebui", "--port", "7860", "--medvram-sdxl", "--no-half-vae", "--use-cpu", "interrogate"]
    if os.path.exists("/usr/sbin/taskpolicy"):
        comandos = ["/usr/sbin/taskpolicy", "-b"] + comandos

    def _init_child():
        os.setsid()
        try:
            os.nice(19)
            ctypes.CDLL("/usr/lib/libc.dylib").setpriority(4, 0, 0x1000)
        except Exception:
            pass

    p = subprocess.Popen(comandos, cwd=RUTA_SD, env=env, preexec_fn=_init_child)
    for _ in range(60):
        try:
            if requests.get(f"{URL_SD_BASE}/sd-models", timeout=2).status_code == 200:
                return p
        except Exception:
            time.sleep(2)
    return p

def detener_sd(p):
    """Descarga el modelo SDXL de la memoria unificada y cierra el proceso para dejar 100% libre la RAM antes de MoviePy."""
    try:
        requests.post(f"{URL_SD_BASE}/unload-checkpoint", timeout=5)
    except Exception:
        pass
    if p and p.poll() is None:
        try:
            os.killpg(os.getpgid(p.pid), signal.SIGTERM)
        except Exception:
            pass
    else:
        # Si SDXL había quedado abierto de antes, cerrarlo también para liberar los ~7 GB de RAM unificada
        try:
            out = subprocess.check_output(["lsof", "-ti:7860"], text=True).strip()
            for pid_s in out.splitlines():
                if pid_s.strip().isdigit():
                    os.kill(int(pid_s.strip()), signal.SIGTERM)
        except Exception:
            pass
    gc.collect()
    time.sleep(1.0)

def generar_foto(prompt, ruta, ancho=768, alto=1344, reintentos=3):
    prompt_low = prompt.lower()
    negativo_actual = NEGATIVO_BASE

    # INYECCIONES DINÁMICAS (Anti-Alucinaciones)
    if "no humans" in prompt_low or "empty scene" in prompt_low:
        negativo_actual += ", person, human, faces, silhouette, people"
    if "destroyed" in prompt_low or "fire" in prompt_low or "ruins" in prompt_low:
        negativo_actual += ", skeleton, bones, skull, zombie, blood, cartoon"
    if "train" in prompt_low or "boat" in prompt_low or "house" in prompt_low or "tunnel" in prompt_low:
        negativo_actual += ", floating objects, impossible architecture, text"

    # CONFIGURACIÓN SDXL LIGHTNING
    payload = {"prompt": prompt, "negative_prompt": negativo_actual, "steps": 6, "cfg_scale": 2.0,
               "width": ancho, "height": alto, "sampler_name": "DPM++ SDE",
               "override_settings": {"sd_model_checkpoint": MODELO_SD}}
    for intento in range(1, reintentos + 1):
        try:
            r = requests.post(URL_API_SD, json=payload, timeout=300)
            r.raise_for_status()
            with open(ruta, "wb") as f: f.write(base64.b64decode(r.json()['images'][0]))
            # Pausa de enfriamiento (0.8s) para que la GPU/MPS de la Mac M4 no congele WindowServer
            time.sleep(0.8)
            return True
        except Exception as e:
            print(f"Error en imagen {ruta} (intento {intento}/{reintentos}): {e}")
            time.sleep(3)
    return False

# ---------------------------------------------------------------------------
# TEXTO SOBRE IMÁGENES (portadas y diapositivas) - Shadow Stack con PIL
# ---------------------------------------------------------------------------
def _fuente(tam):
    return ImageFont.truetype(FUENTE_IMPACT, tam) if os.path.exists(FUENTE_IMPACT) else ImageFont.load_default()

def _envolver(draw, texto, fuente, ancho_max):
    lineas, actual = [], ""
    for palabra in texto.split():
        prueba = f"{actual} {palabra}".strip()
        if draw.textlength(prueba, font=fuente) <= ancho_max: actual = prueba
        else:
            if actual: lineas.append(actual)
            actual = palabra
    if actual: lineas.append(actual)
    return lineas

def encuadrar(img, tam):
    """Recorta al centro hasta cubrir tam sin franjas negras."""
    w, h = tam
    esc = max(w / img.width, h / img.height)
    img = img.resize((int(img.width * esc) + 1, int(img.height * esc) + 1), Image.LANCZOS)
    x, y = (img.width - w) // 2, (img.height - h) // 2
    return img.crop((x, y, x + w, y + h))

def poner_texto(ruta_in, ruta_out, texto, tam=(1080, 1920), pos_y=0.20, fontsize=105, color="#FFDE00", pie=None):
    img = encuadrar(Image.open(ruta_in).convert("RGB"), tam)
    if texto and texto.strip():
        d = ImageDraw.Draw(img)
        f = _fuente(fontsize)
        lineas = _envolver(d, texto.upper(), f, int(tam[0] * 0.85))
        y = int(tam[1] * pos_y)
        for l in lineas:
            x = (tam[0] - d.textlength(l, font=f)) / 2
            for off in (18, 9):  # sombras
                d.text((x, y + off), l, font=f, fill="black", stroke_width=6, stroke_fill="black")
            d.text((x, y), l, font=f, fill=color, stroke_width=10, stroke_fill="black")
            y += int(fontsize * 1.15)
    if pie:
        d = ImageDraw.Draw(img)
        f = _fuente(int(tam[0] * 0.04))
        x = (tam[0] - d.textlength(pie, font=f)) / 2
        d.text((x, tam[1] - int(tam[1] * 0.06)), pie, font=f, fill="white", stroke_width=3, stroke_fill="black")
    img.save(ruta_out)

# ---------------------------------------------------------------------------
# DISEÑO DE PORTADAS (miniaturas competitivas, acordes a la historia)
# ---------------------------------------------------------------------------
PALETAS_TIER = {"FOLCLORE": "#FF5A1F", "ARQUITECTURA": "#F2C94C", "MORBO": "#E50914", "ABSURDO": "#FFE600"}
ETIQUETA_TIER = {"FOLCLORE": "LEYENDA REAL", "ARQUITECTURA": "LUGAR MALDITO", "MORBO": "HISTORIA REAL +18", "ABSURDO": "PARECE MENTIRA"}
FUENTES_CJK = ["/System/Library/Fonts/STHeiti Medium.ttc", "/System/Library/Fonts/PingFang.ttc", "/System/Library/Fonts/Hiragino Sans GB.ttc"]
CANAL_FIRMA = "NARRATIVAS SUMERGIDAS"

def _hex_ok(c, defecto):
    return c.strip() if isinstance(c, str) and re.fullmatch(r'#[0-9a-fA-F]{6}', c.strip()) else defecto

def _fuente_cjk(tam):
    for r in FUENTES_CJK:
        if os.path.exists(r):
            try: return ImageFont.truetype(r, tam)
            except Exception: pass
    return _fuente(tam)

def _gradar(img, acento_rgb):
    """Color grading + viñeta + degradados arriba/abajo para que el texto siempre se lea."""
    w, h = img.size
    img = ImageEnhance.Contrast(img).enhance(1.18)
    img = ImageEnhance.Color(img).enhance(1.12)
    negro = Image.new("RGB", (w, h), (0, 0, 0))
    vig = Image.radial_gradient("L").resize((w, h)).point(lambda v: int(v * 0.7))
    img = Image.composite(negro, img, vig)
    lin = Image.linear_gradient("L")  # negro arriba → blanco abajo
    mask = Image.new("L", (w, h), 0)
    mask.paste(ImageOps.invert(lin).resize((w, int(h * 0.55))).point(lambda v: int(v * 0.72)), (0, 0))
    mask.paste(lin.resize((w, int(h * 0.30))).point(lambda v: int(v * 0.6)), (0, h - int(h * 0.30)))
    img = Image.composite(negro, img, mask)
    # tinte de acento sutil en la base (da identidad de color a la historia)
    tinte = Image.new("RGB", (w, h), acento_rgb)
    m2 = Image.new("L", (w, h), 0)
    m2.paste(lin.resize((w, int(h * 0.25))).point(lambda v: int(v * 0.22)), (0, h - int(h * 0.25)))
    return Image.composite(tinte, img, m2)

def _envolver_cjk(draw, texto, fuente, ancho_max):
    chars = texto.replace(" ", "")
    lineas, actual = [], ""
    for ch in chars:
        if draw.textlength(actual + ch, font=fuente) <= ancho_max: actual += ch
        else: lineas.append(actual); actual = ch
    if actual: lineas.append(actual)
    n = len(lineas)
    if n <= 1: return lineas
    por_linea = -(-len(chars) // n)  # reparto equilibrado
    return [chars[i:i + por_linea] for i in range(0, len(chars), por_linea)]

def disenar_portada(ruta_in, ruta_out, texto, palabra_clave="", etiqueta="", acento="#FFDE00", tam=(1080, 1920),
                    alinear="centro", cjk=False, subtitulo=""):
    W, H = tam
    acento_rgb = ImageColor.getrgb(acento)
    img = _gradar(encuadrar(Image.open(ruta_in).convert("RGB"), tam), acento_rgb)
    d = ImageDraw.Draw(img)
    horizontal = alinear == "izq"
    margen = int(W * 0.06)
    ancho_max = int(W * (0.56 if horizontal else 0.88))
    texto = texto.strip() if cjk else texto.upper().strip()

    # 1) tamaño del título: el más grande que entre en ≤3 líneas
    size = int(W * (0.085 if horizontal else 0.16))
    while size > 28:
        f = _fuente_cjk(size) if cjk else _fuente(size)
        lineas = _envolver_cjk(d, texto, f, ancho_max) if cjk else _envolver(d, texto, f, ancho_max)
        if len(lineas) <= 3 and all(d.textlength(l, font=f) <= ancho_max for l in lineas): break
        size -= 4
    clave = (palabra_clave or "").upper().strip(" ¡!¿?.,")
    esp = d.textlength(" ", font=f)
    if cjk: filas = [[(l, i == len(lineas) - 1 and len(lineas) > 1)] for i, l in enumerate(lineas)]
    else: filas = [[(w, clave != "" and w.strip(" ¡!¿?.,") == clave) for w in l.split()] for l in lineas]

    # 2) posiciones
    y0 = int(H * (0.17 if horizontal else 0.16))
    alto_linea = int(size * 1.08)
    pos = []
    for fi, fila in enumerate(filas):
        ancho = sum(d.textlength(w, font=f) for w, _ in fila) + esp * (len(fila) - 1)
        x = margen if horizontal else (W - ancho) / 2
        for w, es_clave in fila:
            pos.append((x, y0 + fi * alto_linea, w, es_clave))
            x += d.textlength(w, font=f) + esp
    fin_y = y0 + len(filas) * alto_linea

    # 3) brillo (glow) del acento detrás de la palabra clave
    glow = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    gd = ImageDraw.Draw(glow)
    for x, y, w, es_clave in pos:
        if es_clave: gd.text((x, y), w, font=f, fill=acento_rgb + (255,), stroke_width=int(size * 0.08), stroke_fill=acento_rgb + (255,))
    glow = glow.filter(ImageFilter.GaussianBlur(size * 0.22))
    img = Image.alpha_composite(img.convert("RGBA"), glow).convert("RGB")
    d = ImageDraw.Draw(img)

    # 4) texto: sombra dura, contorno negro grueso, relleno blanco / acento
    for x, y, w, es_clave in pos:
        d.text((x + size * 0.05, y + size * 0.07), w, font=f, fill="black", stroke_width=int(size * 0.11), stroke_fill="black")
    for x, y, w, es_clave in pos:
        d.text((x, y), w, font=f, fill=acento_rgb if es_clave else (255, 255, 255), stroke_width=int(size * 0.07), stroke_fill="black")

    # 5) barra de acento bajo el título + subtítulo opcional (español bajo el chino)
    ancho_barra = int(W * (0.22 if horizontal else 0.32))
    bx = margen if horizontal else (W - ancho_barra) // 2
    d.rectangle([bx, fin_y + int(size * 0.05), bx + ancho_barra, fin_y + int(size * 0.05) + max(8, int(size * 0.07))], fill=acento_rgb)
    if subtitulo:
        fs = _fuente(int(size * 0.42))
        ls = _envolver(d, subtitulo.upper(), fs, ancho_max)
        ys = fin_y + int(size * 0.25)
        for l in ls[:2]:
            xs = margen if horizontal else (W - d.textlength(l, font=fs)) / 2
            d.text((xs, ys), l, font=fs, fill="white", stroke_width=4, stroke_fill="black")
            ys += int(size * 0.5)

    # 6) etiqueta tipo badge (arriba) con el color de la historia
    if etiqueta:
        fe = _fuente(int(W * (0.026 if horizontal else 0.045)))
        t = etiqueta.upper()
        tw, th = d.textlength(t, font=fe), int(fe.size * 1.0)
        padx, pady = int(fe.size * 0.7), int(fe.size * 0.45)
        ex = margen if horizontal else (W - tw - 2 * padx) // 2
        ey = int(H * (0.07 if horizontal else 0.095))
        d.rounded_rectangle([ex, ey, ex + tw + 2 * padx, ey + th + 2 * pady], radius=int(fe.size * 0.35), fill=acento_rgb, outline="black", width=4)
        d.text((ex + padx, ey + pady - int(fe.size * 0.08)), t, font=fe, fill="black")

    # 7) firma del canal + marco fino de acento
    ff = _fuente(int(W * (0.018 if horizontal else 0.034)))
    xf = margen if horizontal else (W - d.textlength(CANAL_FIRMA, font=ff)) / 2
    d.text((xf, int(H * (0.92 if horizontal else 0.80))), CANAL_FIRMA, font=ff, fill="white", stroke_width=3, stroke_fill="black")
    inset = int(min(W, H) * 0.018)
    d.rectangle([inset, inset, W - inset, H - inset], outline=acento_rgb, width=max(4, int(min(W, H) * 0.006)))
    img.save(ruta_out)

# ---------------------------------------------------------------------------
# VIDEO: EXPORT / GUION
# ---------------------------------------------------------------------------
def alinear_escenas_con_audio(escenas, ruta_audio, texto_completo=""):
    """Calcula las marcas de tiempo exactas (inicio, fin, duracion) de cada escena con respecto al MP3 real."""
    try:
        a = AudioFileClip(ruta_audio)
        duracion_total = float(a.duration)
        a.close()
    except Exception:
        duracion_total = 0.0

    imgs_validas = [e for e in escenas if e.get("archivo")]
    if not imgs_validas or duracion_total <= 0:
        return escenas

    # 1. Ponderación por palabras de la frase asociada o tamaño de la escena
    pesos = []
    for e in imgs_validas:
        frase = (e.get("frase_guion_asociada") or e.get("tip_texto_pantalla") or "").strip()
        num_palabras = len(re.findall(r'\b\w+\b', frase))
        pesos.append(max(num_palabras, 4)) # mínimo equivalente a 4 palabras

    peso_total = sum(pesos) if sum(pesos) > 0 else 1.0
    tiempo_acumulado = 0.0

    for i, e in enumerate(imgs_validas):
        # Duración proporcional exacta a su fragmento narrado con respecto al MP3
        if i == len(imgs_validas) - 1:
            dur = max(0.5, duracion_total - tiempo_acumulado)
        else:
            dur = (pesos[i] / peso_total) * duracion_total

        e["segundo_inicio"] = round(tiempo_acumulado, 2)
        e["duracion_calculada"] = round(dur, 2)
        tiempo_acumulado += dur
        e["segundo_fin"] = round(tiempo_acumulado, 2)

    return escenas

def exportar_metadata(datos, ruta):
    try:
        with open(os.path.join(ruta, "metadata.md"), "w", encoding="utf-8") as f:
            f.write(f"# 🎬 {datos.get('titulo', 'Video')}\n\n")
            f.write(f"**Tier:** {datos.get('tier', '')}\n")
            if datos.get("serie"): f.write(f"**🎬 Serie / Saga:** {datos.get('serie')}\n")
            if datos.get("parte"): f.write(f"**🔢 Entrega:** {datos.get('parte')}\n")
            if datos.get("gancho_siguiente_parte"): f.write(f"**🔗 Gancho siguiente parte:** {datos.get('gancho_siguiente_parte')}\n")
            ruteo = datos.get("enrutamiento", {})
            f.write(f"**🚦 Publicar en (prioridad):** {', '.join(ruteo.get('publicar', []))}\n")
            f.write(f"**⛔ Omitir:** {', '.join(ruteo.get('omitir', []))}\n\n")

            # SECCIÓN COPIAR Y PEGAR METRICOOL
            meta = datos.get("metadata", {})
            momento_sel = datos.get("momento_dia") or "multi"
            f.write("═══════════════════════════════════════════════════════════════════\n")
            f.write("📋 GUÍA RÁPIDA: COPIAR Y PEGAR EN METRICOOL (3 MINUTOS)\n")
            f.write("═══════════════════════════════════════════════════════════════════\n")
            f.write("1. En Metricool ve a 'Planificación' ➔ 'Crear publicación'.\n")
            f.write("2. Arrastra el archivo de video: {}.mp4\n".format(os.path.basename(ruta.rstrip("/\\"))))
            f.write("3. Selecciona las casillas de las 4 redes: YT, TikTok, Instagram y Facebook.\n")
            f.write("4. Copia y pega cada bloque en su respectiva red:\n\n")
            
            for red in ["youtube_shorts", "tiktok", "facebook_reels", "instagram_reels", "bilibili"]:
                if red in meta:
                    f.write(f"### {red.upper().replace('_', ' ')}\n")
                    f.write(f"**Título:** {meta[red].get('titulo', '')}\n")
                    f.write(f"**Descripción:** {meta[red].get('descripcion', '')}\n")
                    f.write(f"**Hashtags:** {meta[red].get('hashtags', '')}\n")
                    
                    # Garantizar hora de publicación en TODOS los casos (usando la generada por Claude o el horario calibrado según el momento seleccionado)
                    hora_pub = meta[red].get("hora_publicacion")
                    if not hora_pub:
                        dict_red = HORARIOS_RECOMENDADOS_DEFECTO.get(red, {})
                        hora_pub = dict_red.get(momento_sel) or dict_red.get("multi", "20:00 CST - horario de alta interacción")
                    f.write(f"**🕒 Hora recomendada de publicación:** {hora_pub}\n\n")
            f.write(f"**Comentario Fijado:** {meta.get('comentario_fijado', '')}\n")
            f.write(f"**Respuestas:** {', '.join(meta.get('respuestas_interaccion', []))}\n\n")

            horarios = meta.get("estrategia_horaria", {})
            if horarios or any(meta.get(r, {}).get("hora_publicacion") for r in REDES_VIDEO):
                f.write("### ⏰ ESTRATEGIA PARA CUALQUIER MOMENTO DEL DÍA\n")
                if horarios.get("manana"): f.write(f"- 🌅 **Mañana (07:00 - 10:00 CST):** {horarios['manana']}\n")
                if horarios.get("mediodia"): f.write(f"- ☀️ **Mediodía (12:00 - 15:00 CST):** {horarios['mediodia']}\n")
                if horarios.get("tarde"): f.write(f"- 🌇 **Tarde (16:00 - 19:00 CST):** {horarios['tarde']}\n")
                if horarios.get("noche"): f.write(f"- 🌙 **Noche Prime (20:00 - 23:00 CST):** {horarios['noche']}\n")
                if horarios.get("madrugada"): f.write(f"- 🕯️ **Madrugada / Insomnio (00:00 - 04:00 CST):** {horarios['madrugada']}\n")
                f.write("\n")

            cal = meta.get("calendario_publicacion", {})
            if cal:
                f.write("## 📅 CALENDARIO Y DÍAS DE PUBLICACIÓN RECOMENDADOS\n")
                if cal.get("dias_recomendados"): f.write(f"- **Días ideales:** {cal['dias_recomendados']}\n")
                if cal.get("estrategia_escalonada"): f.write(f"- **Estrategia algorítmica:** {cal['estrategia_escalonada']}\n")
                f.write("\n---\n")

            f.write(f"## 📜 GUION COMPLETO\n{datos.get('locucion_elevenlabs', {}).get('texto_completo', '')}\n\n---\n")
            for clave, arch, uso in (("portada", "00_portada.png", "YouTube / Instagram"), ("portada_b", "00_portada_tiktok.png", "TikTok / Facebook"),
                                     ("portada_bilibili", "00_portada_bilibili.png", "Bilibili 16:9 (suspendido, lista para cuando se reactive)")):
                p = datos.get(clave, {})
                if p: f.write(f"## 🖼 PORTADA ({uso})\n- Archivo: {arch}\n- Texto: {p.get('texto_portada', '')} {p.get('texto_portada_zh', '')}\n- Palabra clave: {p.get('palabra_clave', '')} | Etiqueta: {p.get('etiqueta', '')} | Acento: {p.get('color_acento', '')}\n- Prompt: {p.get('prompt', '')}\n\n")
            f.write("---\n")
            f.write("## 🖼 ESCENAS, CRONOMETRAJE (.MP3) Y SFX CAPCUT\n")
            for i, e in enumerate(datos.get('escenas', []), 1):
                t_ini = e.get("segundo_inicio", 0)
                t_fin = e.get("segundo_fin", 0)
                m_ini, s_ini = divmod(int(t_ini), 60)
                m_fin, s_fin = divmod(int(t_fin), 60)
                stamp = f"[{m_ini:02d}:{s_ini:02d} - {m_fin:02d}:{s_fin:02d}]"
                f.write(f"### Escena {i}: {e.get('archivo', '')} {stamp}\n")
                if e.get("frase_guion_asociada"): f.write(f"- Frase audio: \"{e.get('frase_guion_asociada')}\"\n")
                f.write(f"- Movimiento: {e.get('movimiento_camara', 'zoom_in')}\n")
                f.write(f"- Transición: {e.get('transicion', 'crossfade')}\n")
                f.write(f"- SFX: {e.get('sfx_capcut', '')}\n")
                f.write(f"- Texto en Pantalla: {e.get('tip_texto_pantalla', '')}\n")
                f.write(f"- Prompt: {e.get('prompt', '')}\n\n")
    except Exception as e: print(f"Error metadata: {e}")

# Matriz de enrutamiento: publicar siempre en las 4 redes principales + Bilibili
REDES_VIDEO = ["youtube_shorts", "tiktok", "facebook_reels", "instagram_reels", "bilibili"]
RUTEO_TIER = {
    "FOLCLORE": ["youtube_shorts", "tiktok", "instagram_reels", "facebook_reels", "bilibili"],
    "ARQUITECTURA": ["youtube_shorts", "tiktok", "instagram_reels", "facebook_reels", "bilibili"],
    "MORBO": ["youtube_shorts", "tiktok", "instagram_reels", "facebook_reels", "bilibili"],
    "ABSURDO": ["youtube_shorts", "tiktok", "instagram_reels", "facebook_reels", "bilibili"],
}

# Horarios de publicación recomendados (hora Ciudad de México, CST) calibrados por red y por momento del día
HORARIOS_RECOMENDADOS_DEFECTO = {
    "youtube_shorts": {
        "multi": "Viernes o Sábado 19:30 CST - ventana prime time para YouTube Shorts con mayor retención",
        "manana": "08:15 CST - camino al trabajo/estudio, alto consumo móvil matutino",
        "mediodia": "13:30 CST - descanso de almuerzo/comida",
        "tarde": "17:45 CST - salida laboral/escolar",
        "noche": "20:30 CST - prime time nocturno en casa",
        "madrugada": "01:00 CST - nicho insomnio/misterio nocturno"
    },
    "tiktok": {
        "multi": "Domingo o Jueves 21:00 CST - pico semanal de audiencia en TikTok para misterio y leyendas",
        "manana": "07:45 CST - primera revisión matutina de feed",
        "mediodia": "14:00 CST - pico de interacción de mediodía",
        "tarde": "18:30 CST - regreso a casa y tiempo libre",
        "noche": "21:30 CST - máximo pico nocturno de retención en TikTok",
        "madrugada": "02:15 CST - comunidad nocturna activa buscando historias perturbadoras"
    },
    "facebook_reels": {
        "multi": "Sábado o Miércoles 19:00 CST - mayor volumen de compartidos y comentarios familiares",
        "manana": "08:30 CST - lectura matutina y noticias",
        "mediodia": "13:00 CST - break laboral para debate y compartir",
        "tarde": "17:30 CST - pico de interacción vespertina en Facebook",
        "noche": "20:00 CST - hora estelar de debate y etiquetas en comentarios",
        "madrugada": "00:30 CST - usuarios nocturnos conectados"
    },
    "instagram_reels": {
        "multi": "Miércoles o Martes 20:30 CST - día y hora con mayor ratio de guardados y compartidos",
        "manana": "09:00 CST - pausa matutina",
        "mediodia": "13:15 CST - pausa de comida, contenido estético",
        "tarde": "18:00 CST - desconexión y navegación en feed",
        "noche": "20:45 CST - mayor tiempo de visualización y guardados",
        "madrugada": "01:30 CST - estética oscura para desvelados"
    },
    "bilibili": {
        "multi": "Viernes 21:00 CST (Sábado 11:00 Beijing) - hora pico de fin de semana para videos de misterio y lore",
        "manana": "07:30 CST (21:30 Beijing) - prime time nocturno en Asia",
        "mediodia": "13:00 CST (03:00 Beijing) - tráfico de la tarde / usuarios globales",
        "tarde": "18:00 CST (08:00 Beijing) - inicio de mañana en Asia",
        "noche": "21:30 CST (11:30 Beijing) - mediodía en Asia y noche en América",
        "madrugada": "02:00 CST (16:00 Beijing) - tarde asiática activa"
    },
    "facebook": {
        "multi": "Sábado 18:30 CST - pico de lectura y participación en carruseles de fotos",
        "manana": "08:30 CST - inicio de jornada",
        "mediodia": "13:00 CST - break de comida",
        "tarde": "17:30 CST - tarde de interacción",
        "noche": "20:00 CST - debate nocturno",
        "madrugada": "00:30 CST - insomnio"
    },
    "instagram": {
        "multi": "Miércoles o Domingo 20:00 CST - día de mayor retención para carruseles de fotos (guardar y compartir)",
        "manana": "09:00 CST - historias de mañana",
        "mediodia": "13:30 CST - navegación en break",
        "tarde": "18:15 CST - tarde de relax",
        "noche": "20:30 CST - prime time para carruseles",
        "madrugada": "01:00 CST - historias nocturnas"
    },
    "youtube_community": {
        "multi": "Martes o Jueves 18:00 CST - mayor votación en encuestas y comentarios de la pestaña Comunidad",
        "manana": "08:00 CST - votación matutina",
        "mediodia": "12:30 CST - encuesta de mediodía",
        "tarde": "17:00 CST - previa de la tarde",
        "noche": "19:30 CST - comunidad activa antes de videos largos",
        "madrugada": "23:30 CST - debate nocturno"
    }
}

def aplicar_ruteo(ep):
    tier = str(ep.get("tier", "")).upper().strip()
    if tier not in RUTEO_TIER: tier = "ARQUITECTURA"; ep["tier"] = tier
    publicar = RUTEO_TIER[tier]
    ep["enrutamiento"] = {"publicar": publicar, "omitir": [r for r in REDES_VIDEO if r not in publicar]}
    meta = ep.get("metadata", {})
    for r in ep["enrutamiento"]["omitir"]: meta.pop(r, None)

def ajustar_cuota_escenas(escenas, n_objetivo):
    """Poda o muestrea uniformemente una lista de escenas para que no exceda n_objetivo, conservando gancho y cierre."""
    if not escenas or len(escenas) <= n_objetivo:
        return escenas
    if n_objetivo <= 1:
        return escenas[:1]

    paso = (len(escenas) - 1) / (n_objetivo - 1)
    indices = [int(round(i * paso)) for i in range(n_objetivo)]

    vistos = set()
    indices_unicos = []
    for idx in indices:
        if idx not in vistos:
            vistos.add(idx)
            indices_unicos.append(idx)

    if len(indices_unicos) < n_objetivo:
        for idx in range(len(escenas)):
            if idx not in vistos:
                indices_unicos.append(idx)
                vistos.add(idx)
                if len(indices_unicos) == n_objetivo:
                    break
        indices_unicos.sort()

    escenas_podadas = [escenas[i] for i in indices_unicos]

    for num, esc in enumerate(escenas_podadas, 1):
        arch_orig = esc.get("archivo", "")
        nombre_base = re.sub(r'^\d+_', '', arch_orig) or f"escena_{num}.png"
        esc["archivo"] = f"{num:02d}_{nombre_base}"

    return escenas_podadas

def generar_datos_completos(peticion, evitar=(), guion_previo=None, on_guion=None):
    sys_guion = open("prompt_guion.md", encoding="utf-8").read()
    if guion_previo:
        print("\n--- FASE 1: GUION (reutilizado del intento anterior) ---")
        datos_guion = guion_previo
    else:
        print("\n--- FASE 1: GUION Y ESTRATEGIA ---")
        datos_guion = llamar_claude(sys_guion, construir_mensaje(peticion, evitar))
        if not datos_guion: return None

    ep = datos_guion.get("episodio", {})
    aplicar_ruteo(ep)
    print(f"   Tier: {ep.get('tier')} → publicar en: {', '.join(ep['enrutamiento']['publicar'])}")
    if on_guion and not guion_previo: on_guion(datos_guion)

    print("--- FASE 2: DIRECCIÓN DE ARTE ---")
    sys_escenas = open("prompt_escenas.md", encoding="utf-8").read()
    historia = ep.get("locucion_elevenlabs", {}).get("texto_completo", "")

    # Cuota visual calibrada: ~4.5 segundos por escena (rango 4.0 a 5.0 s para dinamismo cinematográfico sin saturar)
    palabras = len(re.findall(r'\b\w+\b', re.sub(r'\[.*?\]', '', historia)))
    duracion_est_s = max(20.0, palabras / 2.3)
    n_escenas_objetivo = max(12, min(26, round(duracion_est_s / 4.5)))
    print(f"   ⏱ Duración estimada: {duracion_est_s:.0f}s ({palabras} palabras) → Cuota visual: {n_escenas_objetivo} escenas (~{duracion_est_s/n_escenas_objetivo:.1f}s/imagen)")

    mensaje_escenas = (
        f"Título: {ep.get('titulo', '')}\n"
        f"TIER: {ep.get('tier', '')}\n"
        f"Duración estimada de locución: {duracion_est_s:.0f} segundos ({palabras} palabras).\n"
        f"CANTIDAD OBLIGATORIA: Genera EXACTAMENTE {n_escenas_objetivo} escenas (ritmo cinematográfico de ~4.5s por imagen; NUNCA más de {n_escenas_objetivo}).\n\n"
        f"Visuales para:\n\n{historia}"
    )
    fb_visual = obtener_feedback_metricas(modo="escenas")
    if fb_visual:
        mensaje_escenas += fb_visual

    datos_escenas = llamar_claude(sys_escenas, mensaje_escenas)
    if not datos_escenas: return None

    ep["estilo_visual_global"] = datos_escenas.get("estilo_visual_global", "")
    ep["portada"] = datos_escenas.get("portada", {})
    ep["portada_b"] = datos_escenas.get("portada_b", {})
    ep["portada_bilibili"] = datos_escenas.get("portada_bilibili", {})

    escenas = datos_escenas.get("escenas", [])
    if len(escenas) > n_escenas_objetivo:
        print(f"   ℹ Claude devolvió {len(escenas)} escenas; ajustando a {n_escenas_objetivo} escenas para mantener ritmo óptimo (~4.5s/imagen).")
        escenas = ajustar_cuota_escenas(escenas, n_escenas_objetivo)

    ep["escenas"] = escenas
    for k in ("momento_dia", "dia_semana", "fecha_liberacion", "hora_programacion", "enfoque_dia", "fecha_prog_iso"):
        if peticion.get(k):
            ep[k] = peticion[k]
    return datos_guion

def mover_clip(clip_img, mov="zoom_in", dur=4.0):
    """Aplica animación de cámara cinematográfica fluida y sin saltos (zoom in/out, pan left/right, tilt up/down, drift)."""
    mov = (mov or "zoom_in").lower().strip()
    factor = 0.08  # 8% para máxima suavidad visual
    W, H = 1080, 1920

    if mov == "zoom_in":
        base = clip_img.resize(height=H)
        if base.w < W: base = base.resize(width=W)
        base = base.crop(x_center=base.w/2, y_center=base.h/2, width=W, height=H)
        c = base.resize(lambda t: 1.0 + factor * (t / max(0.1, dur))).set_position(('center', 'center'))
        return CompositeVideoClip([c], size=(W, H)).set_duration(dur)

    elif mov == "zoom_out":
        base = clip_img.resize(height=H)
        if base.w < W: base = base.resize(width=W)
        base = base.crop(x_center=base.w/2, y_center=base.h/2, width=W, height=H)
        c = base.resize(lambda t: (1.0 + factor) - factor * (t / max(0.1, dur))).set_position(('center', 'center'))
        return CompositeVideoClip([c], size=(W, H)).set_duration(dur)

    elif mov in ("pan_left", "pan"):
        desp = 100
        base = clip_img.resize(height=H)
        if base.w < W + desp: base = base.resize(width=W + desp)
        c = base.set_position(lambda t: (int(-desp * (t / max(0.1, dur))), 'center'))
        return CompositeVideoClip([c], size=(W, H)).set_duration(dur)

    elif mov == "pan_right":
        desp = 100
        base = clip_img.resize(height=H)
        if base.w < W + desp: base = base.resize(width=W + desp)
        c = base.set_position(lambda t: (int(-desp + desp * (t / max(0.1, dur))), 'center'))
        return CompositeVideoClip([c], size=(W, H)).set_duration(dur)

    elif mov in ("tilt_up", "tilt"):
        desp = 120
        base = clip_img.resize(width=W)
        if base.h < H + desp: base = base.resize(height=H + desp)
        c = base.set_position(lambda t: ('center', int(-desp + desp * (t / max(0.1, dur)))))
        return CompositeVideoClip([c], size=(W, H)).set_duration(dur)

    elif mov == "tilt_down":
        desp = 120
        base = clip_img.resize(width=W)
        if base.h < H + desp: base = base.resize(height=H + desp)
        c = base.set_position(lambda t: ('center', int(-desp * (t / max(0.1, dur)))))
        return CompositeVideoClip([c], size=(W, H)).set_duration(dur)

    elif mov == "slow_drift":
        desp = 50
        base = clip_img.resize(height=H + desp)
        if base.w < W + desp: base = base.resize(width=W + desp)
        c = base.set_position(lambda t: (int(-desp * (t / max(0.1, dur))), int(-desp * (t / max(0.1, dur)))))
        return CompositeVideoClip([c], size=(W, H)).set_duration(dur)

    else:
        # Por defecto: zoom sutil
        base = clip_img.resize(height=H)
        if base.w < W: base = base.resize(width=W)
        base = base.crop(x_center=base.w/2, y_center=base.h/2, width=W, height=H)
        c = base.resize(lambda t: 1.0 + (factor / 2) * (t / max(0.1, dur))).set_position(('center', 'center'))
        return CompositeVideoClip([c], size=(W, H)).set_duration(dur)

def ensamblar(escenas, ruta_audio, ruta_carpeta, titulo, portada=None):
    print(f"\n--- ENSAMBLANDO: {titulo} ---")
    try:
        audio = AudioFileClip(ruta_audio)
    except Exception as e:
        print(f"Error cargando audio {ruta_audio}: {e}")
        return

    # Sincronización exacta con respecto al archivo MP3 generado
    escenas_sincronizadas = alinear_escenas_con_audio(escenas, ruta_audio)
    imgs = [e for e in escenas_sincronizadas if os.path.exists(os.path.join(ruta_carpeta, e.get("archivo", "")))]
    if not imgs: return

    duracion_defecto = audio.duration / len(imgs)
    t_trans = 0.35  # duración del crossfade óptico suave
    t_portada = DURACION_PORTADA if (portada and os.path.exists(portada) and DURACION_PORTADA > 0) else 0.0

    capas_totales = []

    # 1. Portada al inicio (miniatura para plataformas)
    if t_portada > 0:
        c_portada = ImageClip(portada).set_duration(t_portada).resize((1080, 1920)).set_start(0.0)
        capas_totales.append(c_portada)

    # 2. Construcción de capas con transiciones ópticas fluidas
    for i, esc in enumerate(imgs):
        t_inicio = t_portada + esc.get("segundo_inicio", i * duracion_defecto)
        dur_clip = esc.get("duracion_calculada", duracion_defecto)
        trans = esc.get("transicion", "crossfade").lower().strip()
        mov = esc.get("movimiento_camara", "zoom_in")

        # Extender clip si el siguiente realiza disolvencia (crossfade), evitando saltos o marcos negros
        proxima_cross = (i < len(imgs) - 1) and (imgs[i + 1].get("transicion", "crossfade").lower().strip() == "crossfade")
        dur_extendida = dur_clip + (t_trans if proxima_cross else (0.5 if i == len(imgs) - 1 else 0.0))

        ruta_img = os.path.join(ruta_carpeta, esc.get("archivo"))
        clip_base = ImageClip(ruta_img)
        clip_anim = mover_clip(clip_base, mov, dur=dur_extendida)

        # Transición de entrada
        if trans == "fade_black":
            clip_anim = clip_anim.fadein(0.25).fadeout(0.25)
        elif trans == "crossfade" and i > 0:
            clip_anim = clip_anim.crossfadein(t_trans)

        clip_anim = clip_anim.set_start(t_inicio)
        capas_totales.append(clip_anim)

        # Texto de gancho en primer clip
        texto_pantalla = esc.get("tip_texto_pantalla")
        if i == 0 and texto_pantalla and texto_pantalla.strip() != "":
            try:
                fuente = FUENTE_IMPACT if os.path.exists(FUENTE_IMPACT) else "Arial-Bold"
                texto = texto_pantalla.upper()
                pos_y = int(1920 * 0.20)
                dur_txt = min(dur_clip, 3.5)

                txt_sombra1 = TextClip(texto, fontsize=105, color='black', font=fuente, method='caption', size=(900, None)).set_position(('center', pos_y + 18)).set_duration(dur_txt).set_start(t_inicio)
                txt_sombra2 = TextClip(texto, fontsize=105, color='black', font=fuente, method='caption', size=(900, None)).set_position(('center', pos_y + 9)).set_duration(dur_txt).set_start(t_inicio)
                txt_borde = TextClip(texto, fontsize=105, color='black', stroke_color='black', stroke_width=15, font=fuente, method='caption', size=(900, None)).set_position(('center', pos_y)).set_duration(dur_txt).set_start(t_inicio)
                txt_color = TextClip(texto, fontsize=105, color='#FFDE00', font=fuente, method='caption', size=(900, None)).set_position(('center', pos_y)).set_duration(dur_txt).set_start(t_inicio)
                capas_totales.extend([txt_sombra1, txt_sombra2, txt_borde, txt_color])
            except Exception as e_txt:
                print(f"Aviso: no se pudo superponer texto en primer clip ({e_txt}), continuando sin texto.")

    duracion_video = t_portada + audio.duration
    vid = CompositeVideoClip(capas_totales, size=(1080, 1920)).set_duration(duracion_video).set_audio(audio.audio_fadeout(0.5))
    destino = os.path.join(ruta_carpeta, f"{titulo}.mp4")
    temporal = os.path.join(ruta_carpeta, f"{titulo}.parcial.mp4")

    # Limitación estricta de CPU/RAM en Mac mini M4: 1 solo hilo en núcleos de eficiencia y tamaño óptimo (~15 MB)
    num_threads = 1
    try:
        vid.write_videofile(
            temporal, fps=24, codec="libx264", audio_codec="aac", preset="veryfast",
            threads=num_threads,
            ffmpeg_params=["-crf", "24", "-maxrate", "3500k", "-bufsize", "7000k", "-x264-params", "threads=1:lookahead_threads=1"],
            logger=None
        )
        os.replace(temporal, destino)
        tam_mb_out = os.path.getsize(destino) / (1024 * 1024)
        print(f"✔ Video ensamblado: {destino} ({tam_mb_out:.1f} MB)")
    finally:
        try: vid.close()
        except Exception: pass
        try: audio.close()
        except Exception: pass
        for cap in capas_totales:
            try: cap.close()
            except Exception: pass
        gc.collect()

def validar_video_actual(mp4, ep, carpeta):
    """
    Verifica estrictamente que el archivo .mp4 a subir corresponda al episodio actual
    (misma carpeta, mismo slug de título, tamaño válido y duración coincidente con 00_locucion.mp3)
    para garantizar que JAMÁS se suba un video pasado o de otra carpeta.
    """
    if not mp4 or not os.path.exists(mp4):
        print(f"✖ [VALIDACIÓN FALLIDA]: El archivo de video '{mp4}' no existe.")
        return False

    ruta_abs_mp4 = os.path.abspath(mp4)
    ruta_abs_esperada = os.path.abspath(os.path.join(carpeta, f"{slug(carpeta)}.mp4"))
    if os.path.dirname(ruta_abs_mp4) != os.path.dirname(ruta_abs_esperada):
        print(f"✖ [ALERTA ANTI-VIDEO PASADO]: La ruta '{mp4}' no pertenece a la carpeta activa '{carpeta}'. Se bloquea la subida.")
        return False

    tit_ep = ep.get("titulo", "")
    if tit_ep and slug(tit_ep) != slug(os.path.basename(carpeta.rstrip("/\\"))):
        print(f"✖ [ALERTA ANTI-VIDEO PASADO]: El título del episodio ('{tit_ep}') no coincide con la carpeta ('{carpeta}'). Se bloquea la subida.")
        return False

    tam_mb = os.path.getsize(mp4) / (1024 * 1024)
    if tam_mb < 0.2:
        print(f"✖ [VALIDACIÓN FALLIDA]: El archivo '{mp4}' está vacío o corrupto ({tam_mb:.2f} MB).")
        return False

    ruta_aud = os.path.join(carpeta, "00_locucion.mp3")
    if os.path.exists(ruta_aud):
        try:
            from moviepy.editor import VideoFileClip
            v_clip = VideoFileClip(mp4)
            dur_v = float(v_clip.duration)
            v_clip.close()
            dur_a = duracion_audio(ruta_aud)
            if dur_a > 0 and abs(dur_v - dur_a) > 3.0:
                print(f"✖ [ALERTA ANTI-VIDEO PASADO]: Duración del MP4 ({dur_v:.1f}s) no coincide con la locución actual ({dur_a:.1f}s). Se bloquea la subida.")
                return False
            print(f"🔒 [VALIDACIÓN DE ARCHIVO OK]: '{os.path.basename(mp4)}' ({tam_mb:.1f} MB, {dur_v:.1f}s) corresponde 100% al episodio '{tit_ep or carpeta}'.")
        except Exception:
            print(f"🔒 [VALIDACIÓN DE ARCHIVO OK]: '{os.path.basename(mp4)}' ({tam_mb:.1f} MB) verificado para '{tit_ep or carpeta}'.")
    return True

def publicar_video_en_4_redes(mp4, ep, carpeta, fecha_prog_iso=None, on_progress=None):
    """
    Valida que el MP4 sea el video actual y lo publica/programa en las 4 redes sociales
    (YouTube Shorts, TikTok, Facebook Page e Instagram Reels) ANTES de subir la carpeta a Google Drive.
    Evita re-subir a redes donde este episodio ya fue publicado en una ejecución previa.
    Imprime un reporte explícito del estado de cada red y devuelve (todas_redes_ok, redes_pendientes).
    """
    print("\n🚀 [FASE A: REDES SOCIALES PRIMERO] Validando archivo y preparando las 4 redes...")
    if not validar_video_actual(mp4, ep, carpeta):
        return False, ["Validación de archivo MP4"]

    if fecha_prog_iso:
        print(f"🕒 [MODO PROGRAMADO ACTIVO]: Ninguna red liberará el video de inmediato.")
        print(f"   Horario objetivo definido: {formatear_hora_cst(fecha_prog_iso)}")
    else:
        print("⚡ [MODO PUBLICACIÓN INMEDIATA]: El video se publicará como público ahora mismo.")

    meta_all = ep.get("metadata", {}) or {}
    primer_com = meta_all.get("comentario_fijado", "")
    ya_publicadas = ep.setdefault("redes_publicadas", {})
    estado_redes = {}
    redes_pendientes = []
    etiqueta_modo = f"PROGRAMADO ({formatear_hora_cst(fecha_prog_iso)})" if fecha_prog_iso else "PUBLICADO INMEDIATO"

    # 1. YouTube Shorts (con primer comentario fijado)
    meta_yt = meta_all.get("youtube_shorts") or meta_all.get("facebook_reels") or meta_all.get("tiktok") or {}
    if ya_publicadas.get("youtube_shorts"):
        vid_prev = ya_publicadas["youtube_shorts"]
        estado_redes["YouTube Shorts"] = f"✅ YA SUBIDO PREVIAMENTE (https://youtube.com/shorts/{vid_prev})"
    elif os.path.exists("token_youtube.json"):
        tit_yt = meta_yt.get("titulo") or ep.get("titulo", carpeta)
        tags_yt = meta_yt.get("hashtags", "#Shorts #LeyendasDeMexico #HistoriaDeMexico")
        if "#shorts" not in tags_yt.lower():
            tags_yt = "#Shorts " + tags_yt
        desc_yt = (meta_yt.get("descripcion", "") + "\n\n" + tags_yt).strip()
        vid_yt = subir_y_programar_youtube_short(
            mp4, tit_yt, desc_yt, tags=tags_yt,
            publish_at_iso=fecha_prog_iso,
            primer_comentario=primer_com
        )
        if vid_yt:
            ya_publicadas["youtube_shorts"] = vid_yt
            if on_progress: on_progress()
            estado_redes["YouTube Shorts"] = f"✅ {etiqueta_modo} -> https://youtube.com/shorts/{vid_yt}"
        else:
            estado_redes["YouTube Shorts"] = "❌ FALLÓ / PENDIENTE (revisar token_youtube.json o cuota de YouTube API)"
            redes_pendientes.append("YouTube Shorts")
    else:
        estado_redes["YouTube Shorts"] = "⚠ NO CONECTADO (falta token_youtube.json)"
        redes_pendientes.append("YouTube Shorts (sin token)")

    # 2. TikTok (modo paramétrico: preparado en local o subida por API)
    meta_tt = meta_all.get("tiktok") or meta_all.get("facebook_reels") or {}
    tit_tt = meta_tt.get("titulo") or ep.get("titulo", carpeta)
    desc_tt = meta_tt.get("descripcion", "")
    tags_tt = meta_tt.get("hashtags", "#parati #historiasreales #misterio")
    caption_tt = f"{tit_tt}\n\n{desc_tt}\n\n{tags_tt}".strip()

    if ya_publicadas.get("tiktok"):
        estado_redes["TikTok"] = f"✅ YA PROCESADO PREVIAMENTE (ID: {ya_publicadas['tiktok']})"
    elif not SUBIR_TIKTOK_AUTOMATICO:
        clip_ok = False
        try:
            subprocess.run(["pbcopy"], input=caption_tt.encode("utf-8"), check=True)
            clip_ok = True
        except Exception:
            pass
        clip_str = " (📋 Copy copiado al Portapapeles Mac)" if clip_ok else ""
        hora_tt = formatear_hora_cst(fecha_prog_iso) if fecha_prog_iso else (meta_tt.get("hora_publicacion") or "Horario libre")
        estado_redes["TikTok"] = f"📁 PREPARADO EN LOCAL{clip_str} | Publicar: {hora_tt}"
        ya_publicadas["tiktok"] = "local_preparado"
        if on_progress: on_progress()
        print(f"ℹ [TIKTOK PARAMÉTRICO]: Subida por API desactivada (SUBIR_TIKTOK_AUTOMATICO=0).")
        print(f"   🎬 Video conservado en Mac mini: '{os.path.basename(mp4)}'")
        print(f"   🕒 Horario recomendado: {hora_tt}")
        if clip_ok:
            print("   📋 ¡Título, copy y hashtags copiados a tu Portapapeles para pegar directo en TikTok!")
        else:
            print(f"   📋 Copy listo en metadata.md:\n      {tit_tt} {tags_tt}")
    elif os.path.exists("token_tiktok.json"):
        res_tt = subir_video_tiktok(mp4, titulo=tit_tt, descripcion=desc_tt, hashtags=tags_tt)
        if res_tt:
            ya_publicadas["tiktok"] = res_tt
            if on_progress: on_progress()
            hora_sug_tt = formatear_hora_cst(fecha_prog_iso) if fecha_prog_iso else (meta_tt.get("hora_publicacion") or "Horario libre")
            estado_redes["TikTok"] = f"✅ EN BORRADORES (ID: {res_tt}) | Programar en app para: {hora_sug_tt}"
        else:
            estado_redes["TikTok"] = "❌ FALLÓ / PENDIENTE (revisar conexión TikTok en Menú -> Panel de Redes)"
            redes_pendientes.append("TikTok")
    else:
        estado_redes["TikTok"] = "⚠ NO CONECTADO (falta token_tiktok.json)"
        redes_pendientes.append("TikTok (sin token)")

    # 3. Facebook Page e Instagram Reels
    meta_fb = meta_all.get("facebook_reels") or meta_all.get("tiktok") or {}
    meta_ig = meta_all.get("instagram_reels") or meta_all.get("facebook_reels") or meta_all.get("tiktok") or {}
    if os.path.exists("token_meta.json"):
        if ya_publicadas.get("facebook_reels"):
            estado_redes["Facebook Page"] = f"✅ YA SUBIDO PREVIAMENTE (ID: {ya_publicadas['facebook_reels']})"
        else:
            tit_fb = meta_fb.get("titulo") or ep.get("titulo", carpeta)
            desc_fb = meta_fb.get("descripcion", "")
            res_fb = subir_video_facebook(mp4, titulo=tit_fb, descripcion=desc_fb, publish_at_iso=fecha_prog_iso)
            if res_fb:
                ya_publicadas["facebook_reels"] = res_fb
                if on_progress: on_progress()
                estado_redes["Facebook Page"] = f"✅ {etiqueta_modo} (ID: {res_fb})"
            else:
                estado_redes["Facebook Page"] = "❌ FALLÓ / PENDIENTE (revisar token de Facebook en Menú -> Panel de Redes)"
                redes_pendientes.append("Facebook Page")

        if ya_publicadas.get("instagram_reels"):
            estado_redes["Instagram Reels"] = f"✅ YA SUBIDO PREVIAMENTE (ID: {ya_publicadas['instagram_reels']})"
        else:
            tit_ig = meta_ig.get("titulo") or ep.get("titulo", carpeta)
            desc_ig = meta_ig.get("descripcion", "")
            tags_ig = meta_ig.get("hashtags", "#HistoriaDeMexico #LeyendasMexicanas #Misterio")
            res_ig = subir_video_instagram(mp4, titulo=tit_ig, descripcion=desc_ig, hashtags=tags_ig, publish_at_iso=fecha_prog_iso)
            if res_ig:
                ya_publicadas["instagram_reels"] = res_ig
                if on_progress: on_progress()
                estado_redes["Instagram Reels"] = f"✅ {etiqueta_modo} (ID: {res_ig})"
            else:
                estado_redes["Instagram Reels"] = "❌ FALLÓ / PENDIENTE (revisar subida a Instagram en Menú -> Panel de Redes)"
                redes_pendientes.append("Instagram Reels")
    else:
        estado_redes["Facebook Page"] = "⚠ NO CONECTADO (falta token_meta.json)"
        estado_redes["Instagram Reels"] = "⚠ NO CONECTADO (falta token_meta.json)"
        redes_pendientes.extend(["Facebook Page (sin token)", "Instagram Reels (sin token)"])

    # 4. Bilibili (preparado en local con video, portada horizontal 16:9 y metadata bilingüe)
    meta_bili = meta_all.get("bilibili") or {}
    port_bili = os.path.join(carpeta, "00_portada_bilibili.png")
    port_bili_ok = "✔ Portada horizontal 16:9" if os.path.exists(port_bili) else "Sin portada"
    estado_redes["Bilibili"] = f"📁 PREPARADO EN LOCAL [{port_bili_ok}] | Video .mp4 y tags bilingües en metadata.md"

    print("\n═══════════════════════════════════════════════════════════════════")
    print(f"📊 REPORTE DETALLADO DE REDES SOCIALES: '{carpeta}'")
    print(f"🎬 Video verificado: {os.path.basename(mp4)} | Episodio: {ep.get('titulo', carpeta)}")
    print("═══════════════════════════════════════════════════════════════════")
    for nombre_red, detalle_est in estado_redes.items():
        print(f"   • {nombre_red:<16}: {detalle_est}")
    if redes_pendientes:
        print(f"\n   ⚠️ REDES PENDIENTES ({len(redes_pendientes)}): {', '.join(redes_pendientes)}")
    else:
        print("\n   🎉 ¡REDES SOCIALES PROCESADAS EXITOSAMENTE!")
        print("   📲 Video .mp4, metadata y portadas conservados en la Mac mini para TikTok y Bilibili.")
    print("═══════════════════════════════════════════════════════════════════")

    return (len(redes_pendientes) == 0), redes_pendientes

# ---------------------------------------------------------------------------
# FLUJO 1: LOTE DE VIDEOS
# ---------------------------------------------------------------------------
def duracion_audio(ruta):
    try:
        a = AudioFileClip(ruta); d = a.duration; a.close(); return d
    except Exception: return 0

def acortar_guion(texto, objetivo_s):
    palabras = int(objetivo_s * 2.5)
    r = llamar_claude("Eres editor de guiones de video corto. Responde SOLO JSON: {\"texto_completo\": \"...\"}",
                      f"Reedita este guion para que dure MÁXIMO {objetivo_s} segundos de locución (~{palabras} palabras). "
                      f"Conserva el gancho inicial, la revelación y el cierre con CTA y nombre del canal. Recorta relleno.\n\n{texto}")
    return (r or {}).get("texto_completo")

def preparar_video(ep, titulo):
    """Crea carpeta, metadata y audio (sin SD). Rechaza/reedita guiones cuyo audio exceda MAX_DURACION_S."""
    os.makedirs(titulo, exist_ok=True)
    ruta_aud = os.path.join(titulo, "00_locucion.mp3")
    for intento in range(3):
        if not os.path.exists(ruta_aud): generar_audio(ep.get("locucion_elevenlabs", {}), ruta_aud)
        dur = duracion_audio(ruta_aud)
        if dur <= MAX_DURACION_S: break
        print(f"⚠ Locución de {dur:.0f}s excede {MAX_DURACION_S}s (penalización en Shorts). Reeditando guion ({intento + 1}/3)...")
        nuevo = acortar_guion(ep["locucion_elevenlabs"].get("texto_completo", ""), MAX_DURACION_S - 10)
        if not nuevo: break
        ep["locucion_elevenlabs"]["texto_completo"] = nuevo
        os.remove(ruta_aud)
    else:
        print("⚠ No se logró bajar de la duración máxima; se usa la última versión.")

    dur = duracion_audio(ruta_aud)
    n_max_escenas = max(12, min(26, round(dur / 4.2)))
    if len(ep.get("escenas", [])) > n_max_escenas:
        print(f"   ℹ Ajustando {len(ep['escenas'])} escenas a {n_max_escenas} según la duración real del MP3 ({dur:.1f}s).")
        ep["escenas"] = ajustar_cuota_escenas(ep["escenas"], n_max_escenas)

    # Calibrar marcas de tiempo exactas segundo a segundo sobre el MP3 definitivo
    ep["escenas"] = alinear_escenas_con_audio(ep.get("escenas", []), ruta_aud, ep.get("locucion_elevenlabs", {}).get("texto_completo", ""))
    exportar_metadata(ep, titulo)
    return ruta_aud

# Portadas: (clave en JSON, archivo, tamaño final, tamaño SDXL, alineación, sufijo de composición)
SUFIJO_HORIZONTAL = ", wide cinematic horizontal composition, main subject on the right third, negative space on the left"
COVERS = [
    ("portada", "00_portada", (1080, 1920), (768, 1344), "centro", ""),                       # A: YouTube / Instagram
    ("portada_b", "00_portada_tiktok", (1080, 1920), (768, 1344), "centro", ""),               # B: TikTok / Facebook
    ("portada_bilibili", "00_portada_bilibili", (1920, 1080), (1344, 768), "izq", SUFIJO_HORIZONTAL),  # 16:9 Bilibili
]

def lista_imagenes_faltantes(ep, titulo):
    """Devuelve la cola de trabajos pendientes (portadas y escenas). Es la base de la reanudación."""
    estilo = ep.get("estilo_visual_global", "")
    cola = []
    for clave, nombre, tam_final, tam_sd, alin, sufijo in COVERS:
        por = ep.get(clave) or (ep.get("portada") if clave == "portada_bilibili" else None) or {}
        final = os.path.join(titulo, f"{nombre}.png")
        if por.get("prompt") and not os.path.exists(final):
            cola.append({"prompt": f"{estilo}, {por['prompt']}{sufijo}, {PROMPT_BASE}", "base": os.path.join(titulo, f"{nombre}_base.png"),
                         "final": final, "tam_sd": tam_sd, "tam_final": tam_final, "alinear": alin, "portada": por, "clave": clave})
    for e in ep.get("escenas", []):
        ruta = os.path.join(titulo, e["archivo"])
        if not os.path.exists(ruta):
            cola.append({"prompt": f"{estilo}, {e.get('prompt', '')}, {PROMPT_BASE}", "base": ruta, "final": ruta,
                         "tam_sd": (768, 1344), "portada": None})
    return cola

def renderizar_portada(it, ep):
    p, tier = it["portada"], str(ep.get("tier", "")).upper()
    horizontal = it["alinear"] == "izq"
    zh = (p.get("texto_portada_zh") or "").strip() if horizontal else ""
    es = (p.get("texto_portada") or ep.get("titulo", "")).strip()
    disenar_portada(it["base"], it["final"], zh or es, palabra_clave=("" if zh else p.get("palabra_clave", "")),
                    etiqueta=p.get("etiqueta") or ETIQUETA_TIER.get(tier, ""),
                    acento=_hex_ok(p.get("color_acento"), PALETAS_TIER.get(tier, "#FFDE00")),
                    tam=it["tam_final"], alinear=it["alinear"], cjk=bool(zh), subtitulo=(es if zh else ""))

def procesar_item(it, ep):
    """Genera (si falta) la imagen base y diseña la portada. Devuelve True si quedó lista."""
    if not os.path.exists(it["base"]): generar_foto(it["prompt"], it["base"], *it["tam_sd"])
    if not os.path.exists(it["base"]): return False
    if it.get("portada") is not None:
        try: renderizar_portada(it, ep)
        except Exception as e: print(f"Error diseñando portada {it['final']}: {e}"); return False
    return os.path.exists(it["final"])

# ---------------------------------------------------------------------------
# LOTES CON REANUDACIÓN (si algo falla, se continúa donde se quedó)
# ---------------------------------------------------------------------------
MANIFEST_VIDEOS = "lote_videos_actual.json"
MANIFEST_HISTORIAS = "lote_historias_actual.json"

def cargar_manifest(ruta):
    try: return json.load(open(ruta, encoding="utf-8")) if os.path.exists(ruta) else None
    except Exception: return None

def guardar_manifest(ruta, m):
    with open(ruta, "w", encoding="utf-8") as f: json.dump(m, f, ensure_ascii=False, indent=2, default=str)

def titulo_de(d):
    return (d.get("episodio", d) or {}).get("titulo", "")

def elegir_lote(ruta, etiqueta, pedir_config=None):
    m = cargar_manifest(ruta)
    if m and not m.get("terminado"):
        hechos = sum(1 for r in m.get("resultados", []) if r)
        if input(f"⏯ Hay un lote de {etiqueta} sin terminar ({hechos}/{len(m.get('resultados', []))} guiones listos). ¿Reanudarlo? [S/n]: ").strip().lower() != "n":
            return m
    res_pedir = pedir_modo(etiqueta)
    if isinstance(res_pedir, tuple):
        peticiones, cfg = res_pedir
    else:
        peticiones = res_pedir
        cfg = pedir_config(len(peticiones)) if pedir_config else {}
    m = {"peticiones": peticiones, "resultados": [None] * len(peticiones), "parciales": {}, "terminado": False,
         "config": cfg}
    guardar_manifest(ruta, m)
    return m

def completar_guiones(m, ruta, generar):
    """Genera los guiones que falten. generar(peticion, usados, i) -> datos|None. Guarda tras cada paso."""
    historias_historicas = obtener_historias_previas()
    for i, pet in enumerate(m["peticiones"]):
        if m["resultados"][i]: continue
        usados_lote = [titulo_de(r) for r in m["resultados"] if r]
        # Unir historias históricas del canal con las ya generadas en el lote actual
        usados = sorted(list(set(historias_historicas + [u for u in usados_lote if u])))
        print(f"\n=========== {i + 1}/{len(m['peticiones'])} ===========")
        datos = None
        for _ in range(2):
            datos = generar(pet, usados, i)
            if datos: break
        if not datos:
            print("✖ Falló este guion (el progreso parcial quedó guardado; se reanuda en la próxima ejecución). Continúo con el siguiente.")
            continue
        m["resultados"][i] = datos
        m["parciales"].pop(str(i), None)
        guardar_manifest(ruta, m)
        tit = titulo_de(datos)
        if tit:
            tipo_contenido = "historia_foto" if "historia" in ruta else "video"
            ep_info = datos.get("episodio", datos) if isinstance(datos, dict) else {}
            detalles_ini = {
                "estado": "guion_listo",
                "tier": ep_info.get("tier"),
                "carpeta": slug(tit, 40),
                "momento_dia": pet.get("momento_dia")
            }
            registrar_historia_usada(tit, tipo=tipo_contenido, detalles=detalles_ini)
            if tit not in historias_historicas: historias_historicas.append(tit)

def flujo_videos(manifest_preparado=None):
    if manifest_preparado:
        m = manifest_preparado
    else:
        m = elegir_lote(MANIFEST_VIDEOS, "videos", pedir_config_videos)
    cfg = m.get("config") or {}
    momento = cfg.get("momento_dia", "multi")
    fecha_lib = cfg.get("fecha_liberacion")
    peticiones = m.get("peticiones", [])

    # Si se eligió semana completa
    if momento == "semana_completa":
        print(f"\n📅 Programación de Semana Completa (Lunes a Domingo):")
        for idx, pet in enumerate(peticiones):
            dia_nom = pet.get("dia_semana", f"Día {idx+1}")
            f_lib = pet.get("fecha_liberacion", "")
            h_prog = pet.get("hora_programacion", "")
            print(f"   • {dia_nom:<9} ({f_lib}): {h_prog} | {pet.get('enfoque_dia', '')}")
    # Si se eligió jornada completa o hay exactamente 5 peticiones y momento es jornada_completa
    elif momento == "jornada_completa" or (len(peticiones) == 5 and momento == "jornada_completa"):
        for idx, pet in enumerate(peticiones):
            franja = ORDEN_5_FRANJAS[idx % len(ORDEN_5_FRANJAS)]
            pet["momento_dia"] = franja
            if fecha_lib:
                pet["fecha_liberacion"] = fecha_lib
        print(f"\n🌅☀️🌇🌙🕯️ Distribución 24H aplicada a los {len(peticiones)} videos" + (f" para el día {fecha_lib}:" if fecha_lib else ":"))
        for idx, pet in enumerate(peticiones):
            print(f"   Video {idx + 1}: {pet.get('momento_dia', '').upper()}")
    else:
        for pet in peticiones:
            pet.setdefault("momento_dia", momento)
            if fecha_lib:
                pet["fecha_liberacion"] = fecha_lib

    def gen(pet, usados, i):
        def guardar_parcial(g):  # fase 1 lista: si falla la fase 2 no se repite el guion
            m["parciales"][str(i)] = g; guardar_manifest(MANIFEST_VIDEOS, m)
        return generar_datos_completos(pet, usados, guion_previo=m["parciales"].get(str(i)), on_guion=guardar_parcial)
    completar_guiones(m, MANIFEST_VIDEOS, gen)
    lote = [r for r in m["resultados"] if r]
    if not lote: return

    trabajos = []  # (ep, carpeta, ruta_audio)
    for datos in lote:
        ep = datos.get("episodio", {})
        carpeta = slug(ep.get("titulo", "Video"))
        # Evitar regenerar o re-subir un video pasado de un lote que ya fue completado y respaldado
        if ep.get("completado_y_respaldado"):
            print(f"✔ '{carpeta}' ya fue publicado en las 4 redes y respaldado en una ejecución previa; se omite.")
            continue
        try: trabajos.append((ep, carpeta, preparar_video(ep, carpeta)))
        except Exception as e: print(f"✖ Falló la preparación de '{carpeta}': {e}")

    fallos = 0
    pendientes = [(ep, c) for ep, c, _ in trabajos if lista_imagenes_faltantes(ep, c)]
    if pendientes:
        p = iniciar_sd()
        try:
            for ep, c in pendientes:
                cola = lista_imagenes_faltantes(ep, c)
                print(f"\nGenerando {len(cola)} imágenes pendientes para '{c}' en SDXL Lightning...")
                for it in cola:
                    if not procesar_item(it, ep): fallos += 1
        finally:
            detener_sd(p)

    print("\n🎬 Ensamblando y publicando videos automáticamente...")
    for ep, c, aud in trabajos:
        mp4 = os.path.join(c, f"{c}.mp4")
        if not os.path.exists(mp4):
            if not os.path.exists(aud) or lista_imagenes_faltantes(ep, c):
                print(f"⚠ '{c}' tiene audio o imágenes pendientes; se omite el ensamblado."); fallos += 1; continue
            ensamblar(ep.get("escenas", []), aud, c, c, portada=os.path.join(c, "00_portada.png"))
        else:
            print(f"✔ '{c}' ya tenía su archivo '{mp4}' ensamblado en local; verificando redes pendientes...")

        try:
            if os.path.exists(mp4):
                dur_mp4 = duracion_audio(aud)
                registrar_historia_usada(ep.get("titulo", c), tipo="video", detalles={
                    "estado": "video_ensamblado",
                    "duracion_audio_s": round(dur_mp4, 1),
                    "num_imagenes": len(ep.get("escenas", [])),
                    "carpeta": c,
                    "archivo_salida": f"{c}.mp4",
                    "tier": ep.get("tier")
                })
                # Determinar fecha/hora de programación ISO
                modo_prog = cfg.get("modo_programacion", "auto_franja")
                momento_actual = ep.get("momento_dia") or cfg.get("momento_dia") or "multi"
                fecha_prog_iso = ep.get("fecha_prog_iso")
                if not fecha_prog_iso:
                    if modo_prog == "auto_franja":
                        fecha_prog_iso = calcular_fecha_programacion(
                            momento_dia=momento_actual,
                            fecha_base=ep.get("fecha_liberacion") or cfg.get("fecha_liberacion"),
                            forzar_programacion=True
                        )
                    elif modo_prog == "personalizada":
                        fecha_prog_iso = calcular_fecha_programacion(
                            momento_dia=momento_actual,
                            hora_especifica=cfg.get("hora_personalizada"),
                            fecha_base=ep.get("fecha_liberacion") or cfg.get("fecha_liberacion"),
                            forzar_programacion=True
                        )

                # PASO 1: Publicar primero en las 4 redes sociales mientras los archivos están en local
                todas_redes_ok, redes_pendientes = publicar_video_en_4_redes(
                    mp4, ep, c,
                    fecha_prog_iso=fecha_prog_iso,
                    on_progress=lambda: guardar_manifest(MANIFEST_VIDEOS, m)
                )

                # PASO 2: Respaldar carpeta completa en Google Drive y optimizar Mac local
                if not ep.get("respaldado_en_drive"):
                    print("\n☁️ [FASE B: RESPALDO EN GOOGLE DRIVE] Subiendo carpeta tras procesar redes sociales...")
                    ok_drive = subir_carpeta_a_gdrive_y_liberar(c, conservar_local=(not todas_redes_ok), redes_pendientes=redes_pendientes)
                    if ok_drive:
                        ep["respaldado_en_drive"] = True
                elif todas_redes_ok and os.path.exists(c):
                    print(f"🧹 ¡Redes completadas y carpeta '{c}' ya respaldada en Google Drive! Optimizando archivos en la Mac...")
                    purgar_archivos_intermedios_local(c)

                if todas_redes_ok and ep.get("respaldado_en_drive"):
                    ep["completado_y_respaldado"] = True
                else:
                    fallos += 1
                guardar_manifest(MANIFEST_VIDEOS, m)
        except Exception as e: print(f"✖ Falló el procesamiento de '{c}': {e}"); fallos += 1

    completo = fallos == 0 and all(r for r in m["resultados"]) and all(
        (r.get("episodio", r) or {}).get("completado_y_respaldado") for r in m["resultados"] if r)
    if completo:
        m["terminado"] = True; guardar_manifest(MANIFEST_VIDEOS, m)
        print("\n✅ Lote de videos terminado.")
    else:
        print("\n⚠ Lote incompleto. Vuelve a ejecutar la opción 1 y elige reanudar: continuará solo con lo que falta.")

# ---------------------------------------------------------------------------
# FLUJO 2: HISTORIAS FOTOGRÁFICAS (carruseles FB / IG / YouTube Community)
# ---------------------------------------------------------------------------
FORMATOS_FOTO = {"1": ("4:5 (feed IG/FB, recomendado)", (896, 1120), (1080, 1350)),
                 "2": ("9:16 (stories / reels)", (768, 1344), (1080, 1920)),
                 "3": ("1:1 (cuadrado)", (1024, 1024), (1080, 1080))}

def generar_historia_foto(peticion, evitar, n_slides):
    sys_p = open("prompt_historia_foto.md", encoding="utf-8").read()
    msg = construir_mensaje(peticion, evitar, prefijo="Historia fotográfica sobre") + f"\n\nNúmero de diapositivas: {n_slides}"
    return llamar_claude(sys_p, msg)

def exportar_historia_md(h, carpeta):
    meta = h.get("metadata", {})
    with open(os.path.join(carpeta, "metadata.md"), "w", encoding="utf-8") as f:
        f.write(f"# 📸 {h.get('titulo', '')}\n\n")
        f.write(f"**Serie / Parte:** {h.get('serie', '')} {h.get('parte', '')}\n\n")
        f.write(f"**Tier:** {h.get('tier', '')}\n\n")

        # SECCIÓN COPIAR Y PEGAR METRICOOL
        f.write("═══════════════════════════════════════════════════════════════════\n")
        f.write("📋 GUÍA RÁPIDA: COPIAR Y PEGAR EN METRICOOL (CARRUSEL DE FOTOS)\n")
        f.write("═══════════════════════════════════════════════════════════════════\n")
        f.write("1. En Metricool ve a 'Planificación' ➔ 'Crear publicación'.\n")
        f.write("2. Arrastra las imágenes rotuladas en orden (01_portada.png, 02_desarrollo.png...).\n")
        f.write("3. Selecciona las casillas de: Instagram, Facebook y YouTube Community.\n")
        f.write("4. Copia y pega el copy y hashtags de cada red abajo:\n\n")

        momento_sel = h.get("momento_dia") or "multi"
        for red in ["facebook", "instagram", "youtube_community"]:
            if red in meta:
                hora_pub = meta[red].get("hora_publicacion")
                if not hora_pub:
                    dict_red = HORARIOS_RECOMENDADOS_DEFECTO.get(red, {})
                    hora_pub = dict_red.get(momento_sel) or dict_red.get("multi", "20:00 CST - horario de alta interacción")
                f.write(f"## {red.upper().replace('_', ' ')}\n**Copy:** {meta[red].get('copy', '')}\n**Hashtags:** {meta[red].get('hashtags', '')}\n**🕒 Hora recomendada de publicación:** {hora_pub}\n\n")
        
        horarios = meta.get("estrategia_horaria", {})
        if horarios:
            f.write("## ⏰ ESTRATEGIA PARA CUALQUIER MOMENTO DEL DÍA\n")
            if horarios.get("manana"): f.write(f"- 🌅 **Mañana (07:00 - 10:00 CST):** {horarios['manana']}\n")
            if horarios.get("mediodia"): f.write(f"- ☀️ **Mediodía (12:00 - 15:00 CST):** {horarios['mediodia']}\n")
            if horarios.get("tarde"): f.write(f"- 🌇 **Tarde (16:00 - 19:00 CST):** {horarios['tarde']}\n")
            if horarios.get("noche"): f.write(f"- 🌙 **Noche Prime (20:00 - 23:00 CST):** {horarios['noche']}\n")
            if horarios.get("madrugada"): f.write(f"- 🕯️ **Madrugada / Insomnio (00:00 - 04:00 CST):** {horarios['madrugada']}\n")
            f.write("\n---\n")

        f.write(f"## 💬 COMUNIDAD\n**Comentario fijado:** {meta.get('comentario_fijado', '')}\n")
        f.write(f"**Encuesta:** {meta.get('encuesta', '')}\n**Gancho próxima parte:** {h.get('gancho_siguiente', '')}\n")
        f.write(f"**Respuestas:** {', '.join(meta.get('respuestas_interaccion', []))}\n\n---\n## 🖼 DIAPOSITIVAS\n")
        for i, s in enumerate(h.get("diapositivas", []), 1):
            f.write(f"### {i}: {s.get('archivo', '')}\n- Texto: {s.get('texto_pantalla', '')}\n- Narración: {s.get('narracion', '')}\n- Prompt: {s.get('prompt', '')}\n\n")

def pedir_config_historias(num_peticiones=1):
    n_slides = pedir_int("¿Diapositivas por historia?", 8, 3, 15)
    print("\nFormato de imagen:")
    for k, (nombre, _, _) in FORMATOS_FOTO.items(): print(f"  {k}) {nombre}")
    op = input("Opción [1]: ").strip() or "1"

    print("\n¿Para qué momento del día deseas enfocar la historia fotográfica?")
    for k, (nombre, _) in FRANJAS_DIA.items(): print(f"  {k}) {nombre}")
    defecto_hora = "7" if num_peticiones == 5 else "1"
    op_hora = input(f"Opción [{defecto_hora}]: ").strip() or defecto_hora
    _, clave_hora = FRANJAS_DIA.get(op_hora, FRANJAS_DIA["1"])

    fecha_liberacion = None
    if num_peticiones == 5 or clave_hora == "jornada_completa":
        print("\n📅 ¿En qué DÍA buscas liberar este lote de 5 historias fotográficas (Jornada 24H Completa)?")
        print("   (Puedes escribir: 'mañana', 'hoy', un día como 'viernes', 'sábado', 'domingo' o fecha 'YYYY-MM-DD')")
        dia_in = input("Día de liberación del lote [mañana]: ").strip() or "mañana"
        fecha_liberacion = resolver_fecha_dia(dia_in)
        print(f"   ✔ Lote enfocado para la jornada del día: {fecha_liberacion}")

    return {
        "n_slides": n_slides,
        "formato": op if op in FORMATOS_FOTO else "1",
        "momento_dia": clave_hora,
        "fecha_liberacion": fecha_liberacion
    }

def flujo_historias_foto():
    m = elegir_lote(MANIFEST_HISTORIAS, "historias fotográficas", pedir_config_historias)
    cfg = m.get("config") or {"n_slides": 8, "formato": "1", "momento_dia": "multi"}
    momento = cfg.get("momento_dia", "multi")
    fecha_lib = cfg.get("fecha_liberacion")
    peticiones = m.get("peticiones", [])

    # Si se eligió jornada completa o hay exactamente 5 peticiones y momento es jornada_completa
    if momento == "jornada_completa" or (len(peticiones) == 5 and momento == "jornada_completa"):
        for idx, pet in enumerate(peticiones):
            franja = ORDEN_5_FRANJAS[idx % len(ORDEN_5_FRANJAS)]
            pet["momento_dia"] = franja
            if fecha_lib:
                pet["fecha_liberacion"] = fecha_lib
        print(f"\n🌅☀️🌇🌙🕯️ Distribución 24H aplicada a las {len(peticiones)} historias fotográficas" + (f" para el día {fecha_lib}:" if fecha_lib else ":"))
        for idx, pet in enumerate(peticiones):
            print(f"   Historia {idx + 1}: {pet.get('momento_dia', '').upper()}")
    else:
        for pet in peticiones:
            pet.setdefault("momento_dia", momento)
            if fecha_lib:
                pet["fecha_liberacion"] = fecha_lib
    _, tam_sd, tam_final = FORMATOS_FOTO.get(cfg.get("formato", "1"), FORMATOS_FOTO["1"])

    def gen(pet, usados, i):
        res = generar_historia_foto(pet, usados, cfg.get("n_slides", 8))
        if not res: return None
        h = res.get("historia", res)
        if pet.get("momento_dia"):
            h["momento_dia"] = pet.get("momento_dia")
        return h
    completar_guiones(m, MANIFEST_HISTORIAS, gen)
    historias = [r for r in m["resultados"] if r]
    if not historias: return

    fallos = 0
    p = iniciar_sd()
    try:
        for h in historias:
            carpeta = "historia_foto_" + slug(h.get("titulo", "historia"), 35)
            os.makedirs(carpeta, exist_ok=True)
            exportar_historia_md(h, carpeta)
            estilo = h.get("estilo_visual_global", "")
            slides = h.get("diapositivas", [])
            print(f"\nDiapositivas de '{carpeta}' ({len(slides)})...")
            for i, s in enumerate(slides, 1):
                base = os.path.join(carpeta, f"base_{s['archivo']}")
                final = os.path.join(carpeta, s["archivo"])
                if os.path.exists(final): continue  # ya lista: se reanuda donde se quedó
                if not os.path.exists(base):
                    generar_foto(f"{estilo}, {s.get('prompt', '')}, {PROMPT_BASE}", base, *tam_sd)
                if os.path.exists(base):
                    poner_texto(base, final, s.get("texto_pantalla", ""), tam=tam_final, pos_y=0.08,
                                fontsize=int(tam_final[0] * 0.075), pie=f"{i}/{len(slides)}  ➜ desliza")
                else:
                    fallos += 1

            if all(os.path.exists(os.path.join(carpeta, s["archivo"])) for s in slides):
                registrar_historia_usada(h.get("titulo", carpeta), tipo="historia_foto", detalles={
                    "estado": "carrusel_completo",
                    "num_imagenes": len(slides),
                    "carpeta": carpeta,
                    "tier": h.get("tier"),
                    "momento_dia": cfg.get("momento_dia")
                })
                # Subir carrusel completo a Google Drive y liberar disco en la Mac
                subir_carpeta_a_gdrive_y_liberar(carpeta)
    finally:
        detener_sd(p)

    completo = fallos == 0 and all(r for r in m["resultados"])
    if completo:
        m["terminado"] = True; guardar_manifest(MANIFEST_HISTORIAS, m)
        print("✅ Historias fotográficas listas. Textos, copys y hora ideal de publicación en metadata.md de cada carpeta.")
        print("   Las imágenes limpias sin texto están en base_*.png por si quieres re-rotular.")
    else:
        print(f"⚠ Lote incompleto ({fallos} diapositivas o guiones fallaron). Ejecuta la opción 2 y elige reanudar: continuará solo con lo que falta.")

# ---------------------------------------------------------------------------
# REANUDACIÓN DE CARPETAS EXISTENTES O INCONCLUSAS
# ---------------------------------------------------------------------------
def parsear_metadata_carpeta(ruta_carpeta):
    """Extrae título, guion, metadata de redes y escenas desde metadata.md o lote_videos_actual.json."""
    nombre_base = os.path.basename(ruta_carpeta.rstrip("/\\"))
    info = {"titulo": nombre_base, "guion": "", "escenas": [], "metadata": {}}

    # Intentar primero recuperar el diccionario completo de metadata desde lote_videos_actual.json
    try:
        m = cargar_manifest(MANIFEST_VIDEOS)
        if m:
            for r in (m.get("resultados") or []):
                if not r: continue
                ep_r = r.get("episodio", r)
                if slug(ep_r.get("titulo", "")) == slug(nombre_base):
                    info["metadata"] = ep_r.get("metadata") or {}
                    break
            if not info["metadata"]:
                for _, g_parc in (m.get("parciales") or {}).items():
                    ep_p = (g_parc or {}).get("episodio", g_parc or {})
                    if slug(ep_p.get("titulo", "")) == slug(nombre_base):
                        info["metadata"] = ep_p.get("metadata") or {}
                        break
    except Exception:
        pass

    ruta_md = os.path.join(ruta_carpeta, "metadata.md")
    if not os.path.exists(ruta_md):
        return info

    try:
        with open(ruta_md, encoding="utf-8") as f:
            contenido = f.read()

        m_tit = re.search(r"^#\s*🎬\s*(.+)$", contenido, re.M)
        if m_tit: info["titulo"] = m_tit.group(1).strip()

        m_gui = re.search(r"##\s*📜\s*GUION\s*\n(.*?)(?=\n---|\n##|$)", contenido, re.DOTALL)
        if m_gui: info["guion"] = m_gui.group(1).strip()

        # Si no había metadata en el manifest, extraerla desde metadata.md
        if not info["metadata"]:
            meta_extraida = {}
            for red_key, red_header in [
                ("youtube_shorts", "YOUTUBE SHORTS"),
                ("tiktok", "TIKTOK"),
                ("facebook_reels", "FACEBOOK REELS"),
                ("instagram_reels", "INSTAGRAM REELS")
            ]:
                patron_red = rf"##\s*{red_header}\s*\n(.*?)(?=\n##\s|\n---|$)"
                m_red = re.search(patron_red, contenido, re.DOTALL)
                if m_red:
                    bloque_r = m_red.group(1)
                    t_r = re.search(r"\*\*Título:\*\*\s*(.+)", bloque_r)
                    d_r = re.search(r"\*\*Descripción:\*\*\s*(.+)", bloque_r)
                    h_r = re.search(r"\*\*Hashtags:\*\*\s*(.+)", bloque_r)
                    meta_extraida[red_key] = {
                        "titulo": t_r.group(1).strip() if t_r else info["titulo"],
                        "descripcion": d_r.group(1).strip() if d_r else "",
                        "hashtags": h_r.group(1).strip() if h_r else ""
                    }
            m_com = re.search(r"\*\*Comentario fijado:\*\*\s*(.+)", contenido)
            if m_com:
                meta_extraida["comentario_fijado"] = m_com.group(1).strip()
            info["metadata"] = meta_extraida

        bloque_escenas = re.search(r"##\s*🖼\s*ESCENAS.*?\n(.*)", contenido, re.DOTALL)
        if bloque_escenas:
            partes = re.split(r"\n###\s*Escena\s*\d+:\s*", "\n" + bloque_escenas.group(1))
            for p in partes[1:]:
                lineas = p.strip().split("\n")
                if not lineas: continue
                primera = lineas[0].strip()
                m_arch = re.match(r"([^\s\[]+)", primera)
                archivo = m_arch.group(1) if m_arch else primera
                if not archivo.lower().endswith(('.png', '.jpg', '.jpeg', '.webp')):
                    continue

                esc = {"archivo": archivo}
                for l in lineas[1:]:
                    l = l.strip()
                    if l.startswith("- Movimiento:"): esc["movimiento_camara"] = l.split(":", 1)[1].strip()
                    elif l.startswith("- Transición:") or l.startswith("- Transicion:"): esc["transicion"] = l.split(":", 1)[1].strip()
                    elif l.startswith("- SFX:"): esc["sfx_capcut"] = l.split(":", 1)[1].strip()
                    elif l.startswith("- Texto en Pantalla:"): esc["tip_texto_pantalla"] = l.split(":", 1)[1].strip()
                    elif l.startswith("- Prompt:"): esc["prompt"] = l.split(":", 1)[1].strip()
                    elif l.startswith("- Frase audio:"): esc["frase_guion_asociada"] = l.split(":", 1)[1].strip().strip('"')
                info["escenas"].append(esc)
    except Exception as e:
        print(f"Aviso al leer metadata.md en '{ruta_carpeta}': {e}")

    return info

def detectar_carpetas_inconclusas(base_dir="."):
    """Detecta carpetas con recursos listos (audio/imágenes), clasificando las que tienen o no .mp4."""
    carpetas = []
    rutas_a_explorar = [base_dir]
    try:
        for entrada in os.scandir(base_dir):
            if entrada.is_dir() and not entrada.name.startswith((".", "venv", "__pycache__", "stable-diffusion-webui")):
                rutas_a_explorar.append(entrada.path)
    except Exception:
        pass

    for r in rutas_a_explorar:
        try:
            for entrada in os.scandir(r):
                if entrada.is_dir() and not entrada.name.startswith((".", "venv", "__pycache__", "stable-diffusion-webui")):
                    p = entrada.path
                    archivos = os.listdir(p)
                    mp3s = [f for f in archivos if f.endswith(".mp3")]
                    pngs = [f for f in archivos if f.lower().endswith(('.png', '.jpg', '.jpeg')) and not f.startswith("00_portada") and not f.endswith("_base.png")]
                    mp4s = [f for f in archivos if f.endswith(".mp4") and not f.endswith(".parcial.mp4")]
                    if mp3s or pngs:
                        dur_aud = duracion_audio(os.path.join(p, mp3s[0])) if mp3s else 0
                        carpetas.append({
                            "ruta": p,
                            "nombre": entrada.name,
                            "mp3": os.path.join(p, mp3s[0]) if mp3s else None,
                            "duracion_audio": dur_aud,
                            "num_imgs": len(pngs),
                            "tiene_mp4": len(mp4s) > 0,
                            "arch_mp4": mp4s[0] if mp4s else None,
                            "tiene_portada": os.path.exists(os.path.join(p, "00_portada.png")),
                            "tiene_meta": os.path.exists(os.path.join(p, "metadata.md"))
                        })
        except Exception:
            pass

    return carpetas

def reanudar_y_ensamblar_carpeta(ruta_carpeta):
    """Reanuda y ensambla el video para una carpeta dada, completando lo que falte."""
    if not os.path.exists(ruta_carpeta):
        print(f"✖ La carpeta '{ruta_carpeta}' no existe.")
        return False

    nombre_carpeta = os.path.basename(ruta_carpeta.rstrip("/\\"))
    print(f"\n📂 Analizando carpeta: {nombre_carpeta} ({ruta_carpeta})")

    # 1. Buscar audio
    archivos = os.listdir(ruta_carpeta)
    mp3s = [f for f in archivos if f.endswith(".mp3")]
    ruta_audio = os.path.join(ruta_carpeta, mp3s[0]) if mp3s else None

    meta_info = parsear_metadata_carpeta(ruta_carpeta)
    titulo_limpio = meta_info.get("titulo") or nombre_carpeta
    titulo_slug = slug(nombre_carpeta)

    if not ruta_audio:
        print("⚠ No se encontró ningún archivo .mp3 de locución en la carpeta.")
        if meta_info.get("guion") and API_KEY_ELEVENLABS:
            if input("¿Deseas generar la locución con ElevenLabs usando el guion de metadata.md? [S/n]: ").strip().lower() != "n":
                ruta_audio = os.path.join(ruta_carpeta, "00_locucion.mp3")
                generar_audio({"texto_completo": meta_info["guion"]}, ruta_audio)
        if not ruta_audio or not os.path.exists(ruta_audio):
            print("✖ No es posible continuar sin audio .mp3.")
            return False

    dur_aud = duracion_audio(ruta_audio)
    print(f"   🎙 Audio detectado: {os.path.basename(ruta_audio)} ({dur_aud:.1f} segundos)")

    # 2. Escenas e imágenes
    escenas_meta = meta_info.get("escenas", [])
    imgs_disco = sorted([f for f in archivos if f.lower().endswith(('.png', '.jpg', '.jpeg', '.webp'))
                         and not f.startswith("00_portada") and not f.endswith("_base.png")])

    print(f"   🖼 Imágenes encontradas en disco: {len(imgs_disco)}")

    # Comprobar imágenes faltantes según metadata.md
    faltantes = [e for e in escenas_meta if not os.path.exists(os.path.join(ruta_carpeta, e["archivo"])) and e.get("prompt")]
    if faltantes:
        print(f"   ⚠ Según metadata.md faltan {len(faltantes)} imágenes de las {len(escenas_meta)} planificadas.")
        print("     1) Generar las imágenes faltantes en SDXL Lightning y luego ensamblar")
        print(f"     2) Ensamblar ahora usando solo las {len(imgs_disco)} imágenes existentes en la carpeta")
        print("     0) Cancelar")
        eleccion = input("   Opción [2]: ").strip() or "2"
        if eleccion == "0":
            return False
        if eleccion == "1":
            p = iniciar_sd()
            try:
                for it in faltantes:
                    ruta_salida = os.path.join(ruta_carpeta, it["archivo"])
                    print(f"   🎨 Generando {it['archivo']}...")
                    generar_foto(it.get("prompt", ""), ruta_salida, 768, 1344)
            finally:
                detener_sd(p)
            archivos = os.listdir(ruta_carpeta)
            imgs_disco = sorted([f for f in archivos if f.lower().endswith(('.png', '.jpg', '.jpeg', '.webp'))
                                 and not f.startswith("00_portada") and not f.endswith("_base.png")])

    if not imgs_disco:
        print("✖ No hay imágenes para ensamblar el video.")
        return False

    # Construir lista de escenas definitiva
    if escenas_meta:
        escenas_finales = [e for e in escenas_meta if os.path.exists(os.path.join(ruta_carpeta, e["archivo"]))]
        archivos_en_meta = {e["archivo"] for e in escenas_meta}
        for img in imgs_disco:
            if img not in archivos_en_meta:
                escenas_finales.append({"archivo": img, "movimiento_camara": "zoom_in", "tip_texto_pantalla": ""})
    else:
        escenas_finales = [{"archivo": img, "movimiento_camara": "zoom_in" if idx % 2 == 0 else "pan", "tip_texto_pantalla": ""}
                           for idx, img in enumerate(imgs_disco)]

    # Si hay un exceso de imágenes para la duración del audio, ofrecer optimización de ritmo
    if dur_aud > 0:
        n_optimo = max(12, min(26, round(dur_aud / 4.5)))
        if len(escenas_finales) > n_optimo + 4:
            print(f"   ℹ La carpeta tiene {len(escenas_finales)} escenas ({dur_aud/len(escenas_finales):.1f}s/escena; ritmo acelerado).")
            print(f"     1) Optimizar a {n_optimo} escenas (~{dur_aud/n_optimo:.1f}s/imagen, ritmo cinematográfico recomendado)")
            print(f"     2) Ensamblar con todas las {len(escenas_finales)} escenas")
            op_ritmo = input("   Opción [1]: ").strip() or "1"
            if op_ritmo == "1":
                escenas_finales = ajustar_cuota_escenas(escenas_finales, n_optimo)
                print(f"   ✔ Escenas ajustadas a {len(escenas_finales)} imágenes seleccionadas a lo largo de la narración.")

    # 3. Portada
    ruta_portada = os.path.join(ruta_carpeta, "00_portada.png")
    if not os.path.exists(ruta_portada):
        if os.path.exists(os.path.join(ruta_carpeta, "00_portada_tiktok.png")):
            ruta_portada = os.path.join(ruta_carpeta, "00_portada_tiktok.png")
        else:
            ruta_portada = None

    if ruta_portada:
        print(f"   🖼 Portada detectada: {os.path.basename(ruta_portada)}")
    else:
        print("   ℹ Sin portada 00_portada.png; se ensamblará el video directamente desde la primera escena.")

    # 4. Sincronización y ensamblaje
    print(f"\n⏱ Calibrando {len(escenas_finales)} escenas con la locución...")
    escenas_sincronizadas = alinear_escenas_con_audio(escenas_finales, ruta_audio, meta_info.get("guion", ""))

    print("🎬 Iniciando renderizado del video...")
    ensamblar(escenas_sincronizadas, ruta_audio, ruta_carpeta, titulo_slug, portada=ruta_portada)

    destino_mp4 = os.path.join(ruta_carpeta, f"{titulo_slug}.mp4")
    if os.path.exists(destino_mp4):
        tam_mb = os.path.getsize(destino_mp4) / (1024 * 1024)
        print(f"\n✅ ¡Video ensamblado exitosamente!")
        print(f"   📁 Archivo: {destino_mp4} ({tam_mb:.1f} MB)")
        registrar_historia_usada(titulo_limpio, tipo="video", detalles={
            "estado": "video_ensamblado",
            "duracion_audio_s": round(dur_aud, 1),
            "num_imagenes": len(escenas_finales),
            "carpeta": nombre_carpeta,
            "archivo_salida": f"{titulo_slug}.mp4"
        })
        # Determinar horario de programación (desde lote_videos_actual.json o preguntando con programación por defecto)
        fecha_prog_iso = None
        m_act = cargar_manifest(MANIFEST_VIDEOS)
        cfg_act = (m_act or {}).get("config") or {}
        modo_prog = cfg_act.get("modo_programacion", "auto_franja")
        if modo_prog == "auto_franja":
            fecha_prog_iso = calcular_fecha_programacion(
                momento_dia=cfg_act.get("momento_dia", "multi"),
                fecha_base=cfg_act.get("fecha_liberacion"),
                forzar_programacion=True
            )
        elif modo_prog == "personalizada":
            fecha_prog_iso = calcular_fecha_programacion(
                momento_dia=cfg_act.get("momento_dia", "multi"),
                hora_especifica=cfg_act.get("hora_personalizada"),
                fecha_base=cfg_act.get("fecha_liberacion"),
                forzar_programacion=True
            )

        # PASO 1: Publicar primero en las 4 redes sociales mientras los archivos están en local
        todas_redes_ok, redes_pendientes = publicar_video_en_4_redes(
            destino_mp4, meta_info, ruta_carpeta, fecha_prog_iso=fecha_prog_iso
        )

        # PASO 2: Subir a Google Drive después de redes sociales (conservando en local si alguna red falló)
        print("\n☁️ [FASE B: RESPALDO EN GOOGLE DRIVE] Subiendo carpeta tras procesar redes sociales...")
        subir_carpeta_a_gdrive_y_liberar(ruta_carpeta, conservar_local=(not todas_redes_ok), redes_pendientes=redes_pendientes)
        return True
    else:
        print(f"\n✖ Ocurrió un error y no se pudo generar el archivo .mp4.")
        return False

def menu_reanudar_carpeta(todos=None):
    if todos is None:
        todos = detectar_carpetas_inconclusas()

    inconclusos = [c for c in todos if not c["tiene_mp4"]]
    con_video = [c for c in todos if c["tiene_mp4"]]

    print("\n===== REANUDAR / ENSAMBLAR VIDEO INCONCLUSO =====")
    if inconclusos:
        print("\n⚡ Carpetas pendientes (audio/imágenes listas, SIN archivo .mp4):")
        for i, c in enumerate(inconclusos, 1):
            aud = f"{c['duracion_audio']:.0f}s" if c["duracion_audio"] else "Sin audio"
            port = "✔ Portada" if c["tiene_portada"] else "Sin portada"
            print(f"  {i}) {c['nombre']} [{c['num_imgs']} imágenes | {aud} | {port}]")
    else:
        print("\nNo se detectaron carpetas pendientes sin video .mp4.")

    if con_video:
        print("\n📦 Carpetas que ya tienen video .mp4 (por si deseas re-ensamblar):")
        base_offset = len(inconclusos)
        for i, c in enumerate(con_video, 1):
            print(f"  {base_offset + i}) {c['nombre']} [{c['num_imgs']} imágenes | ya tiene {c['arch_mp4']}]")

    print("\nOpciones adicionales:")
    if inconclusos:
        print("  T) Ensamblar TODAS las carpetas pendientes sin .mp4")
    print("  R) Escribir ruta manual de carpeta")
    print("  0) Volver al menú principal")

    op = input("\nElige una opción: ").strip()
    if op == "0" or not op:
        return

    if op.upper() == "T" and inconclusos:
        print(f"\n🚀 Procesando {len(inconclusos)} carpetas pendientes en cola...")
        for c in inconclusos:
            reanudar_y_ensamblar_carpeta(c["ruta"])
        return

    if op.upper() == "R":
        ruta_manual = input("Introduce la ruta de la carpeta: ").strip()
        if os.path.exists(ruta_manual):
            reanudar_y_ensamblar_carpeta(ruta_manual)
        else:
            print(f"✖ La ruta '{ruta_manual}' no existe.")
        return

    try:
        idx = int(op)
        todos_en_orden = inconclusos + con_video
        if 1 <= idx <= len(todos_en_orden):
            elegido = todos_en_orden[idx - 1]
            reanudar_y_ensamblar_carpeta(elegido["ruta"])
        else:
            print("Opción inválida.")
    except ValueError:
        print("Opción no reconocida.")

def flujo_semana_completa():
    """Flujo directo para generar y programar los 7 videos de la semana (Lunes a Domingo)."""
    peticiones, cfg_sem = pedir_config_semana_completa()
    m = {
        "peticiones": peticiones,
        "config": cfg_sem,
        "parciales": {},
        "resultados": [None] * len(peticiones),
        "terminado": False
    }
    guardar_manifest(MANIFEST_VIDEOS, m)
    flujo_videos(manifest_preparado=m)

# ---------------------------------------------------------------------------
# MENÚ
# ---------------------------------------------------------------------------
def iniciar():
    if not API_KEY_ANTHROPIC: print("⚠ Falta API_KEY_ANTHROPIC en .env")
    while True:
        carpetas = detectar_carpetas_inconclusas()
        pendientes = [c for c in carpetas if not c["tiene_mp4"]]
        aviso = f" (¡{len(pendientes)} pendiente(s) de ensamblar!)" if pendientes else ""

        print("\n===== NARRATIVAS SUMERGIDAS =====")
        print("  1) Videos verticales (uno o varios)")
        print("  2) Semana Completa (7 videos: Lunes a Domingo con programación automática)")
        print("  3) Historias fotográficas (carruseles FB / IG / YouTube)")
        print(f"  4) Reanudar / Ensamblar carpeta existente (MP3 + imágenes){aviso}")
        print("  5) Rendimiento y Redes Sociales (Métricas, Conexiones & Feedback IA)")
        print("  0) Salir")
        op = input("Opción: ").strip()
        if op == "1": flujo_videos()
        elif op == "2": flujo_semana_completa()
        elif op == "3": flujo_historias_foto()
        elif op == "4": menu_reanudar_carpeta(carpetas)
        elif op == "5": menu_rendimiento_redes()
        elif op == "0": break


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] in ("--analizar", "-a", "analizar"):
        analizar_rendimiento_canal()
        sys.exit(0)
    iniciar()