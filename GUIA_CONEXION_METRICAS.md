# 📊 Conexión de Métricas Multiplataforma para Optimización con IA
**Narrativas Sumergidas — Motor de Retroalimentación de Rendimiento**

El sistema cuenta con un motor de análisis que extrae métricas reales (vistas, likes, retención/engagement) de tus videos y las inyecta automáticamente en los prompts de Claude (`claude-sonnet-5-5`). De esta forma, cada nuevo guion aprende de los aciertos virales y evita los formatos que no generaron interacción.

---

## ⚡ 1. Cómo Ejecutar el Análisis

Puedes ejecutar el análisis en cualquier momento de dos formas:

1. **Desde la terminal directamente:**
   ```bash
   venv/bin/python generador.py --analizar
   ```
2. **Desde el menú interactivo:**
   Ejecuta `venv/bin/python generador.py` y selecciona la opción:
   `4) Analizar rendimiento del canal (YouTube Analytics & Feedback IA)`

El análisis actualiza automáticamente el archivo `metricas_canal.json`, el cual es leído por Claude en cada generación de guión o historia fotográfica.

---

## 🎥 2. Estado de Conexión de Plataformas

| Plataforma | Estado | Token / Configuración |
|---|---|---|
| **YouTube Shorts** | ✅ **Conectado (100%)** | `token_youtube.json` (Canal Oficial: *Narrativas Sumergidas*) |
| **Facebook & Instagram** | ⏳ **Listo para conectar** | Requiere `token_meta.json` o variables en `.env` |
| **TikTok** | ⏳ **Listo para conectar** | Requiere `token_tiktok.json` o variable en `.env` |

---

## 📘 3. Cómo Conectar Facebook e Instagram (Meta Graph API)

Meta agrupa Facebook Pages e Instagram Professional bajo una misma API oficial:

### Paso 1: Crear App en Meta for Developers
1. Ve a [Meta for Developers](https://developers.facebook.com/) con la cuenta dueña de la Página de Facebook y el Instagram de *Narrativas Sumergidas*.
2. Haz clic en **Mis Apps** → **Crear App** → Selecciona tipo **Negocios (Business)**.
3. Nombre de la App: `Narrativas Sumergidas Analytics`.

### Paso 2: Permisos Necesarios
En el **Explorador de la Graph API** (Graph API Explorer), solicita los siguientes permisos:
* `pages_show_list`
* `pages_read_engagement`
* `read_insights`
* `instagram_basic`
* `instagram_manage_insights`

### Paso 3: Obtener Token Permanente
1. Genera un **User Access Token** y luego cámbialo a un **Page Access Token de larga duración** (60 días o sin caducidad).
2. Obtén el ID de tu Página de Facebook y el ID de tu cuenta de Instagram Business.

### Paso 4: Configurar en el Proyecto
Puedes agregarlo en tu archivo `.env`:
```env
META_ACCESS_TOKEN="tu_token_de_meta_aqui"
META_PAGE_ID="1234567890"
META_IG_ACCOUNT_ID="17841400000000000"
```
O guardarlo en un archivo `token_meta.json` en la raíz del proyecto:
```json
{
  "access_token": "tu_token_de_meta_aqui"
}
```

---

## 🎵 4. Cómo Conectar TikTok (TikTok Creator / Display API)

TikTok ofrece acceso a métricas de publicaciones a través de su portal oficial de desarrolladores:

### Paso 1: Crear App en TikTok for Developers
1. Ve a [TikTok for Developers](https://developers.tiktok.com/) e inicia sesión con la cuenta de *Narrativas Sumergidas*.
2. Crea una aplicación y habilita el producto **Display API** o **Content Posting API**.

### Paso 2: Permisos Requeridos
* `user.info.stats` (para estadísticas de perfil)
* `video.list` (para consultar reproducciones, likes, compartidos y comentarios por video)

### Paso 3: Configurar en el Proyecto
Agrega el token en `.env`:
```env
TIKTOK_ACCESS_TOKEN="tu_tiktok_access_token_aqui"
```
O guárdalo en `token_tiktok.json`:
```json
{
  "access_token": "tu_tiktok_access_token_aqui"
}
```

---

## 🧠 5. Cómo Mejora Claude con Estos Datos

Cuando se genera un video o carrusel, el sistema inyecta en el prompt de Claude:

1. **Patrones Ganadores (Top Videos):**
   - Títulos exactos con mayor número de reproducciones (>500-1,000).
   - Ratios altos de engagement (likes/comentarios por vista).
   - *Directriz a Claude:* Replicar la estructura de gancho inicial (0-3s), el misterio arquitectónico y la ambientación oscura que cautivó a la audiencia.

2. **Formatos que Fracasaron:**
   - Videos con menos de 100 reproducciones.
   - *Directriz a Claude:* Bloquear ángulos que el público ignoró (por ejemplo, desastres mecánicos fríos sin aura de misterio folclórico o ganchos genéricos).

Cada vez que subes videos y ejecutas `python generador.py --analizar`, el motor se vuelve más inteligente y preciso.

