import asyncio
import threading
import json
import os
import sys
import io
import time
import re
import atexit
import signal
import urllib.request
import unicodedata
from collections import defaultdict

try:
    if hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(encoding='utf-8', errors='replace', line_buffering=True)
        sys.stderr.reconfigure(encoding='utf-8', errors='replace', line_buffering=True)
except Exception:
    pass

from http.server import BaseHTTPRequestHandler, HTTPServer

# --- TWITCH IMPORTS ---
try:
    import twitchio
    from twitchio.ext import commands
    TWITCH_DISPONIBLE = True
except ImportError:
    TWITCH_DISPONIBLE = False

# ============================================================
# CONFIGURACIÓN BÁSICA
# ============================================================
ARCHIVO_USUARIO_TIKTOK = "tiktok_user.txt"
USUARIO_POR_DEFECTO    = "xxxxxxxxx"
ARCHIVO_TWITCH_CONFIG  = "twitch_config.txt"
ARCHIVO_API_KEY_FISH   = "fish_api.key"   
ARCHIVO_API_KEY_EULER  = "euler_api.key"  

# ============================================================
# FILTRO DE IDIOMA — solo chino, coreano, japonés, español e inglés
# ============================================================
RANGOS_PERMITIDOS = [
    (0x0041, 0x005A),   # Latin A-Z           (inglés)
    (0x0061, 0x007A),   # Latin a-z           (inglés)
    (0x00C0, 0x024F),   # Latin extendido     (español: áéíóúñü, etc.)
    (0x3000, 0x303F),   # Puntuación CJK
    (0x3040, 0x30FF),   # Hiragana + Katakana (japonés)
    (0x3400, 0x4DBF),   # CJK Extensión A
    (0x4E00, 0x9FFF),   # CJK Unificado       (chino / kanji japonés)
    (0xAC00, 0xD7AF),   # Hangul sílabas      (coreano)
    (0x1100, 0x11FF),   # Hangul Jamo         (coreano)
    (0x3130, 0x318F),   # Hangul Compat Jamo  (coreano: ㅋㅋㅋ, ㅠㅠㅠ)
    (0xFF00, 0xFFEF),   # Fullwidth forms     (chino / japonés)
]

# Signos de puntuación comunes que NO cuentan como "letras"
PUNTUACION_PERMITIDA = set(" .,!?;:'\"-()[]{}¡¿…:—–@#&*/\\、。！？：；「」『』（）・〜ー～_")

def _letra_permitida(o):
    for lo, hi in RANGOS_PERMITIDOS:
        if lo <= o <= hi:
            return True
    return False

def idioma_permitido(texto):
    """True si el texto está en chino, coreano, japonés, español o inglés.
       Los emojis, símbolos, números y puntuación NO cuentan: solo se evalúan
       las LETRAS. Si el mensaje empieza con @menciones de nombres en otro
       alfabeto (árabe, etc.), se saltan esas palabras y se evalúa el resto."""
    if not texto:
        return False
    palabras = texto.split()
    if palabras and palabras[0].startswith("@"):
        i = 0
        while i < len(palabras):
            w = palabras[i]
            letras_w = [c for c in w if c.isalpha()]
            fuera = [c for c in letras_w if not _letra_permitida(ord(c))]
            es_mencion_ajena = (i == 0) or fuera or not letras_w
            if es_mencion_ajena and (fuera or not letras_w or w.startswith("@")):
                i += 1
            else:
                break
        resto = " ".join(palabras[i:])
        if any(c.isalpha() for c in resto):
            texto = resto
    total = 0
    permitidos = 0
    for ch in texto:
        if not ch.isalpha():
            continue
        total += 1
        if _letra_permitida(ord(ch)):
            permitidos += 1
    if total == 0:
        return False
    return (permitidos / total) >= 0.6

def cargar_usuario_tiktok():
    try:
        if os.path.exists(ARCHIVO_USUARIO_TIKTOK):
            with open(ARCHIVO_USUARIO_TIKTOK, "r", encoding="utf-8") as f:
                for linea in f:
                    t = linea.strip()
                    if t and not t.startswith("#"):
                        return t.lstrip("@").lower()
    except Exception as e:
        print(f"[AVISO] No se pudo leer {ARCHIVO_USUARIO_TIKTOK}: {e}")
    return USUARIO_POR_DEFECTO

def cargar_twitch_config():
    cfg = {"client_id": "", "client_secret": "", "token": "", "canal": ""}
    try:
        if os.path.exists(ARCHIVO_TWITCH_CONFIG):
            with open(ARCHIVO_TWITCH_CONFIG, "r", encoding="utf-8") as f:
                for linea in f:
                    if "=" in linea:
                        clave, valor = linea.split("=", 1)
                        clave = clave.strip()
                        valor = valor.strip()
                        if clave in cfg:
                            cfg[clave] = valor
    except Exception as e:
        print(f"[AVISO] No se pudo leer {ARCHIVO_TWITCH_CONFIG}: {e}")
    return cfg

def cargar_api_keys():
    key_fish = ""
    key_euler = ""
    try:
        if os.path.exists(ARCHIVO_API_KEY_FISH):
            with open(ARCHIVO_API_KEY_FISH, "r", encoding="utf-8") as f:
                key_fish = f.read().strip()
    except Exception: pass
    try:
        if os.path.exists(ARCHIVO_API_KEY_EULER):
            with open(ARCHIVO_API_KEY_EULER, "r", encoding="utf-8") as f:
                key_euler = f.read().strip()
    except Exception: pass
    return key_fish, key_euler

TIKTOK_USERS = [cargar_usuario_tiktok()]
TWITCH_CFG = cargar_twitch_config()
FISH_API_KEY, EULER_API_KEY = cargar_api_keys()

CHAT_FILE    = "chat.txt"
HTTP_PORT    = 8832

LIMPIAR_AL_CERRAR = True
_ultimo_evento_http = 0.0
TIMEOUT_TIKFINITY_SEG = 15

def _marcar_evento_http():
    global _ultimo_evento_http
    _ultimo_evento_http = time.time()

def _tikfinity_activo():
    if _ultimo_evento_http == 0.0: return False
    return (time.time() - _ultimo_evento_http) < TIMEOUT_TIKFINITY_SEG

try:
    from TikTokLive.events import LikeEvent
    TAP_DISPONIBLE = True
except ImportError:
    TAP_DISPONIBLE = False   # el aviso se muestra en monitor_usuario, solo si se usa TikTok

PAQUETES_COMUNES = [
    ["TikTokLive", "TikTokLive"],
    ["twitchio", "twitchio"],
    ["PIL", "Pillow"]  # Asegura que Pillow esté instalado para convertir WebP a PNG
]

# ============================================================
# DICCIONARIO DE REGALOS
# ============================================================
TRADUCCION_REGALOS = {
    "rose": ("la Rosa", "Rosas"), "rosa": ("la Rosa", "Rosas"),
    "white rose": ("la Rosa blanca", "Rosas blancas"), "heart me": ("el Quiéreme", "Quiéreme"),
    "hand hearts": ("el Corazón con las manos", "Corazones con las manos"),
    "hand heart": ("el Corazón con las manos", "Corazones con las manos"),
    "finger heart": ("el Corazón con los dedos", "Corazones con los dedos"),
    "flame heart": ("el Corazón de fuego", "Corazones de fuego"), "heart": ("el Corazón", "Corazones"),
    "tiktok": ("el TikTok", "TikToks"), "tiktok universe": ("el Universo TikTok", "Universos TikTok"),
    "gg": ("el GG", "GG"), "team bracelet": ("la Pulsera de equipo", "Pulseras de equipo"),
    "ice cream cone": ("el Cono de helado", "Conos de helado"), "doughnut": ("la Dona", "Donas"),
    "donut": ("la Dona", "Donas"), "lollipop": ("la Paleta", "Paletas"), "coffee": ("el Café", "Cafés"),
    "chili": ("el Ají picante", "Ajíes picantes"), "mishka bear": ("el Osito", "Ositos"),
    "corgi": ("el Perrito Corgi", "Perritos Corgi"), "lion": ("el León", "Leones"),
    "panda": ("el Oso Panda", "Osos Panda"), "swan": ("el Cisne", "Cisnes"), "unicorn": ("el Unicornio", "Unicornios"),
    "teddy": ("el Osito de peluche", "Ositos de peluche"), "teddy bear": ("el Osito de peluche", "Ositos de peluche"),
    "perfume": ("el Perfume", "Perfumes"), "paper crane": ("la Grulla de papel", "Grullas de papel"),
    "little crown": ("la Coronita", "Coronitas"), "crown": ("la Corona", "Coronas"), "cap": ("la Gorra", "Gorras"),
    "hat and mustache": ("el Sombrero y bigote", "Sombreros y bigote"), "confetti": ("el Confeti", "Confetis"),
    "sunglasses": ("las Gafas de sol", "Gafas de sol"), "garland": ("la Guirnalda", "Guirnaldas"),
    "love balloon": ("el Globo de amor", "Globos de amor"), "galaxy": ("la Galaxia", "Galaxias"),
    "money gun": ("la Pistola de billetes", "Pistolas de billetes"), "whale diving": ("la Ballena", "Ballenas"),
    "wishing bottle": ("la Botella de los deseos", "Botellas de los deseos"),
    "gaming headset": ("los Audífonos gamer", "Audífonos gamer"), "guitar": ("la Guitarra", "Guitarras"),
    "kiss": ("el Beso", "Besos"), "boxing gloves": ("los Guantes de boxeo", "Guantes de boxeo"),
    "flowers": ("las Flores", "Flores"), "weights": ("las Pesas", "Pesas"), "train": ("el Tren", "Trenes"),
    "sports car": ("el Auto deportivo", "Autos deportivos"), "motorcycle": ("la Motocicleta", "Motocicletas"),
    "maraca": ("la Maraca", "Maracas"), "maracas": ("la Maraca", "Maracas"), "drum": ("el Tambor", "Tambores"),
    "trumpet": ("la Trompeta", "Trompetas"), "piano": ("el Piano", "Pianos"),
    "love you so much": ("el Te quiero mucho", "Te quiero mucho"), "diamond": ("el Diamante", "Diamantes"),
    "crystal ball": ("la Bola de cristal", "Bolas de cristal"), "star": ("la Estrella", "Estrellas"),
    "fireworks": ("los Fuegos artificiales", "Fuegos artificiales"), "rocket": ("el Cohete", "Cohetes"),
    "planet": ("el Planeta", "Planetas"), "moon": ("la Luna", "Lunas"), "sun": ("el Sol", "Soles"),
    "soccer ball": ("el Balón de fútbol", "Balones de fútbol"),
}

def formatear_regalo(nombre_ingles, cantidad):
    if not nombre_ingles: return "el regalo" if cantidad <= 1 else f"enviar {cantidad} regalos"
    clave = nombre_ingles.strip().lower()
    if clave in TRADUCCION_REGALOS:
        singular, plural = TRADUCCION_REGALOS[clave]
        return singular if cantidad <= 1 else f"enviar {cantidad} {plural}"
    parciales = [
        ("heart me", "el Quiéreme", "Quiéreme"), ("rose", "la Rosa", "Rosas"),
        ("heart", "el Corazón", "Corazones"), ("galaxy", "la Galaxia", "Galaxias"),
        ("crown", "la Corona", "Coronas"), ("bear", "el Osito", "Ositos"),
        ("coffee", "el Café", "Cafés"), ("kiss", "el Beso", "Besos"),
        ("maraca", "la Maraca", "Maracas"), ("diamond", "el Diamante", "Diamantes"),
        ("star", "la Estrella", "Estrellas"), ("moon", "la Luna", "Lunas"),
    ]
    for eng, sing, plur in parciales:
        if eng in clave: return sing if cantidad <= 1 else f"enviar {cantidad} {plur}"
    limpio = nombre_ingles.strip()
    if cantidad > 1: return f"{cantidad} regalos {limpio}"
    bajo = limpio.lower()
    if bajo.endswith("a") or bajo.endswith("as"): return f"la {limpio}"
    if bajo.endswith("o") or bajo.endswith("os"): return f"el {limpio}"
    if bajo.endswith("s"): return f"el regalo {limpio}"
    return f"el regalo {limpio}"

# ============================================================
# ICONOS DE REGALOS (para la tabla de Sound Alerts del dashboard)
# Se guardan en iconos_regalos/<nombre>.png y se enlazan a los alias
# en español mediante iconos_regalos/indice.txt  (formato: alias|archivo)
# ============================================================
CARPETA_ICONOS = "iconos_regalos"
ARCHIVO_INDICE_ICONOS = os.path.join(CARPETA_ICONOS, "indice.txt")
_lock_iconos = threading.Lock()
_iconos_procesados = set()
_catalogo_precargado = False

def _normalizar_regalo(s):
    s = unicodedata.normalize("NFD", s or "")
    s = "".join(c for c in s if unicodedata.category(c) != "Mn")
    return re.sub(r"\s+", " ", s.lower()).strip()

def _nucleo_regalo(nombre):
    # Misma lógica que nucleoRegalo() del dashboard Java.
    n = _normalizar_regalo(nombre)
    n = re.sub(r"^enviar\s+\d+\s+", "", n)
    n = re.sub(r"^\d+\s+regalos\s+", "", n)
    n = re.sub(r"^(el|la|los|las|un|una)\s+regalo\s+", "", n)
    n = re.sub(r"^(el|la|los|las|un|una)\s+", "", n)
    return n.strip()

def _url_de_imagen(img):
    if not img: return ""
    if isinstance(img, str):
        return img if img.startswith(("http", "//")) else ""
    candidatos = []
    if isinstance(img, dict):
        for k in ("url_list", "urlList", "m_urls", "urls"):
            v = img.get(k)
            if isinstance(v, (list, tuple)): candidatos.extend(v)
    else:
        for k in ("m_urls", "url_list", "urls"):
            v = getattr(img, k, None)
            if isinstance(v, (list, tuple)): candidatos.extend(v)
        if not candidatos and hasattr(img, "to_dict"):
            try: return _url_de_imagen(img.to_dict())
            except Exception: return ""
    for u in candidatos:
        if isinstance(u, str) and u: return u
    return ""

def _url_icono_gift(gift):
    if gift is None: return ""
    if isinstance(gift, dict):
        for k in ("image", "icon"):
            u = _url_de_imagen(gift.get(k))
            if u: return u
        return ""
    for attr in ("image", "icon", "icon_image", "preview_image"):
        u = _url_de_imagen(getattr(gift, attr, None))
        if u: return u
    if hasattr(gift, "to_dict"):
        try: return _url_icono_gift(gift.to_dict())
        except Exception: pass
    return ""

def _bajar_icono(nombre_ingles, url):
    """Descarga el icono (si no existe) y devuelve {alias: archivo} para el índice."""
    clave_en = _normalizar_regalo(nombre_ingles)
    if not clave_en or not url: return {}
    archivo = (re.sub(r"[^a-z0-9]+", "_", clave_en).strip("_") or "regalo") + ".png"
    ruta = os.path.join(CARPETA_ICONOS, archivo)
    try:
        os.makedirs(CARPETA_ICONOS, exist_ok=True)
        if not os.path.exists(ruta):
            if url.startswith("//"): url = "https:" + url
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=6) as resp:
                datos = resp.read()
            try:
                from PIL import Image
                img = Image.open(io.BytesIO(datos)).convert("RGBA")
                img.save(ruta, "PNG")
            except Exception:
                with open(ruta, "wb") as f: f.write(datos)
    except Exception as e:
        print(f"[ICONOS] No se pudo bajar el icono de '{nombre_ingles}': {e}")
        return {}
    alias = {clave_en}
    for cant in (1, 2):
        try: alias.add(_nucleo_regalo(formatear_regalo(nombre_ingles, cant)))
        except Exception: pass
    return {a: archivo for a in alias if a}

def _actualizar_indice_iconos(nuevos):
    if not nuevos: return
    with _lock_iconos:
        actual = {}
        try:
            if os.path.exists(ARCHIVO_INDICE_ICONOS):
                with open(ARCHIVO_INDICE_ICONOS, "r", encoding="utf-8") as f:
                    for l in f:
                        if "|" in l:
                            a, b = l.rstrip("\r\n").split("|", 1)
                            if a.strip() and b.strip(): actual[a.strip()] = b.strip()
        except Exception: pass
        if all(actual.get(a) == b for a, b in nuevos.items()): return
        actual.update(nuevos)
        try:
            os.makedirs(CARPETA_ICONOS, exist_ok=True)
            tmp = ARCHIVO_INDICE_ICONOS + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                for a in sorted(actual): f.write(f"{a}|{actual[a]}\n")
            os.replace(tmp, ARCHIVO_INDICE_ICONOS)
        except Exception as e:
            print(f"[ICONOS] No se pudo escribir el índice: {e}")

def _procesar_icono_regalo(nombre_ingles, url):
    _actualizar_indice_iconos(_bajar_icono(nombre_ingles, url))

def registrar_icono_regalo(nombre_ingles, url):
    """Descarga en segundo plano el icono de un regalo recién recibido."""
    clave = _normalizar_regalo(nombre_ingles)
    if not clave or not url or clave in _iconos_procesados: return
    _iconos_procesados.add(clave)
    threading.Thread(target=_procesar_icono_regalo, args=(nombre_ingles, url), daemon=True).start()

def _bajar_catalogo(lista):
    nuevos = {}
    n = 0
    for g in lista:
        try:
            nombre = g.get("name") if isinstance(g, dict) else getattr(g, "name", None)
            if not nombre: continue
            clave = _normalizar_regalo(str(nombre))
            if clave in _iconos_procesados: continue
            _iconos_procesados.add(clave)
            r = _bajar_icono(str(nombre), _url_icono_gift(g))
            if r:
                nuevos.update(r)
                n += 1
        except Exception: pass
    _actualizar_indice_iconos(nuevos)
    print(f"[ICONOS] Catálogo de regalos listo: {n} iconos.")

async def precargar_catalogo_iconos(client):
    """Intenta bajar los iconos de TODO el catálogo de regalos (una vez por sesión)."""
    global _catalogo_precargado
    if _catalogo_precargado: return
    _catalogo_precargado = True
    try:
        datos = await client.web.fetch_gift_list()
    except Exception as e:
        print(f"[ICONOS] No se pudo leer el catálogo de regalos (se bajarán según lleguen): {e}")
        return
    try:
        if isinstance(datos, dict):
            lista = datos.get("gifts") or datos.get("data") or []
            if isinstance(lista, dict): lista = lista.get("gifts") or []
        else:
            lista = datos or []
        await asyncio.get_running_loop().run_in_executor(None, _bajar_catalogo, list(lista))
    except Exception as e:
        print(f"[ICONOS] Error procesando el catálogo: {e}")

def limpiar_chat():
    try:
        with open(CHAT_FILE, "w", encoding="utf-8") as f: f.write("")
        print(f"[LIMPIEZA] {CHAT_FILE} vaciado.")
    except Exception as e: print(f"[LIMPIEZA] Error al vaciar {CHAT_FILE}: {e}")

def limpiar_atexit():
    _flush_taps_final()
    if LIMPIAR_AL_CERRAR: limpiar_chat()

atexit.register(limpiar_atexit)

COOLDOWN_USUARIO = 4
COOLDOWN_GLOBAL  = 0.0   # antes 1.5: descartaba mensajes normales; la cola del Java ya regula
LONGITUD_MIN     = 3 
LONGITUD_MAX     = 150
IGNORAR_RISAS    = True

LISTA_NEGRA = {
    "jaja", "jajaja", "jajajaja", "jeje", "jiji", "xd", "xdd", "xddd", "xdddd",
    "lol", "lmao", "wtf", "si", "no", "ok", "hola", "hey", "holaa", "holaaa", "ola", "gg", "ez",
    # --- chino / japonés / coreano ---
    "哈哈", "哈哈哈", "哈哈哈哈", "笑死", "草", "牛",          # chino
    "あはは", "笑笑", "うける", "それな",                       # japonés
    "ㅋㅋ", "ㅋㅋㅋ", "ㅎㅎ", "ㅎㅎㅎ", "ㅇㅇ", "ㄹㅇ",           # coreano
}

_ultimo_por_usuario = defaultdict(float)
_ultimo_global = 0.0
_ultimo_mensaje_por_usuario = {}
_lock = threading.Lock()

PATRON_REGALAME = re.compile(
    r"\b(regalame|regálame|regalas|regalás|regalen|regalar|"
    r"regalarme|regalarnos|regalito|regalitos)\b", re.IGNORECASE
)

_pediches_advertidos = {}
COOLDOWN_AVISO_REGALAME = 10.0
REINICIO_CONTEO_REGALAME = 1200.0
_pediches_conteo = {}

def registrar_evento_regalame(usuario):
    ahora = time.time()
    cant, ultimo_pedido = _pediches_conteo.get(usuario, (0, 0.0))
    if ahora - ultimo_pedido > REINICIO_CONTEO_REGALAME: cant = 0
    cant += 1
    _pediches_conteo[usuario] = (cant, ahora)
    ultimo_aviso = _pediches_advertidos.get(usuario, 0.0)
    if ahora - ultimo_aviso >= COOLDOWN_AVISO_REGALAME:
        _pediches_advertidos[usuario] = ahora
        print(f"[REGALAME DETECTADO] Advertencia x{cant} enviada a @{usuario}")
        with _lock:
            with open(CHAT_FILE, "a", encoding="utf-8") as f:
                f.write(f"__REGALAME__|{usuario}|{cant}\n")

_taps_por_usuario = defaultdict(lambda: {"count": 0, "avatar": ""})
_taps_lock = threading.Lock()
_ultimo_flush_taps = 0.0
TAP_FLUSH_SEG = 2.0

def _acumular_tap(usuario, cantidad, avatar_url=""):
    global _ultimo_flush_taps
    snapshot = None
    with _taps_lock:
        _taps_por_usuario[usuario]["count"] += cantidad
        if avatar_url:
            _taps_por_usuario[usuario]["avatar"] = avatar_url
            
        ahora = time.time()
        if ahora - _ultimo_flush_taps >= TAP_FLUSH_SEG:
            snapshot = dict(_taps_por_usuario)
            _taps_por_usuario.clear()
            _ultimo_flush_taps = ahora
    if snapshot:
        try:
            with _lock:
                with open(CHAT_FILE, "a", encoding="utf-8") as f:
                    for u, data in snapshot.items():
                        f.write(f"__TAPTAP__|{u}|{data['count']}|{data['avatar']}\n")
        except Exception: pass

def _flush_taps_final():
    with _taps_lock:
        snapshot = dict(_taps_por_usuario)
        _taps_por_usuario.clear()
    if not snapshot: return
    try:
        with _lock:
            with open(CHAT_FILE, "a", encoding="utf-8") as f:
                for u, data in snapshot.items():
                    f.write(f"__TAPTAP__|{u}|{data['count']}|{data['avatar']}\n")
    except Exception: pass

_usuarios_registrados = set()

def registrar_usuario(usuario, nickname=""):
    u = (usuario or "").strip().lower().replace("@", "").replace("|", " ").strip()
    if not u or u == "anonimo": return
    if not _recordar(_usuarios_registrados, u): return
    
    nick_limpio = (nickname or "").replace("|", " ").strip()
    try:
        with _lock:
            with open(CHAT_FILE, "a", encoding="utf-8") as f:
                f.write(f"__USUARIO__|{u}|{nick_limpio}\n")
    except Exception: pass

def _flush_taps_loop():
    while True:
        time.sleep(TAP_FLUSH_SEG)
        try: _flush_taps_final()
        except Exception: pass

def escribir_chat(usuario, mensaje):
    usuario = (usuario or "").strip().lower().replace("@", "")
    mensaje = (mensaje or "").strip()
    if not usuario or not mensaje: return
    if len(mensaje) > LONGITUD_MAX:
        mensaje = mensaje[:LONGITUD_MAX]
    with _lock:
        with open(CHAT_FILE, "a", encoding="utf-8") as f:
            f.write(f"{usuario}: {mensaje}\n")
    print(f"[OK] {usuario}: {mensaje}")

def es_comentario_valido(usuario, mensaje_box):
    global _ultimo_global
    ahora = time.time()
    usuario = (usuario or "").strip().lower()
    mensaje = (mensaje_box[0] or "").strip()

    if not usuario or not mensaje:
        return False

    # 1) Advertencia de "regálame" (se hace sobre el original)
    if PATRON_REGALAME.search(mensaje):
        registrar_evento_regalame(usuario)
        return False

    # 1b) Quitar emotes de TikTok tipo [wow] [heart] [rockyloveit] y no leer
    #     mensajes que son solo @menciones (+ emojis) sin texto propio
    mensaje = re.sub(r"\[[A-Za-z0-9_]+\]", "", mensaje).strip()
    mensaje_box[0] = mensaje
    if not any(c.isalpha() for c in re.sub(r"@\S+", "", mensaje)):
        return False

    # 2) FILTRO DE IDIOMA: solo chino, coreano, japonés, español e inglés
    if not idioma_permitido(mensaje):
        print(f"[IDIOMA] Ignorado (no es chino/coreano/japonés/español/inglés): {mensaje[:50]}")
        return False

    # 3) Longitud mínima — 2 caracteres para CJK, 3 para latín
    longitud_min = 2 if any(0x3040 <= ord(c) <= 0x9FFF or 0xAC00 <= ord(c) <= 0xD7AF
                            for c in mensaje) else LONGITUD_MIN
    if len(mensaje) < longitud_min:
        return False

    # 4) Lista negra (siempre en minúsculas, funciona igual para latín)
    if mensaje.lower().strip("!?., ") in LISTA_NEGRA:
        return False

    # 5) Filtro de risas — ahora soporta latín Y CJK/Hangul
    if IGNORAR_RISAS:
        letras = [c for c in mensaje if c.isalpha()]
        if len(letras) < 3:
            return False
        # "jajaja", "哈哈哈", "ㅋㅋㅋ", "wwww", "あはは" repetidos → risa
        if len({c.lower() for c in letras}) == 1:
            return False

    # 6) Cooldowns
    if ahora - _ultimo_global < COOLDOWN_GLOBAL:
        return False
    if ahora - _ultimo_por_usuario[usuario] < COOLDOWN_USUARIO:
        return False
    if _ultimo_mensaje_por_usuario.get(usuario) == mensaje.lower():
        return False

    _ultimo_global = ahora
    _ultimo_por_usuario[usuario] = ahora
    _ultimo_mensaje_por_usuario[usuario] = mensaje.lower()

    # Se devuelve el mensaje ORIGINAL (sin traducir)
    mensaje_box[0] = mensaje
    return True

class H(BaseHTTPRequestHandler):
    def _responder(self, code, data):
        body = json.dumps(data).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Headers", "*")
        self.send_header("Access-Control-Allow-Methods", "*")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        _marcar_evento_http()
        if self.path.startswith("/api/app/info"):
            self._responder(200, {"data": {"author": "Tu", "name": "Puente Voces", "version": "1.0"}})
        elif self.path.startswith("/api/features/categories"):
            self._responder(200, {"data": [{"categoryId": "voces", "categoryName": "Voces Fish"}]})
        elif self.path.startswith("/api/features/actions"):
            self._responder(200, {"data": [{"actionId": "leer", "actionName": "Leer mensaje"}]})
        else: self._responder(200, {"data": []})

    def do_POST(self):
        _marcar_evento_http()
        l = int(self.headers.get("Content-Length", 0))
        raw = self.rfile.read(l)
        try:
            d = json.loads(raw)
            ctx = d.get("context") or d.get("data") or d
            u = ""
            for key in ("username", "uniqueId", "userId", "user", "nickname", "nickName"):
                val = ctx.get(key)
                if isinstance(val, str) and val: u = val; break
                if isinstance(val, dict):
                    for sub in ("uniqueId", "username", "nickname", "userId"):
                        if val.get(sub): u = val[sub]; break
                    if u: break
            m = ""
            for key in ("message", "comment", "text", "content"):
                if ctx.get(key): m = ctx[key]; break
            
            registrar_usuario(u)
            
            mensaje_box = [m]
            if es_comentario_valido(u, mensaje_box):
                escribir_chat(u, mensaje_box[0])
        except Exception: pass
        self._responder(200, {"data": []})

    def do_OPTIONS(self):
        _marcar_evento_http()
        self._responder(200, {})

    def log_message(self, format, *args): return

def iniciar_http():
    print(f"[HTTP] Servidor escuchando en http://127.0.0.1:{HTTP_PORT}")
    HTTPServer(("127.0.0.1", HTTP_PORT), H).serve_forever()

AGRADECER_SEGUIDORES = True
DAR_BIENVENIDA       = True
COOLDOWN_BIENVENIDA  = 4.0
MAX_USUARIOS_MEMORIA = 5000

_ultimo_share = {}
COOLDOWN_SHARE = 10.0
_seguidores_agradecidos = set()
_bienvenidos = set()
_ultima_bienvenida = 0.0

def obtener_nombre_tiktok(user_obj):
    if not user_obj:
        return "amigo"
    
    nombre = ""
    for attr in ("nickname", "nick_name", "display_name", "displayName"):
        val = getattr(user_obj, attr, None)
        if val and str(val).strip():
            nombre = str(val).strip()
            break
            
    if not nombre:
        info = getattr(user_obj, "info", None)
        if isinstance(info, dict):
            for k in ("nickname", "nickName", "display_name", "displayName"):
                val = info.get(k)
                if val and str(val).strip():
                    nombre = str(val).strip()
                    break

    if not nombre:
        try:
            if hasattr(user_obj, "to_dict"):
                d = user_obj.to_dict()
                if isinstance(d, dict):
                    for k in ("nickname", "nickName", "display_name"):
                        val = d.get(k)
                        if val and str(val).strip():
                            nombre = str(val).strip()
                            break
        except Exception:
            pass
            
    if not nombre:
        for attr in ("unique_id", "uniqueId", "username"):
            val = getattr(user_obj, attr, None)
            if val and str(val).strip():
                nombre = str(val).strip()
                break
                
    if not nombre:
        nombre = "amigo"

    nombre = re.sub(r'[^\w\s\.\-\'ÁÉÍÓÚáéíóúÑñ]', '', nombre).strip()
    return nombre or "amigo"

def obtener_unique_id(user_obj):
    if not user_obj: return ""
    for attr in ("unique_id", "uniqueId", "user_id", "userId", "username"):
        val = getattr(user_obj, attr, None)
        if val and str(val).strip():
            return str(val).strip()
    return ""

def _nombre_evento(event):
    user_obj = getattr(event, "user", None) or getattr(event, "user_info", None)
    nombre = obtener_nombre_tiktok(user_obj)
    uid = ""
    if user_obj:
        uid = getattr(user_obj, "unique_id", "") or getattr(user_obj, "uniqueId", "")
    limpio = (nombre or "amigo").replace("@", "").replace("|", " ").replace("\n", " ").replace("\r", " ").strip()
    return limpio, uid.lower()

def _escribir_evento(tipo, nombre):
    with _lock:
        with open(CHAT_FILE, "a", encoding="utf-8") as f:
            f.write(f"__{tipo}__|{nombre}\n")

def _recordar(conjunto, clave):
    if clave in conjunto: return False
    if len(conjunto) >= MAX_USUARIOS_MEMORIA: conjunto.clear()
    conjunto.add(clave)
    return True

# ============================================================
# DESCARGA DE AVATARES
# ============================================================
def descargar_avatar_local(user_obj, username_id):
    if not user_obj or not username_id: return ""
    
    u_limpio = re.sub(r'[^a-zA-Z0-9._-]', '', username_id).lower()
    if not u_limpio: return ""
    
    local_file = os.path.join("avatares_taptap", f"{u_limpio}.png")
    
    if os.path.exists(local_file): return local_file

    avatar_url = ""
    if hasattr(user_obj, "to_dict"):
        try:
            d = user_obj.to_dict()
            for key in ("avatarThumb", "avatar_thumb", "avatarMedium", "avatar_medium"):
                if key in d and isinstance(d[key], dict) and "url_list" in d[key]:
                    urls = d[key]["url_list"]
                    if urls:
                        avatar_url = urls[0]
                        break
        except Exception: pass
    
    if not avatar_url:
        for attr in ("avatar_thumb", "avatarThumb", "avatar_medium", "avatarMedium", "avatar_larger", "avatarLarger"):
            avatar_obj = getattr(user_obj, attr, None)
            if avatar_obj:
                if isinstance(avatar_obj, str) and avatar_obj.startswith("http"):
                    avatar_url = avatar_obj
                    break
                elif hasattr(avatar_obj, "url_list") and avatar_obj.url_list:
                    avatar_url = avatar_obj.url_list[0]
                    break
                elif isinstance(avatar_obj, dict) and "url_list" in avatar_obj and avatar_obj["url_list"]:
                    avatar_url = avatar_obj["url_list"][0]
                    break

    if avatar_url:
        try:
            if not avatar_url.startswith("http"):
                avatar_url = "https:" + avatar_url
            
            req = urllib.request.Request(avatar_url, headers={'User-Agent': 'Mozilla/5.0'})
            with urllib.request.urlopen(req, timeout=3) as resp:
                img_data = resp.read()
                os.makedirs("avatares_taptap", exist_ok=True)
                try:
                    from PIL import Image
                    img = Image.open(io.BytesIO(img_data))
                    if img.mode in ("RGBA", "P"):
                        img = img.convert("RGBA")
                    else:
                        img = img.convert("RGB")
                    img.save(local_file, "PNG")
                except Exception:
                    with open(local_file, "wb") as f_out:
                        f_out.write(img_data)
            return local_file
        except Exception as ex:
            pass
    return ""

if TWITCH_DISPONIBLE:
    class TwitchBot(commands.Bot):
        def __init__(self):
            super().__init__(
                client_id=TWITCH_CFG.get("client_id", ""),
                client_secret=TWITCH_CFG.get("client_secret", ""),
                token=TWITCH_CFG.get("token", ""),
                prefix='!',
                initial_channels=[TWITCH_CFG.get("canal", "")]
            )

        async def event_ready(self):
            print(f"[TWITCH] Conectado exitosamente al canal de {self.nick}")

        async def event_message(self, message):
            if message.echo: return
            u = message.author.name
            m = message.content
            bits = message.tags.get("bits")
            if bits:
                try: cant = int(bits)
                except: cant = 1
                frase_regalo = formatear_regalo("los Bits", cant)
                linea = f"__REGALO__|{frase_regalo}|{u}|{cant}"
                with _lock:
                    with open(CHAT_FILE, "a", encoding="utf-8") as f:
                        f.write(linea + "\n")
                print(f"[TWITCH BITS] {u} -> {frase_regalo}")

            registrar_usuario(u)
            
            mensaje_box = [m]
            if es_comentario_valido(u, mensaje_box):
                print(f"[TWITCH] {u}: {mensaje_box[0]}")
                escribir_chat(u, mensaje_box[0])
            else:
                print(f"[IGNORADO TWITCH] {u}: {m}")

        async def event_raw_usernotice(self, channel, tags):
            msg_id = tags.get("msg-id")
            if msg_id in ("sub", "resub", "subgift", "submysterygift"):
                u = tags.get("display-name", tags.get("login", "alguien"))
                frase_regalo = formatear_regalo("la Suscripción", 1)
                linea = f"__REGALO__|{frase_regalo}|{u}|1"
                with _lock:
                    with open(CHAT_FILE, "a", encoding="utf-8") as f:
                        f.write(linea + "\n")
                print(f"[TWITCH SUB] {u} -> {frase_regalo}")
else:
    TwitchBot = None

async def monitor_usuario(usuario):
    try:
        from TikTokLive import TikTokLiveClient
        from TikTokLive.events import ConnectEvent, DisconnectEvent, CommentEvent, GiftEvent
        from TikTokLive.client.web.web_settings import WebDefaults
        
        if EULER_API_KEY:
            WebDefaults.tiktok_sign_api_key = EULER_API_KEY
            print("[TIKTOK] API Key de Euler cargada desde euler_api.key correctamente.")
        else:
            print("[AVISO] No se encontró euler_api.key. Conectando de forma anónima.")
            
    except ImportError:
        print("[ERROR] Falta la librería TikTokLive. Instala con: pip install TikTokLive")
        return

    try:
        from TikTokLive.events import FollowEvent, JoinEvent
        SOCIAL_DISPONIBLE = True
    except ImportError: SOCIAL_DISPONIBLE = False

    try:
        from TikTokLive.events import ShareEvent
        SHARE_DISPONIBLE = True
    except ImportError: SHARE_DISPONIBLE = False

    if not TAP_DISPONIBLE:
        print("[AVISO] Este TikTokLive no expone LikeEvent. Actualiza: pip install -U TikTokLive")

    client = TikTokLiveClient(unique_id=f"@{usuario}")

    @client.on(ConnectEvent)
    async def on_connect(event):
        print(f"[TIKTOK] Conectado al live de @{usuario}")
        try: asyncio.create_task(precargar_catalogo_iconos(client))
        except Exception: pass
    @client.on(DisconnectEvent)
    async def on_disconnect(event): print(f"[TIKTOK] Desconectado de @{usuario}. Reintentando...")

    @client.on(CommentEvent)
    async def on_comment(event):
        try:
            user_obj = (getattr(event, "user", None) or getattr(event, "user_info", None))
            u = "anonimo"
            nick = "amigo"
            if user_obj is not None:
                u = obtener_unique_id(user_obj)
                if not u: u = "anonimo"
                nick = obtener_nombre_tiktok(user_obj)
            
            # Descargar avatar y registrar con ambos nombres
            descargar_avatar_local(user_obj, u)
            registrar_usuario(u, nick)
            
            m_original = (getattr(event, "comment", None) or getattr(event, "message", None) or getattr(event, "text", None) or "").strip()
            mensaje_box = [m_original]
            if es_comentario_valido(u, mensaje_box):
                m_traducido = mensaje_box[0]
                print(f"[TIKTOK @{usuario}] {u}: {m_traducido}")
                escribir_chat(u, m_traducido)
            else:
                print(f"[IGNORADO] {u}: {m_original}")
        except Exception: pass

    @client.on(GiftEvent)
    async def on_gift(event: GiftEvent):
        try:
            gift = getattr(event, "gift", None)
            if gift is not None:
                try:
                    _nom_icono = getattr(gift, "name", None) or getattr(gift, "gift_name", None)
                    if _nom_icono: registrar_icono_regalo(str(_nom_icono), _url_icono_gift(gift))
                except Exception: pass
            es_combo =bool(getattr(gift, "streakable", False) or getattr(gift, "combo", False)) if gift else False
            fin_racha = bool(getattr(event, "repeat_end", 0))
            if es_combo and not fin_racha: return
            
            user_obj = getattr(event, "user", None)
            donador = obtener_nombre_tiktok(user_obj)
            donador = donador.replace("@", "").replace("|", " ").strip() or "amigo"
            
            raw_gift_name = getattr(gift, "name", None) or getattr(gift, "gift_name", None) or "Regalo" if gift else "Regalo"
            count = getattr(event, "repeat_count", 1) or 1
            try: count = max(1, int(count))
            except: count = 1
            frase_regalo = formatear_regalo(str(raw_gift_name), count).replace("|", " ").strip()
            
            try: valor = int(getattr(gift, "diamond_count", 0) or getattr(gift, "diamondCount", 0) or 1)
            except Exception: valor = 1
            valor = max(1, valor)
            
            linea = f"__REGALO__|{frase_regalo}|{donador}|{count}|{valor}"
            print(f"[TIKTOK REGALO @{usuario}] {donador} -> {frase_regalo}")
            with _lock:
                with open(CHAT_FILE, "a", encoding="utf-8") as f:
                    f.write(linea + "\n")
        except Exception: pass

    if SOCIAL_DISPONIBLE:
        @client.on(FollowEvent)
        async def on_follow(event):
            try:
                if not AGRADECER_SEGUIDORES: return
                nombre, uid = _nombre_evento(event)
                user_obj = getattr(event, "user", None)
                if uid: 
                    descargar_avatar_local(user_obj, uid)
                    registrar_usuario(uid, nombre)
                
                if not nombre or uid == usuario.lower() or not _recordar(_seguidores_agradecidos, uid or nombre.lower()): return
                print(f"[TIKTOK SEGUIDOR @{usuario}] {nombre}")
                _escribir_evento("SEGUIDOR", nombre)
            except Exception: pass

        @client.on(JoinEvent)
        async def on_join(event):
            global _ultima_bienvenida
            try:
                user_obj = getattr(event, "user", None)
                nombre, uid = _nombre_evento(event)
                
                if uid: 
                    descargar_avatar_local(user_obj, uid)
                    registrar_usuario(uid, nombre)
                
                if not DAR_BIENVENIDA: return
                if not nombre or uid == usuario.lower(): return
                ahora = time.time()
                if ahora - _ultima_bienvenida < COOLDOWN_BIENVENIDA or not _recordar(_bienvenidos, uid or nombre.lower()): return
                _ultima_bienvenida = ahora
                print(f"[TIKTOK ENTRÓ @{usuario}] {nombre}")
                _escribir_evento("BIENVENIDA", nombre)
            except Exception: pass

    if SHARE_DISPONIBLE:
        @client.on(ShareEvent)
        async def on_share(event):
            try:
                nombre, uid = _nombre_evento(event)
                user_obj = getattr(event, "user", None)
                if uid: 
                    descargar_avatar_local(user_obj, uid)
                    registrar_usuario(uid, nombre)
                
                if uid and uid == usuario.lower(): return
                nombre = nombre or "amigo"
                ahora = time.time()
                clave = uid or nombre.lower()
                if ahora - _ultimo_share.get(clave, 0.0) < COOLDOWN_SHARE: return
                _ultimo_share[clave] = ahora
                print(f"[TIKTOK COMPARTIR @{usuario}] {nombre}")
                _escribir_evento("COMPARTIR", nombre)
            except Exception: pass

    if TAP_DISPONIBLE:
        @client.on(LikeEvent)
        async def on_tap(event):
            try:
                user_obj = getattr(event, "user", None)
                u_nick = obtener_nombre_tiktok(user_obj)
                u_id = obtener_unique_id(user_obj) or u_nick
                
                descargar_avatar_local(user_obj, u_id)
                registrar_usuario(u_id, u_nick)
                
                avatar_local_path = descargar_avatar_local(user_obj, u_id)
                
                cnt = getattr(event, "count", None) or getattr(event, "like_count", None) or 1
                try: cnt = max(1, int(cnt))
                except: cnt = 1
                
                _acumular_tap(u_nick.replace("|", " ").strip() or "anonimo", cnt, avatar_local_path)
            except Exception as e:
                print(f"[TIKTOK TAP] Error extrayendo datos: {e}")

    print(f"[TIKTOK] Conectando al live de @{usuario}...")
    while True:
        try:
            if _tikfinity_activo():
                print("[MODO] TikFinity activo - desconectando conexión directa de TikTok...")
                return
            await client.connect()
        except Exception as e:
            print(f"[TIKTOK @{usuario}] Error: {e}. Reintentando en 10s...")
            await asyncio.sleep(10)

# ============================================================
# KICK  (API oficial + webhooks firmados)
# ============================================================
# Flujo:  Kick --POST firmado--> túnel público --> 127.0.0.1:<puerto Kick>
# - Se usa un puerto APARTE del de TikFinity (8832) para que el túnel solo
#   exponga webhooks firmados por Kick y nunca el endpoint genérico de chat.
# - Todo con librería estándar (sin pip): la firma RSA se verifica a mano.
import base64
import hashlib
import hmac
import datetime as _dt
import urllib.parse
import urllib.error
from collections import OrderedDict
from http.server import ThreadingHTTPServer

ARCHIVO_KICK_CONFIG = "kick_config.txt"
KICK_API            = "https://api.kick.com/public/v1"
KICK_OAUTH_TOKEN    = "https://id.kick.com/oauth/token"
KICK_PUERTO_DEFECTO = 8833
KICK_EVENTOS = [
    ("chat.message.sent", 1),
    ("channel.followed", 1),
    ("channel.subscription.new", 1),
    ("channel.subscription.renewal", 1),
    ("channel.subscription.gifts", 1),
    ("kicks.gifted", 1),
]
KICK_TOLERANCIA_SEG = 900    # antigüedad máxima aceptada de un webhook
KICK_REVISION_SEG   = 900    # cada cuánto se revisa que sigan las suscripciones
PATRON_EMOTE_KICK   = re.compile(r"\[emote:\d+:[^\]]*\]")

def cargar_kick_config():
    cfg = {"client_id": "", "client_secret": "", "canal": "", "puerto": str(KICK_PUERTO_DEFECTO)}
    try:
        if os.path.exists(ARCHIVO_KICK_CONFIG):
            with open(ARCHIVO_KICK_CONFIG, "r", encoding="utf-8") as f:
                for linea in f:
                    t = linea.strip()
                    if not t or t.startswith("#") or "=" not in t: continue
                    clave, valor = t.split("=", 1)
                    clave = clave.strip()
                    if clave in cfg: cfg[clave] = valor.strip()
    except Exception as e:
        print(f"[AVISO] No se pudo leer {ARCHIVO_KICK_CONFIG}: {e}")
    canal = cfg["canal"].strip().lstrip("@")
    if "/" in canal: canal = canal.rstrip("/").rsplit("/", 1)[-1]   # acepta https://kick.com/usuario
    cfg["canal"] = canal.lower()
    return cfg

KICK_CFG = cargar_kick_config()

# Plataforma elegida en el dashboard (botón "Plataforma"): tiktok | kick | ambas
ARCHIVO_PLATAFORMA = "plataforma.txt"

def cargar_plataforma():
    try:
        if os.path.exists(ARCHIVO_PLATAFORMA):
            with open(ARCHIVO_PLATAFORMA, "r", encoding="utf-8-sig") as f:
                for linea in f:
                    t = linea.strip().lower()
                    if t and not t.startswith("#"):
                        return t if t in ("kick", "ambas") else "tiktok"
    except Exception as e:
        print(f"[AVISO] No se pudo leer {ARCHIVO_PLATAFORMA}: {e}")
    return "tiktok"

PLATAFORMA = cargar_plataforma()

def kick_configurado():
    return all([KICK_CFG.get("client_id"), KICK_CFG.get("client_secret"), KICK_CFG.get("canal")])

def kick_activo():
    return PLATAFORMA in ("kick", "ambas") and kick_configurado()

def _kick_puerto():
    try:
        p = int(KICK_CFG.get("puerto") or KICK_PUERTO_DEFECTO)
        if 1024 <= p <= 65535 and p != HTTP_PORT: return p
    except ValueError: pass
    return KICK_PUERTO_DEFECTO

_kick = {"token": "", "token_exp": 0.0, "broadcaster_id": None, "clave": None}
_kick_ids_vistos = OrderedDict()
_lock_kick = threading.Lock()

# ---------------- RSA PKCS#1 v1.5 + SHA-256 (solo verificar) ----------------
_SHA256_DIGESTINFO = bytes.fromhex("3031300d060960864801650304020105000420")

def _der_leer(b, i):
    """Lee un TLV DER en el offset i -> (tag, inicio_contenido, fin_contenido)."""
    tag = b[i]; i += 1
    largo = b[i]; i += 1
    if largo & 0x80:
        n = largo & 0x7F
        if n == 0 or n > 4: raise ValueError("DER: longitud no soportada")
        largo = int.from_bytes(b[i:i + n], "big"); i += n
    if i + largo > len(b): raise ValueError("DER: truncado")
    return tag, i, i + largo

def _rsa_cargar_pem(pem):
    cuerpo = "".join(l.strip() for l in pem.strip().splitlines() if l.strip() and not l.strip().startswith("-----"))
    der = base64.b64decode(cuerpo)
    tag, ini, _ = _der_leer(der, 0)
    if tag != 0x30: raise ValueError("clave pública no válida")
    tag2, _, fin2 = _der_leer(der, ini)
    if tag2 == 0x30:                                   # SubjectPublicKeyInfo
        tag3, i3, f3 = _der_leer(der, fin2)            # BIT STRING
        if tag3 != 0x03: raise ValueError("clave pública no válida")
        interior = der[i3 + 1:f3]                      # salta el byte de bits sin usar
        tag4, ini4, _ = _der_leer(interior, 0)
        if tag4 != 0x30: raise ValueError("clave pública no válida")
        datos, off = interior, ini4
    else:                                              # PKCS#1: SEQUENCE { n, e }
        datos, off = der, ini
    t1, a1, b1 = _der_leer(datos, off)
    t2, a2, b2 = _der_leer(datos, b1)
    if t1 != 0x02 or t2 != 0x02: raise ValueError("clave pública no válida")
    return int.from_bytes(datos[a1:b1], "big"), int.from_bytes(datos[a2:b2], "big")

def _rsa_verificar(clave, mensaje, firma):
    n, e = clave
    k = (n.bit_length() + 7) // 8
    if len(firma) != k: return False
    s = int.from_bytes(firma, "big")
    if s >= n: return False
    em = pow(s, e, n).to_bytes(k, "big")
    t = _SHA256_DIGESTINFO + hashlib.sha256(mensaje).digest()
    if k < len(t) + 11: return False
    esperado = b"\x00\x01" + b"\xff" * (k - len(t) - 3) + b"\x00" + t
    return hmac.compare_digest(em, esperado)

def _rfc3339_a_epoch(ts):
    m = re.match(r"^(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2})(?:\.\d+)?(Z|[+-]\d{2}:\d{2})?$", (ts or "").strip())
    if not m: raise ValueError("timestamp no válido")
    zona = m.group(2)
    if not zona or zona == "Z": zona = "+00:00"
    return _dt.datetime.fromisoformat(m.group(1) + zona).timestamp()

# ---------------- Cliente HTTP mínimo ----------------
def _resumen(obj, n=200):
    t = obj if isinstance(obj, str) else json.dumps(obj, ensure_ascii=False)
    return t[:n]

def _kick_http(metodo, url, token="", json_body=None, form=None, timeout=20):
    cab = {"Accept": "application/json", "User-Agent": "PuenteVoces/1.0"}
    datos = None
    if token: cab["Authorization"] = f"Bearer {token}"
    if json_body is not None:
        datos = json.dumps(json_body).encode("utf-8"); cab["Content-Type"] = "application/json"
    elif form is not None:
        datos = urllib.parse.urlencode(form).encode("utf-8"); cab["Content-Type"] = "application/x-www-form-urlencoded"
    req = urllib.request.Request(url, data=datos, headers=cab, method=metodo)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            estado, crudo = r.status, r.read()
    except urllib.error.HTTPError as e:
        estado, crudo = e.code, (e.read() or b"")
    texto = crudo.decode("utf-8", "replace")
    try: return estado, (json.loads(texto) if texto else {})
    except ValueError: return estado, texto

def _kick_obtener_token():
    ahora = time.time()
    if _kick["token"] and ahora < _kick["token_exp"] - 120: return _kick["token"]
    st, d = _kick_http("POST", KICK_OAUTH_TOKEN, form={
        "grant_type": "client_credentials",
        "client_id": KICK_CFG["client_id"], "client_secret": KICK_CFG["client_secret"]})
    if st != 200 or not isinstance(d, dict) or not d.get("access_token"):
        raise RuntimeError(f"No se pudo obtener el token de Kick (HTTP {st}). Revisa client_id y client_secret. {_resumen(d)}")
    _kick["token"] = d["access_token"]
    try: dur = float(d.get("expires_in") or 3600)
    except (TypeError, ValueError): dur = 3600.0
    _kick["token_exp"] = ahora + dur
    return _kick["token"]

def _kick_resolver_canal(token):
    slug = KICK_CFG["canal"]
    st, d = _kick_http("GET", f"{KICK_API}/channels?slug={urllib.parse.quote(slug)}", token)
    datos = d.get("data") if isinstance(d, dict) else None
    if isinstance(datos, list): datos = datos[0] if datos else None
    bid = datos.get("broadcaster_user_id") if isinstance(datos, dict) else None
    if st != 200 or not bid:
        raise RuntimeError(f"No se encontró el canal '{slug}' en Kick (HTTP {st}). Usa el nombre exacto de kick.com/<canal>. {_resumen(d)}")
    return int(bid)

def _buscar_pem(obj):
    if isinstance(obj, str): return obj if "-----BEGIN" in obj else None
    if isinstance(obj, dict):
        for v in obj.values():
            r = _buscar_pem(v)
            if r: return r
    if isinstance(obj, list):
        for v in obj:
            r = _buscar_pem(v)
            if r: return r
    return None

def _kick_cargar_clave(token=""):
    st, d = _kick_http("GET", f"{KICK_API}/public-key", token)
    pem = _buscar_pem(d)
    if st != 200 or not pem:
        raise RuntimeError(f"No se pudo obtener la clave pública de Kick (HTTP {st}). {_resumen(d)}")
    _kick["clave"] = _rsa_cargar_pem(pem)

def _kick_asegurar_suscripciones(token, bid):
    """Crea solo las suscripciones que falten. Devuelve la lista de errores por evento."""
    existentes = set()
    st, d = _kick_http("GET", f"{KICK_API}/events/subscriptions?broadcaster_user_id={bid}", token)
    if st == 200 and isinstance(d, dict) and isinstance(d.get("data"), list):
        for s in d["data"]:
            if not isinstance(s, dict): continue
            nombre = s.get("event") or s.get("name") or s.get("type")
            dueno = s.get("broadcaster_user_id")
            if nombre and (dueno is None or str(dueno) == str(bid)): existentes.add(str(nombre))
    faltan = [(n, v) for n, v in KICK_EVENTOS if n not in existentes]
    if not faltan: return []
    st, d = _kick_http("POST", f"{KICK_API}/events/subscriptions", token, json_body={
        "broadcaster_user_id": bid, "method": "webhook",
        "events": [{"name": n, "version": v} for n, v in faltan]})
    if st in (401, 403):
        raise RuntimeError(f"Kick rechazó la suscripción (HTTP {st}). Puede que exija un token de usuario con el permiso "
                           f"events:subscribe en lugar del token de aplicación. {_resumen(d)}")
    if st not in (200, 201):
        raise RuntimeError(f"No se pudo suscribir a los eventos (HTTP {st}). {_resumen(d)}")
    errores = []
    for r in (d.get("data") if isinstance(d, dict) else None) or []:
        if isinstance(r, dict) and r.get("error"): errores.append(f"{r.get('name')}: {r['error']}")
    return errores

# ---------------- Eventos de Kick -> chat.txt ----------------
def _kick_nombre(persona, defecto="alguien"):
    if not isinstance(persona, dict): return defecto
    n = persona.get("username") or persona.get("channel_slug") or ""
    n = str(n).replace("@", "").replace("|", " ").replace("\n", " ").replace("\r", " ").strip()
    return n or defecto

def _kick_regalo(frase, emisor, cantidad, valor):
    frase = frase.replace("|", " ").replace("\n", " ").strip()
    emisor = (emisor or "alguien").replace("|", " ").strip() or "alguien"
    linea = f"__REGALO__|{frase}|{emisor}|{max(1, int(cantidad))}|{max(1, int(valor))}"
    with _lock:
        with open(CHAT_FILE, "a", encoding="utf-8") as f: f.write(linea + "\n")
    print(f"[KICK REGALO] {emisor} -> {frase}")

def _kick_evento(tipo, d):
    global _ultima_bienvenida
    slug = KICK_CFG["canal"].lower()

    if tipo == "chat.message.sent":
        s = d.get("sender") or {}
        nombre = _kick_nombre(s, "")
        if not nombre: return
        uid = nombre.lower()
        texto = PATRON_EMOTE_KICK.sub("", str(d.get("content") or ""))
        texto = re.sub(r"\s+", " ", texto).strip()
        registrar_usuario(uid, nombre)
        es_dueno = uid == slug or str(s.get("channel_slug", "")).lower() == slug
        if DAR_BIENVENIDA and not es_dueno:
            ahora = time.time()
            if ahora - _ultima_bienvenida >= COOLDOWN_BIENVENIDA and _recordar(_bienvenidos, uid):
                _ultima_bienvenida = ahora
                print(f"[KICK BIENVENIDA] {nombre}")
                _escribir_evento("BIENVENIDA", nombre)
        caja = [texto]
        if es_comentario_valido(uid, caja):
            print(f"[KICK] {uid}: {caja[0]}")
            escribir_chat(uid, caja[0])
        else:
            print(f"[IGNORADO KICK] {uid}: {texto}")

    elif tipo == "channel.followed":
        if not AGRADECER_SEGUIDORES: return
        nombre = _kick_nombre(d.get("follower"), "")
        if not nombre: return
        uid = nombre.lower()
        registrar_usuario(uid, nombre)
        if uid == slug or not _recordar(_seguidores_agradecidos, uid): return
        print(f"[KICK SEGUIDOR] {nombre}")
        _escribir_evento("SEGUIDOR", nombre)

    elif tipo in ("channel.subscription.new", "channel.subscription.renewal"):
        nombre = _kick_nombre(d.get("subscriber"), "alguien")
        frase = "la Suscripción" if tipo.endswith(".new") else "la renovación de la Suscripción"
        _kick_regalo(frase, nombre, 1, 1)

    elif tipo == "channel.subscription.gifts":
        g = d.get("gifter") or {}
        anonimo = isinstance(g, dict) and bool(g.get("is_anonymous"))
        nombre = "alguien" if anonimo else _kick_nombre(g, "alguien")
        giftees = d.get("giftees")
        n = len(giftees) if isinstance(giftees, list) and giftees else 1
        frase = "una Suscripción regalada" if n <= 1 else f"enviar {n} Suscripciones regaladas"
        _kick_regalo(frase, nombre, n, 1)

    elif tipo == "kicks.gifted":
        s = d.get("sender") or d.get("gifter") or {}
        anonimo = isinstance(s, dict) and bool(s.get("is_anonymous"))
        nombre = "alguien" if anonimo else _kick_nombre(s, "alguien")
        regalo = d.get("gift") or {}
        try: monto = max(1, int(regalo.get("amount") or 1))
        except (TypeError, ValueError): monto = 1
        _kick_regalo(formatear_regalo(str(regalo.get("name") or "KICKs"), 1), nombre, 1, monto)

def kick_procesar_webhook(cab, cuerpo):
    """Valida un webhook de Kick y lo procesa. Devuelve (código_http, texto)."""
    firma_b64 = cab.get("Kick-Event-Signature")
    msg_id    = cab.get("Kick-Event-Message-Id")
    ts        = cab.get("Kick-Event-Message-Timestamp")
    tipo      = cab.get("Kick-Event-Type")
    if not (firma_b64 and msg_id and ts and tipo): return 400, "faltan cabeceras"

    if _kick["clave"] is None:
        try: _kick_cargar_clave(_kick["token"])
        except Exception as e:
            print(f"[KICK] No se pudo cargar la clave pública para verificar: {e}")
            return 503, "clave no disponible"
    try:
        firma = base64.b64decode(firma_b64, validate=True)
        firmado = f"{msg_id}.{ts}.".encode("utf-8") + cuerpo
        valido = _rsa_verificar(_kick["clave"], firmado, firma)
    except Exception:
        valido = False
    if not valido:
        print("[KICK] Webhook rechazado: firma inválida.")
        return 400, "firma inválida"

    try: edad = abs(time.time() - _rfc3339_a_epoch(ts))
    except ValueError: return 400, "timestamp inválido"
    if edad > KICK_TOLERANCIA_SEG:
        print(f"[KICK] Webhook rechazado por antigüedad ({int(edad)}s). Si pasa siempre, revisa la hora de tu PC.")
        return 400, "antiguo"

    try: datos = json.loads(cuerpo.decode("utf-8"))
    except Exception: return 400, "json inválido"
    if not isinstance(datos, dict): return 400, "json inválido"

    # La firma es la misma para todas las apps de Kick: solo se aceptan eventos de NUESTRO canal.
    bro = datos.get("broadcaster")
    bro_id = bro.get("user_id") if isinstance(bro, dict) else None
    if _kick["broadcaster_id"] is None or str(bro_id) != str(_kick["broadcaster_id"]):
        print(f"[KICK] Webhook ignorado: no es de este canal (broadcaster {bro_id}).")
        return 200, "otro canal"

    with _lock_kick:
        if msg_id in _kick_ids_vistos: return 200, "duplicado"
        _kick_ids_vistos[msg_id] = True
        while len(_kick_ids_vistos) > 2000: _kick_ids_vistos.popitem(last=False)
        try: _kick_evento(tipo, datos)
        except Exception as e: print(f"[KICK] Error procesando {tipo}: {type(e).__name__}: {e}")
    return 200, "ok"

class KickH(BaseHTTPRequestHandler):
    timeout = 15
    def _resp(self, codigo, texto="ok"):
        cuerpo = texto.encode("utf-8")
        self.send_response(codigo)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Content-Length", str(len(cuerpo)))
        self.end_headers()
        self.wfile.write(cuerpo)
    def do_GET(self): self._resp(200, "puente kick activo")
    def do_POST(self):
        try: largo = int(self.headers.get("Content-Length", "0"))
        except ValueError: largo = 0
        if largo <= 0: return self._resp(400, "sin cuerpo")
        if largo > 262144: return self._resp(413, "demasiado grande")
        codigo, texto = kick_procesar_webhook(self.headers, self.rfile.read(largo))
        self._resp(codigo, texto)
    def log_message(self, format, *args): return

def iniciar_http_kick():
    puerto = _kick_puerto()
    try:
        srv = ThreadingHTTPServer(("127.0.0.1", puerto), KickH)
    except OSError as e:
        print(f"[KICK] No se pudo abrir el puerto {puerto}: {e}")
        return
    srv.daemon_threads = True
    print(f"[KICK] Receptor de webhooks en http://127.0.0.1:{puerto}  (apunta tu túnel a este puerto)")
    srv.serve_forever()

async def monitor_kick():
    fallos = 0
    while True:
        try:
            token = await asyncio.to_thread(_kick_obtener_token)
            if _kick["broadcaster_id"] is None:
                _kick["broadcaster_id"] = await asyncio.to_thread(_kick_resolver_canal, token)
                print(f"[KICK] Canal '{KICK_CFG['canal']}' encontrado (id {_kick['broadcaster_id']}).")
            if _kick["clave"] is None:
                await asyncio.to_thread(_kick_cargar_clave, token)
            errores = await asyncio.to_thread(_kick_asegurar_suscripciones, token, _kick["broadcaster_id"])
            for e in errores: print(f"[KICK] Suscripción con error -> {e}")
            if not errores:
                print(f"[KICK] Suscripciones activas. Recuerda: Webhook URL en kick.com/settings/developer = "
                      f"URL pública de tu túnel (que apunte al puerto {_kick_puerto()}).")
            fallos = 0
            espera = KICK_REVISION_SEG
        except asyncio.CancelledError: raise
        except Exception as e:
            fallos += 1
            espera = min(30 * (2 ** (fallos - 1)), 300)
            print(f"[KICK] Error: {e}")
            print(f"[KICK] Ajustes de tu app en kick.com/settings/developer:\n"
                  f"[KICK]   - Redirigir URL        : http://127.0.0.1:{_kick_puerto()}\n"
                  f"[KICK]   - Habilitar webhooks   : ACTIVADO, con la URL pública del túnel (https://....trycloudflare.com)\n"
                  f"[KICK]   - Scope obligatorio    : \"Suscríbete a los eventos\"\n"
                  f"[KICK]   - Client ID / Secret   : los de esa misma app (botón Live del Dashboard)")
            print(f"[KICK] Reintentando en {espera}s...")
        await asyncio.sleep(espera)

def tiktok_configurado():
    """True si la plataforma incluye TikTok y hay un usuario real (no el de relleno)."""
    if PLATAFORMA not in ("tiktok", "ambas"): return False
    return any(not re.fullmatch(r"x+", (u or "x"), re.IGNORECASE) for u in TIKTOK_USERS)

async def iniciar_todos():
    tarea_monitor_tiktok = None
    tarea_twitch = None
    twitch_listo = TWITCH_DISPONIBLE and all([
        TWITCH_CFG.get("client_id"), TWITCH_CFG.get("client_secret"),
        TWITCH_CFG.get("token"), TWITCH_CFG.get("canal")
    ])

    if twitch_listo:
        print(f"[TWITCH] Iniciando conexión asíncrona al canal {TWITCH_CFG['canal']}...")
        bot = TwitchBot()
        tarea_twitch = asyncio.create_task(bot.start())
    elif not TWITCH_DISPONIBLE:
        print("[AVISO] Twitch no está instalado. Ejecuta: pip install twitchio")
    else:
        print("[AVISO] Twitch no configurado. Revisa twitch_config.txt.")

    tarea_kick = None
    if kick_activo():
        print(f"[KICK] Iniciando conexión al canal {KICK_CFG['canal']}...")
        tarea_kick = asyncio.create_task(monitor_kick())
    elif PLATAFORMA in ("kick", "ambas"):
        print("[AVISO] Kick no configurado. Revisa kick_config.txt (client_id, client_secret y canal).")
    else:
        print("[MODO] Plataforma TikTok: Kick desactivado.")

    if PLATAFORMA == "kick":
        print("[MODO] Plataforma Kick: TikTok desactivado.")
    elif not tiktok_configurado():
        print("[AVISO] Usuario de TikTok sin configurar: se omite la conexión a TikTok.")

    while True:
        try:
            if _tikfinity_activo():
                if tarea_monitor_tiktok is not None and not tarea_monitor_tiktok.done():
                    print("[MODO] TikFinity activo - deteniendo conexión directa a TikTok.")
                    tarea_monitor_tiktok.cancel()
                    try: await tarea_monitor_tiktok
                    except Exception: pass
                    tarea_monitor_tiktok = None
            else:
                if tiktok_configurado() and (tarea_monitor_tiktok is None or tarea_monitor_tiktok.done()):
                    print(f"[MODO] Sin TikFinity - conectando directo a TikTok Live...")
                    usuarios_tareas = [asyncio.create_task(monitor_usuario(u)) for u in TIKTOK_USERS]
                    tarea_monitor_tiktok = asyncio.gather(*usuarios_tareas, return_exceptions=True)
            await asyncio.sleep(10)
        except asyncio.CancelledError: break
        except Exception as e:
            print(f"[ERROR bucle] {type(e).__name__}: {e}")
            await asyncio.sleep(10)

def manejar_salida(signum, frame):
    print("\n[PUENTE] Cerrando por señal...")
    _flush_taps_final()
    if LIMPIAR_AL_CERRAR: limpiar_chat()
    sys.exit(0)

if __name__ == "__main__":
    if not os.path.exists(CHAT_FILE): open(CHAT_FILE, "w", encoding="utf-8").close()

    try:
        signal.signal(signal.SIGINT, manejar_salida)
        signal.signal(signal.SIGTERM, manejar_salida)
        if hasattr(signal, "SIGBREAK"): signal.signal(signal.SIGBREAK, manejar_salida)
    except Exception: pass

    print("=" * 60)
    if PLATAFORMA != "kick":
        print(f"Usuarios TikTok : {', '.join('@'+u for u in TIKTOK_USERS)}")
    if TWITCH_DISPONIBLE and TWITCH_CFG.get("canal"):
        print(f"Canal Twitch    : {TWITCH_CFG['canal']}")
    else: print(f"Canal Twitch    : NO CONFIGURADO")
    print(f"Plataforma      : {PLATAFORMA.upper()}")
    if kick_activo(): print(f"Canal Kick      : {KICK_CFG['canal']}  (webhooks en puerto {_kick_puerto()})")
    elif PLATAFORMA in ("kick", "ambas"): print(f"Canal Kick      : NO CONFIGURADO")
    print(f"Archivo salida  : {CHAT_FILE}")
    print(f"Puerto HTTP     : {HTTP_PORT}")
    print(f"Modo            : HÍBRIDO MULTI-PLATAFORMA")
    print("=" * 60)

    t = threading.Thread(target=iniciar_http, daemon=True)
    t.start()
    threading.Thread(target=_flush_taps_loop, daemon=True).start()
    if kick_activo():
        threading.Thread(target=iniciar_http_kick, daemon=True).start()

    try: asyncio.run(iniciar_todos())
    except KeyboardInterrupt: print("\n[PUENTE] Detenido por el usuario.")
    finally:
        _flush_taps_final()
        if LIMPIAR_AL_CERRAR: limpiar_chat()
        print("[PUENTE] Cerrado.")