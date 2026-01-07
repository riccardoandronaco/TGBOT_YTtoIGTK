# TGBOT_YTtoIGTK

Un bot Telegram privato per scaricare video YouTube Shorts e caricarli automaticamente su Instagram e TikTok (Sperimentale).

## Funzionalità

- **Download YouTube**: Scarica video da YouTube (Shorts) inviando semplicemente il link o monitorando un canale.
- **Anteprima Telegram**: Visualizza il video scaricato direttamente in chat.
- **Upload Multi-Piattaforma**:
  - **Instagram**: Caricamento automatico tramite API (Instagrapi).
  - **TikTok (Sperimentale)**: Caricamento automatico tramite automazione browser (Playwright).
- **Gestione Sessioni**: Login automatico su Instagram con salvataggio sessione.
- **Gestione Cookie TikTok**: Supporto per l'utilizzo di cookie esportati per il login TikTok.
- **Pulizia Automatica**: Rimozione dei file scaricati dopo l'upload per risparmiare spazio.

## Prerequisiti

- Python 3.8+
- Un account Telegram e un Bot Token (ottenibile da @BotFather).
- Un account Instagram.
- Un account TikTok (per la funzione sperimentale).

## Installazione

1.  Clona il repository:
    ```bash
    git clone https://github.com/riccardoandronaco/TGBOT_YTtoIGTK.git
    cd TGBOT_YTtoIGTK
    ```

2.  Crea un ambiente virtuale (opzionale ma consigliato):
    ```bash
    python -m venv venv
    # Windows
    venv\Scripts\activate
    # Linux/Mac
    source venv/bin/activate
    ```

3.  Installa le dipendenze:
    ```bash
    pip install -r requirements.txt
    ```

4.  Installa i browser per Playwright (necessario per TikTok):
    ```bash
    playwright install chromium
    ```

5.  Configura il file `.env`:
    - Crea un file `.env` nella root del progetto basandoti sulle variabili necessarie.
    - Esempio di configurazione:
      ```env
      TELEGRAM_BOT_TOKEN=il_tuo_token_telegram
      INSTAGRAM_USERNAME=il_tuo_username_instagram
      INSTAGRAM_PASSWORD=la_tua_password_instagram
      TIKTOK_USERNAME=il_tuo_username_tiktok
      TIKTOK_COOKIES_PATH=config/tiktok_cookies.txt
      ALLOWED_USER_IDS=123456789,987654321
      DOWNLOAD_PATH=downloads
      YOUTUBE_CHANNEL_URL=https://www.youtube.com/@TuoCanale
      ```

6.  Configura i cookie TikTok (Necessario per l'upload):
    - Usa un'estensione browser come "Get cookies.txt LOCALLY" per esportare i cookie di TikTok mentre sei loggato.
    - Salva il file in `config/tiktok_cookies.txt` (formato Netscape).

## Utilizzo

1.  Avvia il bot:
    ```bash
    python src/bot.py
    ```
2.  Interagisci con il bot su Telegram:
    - **Invia un link** di un YouTube Short per scaricarlo e prepararlo all'invio.
    - Usa **/fetch** per cercare il prossimo video non pubblicato dal canale configurato.
3.  Usa i pulsanti Inline che compaiono per scegliere dove caricare:
    - "Instagram"
    - "TikTok"
    - "Tutti e due"

## Note Importanti

- **Instagram**: Al primo avvio potrebbe richiedere la verifica (2FA) o checkpoint. Il bot cercherà di gestire il login, ma controlla i log se fallisce.
- **TikTok (Sperimentale)**: Utilizza l'automazione del browser (Playwright) simulando un utente reale.
  - È **fragile**: cambiamenti nell'interfaccia di TikTok potrebbero rompere l'upload.
  - Richiede che i cookie siano aggiornati se la sessione scade.
  - Non interagire con la finestra del browser (se visibile) durante il caricamento.
- **Sicurezza**: Non condividere mai il file `.env`, `session.json` o i file dei cookie.
