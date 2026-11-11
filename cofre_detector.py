"""
Detector de cofres de Fortnite por sonido.

- Captura el audio del sistema (WASAPI loopback) y compara con cofre.wav.
- Cada cofre detectado escribe  __COFRE__|+1  en chat.txt (lo lee el Dashboard).
- Solo cuenta el sonido si pulsaste la tecla de interactuar (E) en los últimos 3 s, así no suma
  cuando espectas a un compañero ni cuando otro abre un cofre cerca de ti.
- Atajos globales (funcionan con Fortnite en primer plano):
      F9  = +1      F10 = -1      Ctrl+F10 = reiniciar a 0

Requisitos (Windows):   pip install numpy pyaudiowpatch
Uso:
    python cofre_detector.py              # normal
    python cofre_detector.py --monitor    # muestra la puntuación en vivo (para calibrar)
    python cofre_detector.py --umbral 0.45 --rel-db 20
    python cofre_detector.py --tecla F          # otra tecla de interactuar
    python cofre_detector.py --tecla none      # sin condición de tecla (cuenta todo sonido de cofre)
"""
import argparse
import os
import sys
import threading
import time

import numpy as np

import cofre_core as core

AQUI = os.path.dirname(os.path.abspath(__file__))
CHAT_FILE = "chat.txt"
_lock = threading.Lock()
_ultima_tecla = 0.0          # momento en que se pulsó por última vez la tecla de interactuar


def escribir(linea, lock=None):
    try:
        with (lock or _lock):
            with open(CHAT_FILE, "a", encoding="utf-8") as f:
                f.write(linea + "\n")
    except Exception as e:
        print(f"[COFRE] No se pudo escribir en {CHAT_FILE}: {e}")


def vk_de(tecla):
    """'E' o 'E,CLIC' -> lista de teclas virtuales de Windows. None = sin condición de tecla.
    CLIC = botón izquierdo del ratón (romper una caja con el pico no pulsa E)."""
    t = (tecla or "").strip().upper()
    if t in ("", "NONE", "NINGUNA", "NO"):
        return None
    vks = []
    for parte in t.split(","):
        parte = parte.strip()
        if parte in ("CLIC", "CLICK", "MOUSE", "RATON"):
            vks.append(0x01)
        elif len(parte) == 1 and parte.isalnum():
            vks.append(ord(parte))
        else:
            print(f"[COFRE] Tecla '{parte}' no reconocida (usa letras/números, CLIC o 'none'). Se ignora.")
    return vks or None


# ---------------- atajos globales sin dependencias (solo Windows) ----------------
def hilo_atajos(lock=None, vk_tecla=None):
    global _ultima_tecla
    if os.name != "nt":
        print("[COFRE] Atajos globales solo disponibles en Windows.")
        return
    import ctypes
    gk = ctypes.windll.user32.GetAsyncKeyState
    VK_F9, VK_F10, VK_CTRL = 0x78, 0x79, 0x11
    antes = {VK_F9: False, VK_F10: False}
    while True:
        if vk_tecla and any(gk(v) & 0x8000 for v in vk_tecla):
            _ultima_tecla = time.time()
        for vk in (VK_F9, VK_F10):
            abajo = bool(gk(vk) & 0x8000)
            if abajo and not antes[vk]:
                if vk == VK_F9:
                    escribir("__COFRE__|+1", lock)
                    print("[COFRE] +1 (F9)")
                elif gk(VK_CTRL) & 0x8000:
                    escribir("__COFRE__|RESET", lock)
                    print("[COFRE] reinicio (Ctrl+F10)")
                else:
                    escribir("__COFRE__|-1", lock)
                    print("[COFRE] -1 (F10)")
            antes[vk] = abajo
        time.sleep(0.03)


# ---------------- captura del audio del sistema ----------------
def abrir_loopback():
    try:
        import pyaudiowpatch as pa
    except ImportError:
        print("[COFRE] Falta pyaudiowpatch.  Instala con:  pip install pyaudiowpatch")
        return None, None, None
    p = pa.PyAudio()
    try:
        wasapi = p.get_host_api_info_by_type(pa.paWASAPI)
        salida = p.get_device_info_by_index(wasapi["defaultOutputDevice"])
        if not salida.get("isLoopbackDevice"):
            for lb in p.get_loopback_device_info_generator():
                if salida["name"] in lb["name"]:
                    salida = lb
                    break
        return p, pa, salida
    except Exception as e:
        print(f"[COFRE] No se pudo localizar el dispositivo loopback: {e}")
        return None, None, None


def bucle_audio(args, lock=None):
    huella, nivel_db = core.cargar_huella(args.wav)
    det = core.Detector(huella, nivel_db, umbral=args.umbral,
                        refractario=args.refractario, rel_db=args.rel_db)
    p, pa, dev = abrir_loopback()
    if dev is None:
        return
    vk = vk_de(args.tecla) if os.name == "nt" else None
    if vk:
        print(f"[COFRE] Solo cuenta si pulsas '{args.tecla.upper()}' en los {args.ventana:g} s previos al sonido.")
    sr = int(dev["defaultSampleRate"])
    canales = int(dev["maxInputChannels"])
    print(f"[COFRE] Escuchando: {dev['name']}  ({sr} Hz, {canales} canales)")
    stream = p.open(format=pa.paInt16, channels=canales, rate=sr, input=True,
                    input_device_index=dev["index"], frames_per_buffer=2048)
    ultimo_print = 0.0
    try:
        while True:
            datos = stream.read(2048, exception_on_overflow=False)
            x = np.frombuffer(datos, dtype=np.int16).astype(np.float32) / 32768.0
            x = x.reshape(-1, canales).mean(axis=1)
            x = core.remuestrear(x, sr)
            for _ in det.alimentar(x):
                if vk and time.time() - _ultima_tecla > args.ventana:
                    print(f"[COFRE] Sonido de cofre ignorado (no pulsaste {args.tecla.upper()}).")
                    continue
                escribir("__COFRE__|+1", lock)
                print("[COFRE] ¡Cofre detectado! +1")
            if args.monitor and time.time() - ultimo_print > 0.25:
                ultimo_print = time.time()
                barra = "#" * int(max(0.0, det.ultima_puntuacion) * 40)
                print(f"\r puntuación {det.ultima_puntuacion:5.2f} (umbral {args.umbral:.2f}) {barra:<40}",
                      end="", flush=True)
    except KeyboardInterrupt:
        pass
    finally:
        stream.stop_stream()
        stream.close()
        p.terminate()


def iniciar_en_hilo(chat_file=None, lock=None, wav=None, **kw):
    """Para lanzarlo desde puente.py:  cofre_detector.iniciar_en_hilo(CHAT_FILE, _lock)"""
    global CHAT_FILE
    if chat_file:
        CHAT_FILE = chat_file
    args = argparse.Namespace(wav=wav or os.path.join(AQUI, "cofre.wav"),
                              umbral=kw.get("umbral", 0.50), refractario=kw.get("refractario", 3.0),
                              rel_db=kw.get("rel_db", 15.0), monitor=False,
                              tecla=kw.get("tecla", "E"), ventana=kw.get("ventana", 3.0))
    threading.Thread(target=hilo_atajos, args=(lock, vk_de(args.tecla)), daemon=True).start()
    threading.Thread(target=bucle_audio, args=(args, lock), daemon=True).start()


def main():
    global CHAT_FILE
    ap = argparse.ArgumentParser()
    ap.add_argument("--wav", default=os.path.join(AQUI, "cofre.wav"))
    ap.add_argument("--umbral", type=float, default=0.50, help="0-1; más bajo = más sensible")
    ap.add_argument("--refractario", type=float, default=3.0, help="segundos mínimos entre cofres")
    ap.add_argument("--rel-db", type=float, default=15.0,
                    help="ignora sonidos más de N dB por debajo de tu cofre (cofres lejanos)")
    ap.add_argument("--tecla", default="E",
                    help="tecla de interactuar; solo cuenta si la pulsaste poco antes ('none' = sin condición)")
    ap.add_argument("--ventana", type=float, default=3.0,
                    help="segundos máximos entre pulsar la tecla y oír el cofre")
    ap.add_argument("--chat", default=CHAT_FILE, help="ruta de chat.txt donde escribir los cofres")
    ap.add_argument("--monitor", action="store_true", help="muestra la puntuación en vivo")
    args = ap.parse_args()
    CHAT_FILE = args.chat
    if not os.path.exists(CHAT_FILE):
        open(CHAT_FILE, "w", encoding="utf-8").close()
    threading.Thread(target=hilo_atajos, args=(None, vk_de(args.tecla)), daemon=True).start()
    bucle_audio(args)


if __name__ == "__main__":
    main()
