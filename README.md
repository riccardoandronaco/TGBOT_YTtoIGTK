# TGBOT_YTtoIGTK

Un bot Telegram privato per scaricare video YouTube Shorts e caricarli automaticamente su Instagram.

## Funzionalità

- Scarica video da YouTube (Shorts) inviando semplicemente il link.
- Anteprima del video scaricato direttamente su Telegram.
- Pulsante per confermare la pubblicazione su Instagram.
- Gestione automatica del login Instagram (con salvataggio sessione).
- Pulizia automatica dei file scaricati dopo l'upload.

## Prerequisiti

- Python 3.8+
- Un account Telegram e un Bot Token (ottenibile da @BotFather).
- Un account Instagram.

## Installazione

1.  Clona il repository o scarica i file.
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
    - Rinomina o crea un file `.env` nella root del progetto.
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
6.  (Opzionale) Configura i cookie TikTok:
    - Esporta i cookie di TikTok in formato Netscape (usa estensioni come "Get cookies.txt LOCALLY").
    - Salva il file in `config/tiktok_cookies.txt`.

## Utilizzo

1.  Avvia il bot:
    ```bash
    python src/bot.py
    ```
2.  Invia un link YouTube Short al bot oppure usa `/fetch` per cercare nuovi video dal canale configurato.
3.  Usa i pulsanti per caricare su Instagram, TikTok o entrambi.

## Note

- **Instagram**: Al primo avvio potrebbe richiedere la verifica (2FA) o checkpoint. Instagrapi salva la sessione in `session.json`.
- **TikTok**: Utilizza automazione browser (Playwright). Non interagire con la finestra del browser mentre carica.
    ```
2.  Apri il tuo bot su Telegram.
3.  Invia `/start`.
4.  Incolla un link di un YouTube Short.
5.  Attendi il download e l'anteprima.
6.  Clicca su "Pubblica su Instagram" per caricare il video.

## Note Importanti

- **Instagram Login**: La prima volta che esegui l'upload, potrebbe essere richiesto un controllo di sicurezza da parte di Instagram (codice via SMS/Email). `instagrapi` cerca di gestire il login, ma se fallisce, controlla i log.
- **Sicurezza**: Non condividere mai il file `.env` o `session.json`.
