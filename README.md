# Narrativas Sumergidas

Motor de automatización semi-asistida de contenido multiplataforma: videos verticales (9:16) e historias fotográficas (carruseles) de True Crime, arquitectura maldita, leyendas urbanas y folclore. Contexto completo en [`gemini.md`](gemini.md).

## Archivos principales
| Archivo | Función |
|---|---|
| `generador.py` | Menú: videos en lote / historias fotográficas |
| `prompt_guion.md` | Guion, tier, enrutamiento por red, metadata y hora ideal |
| `prompt_escenas.md` | Dirección de arte, escenas y portadas A/B |
| `prompt_historia_foto.md` | Historias fotográficas (FB / IG / YouTube Community) |
| `gemini.md` | Contexto maestro del proyecto |
| `requirements.txt` | Dependencias Python |

## Plan de instalación (Mac mini M4, Apple Silicon)
1. **Herramientas base**
   ```bash
   xcode-select --install
   brew install python@3.10 git ffmpeg imagemagick wget
   ```
2. **Clonar el repo y crear entorno**
   ```bash
   git clone https://github.com/fminajasc1000/narrativasumergidas.git
   cd narrativasumergidas
   python3.10 -m venv venv && source venv/bin/activate
   pip install -r requirements.txt
   ```
3. **Claves (`.env`, nunca se versiona)**
   ```
   API_KEY_ANTHROPIC=...
   API_KEY_ELEVENLABS=...
   VOICE_ID_ELEVENLABS=...
   ```
4. **Stable Diffusion WebUI (AUTOMATIC1111)** dentro del proyecto (ruta `stable-diffusion-webui/`, ignorada por git):
   ```bash
   git clone https://github.com/AUTOMATIC1111/stable-diffusion-webui.git
   ```
   Descarga el modelo `realvisxlV50_v50LightningBakedvae_full_fp16.safetensors` y colócalo en `stable-diffusion-webui/models/Stable-diffusion/`. Ajustes: 6 pasos, CFG 2.0, sampler `DPM++ SDE`, 768x1344. `generador.py` arranca el WebUI solo (`--api --nowebui --port 7860`).
5. **Ejecutar**
   ```bash
   source venv/bin/activate
   python generador.py
   ```

## Flujo, Cinemática y Reanudación Inteligente (v4.4)
1. **Transiciones Ópticas y Cinemática:** Disolvencia óptica fluida (`crossfade` de 0.35 s) con `CompositeVideoClip` layering para eliminar saltos bruscos y destellos negros. Movimientos de cámara dinámicos (`zoom_in`, `zoom_out`, `pan_left`, `pan_right`, `tilt_up`, `tilt_down`, `slow_drift`) y soporte para fundido a negro teatral (`fade_black`).
2. **Generación para Cualquier Momento del Día (Videos e Historias):** Selector de momento del día (Mañana, Mediodía, Tarde, Noche, Madrugada o Multi-horario 24h). Tanto para videos como para carruseles de fotos, adapta la iluminación de SDXL (niebla matutina, sol de mediodía, crepúsculo dorado o penumbra nocturna) y genera copys y estrategias específicas para cada franja en `metadata.md`.
3. **Cuota Visual Equilibrada:** Genera entre 18 y 25 escenas (~4.5 s por imagen) adaptadas a la duración de la locución para apreciar el arte sin saturar al espectador.
4. **Detección y Reanudación de Carpetas:** Si una generación quedó a medias (por ejemplo, con el `.mp3` y las imágenes generadas pero sin ensamblar el `.mp4`), el menú principal lo detecta y muestra la opción `3) Reanudar / Ensamblar carpeta existente`. Permite reanudar de inmediato, generar solo las imágenes faltantes si hiciera falta, o ensamblar con las existentes.
5. **Sincronización Exacta MP3 vs Escenas:** Los cortes de cada escena se calculan proporcionalmente sobre la duración real del archivo de audio `.mp3` generado por ElevenLabs, registrando los intervalos exactos `[MM:SS - MM:SS]` en `metadata.md`.
6. **Control Térmico y de CPU en Mac mini M4:** Los procesos de renderizado de MoviePy (máximo 4 threads, preset `veryfast`) y de Stable Diffusion WebUI (`OMP_NUM_THREADS=4`) están limitados para evitar el 100% de uso de CPU y garantizar la estabilidad de los periféricos USB/Bluetooth.
7. **Ensamblado Automático sin Freno:** Tras generar las imágenes, el video se ensambla **automáticamente** (sin pausas). Cada video se clasifica por *tier* (ABSURDO, ARQUITECTURA, FOLCLORE, MORBO) y su `metadata.md` indica en qué redes publicar, cuáles omitir y la hora ideal de publicación. Si la locución excede 120 s, el guion se reedita automáticamente.

