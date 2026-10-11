import requests, base64, time, json, os, re, subprocess, signal, random
from PIL import Image, ImageDraw, ImageFont
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
    """Devuelve lista de 'peticiones' (n) con el tema/historia elegido por el usuario."""
    print(f"\n¿Cómo quieres definir {etiqueta}?")
    print("  1) Aleatorio")
    print("  2) Yo doy el TEMA (varios temas separados por |)")
    print("  3) Yo doy la HISTORIA completa (se adapta tal cual)")
    modo = input("Opción [1]: ").strip() or "1"
    n = 1 if modo == "3" else pedir_int(f"¿Cuántos {etiqueta} generar?", 1)
    if modo == "3":
        return [{"tipo": "historia", "texto": pedir_multilinea("Pega tu historia")}]
    if modo == "2":
        temas = [t.strip() for t in input("Tema(s): ").split("|") if t.strip()]
        if temas:
            return [{"tipo": "tema", "texto": temas[i % len(temas)]} for i in range(n)]
    return [{"tipo": "tema", "texto": None} for _ in range(n)]

def construir_mensaje(peticion, evitar, prefijo="Historia sobre"):
    if peticion["tipo"] == "historia":
        return f"Adapta ESTA historia proporcionada por el creador (no inventes otra):\n\n{peticion['texto']}"
    tema = peticion["texto"] or f"Evento real o leyenda urbana oscura sobre: {random.choice(TEMAS_AZAR)}"
    msg = f"{prefijo}: {tema}"
    if evitar: msg += "\n\nIMPORTANTE: NO repitas estos casos ya usados en este lote: " + "; ".join(evitar)
    return msg

# ---------------------------------------------------------------------------
# CLAUDE / ELEVENLABS
# ---------------------------------------------------------------------------
def llamar_claude(prompt_sistema, mensaje):
    try:
        res = requests.post("https://api.anthropic.com/v1/messages",
                            json={"model": "claude-sonnet-5-5", "max_tokens": 8192, "system": prompt_sistema,
                                  "messages": [{"role": "user", "content": mensaje}]},
                            headers={"x-api-key": API_KEY_ANTHROPIC, "anthropic-version": "2023-06-01"})
        res.raise_for_status()
        txt = next((b.get('text') for b in res.json().get('content', []) if b.get('type') == 'text'), "").strip()
        match = re.search(r'\{[\s\S]*\}', txt)
        return json.loads(json_repair.repair_json(match.group(0))) if match else None
    except Exception as e:
        print(f"Error en API de Claude: {e}")
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
    comandos = ["./webui.sh", "--api", "--nowebui", "--port", "7860", "--medvram-sdxl", "--no-half-vae", "--use-cpu", "interrogate"]
    p = subprocess.Popen(comandos, cwd=RUTA_SD, env=env, preexec_fn=os.setsid)
    for _ in range(60):
        try:
            if requests.get(f"{URL_SD_BASE}/sd-models", timeout=2).status_code == 200: return p
        except: time.sleep(2)
    return p

def detener_sd(p):
    if p and p.poll() is None: os.killpg(os.getpgid(p.pid), signal.SIGTERM)

def generar_foto(prompt, ruta, ancho=768, alto=1344):
    try:
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
        r = requests.post(URL_API_SD, json=payload, timeout=300)
        r.raise_for_status()
        with open(ruta, "wb") as f: f.write(base64.b64decode(r.json()['images'][0]))
    except Exception as e: print(f"Error en imagen {ruta}: {e}")

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
# VIDEO: EXPORT / GUION
# ---------------------------------------------------------------------------
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
            f.write(f"**Respuestas:** {', '.join(meta.get('respuestas_interaccion', []))}\n\n---\n")
            for clave, arch, uso in (("portada", "00_portada.png", "YouTube / Instagram"), ("portada_b", "00_portada_tiktok.png", "TikTok / Facebook")):
                p = datos.get(clave, {})
                if p: f.write(f"## 🖼 PORTADA ({uso})\n- Archivo: {arch}\n- Texto: {p.get('texto_portada', '')}\n- Prompt: {p.get('prompt', '')}\n\n")
            f.write("---\n")
            f.write("## 🖼 ESCENAS Y SFX CAPCUT\n")
            for i, e in enumerate(datos.get('escenas', []), 1):
                f.write(f"### Escena {i}: {e.get('archivo', '')}\n")
                f.write(f"- Movimiento: {e.get('movimiento_camara', '')}\n")
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

def generar_datos_completos(peticion, evitar=()):
    print("\n--- FASE 1: GUION Y ESTRATEGIA ---")
    sys_guion = open("prompt_guion.md", encoding="utf-8").read()
    datos_guion = llamar_claude(sys_guion, construir_mensaje(peticion, evitar))
    if not datos_guion: return None

    ep = datos_guion.get("episodio", {})
    aplicar_ruteo(ep)
    print(f"   Tier: {ep.get('tier')} → publicar en: {', '.join(ep['enrutamiento']['publicar'])}")

    print("--- FASE 2: DIRECCIÓN DE ARTE ---")
    sys_escenas = open("prompt_escenas.md", encoding="utf-8").read()
    historia = ep.get("locucion_elevenlabs", {}).get("texto_completo", "")
    datos_escenas = llamar_claude(sys_escenas, f"Título: {ep.get('titulo', '')}\nTIER: {ep.get('tier', '')}\n\nVisuales para:\n\n{historia}")
    if not datos_escenas: return None

    ep["estilo_visual_global"] = datos_escenas.get("estilo_visual_global", "")
    ep["portada"] = datos_escenas.get("portada", {})
    ep["portada_b"] = datos_escenas.get("portada_b", {})
    ep["escenas"] = datos_escenas.get("escenas", [])
    return datos_guion

def mover_clip(clip, mov, factor=0.12):
    clip = clip.resize(height=1920)
    if clip.w < 1080: clip = clip.resize(width=1080)
    clip = clip.crop(x_center=clip.w/2, y_center=clip.h/2, width=1080, height=1920)
    if mov == "zoom_in": return clip.resize(lambda t: 1 + factor * (t / clip.duration))
    return clip.resize(lambda t: 1 + (factor/2) * (t / clip.duration))

def ensamblar(escenas, ruta_audio, ruta_carpeta, titulo, portada=None):
    print(f"\n--- ENSAMBLANDO: {titulo} ---")
    try: audio = AudioFileClip(ruta_audio)
    except: return
    imgs = [e for e in escenas if os.path.exists(os.path.join(ruta_carpeta, e.get("archivo", "")))]
    if not imgs: return
    duracion_base = audio.duration / len(imgs)
    clips = []

    for i, esc in enumerate(imgs):
        duracion_clip = duracion_base + 1.2 if i == len(imgs) - 1 else duracion_base
        base_clip = mover_clip(ImageClip(os.path.join(ruta_carpeta, esc.get("archivo"))).set_duration(duracion_clip), esc.get("movimiento_camara")).crossfadein(0.1)
        capas_escena = [base_clip.set_position(('center', 'center'))]

        texto_pantalla = esc.get("tip_texto_pantalla")
        if i == 0 and texto_pantalla and texto_pantalla.strip() != "":
            fuente = FUENTE_IMPACT if os.path.exists(FUENTE_IMPACT) else "Arial-Bold"
            texto = texto_pantalla.upper()
            pos_y = int(1920 * 0.20)

            txt_sombra1 = TextClip(texto, fontsize=105, color='black', font=fuente, method='caption', size=(900, None)).set_position(('center', pos_y + 18)).set_duration(duracion_clip)
            txt_sombra2 = TextClip(texto, fontsize=105, color='black', font=fuente, method='caption', size=(900, None)).set_position(('center', pos_y + 9)).set_duration(duracion_clip)
            txt_borde = TextClip(texto, fontsize=105, color='black', stroke_color='black', stroke_width=15, font=fuente, method='caption', size=(900, None)).set_position(('center', pos_y)).set_duration(duracion_clip)
            txt_color = TextClip(texto, fontsize=105, color='#FFDE00', font=fuente, method='caption', size=(900, None)).set_position(('center', pos_y)).set_duration(duracion_clip)
            capas_escena.extend([txt_sombra1, txt_sombra2, txt_borde, txt_color])

        clips.append(CompositeVideoClip(capas_escena, size=(1080, 1920)))

    # PORTADA FINGIDA: primer frame breve = miniatura automática en las plataformas
    if portada and os.path.exists(portada) and DURACION_PORTADA > 0:
        clips.insert(0, ImageClip(portada).set_duration(DURACION_PORTADA).resize((1080, 1920)))

    vid = concatenate_videoclips(clips, method="compose").set_audio(audio.audio_fadeout(0.5))
    vid.write_videofile(os.path.join(ruta_carpeta, f"{titulo}.mp4"), fps=24, codec="libx264", audio_codec="aac", preset="ultrafast", logger=None)

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
    exportar_metadata(ep, titulo)
    return ruta_aud

def lista_imagenes_faltantes(ep, titulo):
    """Devuelve [(prompt_completo, ruta_base, texto_portada|None, ruta_final)] de lo que falta por generar."""
    estilo = ep.get("estilo_visual_global", "")
    cola = []
    # Portada A (arquitectura/ambiente: YouTube e Instagram) y B (rostro desgastado/perturbadora: TikTok y Facebook)
    for clave, nombre in (("portada", "00_portada"), ("portada_b", "00_portada_tiktok")):
        por = ep.get(clave) or {}
        final = os.path.join(titulo, f"{nombre}.png")
        if por.get("prompt") and not os.path.exists(final):
            cola.append((f"{estilo}, {por['prompt']}, {PROMPT_BASE}", os.path.join(titulo, f"{nombre}_base.png"),
                         por.get("texto_portada", ep.get("titulo", "")), final))
    for e in ep.get("escenas", []):
        if not os.path.exists(os.path.join(titulo, e["archivo"])):
            cola.append((f"{estilo}, {e.get('prompt', '')}, {PROMPT_BASE}", os.path.join(titulo, e["archivo"]), None, None))
    return cola

def flujo_videos():
    peticiones = pedir_modo("videos")
    lote, titulos_usados = [], []

    if input("¿Reanudar el último lote guardado (ultimo_lote.json)? [s/N]: ").strip().lower() == "s" and os.path.exists("ultimo_lote.json"):
        lote = json.load(open("ultimo_lote.json", encoding="utf-8"))
    else:
        for i, pet in enumerate(peticiones, 1):
            print(f"\n=========== VIDEO {i}/{len(peticiones)} ===========")
            datos = generar_datos_completos(pet, titulos_usados)
            if not datos:
                print("✖ Falló este video, continúo con el siguiente."); continue
            titulos_usados.append(datos.get("episodio", {}).get("titulo", ""))
            lote.append(datos)
            with open("ultimo_lote.json", "w", encoding="utf-8") as f: json.dump(lote, f, ensure_ascii=False, indent=2)
    if not lote: return

    trabajos = []  # (ep, carpeta, ruta_audio)
    for datos in lote:
        ep = datos.get("episodio", {})
        carpeta = slug(ep.get("titulo", "Video"))
        trabajos.append((ep, carpeta, preparar_video(ep, carpeta)))

    pendientes = [(ep, c) for ep, c, _ in trabajos if lista_imagenes_faltantes(ep, c)]
    if pendientes:
        p = iniciar_sd()
        try:
            for ep, c in pendientes:
                cola = lista_imagenes_faltantes(ep, c)
                print(f"\nGenerando {len(cola)} imágenes para '{c}' en SDXL Lightning...")
                for prompt, ruta, texto_portada, ruta_final in cola:
                    generar_foto(prompt, ruta)
                    if texto_portada is not None and os.path.exists(ruta):  # es una portada (A o B)
                        poner_texto(ruta, ruta_final, texto_portada, pos_y=0.15, fontsize=120)
        finally:
            detener_sd(p)

    print("\n🎬 Imágenes listas. Ensamblando videos automáticamente (sin pausa)...")
    for ep, c, aud in trabajos:
        if os.path.exists(aud):
            ensamblar(ep.get("escenas", []), aud, c, c, portada=os.path.join(c, "00_portada.png"))
    print("\n✅ Lote de videos terminado.")

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
        f.write(f"## 💬 COMUNIDAD\n**Comentario fijado:** {meta.get('comentario_fijado', '')}\n")
        f.write(f"**Encuesta:** {meta.get('encuesta', '')}\n**Gancho próxima parte:** {h.get('gancho_siguiente', '')}\n")
        f.write(f"**Respuestas:** {', '.join(meta.get('respuestas_interaccion', []))}\n\n---\n## 🖼 DIAPOSITIVAS\n")
        for i, s in enumerate(h.get("diapositivas", []), 1):
            f.write(f"### {i}: {s.get('archivo', '')}\n- Texto: {s.get('texto_pantalla', '')}\n- Narración: {s.get('narracion', '')}\n- Prompt: {s.get('prompt', '')}\n\n")

def flujo_historias_foto():
    peticiones = pedir_modo("historias fotográficas")
    n_slides = pedir_int("¿Diapositivas por historia?", 8, 3, 15)
    print("\nFormato de imagen:")
    for k, (nombre, _, _) in FORMATOS_FOTO.items(): print(f"  {k}) {nombre}")
    nombre_f, tam_sd, tam_final = FORMATOS_FOTO.get(input("Opción [1]: ").strip() or "1", FORMATOS_FOTO["1"])

    historias, usados = [], []
    for i, pet in enumerate(peticiones, 1):
        print(f"\n=========== HISTORIA {i}/{len(peticiones)} ===========")
        h = generar_historia_foto(pet, usados, n_slides)
        h = h.get("historia", h) if h else None
        if not h: print("✖ Falló, continúo."); continue
        usados.append(h.get("titulo", ""))
        historias.append(h)
    if not historias: return

    p = iniciar_sd()
    try:
        for h in historias:
            carpeta = "historia_foto_" + slug(h.get("titulo", "historia"), 35)
            os.makedirs(carpeta, exist_ok=True)
            exportar_historia_md(h, carpeta)
            estilo = h.get("estilo_visual_global", "")
            slides = h.get("diapositivas", [])
            print(f"\nGenerando {len(slides)} diapositivas para '{carpeta}'...")
            for i, s in enumerate(slides, 1):
                base = os.path.join(carpeta, f"base_{s['archivo']}")
                final = os.path.join(carpeta, s["archivo"])
                if os.path.exists(final): continue
                if not os.path.exists(base):
                    generar_foto(f"{estilo}, {s.get('prompt', '')}, {PROMPT_BASE}", base, *tam_sd)
                if os.path.exists(base):
                    poner_texto(base, final, s.get("texto_pantalla", ""), tam=tam_final, pos_y=0.08,
                                fontsize=int(tam_final[0] * 0.075), pie=f"{i}/{len(slides)}  ➜ desliza")
    finally:
        detener_sd(p)

    print("✅ Historias fotográficas listas (sin pausa). Textos, copys y hora ideal de publicación en metadata.md de cada carpeta.")
    print("   Las imágenes limpias sin texto están en base_*.png por si quieres re-rotular.")

# ---------------------------------------------------------------------------
# MENÚ
# ---------------------------------------------------------------------------
def iniciar():
    if not API_KEY_ANTHROPIC: print("⚠ Falta API_KEY_ANTHROPIC en .env")
    while True:
        print("\n===== NARRATIVAS SUMERGIDAS =====")
        print("  1) Videos verticales (uno o varios)")
        print("  2) Historias fotográficas (carruseles FB / IG / YouTube)")
        print("  0) Salir")
        op = input("Opción: ").strip()
        if op == "1": flujo_videos()
        elif op == "2": flujo_historias_foto()
        elif op == "0": break

if __name__ == "__main__": iniciar()