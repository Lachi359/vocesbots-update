import asyncio
import sys
import os
import datetime

sys.stdout.reconfigure(encoding='utf-8')
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))

try:
    from winrt.windows.media.control import GlobalSystemMediaTransportControlsSessionManager
    from winrt.windows.storage.streams import DataReader
except Exception as e:
    print(f"Falta el paquete winrt|{str(e).replace('|', '-')}||0|0", flush=True)
    sys.exit(1)

last_key = None
last_image = ""
manager = None

def clean(text):
    if not text:
        return ""
    return text.replace("|", "-").replace("\n", " ").replace("\r", " ").strip()

def ms_from_timedelta(td):
    if td is None:
        return 0
    try:
        return int(td.total_seconds() * 1000)
    except Exception:
        return 0

def now_utc():
    return datetime.datetime.now(datetime.timezone.utc)

async def get_media_info():
    global last_key, last_image, manager

    if manager is None:
        manager = await GlobalSystemMediaTransportControlsSessionManager.request_async()
    
    # 1. Intentar obtener la sesión actual
    current = manager.get_current_session()
    
    # 2. Si no hay actual, buscar entre todas las sesiones abiertas
    if current is None:
        sessions = manager.get_sessions()
        for session in sessions:
            try:
                pb = session.get_playback_info()
                # 4 = PLAYING (Reproduciendo)
                if pb and pb.playback_status == 4:
                    current = session
                    break
            except Exception:
                pass

    if current:
        try:
            info = await current.try_get_media_properties_async()
            if not info or (not info.title and not info.artist):
                raise Exception("Metadatos vacíos")
                
            title = clean(info.title) or clean(info.album_title) or "Sin título"
            artist = clean(info.artist) or current.source_app_user_model_id or "Desconocido"
            key = (title, artist)

            image_path = last_image if key == last_key else ""

            # Intentar obtener la portada
            if key != last_key and info.thumbnail:
                try:
                    stream = await info.thumbnail.open_read_async()
                    reader = DataReader(stream)
                    await reader.load_async(stream.size)
                    buffer = bytearray(stream.size)
                    reader.read_bytes(buffer)

                    image_path = os.path.join(SCRIPT_DIR, "portada.jpg")
                    with open(image_path, "wb") as f:
                        f.write(buffer)

                    last_key = key
                    last_image = image_path
                except Exception:
                    image_path = ""
                    last_key = key
                    last_image = ""

            position_ms = 0
            duration_ms = 0
            try:
                timeline = current.get_timeline_properties()
                if timeline is not None:
                    position_ms = ms_from_timedelta(timeline.position)
                    duration_ms = ms_from_timedelta(timeline.end_time)

                    is_playing = False
                    try:
                        pb = current.get_playback_info()
                        is_playing = (pb.playback_status == 4)
                    except Exception:
                        pass

                    if is_playing and timeline.last_updated_time is not None:
                        try:
                            last = timeline.last_updated_time
                            if last.tzinfo is None:
                                last = last.replace(tzinfo=datetime.timezone.utc)
                            elapsed_ms = (now_utc() - last).total_seconds() * 1000.0
                            if elapsed_ms > 0:
                                position_ms += int(elapsed_ms)
                        except Exception:
                            pass

                    if position_ms < 0: position_ms = 0
                    if duration_ms > 0 and position_ms > duration_ms: position_ms = duration_ms
            except Exception:
                pass

            print(f"{title}|{artist}|{image_path}|{position_ms}|{duration_ms}", flush=True)
            return
            
        except Exception:
            pass

    # Si no hay ninguna sesión activa (o falló)
    last_key = None
    last_image = ""
    print("Esperando música...|Sin reproducción||0|0", flush=True)


async def main_loop():
    while True:
        try:
            await get_media_info()
        except Exception as e:
            print(f"Error de lectura|{str(e).replace('|', '-')}||0|0", flush=True)
        await asyncio.sleep(1)


if __name__ == "__main__":
    try:
        asyncio.run(main_loop())
    except Exception as e:
        print(f"Fallo fatal|{str(e).replace('|', '-')}||0|0", flush=True)