"""Núcleo de detección del sonido de cofre (solo numpy). Sin dependencias de audio."""
import wave
import numpy as np

SR = 22050            # frecuencia interna de análisis
FRAME = 512
HOP = 256
NBANDS = 24
FMIN, FMAX = 150.0, 10000.0
TEMPLATE_SEG = 1.0    # segundos de la muestra usados como huella (desde el primer golpe)


def a_mono(x):
    x = np.asarray(x, dtype=np.float32)
    return x.mean(axis=1) if x.ndim == 2 else x


def remuestrear(x, sr_in, sr_out=SR):
    """Remuestreo simple (promedio + interpolación). Suficiente para una huella espectral."""
    x = np.asarray(x, dtype=np.float32)
    if sr_in == sr_out:
        return x
    k = max(1, int(round(sr_in / sr_out)))
    if k > 1:                                   # antialias barato
        n = len(x) // k * k
        x = x[:n].reshape(-1, k).mean(axis=1)
        sr_in = sr_in / k
    if sr_in == sr_out:
        return x
    t_out = np.arange(int(len(x) * sr_out / sr_in)) * (sr_in / sr_out)
    return np.interp(t_out, np.arange(len(x)), x).astype(np.float32)


_WIN = np.hanning(FRAME).astype(np.float32)
_freqs = np.fft.rfftfreq(FRAME, 1.0 / SR)
_edges = np.geomspace(FMIN, FMAX, NBANDS + 1)
_BANDIDX = [np.where((_freqs >= _edges[i]) & (_freqs < _edges[i + 1]))[0] for i in range(NBANDS)]


def bandas_log(x):
    """x mono a SR -> matriz (frames, NBANDS) de energía en dB."""
    n = (len(x) - FRAME) // HOP + 1
    if n <= 0:
        return np.zeros((0, NBANDS), np.float32)
    idx = np.arange(FRAME)[None, :] + HOP * np.arange(n)[:, None]
    esp = np.abs(np.fft.rfft(x[idx] * _WIN, axis=1)) ** 2
    out = np.empty((n, NBANDS), np.float32)
    for b, ix in enumerate(_BANDIDX):
        out[:, b] = esp[:, ix].mean(axis=1) if len(ix) else 0
    return 10.0 * np.log10(out + 1e-9)


def _norm(m):
    m = m - m.mean(axis=0, keepdims=True)       # quita la ganancia por banda
    n = np.linalg.norm(m)
    return m / n if n > 1e-9 else m


def cargar_huella(ruta_wav, seg=TEMPLATE_SEG):
    w = wave.open(ruta_wav)
    sr = w.getframerate()
    ch = w.getnchannels()
    datos = np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16).astype(np.float32) / 32768.0
    mono = datos.reshape(-1, ch).mean(axis=1) if ch > 1 else datos
    mono = remuestrear(mono, sr)
    ini = int(np.argmax(np.abs(mono) > 0.02))   # salta el silencio inicial
    mono = mono[ini: ini + int(seg * SR)]
    raw = bandas_log(mono)
    return _norm(raw), float(raw.mean())


class Detector:
    """Alimenta con audio mono a SR; devuelve la puntuación de cada ventana y dispara eventos."""

    def __init__(self, huella, nivel_huella_db, umbral=0.50, refractario=3.0, rel_db=15.0):
        """rel_db: la ventana debe tener un nivel medio >= (nivel de la muestra - rel_db) dB.
        Sirve para ignorar cofres lejanos de otros jugadores (suenan mucho más bajo que el tuyo)."""
        self.h = huella
        self.nivel_min_db = nivel_huella_db - rel_db
        self.nf = huella.shape[0]
        self.umbral = umbral
        self.refractario = refractario
        self.buf = np.zeros(0, np.float32)
        self.feats = np.zeros((0, NBANDS), np.float32)
        self.t = 0.0                       # tiempo (s) del final de lo procesado
        self.ultimo = -1e9
        self.pico = 0.0
        self.pico_t = 0.0
        self.ultima_puntuacion = 0.0

    def alimentar(self, x):
        """x: float32 mono a SR. Devuelve lista de tiempos (s) en que se detectó un cofre."""
        eventos = []
        self.buf = np.concatenate([self.buf, x])
        n = (len(self.buf) - FRAME) // HOP + 1
        if n <= 0:
            return eventos
        usado = (n - 1) * HOP + FRAME
        nuevos = bandas_log(self.buf[:usado])
        self.buf = self.buf[n * HOP:]
        self.feats = np.vstack([self.feats, nuevos])[-(self.nf + 400):]
        for i in range(len(nuevos)):
            self.t += HOP / SR
            fin = len(self.feats) - len(nuevos) + i + 1
            if fin < self.nf:
                continue
            ventana = self.feats[fin - self.nf: fin]
            if ventana.mean() < self.nivel_min_db:  # demasiado bajo: cofre lejano o silencio
                continue
            s = float((_norm(ventana) * self.h).sum())
            self.ultima_puntuacion = s
            if s >= self.umbral and self.t - self.ultimo > self.refractario:
                if s > self.pico:
                    self.pico, self.pico_t = s, self.t
            elif self.pico > 0 and self.t - self.pico_t > 0.12:
                # la puntuación dejó de subir: este es el pico -> evento
                eventos.append(self.pico_t)
                self.ultimo = self.pico_t
                self.pico = 0.0
        return eventos
