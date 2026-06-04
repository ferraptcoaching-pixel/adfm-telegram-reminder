#!/usr/bin/env python3
import os
import sys
import json
import re
import requests
from datetime import datetime, time
import pytz
from dotenv import load_dotenv

# Carica configurazione da file .env
load_dotenv()

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")
CALENDAR_NAME_OR_ID = os.getenv("CALENDAR_ID", "Coaching")
CREDENTIALS_FILE = os.getenv("GOOGLE_CREDENTIALS_FILE", "credentials.json")
TOKEN_FILE = os.getenv("GOOGLE_TOKEN_FILE", "token.json")

# Scope di sola lettura per Google Calendar
SCOPES = ['https://www.googleapis.com/auth/calendar.readonly']

DAYS_IT = {
    "Monday": "lunedì", "Tuesday": "martedì", "Wednesday": "mercoledì",
    "Thursday": "giovedì", "Friday": "venerdì", "Saturday": "sabato", "Sunday": "domenica"
}

MONTHS_IT = {
    "January": "gennaio", "February": "febbraio", "March": "marzo", "April": "aprile",
    "May": "maggio", "June": "giugno", "July": "luglio", "August": "agosto",
    "September": "settembre", "October": "ottobre", "November": "novembre", "December": "dicembre"
}

def format_date_it(dt):
    """Formatta la data nel formato italiano 'cccc d MMMM' (es. martedì 2 giugno)."""
    day_name_en = dt.strftime('%A')
    day_num = dt.strftime('%-d')
    month_name_en = dt.strftime('%B')
    
    day_name_it = DAYS_IT.get(day_name_en, day_name_en.lower())
    month_name_it = MONTHS_IT.get(month_name_en, month_name_en.lower())
    
    return f"{day_name_it} {day_num} {month_name_it}"

def get_calendar_service():
    """Inizializza e restituisce il servizio Google Calendar supportando OAuth e Service Account."""
    from googleapiclient.discovery import build
    
    if not os.path.exists(CREDENTIALS_FILE):
        print(f"Errore: Il file delle credenziali '{CREDENTIALS_FILE}' non esiste.", file=sys.stderr)
        print("Crea le credenziali nella Google Cloud Console e posiziona il file credentials.json in questa cartella.", file=sys.stderr)
        sys.exit(1)
        
    with open(CREDENTIALS_FILE, 'r') as f:
        creds_data = json.load(f)
        
    if creds_data.get('type') == 'service_account':
        from google.oauth2 import service_account
        creds = service_account.Credentials.from_service_account_file(
            CREDENTIALS_FILE, scopes=SCOPES
        )
        return build('calendar', 'v3', credentials=creds)
    else:
        from google.oauth2.credentials import Credentials
        from google_auth_oauthlib.flow import InstalledAppFlow
        from google.auth.transport.requests import Request
        
        creds = None
        if os.path.exists(TOKEN_FILE):
            creds = Credentials.from_authorized_user_file(TOKEN_FILE, SCOPES)
            
        if not creds or not creds.valid:
            if creds and creds.expired and creds.refresh_token:
                creds.refresh(Request())
            else:
                flow = InstalledAppFlow.from_client_secrets_file(CREDENTIALS_FILE, SCOPES)
                creds = flow.run_local_server(port=0)
            with open(TOKEN_FILE, 'w') as token:
                token.write(creds.to_json())
                
        return build('calendar', 'v3', credentials=creds)

def get_calendar_id_by_name(service, name_or_id):
    """Cerca tra i calendari dell'utente quello che si chiama esattamente come il nome inserito (es. 'Coaching')."""
    if name_or_id == 'primary':
        return 'primary'
    try:
        calendar_list = service.calendarList().list().execute()
        for calendar_entry in calendar_list.get('items', []):
            if calendar_entry.get('summary') == name_or_id:
                print(f"Calendario trovato! '{name_or_id}' corrisponde all'ID: {calendar_entry.get('id')}")
                return calendar_entry.get('id')
    except Exception as e:
        print(f"Avviso durante la ricerca del calendario '{name_or_id}': {e}", file=sys.stderr)
    
    print(f"Calendario '{name_or_id}' non trovato nella lista. Uso '{name_or_id}' direttamente come ID.")
    return name_or_id

def is_valid_event_type(summary):
    """Filtra gli eventi includendo SOLO quelli richiesti dall'utente."""
    if not summary:
        return False
    
    summary_lower = summary.lower()
    valid_keywords = [
        'consulenza conoscitiva',
        'analisi chinesiologica',
        'consulenza con il dott. davide ferrara',
        'call di recup',
        'consulenza chinesiologica'
    ]
    
    for keyword in valid_keywords:
        if keyword in summary_lower:
            return True
            
    return False

def clean_client_name(summary):
    """Rimuove parentesi e prefissi delle tipologie di call per ricavare il nome pulito del cliente."""
    if not summary:
        return ""
    
    # Rimuove le parentesi tonde ed eventualmente quadre
    cleaned = summary.replace('(', '').replace(')', '').replace('[', '').replace(']', '')
    
    # Prefissi da rimuovere in modo case-insensitive
    replacements = [
        'Analisi chinesiologica ADFM ',
        'Consulenza conoscitiva ADFM ',
        'Consulenza con il dott. Davide Ferrara ',
        'Call di recup percorso ADFM ',
        'Consulenza chinesiologica ADFM ',
        'Analisi chinesiologica ',
        'Consulenza conoscitiva ',
        'Consulenza con il dott. Davide Ferrara',
        'Call di recup ',
        'Consulenza chinesiologica '
    ]
    
    for rep in replacements:
        cleaned = re.sub(re.escape(rep), '', cleaned, flags=re.IGNORECASE)
        
    return cleaned.strip()

def get_first_name(summary):
    """Estrae solo la prima parola del nome del cliente."""
    cleaned = clean_client_name(summary)
    if not cleaned:
        return "Cliente"
    return cleaned.split(' ')[0]

def get_past_calls_count(service, calendar_id, client_name):
    """Cerca lo storico degli eventi per ricavare il numero di call del cliente."""
    # Cerchiamo direttamente il nome del cliente (la ricerca a testo libero su GCal è potente)
    query = client_name
    try:
        events_result = service.events().list(
            calendarId=calendar_id,
            q=query,
            singleEvents=True,
            orderBy='startTime'
        ).execute()
        events = events_result.get('items', [])
        
        # Filtriamo per contare solo le call reali del cliente (escludendo compiti generici)
        valid_past_events = [e for e in events if is_valid_event_type(e.get('summary', ''))]
        
        return len(valid_past_events)
    except Exception as e:
        print(f"Avviso durante la ricerca dello storico per '{client_name}': {e}", file=sys.stderr)
        return 1

def send_telegram_message(text):
    """Invia un messaggio in formato HTML alla chat Telegram configurata."""
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        print("Errore: TELEGRAM_BOT_TOKEN o TELEGRAM_CHAT_ID non configurati in .env", file=sys.stderr)
        return False
        
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": text,
        "parse_mode": "HTML"
    }
    
    try:
        response = requests.post(url, json=payload)
        response_data = response.json()
        if not response_data.get("ok"):
            print(f"Errore Telegram: {response_data.get('description')}", file=sys.stderr)
            return False
        return True
    except Exception as e:
        print(f"Errore di connessione a Telegram: {e}", file=sys.stderr)
        return False

def main():
    if not TELEGRAM_BOT_TOKEN or TELEGRAM_BOT_TOKEN == "tuo_telegram_bot_token_qui":
        print("Per favore configura le variabili d'ambiente nel file .env prima di eseguire.", file=sys.stderr)
        sys.exit(1)

    print("Connessione a Google Calendar in corso...")
    service = get_calendar_service()
    
    # Trova l'ID del calendario Coaching
    calendar_id = get_calendar_id_by_name(service, CALENDAR_NAME_OR_ID)
    
    # Imposta la data odierna nel fuso orario di Roma
    rome_tz = pytz.timezone('Europe/Rome')
    now = datetime.now(rome_tz)
    
    # Definisce l'intervallo di tempo per oggi (da 00:00:00 a 23:59:59)
    start_of_day = datetime.combine(now.date(), time.min).astimezone(rome_tz)
    end_of_day = datetime.combine(now.date(), time.max).astimezone(rome_tz)
    
    time_min = start_of_day.isoformat()
    time_max = end_of_day.isoformat()
    
    print(f"Recupero eventi dal calendario '{CALENDAR_NAME_OR_ID}' per oggi: {now.date().strftime('%d/%m/%Y')}...")
    
    try:
        events_result = service.events().list(
            calendarId=calendar_id,
            timeMin=time_min,
            timeMax=time_max,
            singleEvents=True,
            orderBy='startTime'
        ).execute()
        events = events_result.get('items', [])
    except Exception as e:
        print(f"Errore durante il recupero degli eventi di oggi: {e}", file=sys.stderr)
        sys.exit(1)
        
    # Filtriamo gli eventi per considerare solo le call valide
    valid_events = [e for e in events if is_valid_event_type(e.get('summary', ''))]
        
    if not valid_events:
        print("Nessun evento o call in programma per oggi.")
        send_telegram_message("☕️ <b>ADFM Reminder:</b> Nessuna call in programma per oggi!")
        return

    print(f"Trovate {len(valid_events)} call valide per oggi su {len(events)} totali. Elaborazione...")
    
    for event in valid_events:
        summary = event.get('summary', '')
        client_name = clean_client_name(summary)
        if not client_name:
            continue
            
        # Conteggio storico delle call per il cliente
        conteggio = get_past_calls_count(service, calendar_id, client_name)
        
        # Dati del meeting corrente
        start_time_str = event.get('start', {}).get('dateTime')
        if not start_time_str:
            continue
            
        start_time = datetime.fromisoformat(start_time_str).astimezone(rome_tz)
        date_formatted = format_date_it(start_time)
        time_formatted = start_time.strftime('%H:%M')
        
        # Link del meet o location fisica
        link = event.get('hangoutLink') or event.get('location', 'Nessun link disponibile')
        
        first_name = get_first_name(summary)
        
        # Genera il messaggio corretto in base allo storico
        if conteggio <= 1:
            # 1ª call
            messaggio = (
                f"Ciao <b>{first_name}</b>, buongiorno!☺️\n\n"
                f"Ti mando il promemoria del meet di oggi, *<b>{date_formatted}</b>* alle ore *<b>{time_formatted}</b>* \n\n"
                f"Ecco il link 😊\n{link}\n\n"
                f"Per qualsiasi cosa sono a disposizione, quindi non farti problemi a chiedere qui in chat 🔥\n\n"
                f"Buona giornata 😉"
            )
        elif conteggio == 2:
            # 2ª call
            messaggio = (
                f"Buongiorno <b>{first_name}</b>!\n"
                f"Ti mando il promemoria del meet di oggi, *<b>{date_formatted}</b>* alle ore *<b>{time_formatted}</b>* \n\n"
                f"Ti mando qui sotto il link\n"
                f"👉 {link}\n\n"
                f"Buona giornata 💪"
            )
        else:
            # 3ª o successiva call
            messaggio = (
                f"Ciao <b>{first_name}</b>, buongiorno!\n\n"
                f"Ti mando qui sotto il reminder per la chiamata di oggi, *<b>{date_formatted}</b>* alle ore *<b>{time_formatted}</b>* Anzi ⤵️\n"
                f"{link}\n\n"
                f"A più tardi👊"
            )
            
        print(f"Invio promemoria per {client_name} (Call n. {conteggio})...")
        
        # 1. Invia il nome del cliente (senza parentesi o scritte extra)
        send_telegram_message(f"👤 <b>Cliente:</b> {client_name} (Call n. {conteggio})")
        
        # 2. Invia il messaggio WhatsApp già formattato pronto da copiare e incollare
        send_telegram_message(messaggio)
        
    print("Processamento completato con successo!")

if __name__ == "__main__":
    main()
