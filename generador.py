import requests, base64, time, json, os, re, subprocess, signal, random
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

def pedir_modo(etiqueta):
    """Devuelve la lista de 'peticiones'. Opción 2 admite varios temas y N por tema (o N sobre un solo tema)."""
    print(f"\n¿Cómo quieres definir {etiqueta}?")
    print("  1) Aleatorio")
    print("  2) Yo doy el TEMA (uno o varios separados por |; puedo pedir N por tema)")
    print("  3) Yo doy la HISTORIA completa (se adapta tal cual)")
    modo = input("Opción [1]: ").strip() or "1"
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
    "1": ("Cualquier momento / Multi-horario (estrategia 24h completa en metadata, recomendado)", "multi"),
    "2": ("Mañana (07:00 - 10:00 CST: despertar, transporte, datos insólitos)", "manana"),
    "3": ("Mediodía (12:00 - 15:00 CST: break de almuerzo, misterio y arquitectura)", "mediodia"),
    "4": ("Tarde (16:00 - 19:00 CST: salida laboral/escolar, debate en Facebook)", "tarde"),
    "5": ("Noche Prime (20:00 - 23:00 CST: máxima oscuridad, terror inmersivo)", "noche"),
    "6": ("Madrugada / Insomnio (00:00 - 04:00 CST: paranoia y misterio para desvelados)", "madrugada"),
}

def pedir_config_videos():
    print("\n¿Para qué momento del día deseas enfocar la historia / publicación?")
    for k, (nombre, _) in FRANJAS_DIA.items():
        print(f"  {k}) {nombre}")
    op = input("Opción [1]: ").strip() or "1"
    _, clave = FRANJAS_DIA.get(op, FRANJAS_DIA["1"])
    return {"momento_dia": clave}

def construir_mensaje(peticion, evitar, prefijo="Historia sobre"):
    if peticion["tipo"] == "historia":
        return f"Adapta ESTA historia proporcionada por el creador (no inventes otra):\n\n{peticion['texto']}"
    tema = peticion["texto"] or f"Evento real o leyenda urbana oscura sobre: {random.choice(TEMAS_AZAR)}"
    msg = f"{prefijo}: {tema}"
    if peticion.get("de", 1) > 1:
        msg += (f"\n\nEs el caso {peticion['variante']} de {peticion['de']} sobre el MISMO tema. Debe ser un caso, lugar, personaje o ángulo "
                f"DISTINTO a los demás de esta serie, con su propio gancho y título; no repitas datos ni final.")

    momento = peticion.get("momento_dia")
    if momento and momento != "multi":
        descripciones = {
            "manana": "MAÑANA (07:00-10:00 CST: camino al trabajo/estudio, tono intrigante y dato insólito matutino)",
            "mediodia": "MEDIODÍA (12:00-15:00 CST: break de comida/almuerzo, misterio envolvente)",
            "tarde": "TARDE (16:00-19:00 CST: salida de actividades, debate moral y tensión)",
            "noche": "NOCHE PRIME (20:00-23:00 CST: máxima oscuridad, terror inmersivo)",
            "madrugada": "MADRUGADA / INSOMNIO (00:00-04:00 CST: atmósfera de paranoia para los que no pueden dormir)",
        }
        msg += f"\n\nMOMENTO DEL DÍA OBJETIVO: Diseña el gancho, el tono y la ambientación lumínica específicamente para la franja de {descripciones.get(momento, momento.upper())}."
    else:
        msg += "\n\nMOMENTO DEL DÍA: Flexible / Cualquier momento. Incluye en la metadata la tabla 'estrategia_horaria' adaptada para mañana, mediodía, tarde, noche y madrugada."

    if evitar:
        # Tomar los últimos 40 para no saturar el prompt pero garantizar cobertura absoluta
        lista_evitar = evitar[-40:] if len(evitar) > 40 else evitar
        msg += ("\n\n⛔ REGLA ESTRICTA DE NO REPETICIÓN: El canal YA produjo contenido sobre los siguientes casos/títulos. "
                "Está TERMINANTEMENTE PROHIBIDO repetir cualquiera de estos casos, eventos, leyendas o títulos; "
                "debes elegir un caso, lugar o suceso completamente NUEVO y no explorado:\n- " + "\n- ".join(lista_evitar))
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
def iniciar_sd():
    try:
        if requests.get(f"{URL_SD_BASE}/sd-models", timeout=2).status_code == 200: return None
    except: pass
    env = os.environ.copy()
    env.pop("VIRTUAL_ENV", None)
    # Control de uso de CPU: evitar que sature los 10 núcleos y desconecte periféricos en Mac mini M4
    env["OMP_NUM_THREADS"] = "4"
    env["MKL_NUM_THREADS"] = "4"
    env["OPENBLAS_NUM_THREADS"] = "4"
    env["VECLIB_MAXIMUM_THREADS"] = "4"
    env["NUMEXPR_NUM_THREADS"] = "4"
    comandos = ["nice", "-n", "10", "./webui.sh", "--api", "--nowebui", "--port", "7860", "--medvram-sdxl", "--no-half-vae", "--use-cpu", "interrogate"]
    p = subprocess.Popen(comandos, cwd=RUTA_SD, env=env, preexec_fn=os.setsid)
    for _ in range(60):
        try:
            if requests.get(f"{URL_SD_BASE}/sd-models", timeout=2).status_code == 200: return p
        except: time.sleep(2)
    return p

def detener_sd(p):
    if p and p.poll() is None: os.killpg(os.getpgid(p.pid), signal.SIGTERM)

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
            ruteo = datos.get("enrutamiento", {})
            f.write(f"**🚦 Publicar en (prioridad):** {', '.join(ruteo.get('publicar', []))}\n")
            f.write(f"**⛔ Omitir:** {', '.join(ruteo.get('omitir', []))}\n\n")
            f.write(f"## 📜 GUION\n{datos.get('locucion_elevenlabs', {}).get('texto_completo', '')}\n\n---\n")
            f.write("## 📱 COPY PARA REDES SOCIALES\n")
            meta = datos.get("metadata", {})
            for red in ["youtube_shorts", "tiktok", "facebook_reels", "instagram_reels", "bilibili"]:
                if red in meta:
                    f.write(f"### {red.upper().replace('_', ' ')}\n")
                    f.write(f"**Título:** {meta[red].get('titulo', '')}\n")
                    f.write(f"**Descripción:** {meta[red].get('descripcion', '')}\n")
                    f.write(f"**Hashtags:** {meta[red].get('hashtags', '')}\n")
                    f.write(f"**🕒 Hora ideal de publicación:** {meta[red].get('hora_publicacion', '')}\n\n")
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

            f.write("---\n")
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

# Matriz de enrutamiento (v4.0): folclore/arquitectura → YouTube Shorts y TikTok (+IG/FB); viscerales → solo Facebook y TikTok
REDES_VIDEO = ["youtube_shorts", "tiktok", "facebook_reels", "instagram_reels"]
RUTEO_TIER = {
    "FOLCLORE": ["youtube_shorts", "tiktok", "instagram_reels", "facebook_reels"],
    "ARQUITECTURA": ["youtube_shorts", "tiktok", "instagram_reels", "facebook_reels"],
    "MORBO": ["facebook_reels", "tiktok"],
    "ABSURDO": ["facebook_reels", "tiktok"],
}

def aplicar_ruteo(ep):
    tier = str(ep.get("tier", "")).upper().strip()
    if tier not in RUTEO_TIER: tier = "ARQUITECTURA"; ep["tier"] = tier
    publicar = RUTEO_TIER[tier]
    ep["enrutamiento"] = {"publicar": publicar, "omitir": [r for r in REDES_VIDEO if r not in publicar]}
    meta = ep.get("metadata", {})
    for r in ep["enrutamiento"]["omitir"]: meta.pop(r, None)
    meta.pop("bilibili", None)  # suspendido

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

    # Limitación de CPU a 4 threads y preset mediano para evitar 100% de CPU y desconexión de periféricos
    num_threads = min(4, max(1, (os.cpu_count() or 4) // 2))
    try:
        vid.write_videofile(temporal, fps=24, codec="libx264", audio_codec="aac", preset="veryfast", threads=num_threads, logger=None)
        os.replace(temporal, destino)
        print(f"✔ Video ensamblado: {destino}")
    finally:
        try: vid.close()
        except: pass
        try: audio.close()
        except: pass

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
    with open(ruta, "w", encoding="utf-8") as f: json.dump(m, f, ensure_ascii=False, indent=2)

def titulo_de(d):
    return (d.get("episodio", d) or {}).get("titulo", "")

def elegir_lote(ruta, etiqueta, pedir_config=None):
    m = cargar_manifest(ruta)
    if m and not m.get("terminado"):
        hechos = sum(1 for r in m.get("resultados", []) if r)
        if input(f"⏯ Hay un lote de {etiqueta} sin terminar ({hechos}/{len(m.get('resultados', []))} guiones listos). ¿Reanudarlo? [S/n]: ").strip().lower() != "n":
            return m
    peticiones = pedir_modo(etiqueta)
    m = {"peticiones": peticiones, "resultados": [None] * len(peticiones), "parciales": {}, "terminado": False,
         "config": pedir_config() if pedir_config else {}}
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

def flujo_videos():
    m = elegir_lote(MANIFEST_VIDEOS, "videos", pedir_config_videos)
    cfg = m.get("config") or {}
    momento = cfg.get("momento_dia", "multi")
    for pet in m.get("peticiones", []):
        pet.setdefault("momento_dia", momento)

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

    print("\n🎬 Ensamblando videos automáticamente (sin pausa)...")
    for ep, c, aud in trabajos:
        mp4 = os.path.join(c, f"{c}.mp4")
        if os.path.exists(mp4): print(f"✔ '{c}' ya estaba ensamblado."); continue
        if not os.path.exists(aud) or lista_imagenes_faltantes(ep, c):
            print(f"⚠ '{c}' tiene audio o imágenes pendientes; se omite el ensamblado."); fallos += 1; continue
        try:
            ensamblar(ep.get("escenas", []), aud, c, c, portada=os.path.join(c, "00_portada.png"))
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
        except Exception as e: print(f"✖ Falló el ensamblado de '{c}': {e}"); fallos += 1

    completo = fallos == 0 and all(r for r in m["resultados"]) and all(os.path.exists(os.path.join(c, f"{c}.mp4")) for _, c, _ in trabajos)
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
        for red in ["facebook", "instagram", "youtube_community"]:
            if red in meta:
                f.write(f"## {red.upper().replace('_', ' ')}\n**Copy:** {meta[red].get('copy', '')}\n**Hashtags:** {meta[red].get('hashtags', '')}\n**🕒 Hora ideal de publicación:** {meta[red].get('hora_publicacion', '')}\n\n")
        
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

def pedir_config_historias():
    n_slides = pedir_int("¿Diapositivas por historia?", 8, 3, 15)
    print("\nFormato de imagen:")
    for k, (nombre, _, _) in FORMATOS_FOTO.items(): print(f"  {k}) {nombre}")
    op = input("Opción [1]: ").strip() or "1"

    print("\n¿Para qué momento del día deseas enfocar la historia fotográfica?")
    for k, (nombre, _) in FRANJAS_DIA.items(): print(f"  {k}) {nombre}")
    op_hora = input("Opción [1]: ").strip() or "1"
    _, clave_hora = FRANJAS_DIA.get(op_hora, FRANJAS_DIA["1"])

    return {"n_slides": n_slides, "formato": op if op in FORMATOS_FOTO else "1", "momento_dia": clave_hora}

def flujo_historias_foto():
    m = elegir_lote(MANIFEST_HISTORIAS, "historias fotográficas", pedir_config_historias)
    cfg = m.get("config") or {"n_slides": 8, "formato": "1", "momento_dia": "multi"}
    momento = cfg.get("momento_dia", "multi")
    for pet in m.get("peticiones", []):
        pet.setdefault("momento_dia", momento)
    _, tam_sd, tam_final = FORMATOS_FOTO.get(cfg.get("formato", "1"), FORMATOS_FOTO["1"])

    def gen(pet, usados, i):
        h = generar_historia_foto(pet, usados, cfg.get("n_slides", 8))
        return h.get("historia", h) if h else None
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
    """Extrae título, guion y escenas desde metadata.md si existe en la carpeta."""
    ruta_md = os.path.join(ruta_carpeta, "metadata.md")
    info = {"titulo": os.path.basename(ruta_carpeta.rstrip("/\\")), "guion": "", "escenas": []}
    if not os.path.exists(ruta_md):
        return info

    try:
        with open(ruta_md, encoding="utf-8") as f:
            contenido = f.read()

        m_tit = re.search(r"^#\s*🎬\s*(.+)$", contenido, re.M)
        if m_tit: info["titulo"] = m_tit.group(1).strip()

        m_gui = re.search(r"##\s*📜\s*GUION\s*\n(.*?)(?=\n---|\n##|$)", contenido, re.DOTALL)
        if m_gui: info["guion"] = m_gui.group(1).strip()

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
        print("  2) Historias fotográficas (carruseles FB / IG / YouTube)")
        print(f"  3) Reanudar / Ensamblar carpeta existente (MP3 + imágenes){aviso}")
        print("  0) Salir")
        op = input("Opción: ").strip()
        if op == "1": flujo_videos()
        elif op == "2": flujo_historias_foto()
        elif op == "3": menu_reanudar_carpeta(carpetas)
        elif op == "0": break

if __name__ == "__main__": iniciar()