# Guida al Deploy su Raspberry Pi 3B+ (e successivi)

Questo bot può girare su un Raspberry Pi. Tuttavia, poiché utilizza un browser automatizzato (Chromium via Playwright) per caricare su TikTok, le risorse (RAM) sono limitate sul Pi 3B+ (1GB).

## 1. Prerequisiti Sistema Operativo
⚠️ **È altamente consigliato utilizzare Raspberry Pi OS (64-bit).**
Molte librerie moderne (come Playwright) hanno supporto limitato o nullo per sistemi a 32-bit.
- Scarica "Raspberry Pi OS Lite (64-bit)" se non ti serve l'interfaccia grafica (risparmi RAM).

## 2. Preparazione Sistema
Aggiorna il sistema e installa le dipendenze di sistema necessarie (git, python, ffmpeg).

```bash
sudo apt update && sudo apt upgrade -y
sudo apt install git python3-pip python3-venv ffmpeg -y
```

### Aumentare lo Swap (CRITICO per Pi 3)
Il Pi 3 ha solo 1GB di RAM. Quando il browser si apre per l'upload, potrebbe crashare. Aumenta lo swap a 2GB.

1. Modifica il file di configurazione dphys-swapfile:
   ```bash
   sudo nano /etc/dphys-swapfile
   ```
2. Cerca la riga `CONF_SWAPSIZE=100` e cambiala in:
   ```bash
   CONF_SWAPSIZE=2048
   ```
3. Salva (Ctrl+O, Invio) ed esci (Ctrl+X).
4. Riavvia il servizio di swap:
   ```bash
   sudo /etc/init.d/dphys-swapfile restart
   ```

## 3. Installazione Bot

1. **Clona la repository**:
   ```bash
   cd /home/pi
   git clone https://github.com/riccardoandronaco/TGBOT_YTtoIGTK.git
   cd TGBOT_YTtoIGTK
   ```

2. **Crea environment Python**:
   ```bash
   python3 -m venv venv
   source venv/bin/activate
   ```

3. **Installa dipendenze**:
   ```bash
   pip install --upgrade pip
   pip install -r requirements.txt
   ```

4. **Installa Playwright e Browser**:
   Questo passaggio richiede tempo.
   ```bash
   playwright install chromium
   playwright install-deps
   ```
   *Nota: Se `playwright install-deps` fallisce, potrebbe essere necessario installare manualmente le dipendenze elencate nell'errore.*

## 4. Configurazione

1. **Crea il file .env**:
   ```bash
   nano .env
   ```
   Incolla le tue variabili (Telegram Token, IG user/pass, ecc.). Salva e esci.

2. **Cookie TikTok**:
   Devi trasferire il file dei cookie dal tuo PC al Raspberry. Puoi usare `scp` o creare il file incollando il contenuto.
   ```bash
   nano config/tiktok_cookies.txt
   # Incolla il contenuto del tuo file cookie Netscape esportato
   ```

3. **Sessione Instagram** (Opzionale ma consigliato):
   Se hai già un file `session.json` funzionante sul tuo PC, copialo nella cartella del bot sul Raspberry per evitare problemi di login/checkpoint.

## 5. Test Manuale
Lancia il bot manualmente per vedere se parte e se riesce a fare un upload (specialmente su TikTok per testare la RAM).

```bash
python src/bot.py
```

## 6. Avvio Automatico (Service)
Per far girare il bot in background e riavviarlo se il Raspberry si riaccende.

1. **Crea il file di servizio**:
   ```bash
   sudo nano /etc/systemd/system/tgbot_yt2ig.service
   ```

2. **Incolla questa configurazione** (assicurati che i percorsi siano corretti):
   ```ini
   [Unit]
   Description=Telegram Bot YT to IG/TikTok
   After=network.target

   [Service]
   Type=simple
   User=pi
   WorkingDirectory=/home/pi/TGBOT_YTtoIGTK
   ExecStart=/home/pi/TGBOT_YTtoIGTK/venv/bin/python /home/pi/TGBOT_YTtoIGTK/src/bot.py
   Restart=always
   RestartSec=10

   [Install]
   WantedBy=multi-user.target
   ```

3. **Attiva il servizio**:
   ```bash
   sudo systemctl daemon-reload
   sudo systemctl enable tgbot_yt2ig.service
   sudo systemctl start tgbot_yt2ig.service
   ```

4. **Controlla i log**:
   ```bash
   sudo journalctl -u tgbot_yt2ig.service -f
   ```
