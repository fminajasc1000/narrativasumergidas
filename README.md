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

## Flujo
Tras generar las imágenes, el video se ensambla **automáticamente** (sin pausa). Cada video se clasifica por *tier* (ABSURDO, ARQUITECTURA, FOLCLORE, MORBO, TRAGEDIA) y su `metadata.md` indica en qué redes publicar, cuáles omitir y la hora ideal. Bilibili está suspendido temporalmente.

