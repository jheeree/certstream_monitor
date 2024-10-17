import asyncio
import websockets
import json
import sqlite3
import configparser
import requests
import re
import time
import logging
import signal
import sys
from datetime import datetime, timedelta
from colorama import Fore, Style, init

init(autoreset=True)

LOG_FORMAT = '%(asctime)s [%(levelname)s] - %(message)s'
logging.basicConfig(filename='stream_data.log', level=logging.INFO, format=LOG_FORMAT)
logger = logging.getLogger()

loop = None

warned_telegram = False
warned_discord = False
warned_teams = False
warned_slack = False

def log_message(level, message, exception=None):
    timestamp = f"{Fore.CYAN}{datetime.now().strftime('[%Y-%m-%d %H:%M:%S]')}{Style.RESET_ALL}"
    level_color = {
        'INFO': Fore.GREEN,
        'WARN': Fore.YELLOW,
        'ERROR': Fore.RED
    }.get(level, Fore.WHITE)
    
    colored_message = f"{timestamp} {level_color}[{level}]{Style.RESET_ALL} {Fore.WHITE}- {message}{Style.RESET_ALL}"
    
    logger.log(logging.INFO if level == 'INFO' else logging.WARNING if level == 'WARN' else logging.ERROR, message)
    
    print(colored_message)

def show_banner():
    banner = f"""
{Fore.GREEN} ██████╗███████╗██████╗ ████████╗███████╗████████╗██████╗ ███████╗ █████╗ ███╗   ███╗    ███╗   ███╗ ██████╗ ███╗   ██╗██╗████████╗ ██████╗ ██████╗ {Style.RESET_ALL}
{Fore.GREEN}██╔════╝██╔════╝██╔══██╗╚══██╔══╝██╔════╝╚══██╔══╝██╔══██╗██╔════╝██╔══██╗████╗ ████║    ████╗ ████║██╔═══██╗████╗  ██║██║╚══██╔══╝██╔═══██╗██╔══██╗{Style.RESET_ALL}
{Fore.GREEN}██║     █████╗  ██████╔╝   ██║   ███████╗   ██║   ██████╔╝█████╗  ███████║██╔████╔██║    ██╔████╔██║██║   ██║██╔██╗ ██║██║   ██║   ██║   ██║██████╔╝{Style.RESET_ALL}
{Fore.GREEN}██║     ██╔══╝  ██╔══██╗   ██║   ╚════██║   ██║   ██╔══██╗██╔══╝  ██╔══██║██║╚██╔╝██║    ██║╚██╔╝██║██║   ██║██║╚██╗██║██║   ██║   ██║   ██║██╔══██╗{Style.RESET_ALL}
{Fore.GREEN}╚██████╗███████╗██║  ██║   ██║   ███████║   ██║   ██║  ██║███████╗██║  ██║██║ ╚═╝ ██║    ██║ ╚═╝ ██║╚██████╔╝██║ ╚████║██║   ██║   ╚██████╔╝██║  ██║{Style.RESET_ALL}
{Fore.GREEN} ╚═════╝╚══════╝╚═╝  ╚═╝   ╚═╝   ╚══════╝   ╚═╝   ╚═╝  ╚═╝╚══════╝╚═╝  ╚═╝╚═╝     ╚═╝    ╚═╝     ╚═╝ ╚═════╝ ╚═╝  ╚═══╝╚═╝   ╚═╝    ╚═════╝ ╚═╝  ╚═╝{Style.RESET_ALL}
                                                                                                                                                    
"""
    print(banner)

# Función para crear la base de datos si no existe
def create_db():
    conn = sqlite3.connect('stream_data.sqlite')
    cursor = conn.cursor()
    cursor.execute('''CREATE TABLE IF NOT EXISTS certificates (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        fecha_deteccion TEXT,
                        dominio TEXT
                    )''')
    conn.commit()
    conn.close()

# Función para verificar si el dominio ya existe en la base de datos y obtener su última fecha de detección
def check_domain_in_db(dominio):
    conn = sqlite3.connect('stream_data.sqlite')
    cursor = conn.cursor()
    cursor.execute("SELECT fecha_deteccion FROM certificates WHERE dominio = ?", (dominio,))
    result = cursor.fetchone()
    conn.close()
    
    if result:
        last_detection_time = datetime.strptime(result[0], '%Y-%m-%d %H:%M:%S')
        return last_detection_time
    return None

# Función para insertar un nuevo dominio en la base de datos
def insert_into_db(dominio):
    conn = sqlite3.connect('stream_data.sqlite')
    cursor = conn.cursor()
    fecha_deteccion = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    cursor.execute("INSERT INTO certificates (fecha_deteccion, dominio) VALUES (?, ?)", (fecha_deteccion, dominio))
    conn.commit()
    conn.close()
    
    log_message('INFO', f"{dominio} agregado a base de datos.")
    send_notifications(dominio, nuevo=True)

# Función para actualizar la fecha de detección del dominio existente en la base de datos
def update_detection_time(dominio):
    conn = sqlite3.connect('stream_data.sqlite')
    cursor = conn.cursor()
    nueva_fecha = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    cursor.execute("UPDATE certificates SET fecha_deteccion = ? WHERE dominio = ?", (nueva_fecha, dominio))
    conn.commit()
    conn.close()
    
    log_message('INFO', f"Fecha de detección de {dominio} actualizada en la base de datos.")
    send_notifications(dominio, nuevo=False)

# Función para verificar si un dominio está permitido basado en los filtros del config.ini
def dominio_permitido(dominio):
    config = read_config()
    filtros = config['app'].get('filtros', '').split(',')
    
    patrones = []
    
    for filtro in filtros:
        if '.' in filtro:
            patron = re.compile(rf'{re.escape(filtro)}$', re.IGNORECASE)
        else:
            patron = re.compile(rf'{re.escape(filtro)}', re.IGNORECASE)
        patrones.append(patron)
    
    return any(patron.search(dominio) for patron in patrones)

# Función para enviar notificaciones a Telegram, Discord, Teams y Slack
def send_notifications(dominio, nuevo=True):
    global warned_telegram, warned_discord, warned_teams, warned_slack
    config = read_config()
    action = "Nueva detección para" if nuevo else "Se ha renovado el certificado para"
    message = f'#CertStream\n• {action}:\nhttps://{dominio}'

    # Notificación a Telegram
    api_key = config['telegram'].get('api_key')
    chat_id = config['telegram'].get('chat_id')
    if api_key and chat_id:
        send_telegram_notification(api_key, chat_id, message)
    elif not warned_telegram:
        log_message('WARN', "Webhook de Telegram no detectado")
        warned_telegram = True

    # Notificación a Discord
    discord_webhook = config['discord'].get('webhook_url')
    if discord_webhook:
        send_discord_notification(discord_webhook, message)
    elif not warned_discord:
        log_message('WARN', "Webhook de Discord no detectado")
        warned_discord = True

    # Notificación a Teams
    teams_webhook = config['teams'].get('webhook_url')
    if teams_webhook and teams_webhook.startswith("https://"):
        send_teams_notification(teams_webhook, message)
    elif not warned_teams:
        log_message('WARN', "Webhook de Teams no detectado o inválido")
        warned_teams = True
        
    # Notificación a Slack
    slack_webhook = config['slack'].get('webhook_url')
    if slack_webhook and slack_webhook.startswith("https://"):
        send_slack_notification(slack_webhook, message)
    elif not warned_slack:
        log_message('WARN', "Webhook de Slack no detectado o inválido")
        warned_slack = True

# Función para enviar notificación a Telegram
def send_telegram_notification(api_key, chat_id, message):
    url = f"https://api.telegram.org/bot{api_key}/sendMessage"
    payload = {
        'chat_id': chat_id,
        'text': message
    }
    
    try:
        response = requests.post(url, json=payload)
        if response.status_code == 200:
            log_message('INFO', "Mensaje enviado a Telegram.")
        else:
            log_message('WARN', f"Error al enviar mensaje a Telegram: {response.text}")
    except Exception as e:
        log_message('ERROR', f"Excepción al enviar mensaje a Telegram: {e}")

# Función para enviar notificación a Discord
def send_discord_notification(webhook_url, message):
    payload = {
        'content': message
    }
    
    try:
        response = requests.post(webhook_url, json=payload)
        if response.status_code == 204:
            log_message('INFO', "Mensaje enviado a Discord.")
        else:
            log_message('WARN', f"Error al enviar mensaje a Discord: {response.text}")
    except Exception as e:
        log_message('ERROR', f"Excepción al enviar mensaje a Discord: {e}")

# Función para enviar notificación a Microsoft Teams
def send_teams_notification(webhook_url, message):
    payload = {
        '@type': 'MessageCard',
        '@context': 'http://schema.org/extensions',
        'summary': 'Certificado detectado',
        'themeColor': '0076D7',
        'sections': [{
            'activityTitle': 'Nueva detección de certificado',
            'text': message
        }]
    }
    
    try:
        response = requests.post(webhook_url, json=payload)
        if response.status_code == 200:
            log_message('INFO', "Mensaje enviado a Teams.")
        else:
            log_message('WARN', f"Error al enviar mensaje a Teams: {response.text}")
    except Exception as e:
        log_message('ERROR', f"Excepción al enviar mensaje a Teams: {e}")

# Función para enviar notificación a Slack
def send_slack_notification(webhook_url, message):
    payload = {
        'text': message
    }
    
    try:
        response = requests.post(webhook_url, json=payload)
        if response.status_code == 200:
            log_message('INFO', "Mensaje enviado a Slack.")
        else:
            log_message('WARN', f"Error al enviar mensaje a Slack: {response.text}")
    except Exception as e:
        log_message('ERROR', f"Excepción al enviar mensaje a Slack: {e}")

def read_config():
    config = configparser.ConfigParser()
    config.read('config.ini')
    return config

# Función principal para el manejo de CertStream
async def listen_to_certstream():
    while True:
        try:
            async with websockets.connect("wss://certstream.calidog.io/") as websocket:
                log_message('INFO', "Conectado a CertStream.")
                while True:
                    message = await websocket.recv()
                    data = json.loads(message)
                    all_domains = data['data']['leaf_cert']['all_domains']

                    for dominio in all_domains:
                        if dominio_permitido(dominio):
                            last_detection = check_domain_in_db(dominio)
                            if last_detection:
                                time_difference = datetime.now() - last_detection
                                if time_difference > timedelta(hours=24):
                                    update_detection_time(dominio)
                            else:
                                insert_into_db(dominio)
        except Exception as e:
            log_message('ERROR', f"Excepción en CertStream: {e}. Reintentando conexión en 5 segundos...")
            await asyncio.sleep(5)

def signal_handler(signal, frame):
    log_message('WARN', 'Detención manual. Cerrando el programa')
    if loop is not None:
        loop.stop()
    sys.exit(0)

if __name__ == "__main__":
    show_banner()
    create_db()
    signal.signal(signal.SIGINT, signal_handler)

    try:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        loop.run_until_complete(listen_to_certstream())
    except KeyboardInterrupt:
        log_message('WARN', 'Detención manual. Cerrando el programa')
    finally:
        if loop is not None and loop.is_running():
            loop.stop()
