# ⚙️ CONTEXTO MAESTRO DEL PROYECTO: NARRATIVAS SUMERGIDAS
**Versión de Arquitectura:** 3.1 (SDXL Lightning, Multiplataforma, Lotes, Portadas e Historias Fotográficas)
**Hardware Objetivo:** Mac mini M4 (16GB RAM) - Apple Silicon
**Nicho del Canal:** True Crime, Arquitectura Maldita, Leyendas Urbanas Oscuras, Supervivencia Extrema y Folclore (Enfoque en minidocumentales inmersivos de 90-120 segundos).

---

## 1. RESUMEN DEL SISTEMA Y STACK TECNOLÓGICO
Este proyecto es un motor de automatización semi-asistida para crear videos verticales virales (9:16). El flujo de trabajo genera guiones, locuciones, metadatos, prompts fotográficos, renderiza las imágenes y ensambla el video con un motor de texto multicapa (*Shadow Stack*).

*   **Cerebro Narrativo y Director de Arte:** API de Anthropic (`claude-sonnet-5-5`).
*   **Locución Sintética:** API de ElevenLabs (`eleven_multilingual_v2`).
*   **Motor Visual:** Stable Diffusion XL vía API local (WebUI puerto 7860).
    *   **Modelo Obligatorio:** `realvisxlV50_v50LightningBakedvae_full_fp16.safetensors`
    *   **Ajustes SDXL Lightning:** 6 pasos, CFG 2.0, Resolución 768x1344, Sampler `DPM++ SDE`.
*   **Ensamblaje de Video:** Python con la librería `MoviePy` (con animaciones de zoom/pan y corrección de encuadre estricta sin franjas negras).

---

## 1.1 NOVEDADES v3.1 (`generador.py`)
`generador.py` ahora abre un **menú**: `1) Videos verticales`, `2) Historias fotográficas`, `0) Salir`.

*   **Definición del contenido (ambos flujos):** 1) Aleatorio, 2) Tema(s) del usuario (varios separados por `|`), 3) Historia completa del usuario (se adapta tal cual, una por ejecución).
*   **Generación en lote (N videos):** el usuario elige la cantidad. Claude recibe la lista de casos ya usados para no repetir. SDXL arranca una sola vez y hay **un solo Freno del Director** para todo el lote, tras el cual se ensamblan todos los `.mp4`. El lote se guarda en `ultimo_lote.json` y puede reanudarse.
*   **Portada:** `prompt_escenas.md` devuelve `portada` (`prompt` + `texto_portada`, máx. 6 palabras). Se genera `00_portada.png` con el título impreso (Shadow Stack con PIL) y se inserta como primer frame del video (`DURACION_PORTADA`, 0.35 s; 0 desactiva) para que funcione como miniatura. El creador puede reemplazar `00_portada.png` durante el Freno del Director.
*   **Historias fotográficas (carruseles FB / IG / YouTube Community):** usan `prompt_historia_foto.md`. Claude devuelve 3-15 diapositivas (texto en pantalla ≤12 palabras + prompt SDXL), serie/parte, `gancho_siguiente`, `encuesta` A/B, comentario fijado y copys para Facebook, Instagram y YouTube Community, enfocados en generar comunidad (debate, votación, guardar/compartir). Formatos: 4:5 (896x1120 → 1080x1350, por defecto), 9:16 (768x1344) o 1:1 (1024x1024). Salida en `historia_foto_<titulo>/` con diapositivas finales, `base_*.png` (sin texto) y `metadata.md`. Aplican las mismas reglas core de prompts (sección 2).
*   **Seguridad:** las API keys se leen de `.env` (`API_KEY_ANTHROPIC`, `API_KEY_ELEVENLABS`, `VOICE_ID_ELEVENLABS`); nunca deben escribirse en el código. `VOICE_ID_ELEVENLABS` tiene prioridad sobre el voice_id del guion.

---
## 1.5 NOVEDADES v4.4 (`generador.py` - Transiciones Ópticas, Movimientos y Flexibilidad 24h)
*   **Transiciones Ópticas Suaves:** Eliminados los cortes planos o destellos negros con `CompositeVideoClip` layering. Disolvencia óptica suave (`crossfade` de 0.35 s) donde la escena saliente se funde suavemente con la entrante sin huecos. Soporte para `fade_black` (fundido a negro teatral para saltos de época o muerte) y `cut` (corte directo para gancho inicial).
*   **Cámara Cinematográfica Dinámica:** `mover_clip` soporta 7 movimientos fluidos: `zoom_in` (+8%), `zoom_out` (-8%), `pan_left`, `pan_right`, `tilt_up`, `tilt_down` y `slow_drift` diagonal orgánico.
*   **Cuota Visual Calibrada:** Se calcula automáticamente entre 18 y 25 escenas (~4.5 s por imagen) para que el espectador aprecie los detalles de SDXL sin saturarse ni generar excesos absurdos como 64 imágenes.
*   **Flexibilidad 24 Horas (Videos e Historias):** Selector de momento del día (Mañana, Mediodía, Tarde, Noche Prime, Madrugada/Insomnio, o Multi-horario completo). La iluminación de las imágenes se adapta al momento (niebla matutina, sol duro de mediodía, hora dorada crepuscular o penumbra nocturna) y `metadata.md` incluye la estrategia de publicación y copys para cualquier hora (tanto para videos como para carruseles de fotos).
*   **Historial Permanente y Git Commit por Historia:** Seguimiento atómico en `historias_usadas.json` y respaldo en `~/.narrativas_historias_usadas.json`. Cada historia procesada genera automáticamente un commit en Git (`historia(tipo): registrar '...' en historial permanente`), asegurando que nunca se repita contenido aunque el usuario borre carpetas multimedia para liberar disco.

---

## 1.2 CALIBRACIÓN ALGORÍTMICA v4.0 (MÉTRICAS REALES)
**Datos de YouTube Shorts (7 videos):** La Casa de las Brujas (1m53s) 1,003 vistas · Lecumberri (1m26s) 673 · Mansión Winchester (1m27s) 604 · Faro abandonado (1m08s) 167 · Pelayo (2m56s) 69 · Kaprun (1m11s) 14. Facebook viraliza lo absurdo/escatológico (58k con "El pedo más letal", pero 144 en YouTube); TikTok premia morbo visual y leyendas locales (600-850); Instagram solo sobrevive con estética gótica/arquitectónica; Bilibili sin tracción (envíos suspendidos).

**Conclusiones aplicadas:**
*   **Duración:** locución ideal de **85-115 s** (≈215-280 palabras). `MAX_DURACION_S = 120` en `generador.py`: si el audio de ElevenLabs lo excede, el guion se reedita automáticamente (hasta 3 intentos) y se regenera la locución.
*   **Temática:** se priorizan leyendas urbanas latinas, folclore mexicano y arquitectura maldita. **Prohibidas** tragedias internacionales o desastres sin componente sobrenatural (el tier `TRAGEDIA` fue eliminado).
*   **Tiers y enrutamiento (forzado en Python, `RUTEO_TIER`):** `FOLCLORE`/`ARQUITECTURA` → YouTube Shorts y TikTok (+ Instagram y Facebook); `MORBO`/`ABSURDO` (gore, escatología) → **solo Facebook y TikTok** para proteger la retención de YouTube. La metadata de redes omitidas se descarta y `metadata.md` lista "Publicar en / Omitir".
*   **Metadata localizada:** jerga y términos locales de México/Latinoamérica en títulos, descripciones y hashtags. Cada red incluye `hora_publicacion` (hora CDMX); Bilibili suspendido hasta tener un prompt propio (idealmente con subtítulos en mandarín).
*   **Visual:** realismo mágico y época estricta; sin anacronismos ni escenarios modernos; `antique, decaying architecture` en leyendas; portadas A/B (`00_portada.png` arquitectura para YouTube/IG, `00_portada_tiktok.png` rostro/elemento perturbador para TikTok/FB).
*   **Flujo:** ya **no hay pausa** (Freno del Director eliminado): tras generar imágenes se ensambla el video automáticamente. Repositorio: `github.com/fminajasc1000/narrativasumergidas`.

---

## 2. REGLAS CORE DE LA INGENIERÍA DE PROMPTS (SINTONÍA ABSOLUTA)
El sistema sufrió problemas de "alucinaciones IA" y anacronismos que fueron erradicados implementando estas reglas inquebrantables en el código y en los prompts:

1.  **Inyección Dinámica de Negativos (El Antivirus):** El script de Python escanea los prompts de Claude. Si detecta "no humans", inyecta bloqueos masivos contra rostros y siluetas. Si detecta destrucción o fuego, inyecta bloqueos contra monstruos, sangre y esqueletos literales.
2.  **Literalidad Extrema y Anti-Clichés:** La IA tiene prohibido usar conceptos ambiguos. Si el guion requiere un socavón ardiente, el prompt debe bloquear la palabra volcán `(no volcano:1.5)`. Si pide urnas funerarias, se usan palabras primitivas como `antique brass jars`.
3.  **Desgaste Contextual y Prohibición de Belleza:** Está prohibido generar "modelos atractivos" o gente joven. Si la historia es de los 70s, el prompt debe exigir `exhausted 1970s soviet geologist with dirty sweaty face`.
4.  **Estrategia Multiplataforma (Metadatos):** Un mismo video se vende con 5 ángulos psicológicos distintos:
    *   *YouTube Shorts:* Documental histórico y SEO.
    *   *TikTok:* Shock visual, incredulidad y terror.
    *   *Facebook Reels:* Polémica, debate ético o moral.
    *   *Instagram Reels:* Visual, misterioso, para guardar.
    *   *Bilibili:* Lore, detalle oculto para incentivar "bullet comments".
5.  **El Freno de Mano del Director:** El código Python PAUSA la ejecución justo antes de ensamblar el `.mp4`. Esto permite al creador humano revisar la carpeta de imágenes, detectar alucinaciones, reemplazarlas y luego presionar ENTER para el render final.

---

## 3. ARCHIVOS MAESTROS DEL SISTEMA

### A. `prompt_guion.md` (El Cerebro Narrativo)
```text
Rol: Guionista True Crime.
Formato: LARGO (170-190 palabras).

Estructura:
1. Gancho (0-3s): Afirmación visual.
2. Desarrollo: Tensión, contexto arquitectónico o folclore.
3. Cierre (CTA): Pregunta desafiante + Nombre del canal. Prohibido decir "Suscríbete". Ej: "¿Tú qué harías? Abre la puerta a Narrativas Sumergidas."

REGLAS DE METADATA (5 REDES):
Vende el mismo video adaptando la psicología a cada red:
- YOUTUBE SHORTS: Ángulo documental e histórico. SEO puro.
- TIKTOK: Ángulo perturbador y shock rápido. Incredulidad.
- FACEBOOK REELS: Ángulo polémico. Pregunta moral para forzar peleas en comentarios.
- INSTAGRAM REELS: Ángulo visual y misterioso. Invita a guardar y compartir el caso.
- BILIBILI: Ángulo de "lore" y análisis profundo. Títulos intrigantes que inviten a la comunidad a desmenuzar el misterio.

Salida JSON estricta:
{
  "episodio": {
    "titulo": "Título Interno",
    "locucion_elevenlabs": {
      "voice_id": "6bNjXphfWPUDHuFkgDt3",
      "texto_completo": "[Tono] Texto..."
    },
    "metadata": {
      "youtube_shorts": {"titulo": "...", "descripcion": "...", "hashtags": "..."},
      "tiktok": {"titulo": "...", "descripcion": "...", "hashtags": "..."},
      "facebook_reels": {"titulo": "...", "descripcion": "...", "hashtags": "..."},
      "instagram_reels": {"titulo": "...", "descripcion": "...", "hashtags": "..."},
      "bilibili": {"titulo": "...", "descripcion": "...", "hashtags": "..."},
      "comentario_fijado": "Pregunta polémica general",
      "respuestas_interaccion": ["R1", "R2"]
    }
  }
}