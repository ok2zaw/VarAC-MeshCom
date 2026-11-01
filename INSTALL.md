# Instalace a provoz MeshCom Gateway na Raspberry Pi / Debian

> **Uživatelský účet:** Návod i `setup_rpi.sh` předpokládají účet `pi`. Pokud
> používáte jiný účet, po instalaci a před spuštěním služeb nahraďte `pi` jeho
> skutečným jménem v cestách `/home/pi`, ve vlastnictví `pi:pi`, v cestách Maildir
> a v obou souborech `systemd/*.service`. V jednotkách upravte hlavně `User`,
> `Group`, `WorkingDirectory`, `EnvironmentFile` a `ExecStart`. Poté spusťte
> `sudo systemctl daemon-reload` a služby restartujte.

Tento projekt je navržen jako obousměrná gateway mezi MeshCom a e-mailem. Důraz je kladen na jednoduchou a bezpečnou architekturu:

- MeshCom komunikuje přes UDP na lokální síti
- Raspberry Pi přebírá zprávy a přeposílá je přes externí SMTP server
- pro příchozí poštu se používá externí IMAP služba nebo lokální Maildir fronta
- samotný Raspberry Pi není veřejný mailserver; je to pouze gateway a fronta pro přeposílání

Doporučená varianta je: Raspberry Pi + Debian + lokální Maildir + externí SMTP/IMAP poskytovatel nebo přístup k mailserveru ve firmě / na VPS.

## 1. Požadavky

- Raspberry Pi OS / Debian 12
- přístup k internetu
- lokální síť s MeshCom node
- SMTP server pro odchozí poštu
- IMAP server pro příchozí poštu (volitelné, pokud používáte Maildir/cron nebo externí frontu)

## 2. Instalace systému

```bash
sudo apt update
sudo apt upgrade -y
sudo apt install -y git python3 python3-venv python3-pip mailutils
```

Pokud budete používat lokální Maildir frontu, vytvořte ji:

```bash
mkdir -p /home/pi/Maildir/{new,cur,tmp}
chown -R pi:pi /home/pi/Maildir
```

## 3. Stažení projektu

```bash
cd /home/pi
git clone https://github.com/ok2zaw/VarAC-MeshCom.git meshcom-gateway
cd /home/pi/meshcom-gateway
```

Projekt instalujte pod uživatelem `pi`, protože obě systemd služby pod tímto
uživatelem zapisují konfiguraci, SQLite databázi a Maildir. Pokud používáte
jméno jiného účtu, upravte `User`, `Group` a cesty v obou souborech v `systemd/`.

## 4. Virtuální prostředí

```bash
python3 -m venv .venv
.venv/bin/python -m pip install --upgrade pip
.venv/bin/python -m pip install -r requirements.txt
```

Spusťte tyto příkazy jako uživatel `pi`, bez `sudo`. `sudo` patří k instalaci
systémových balíčků (`apt`) a ke správě služeb, ne k instalaci balíčků do `.venv`.
Skript `setup_rpi.sh` spuštěný jako root skončí s vysvětlením, aby nevytvořil
virtuální prostředí vlastněné rootem.

Pokud už instalace skončila chybou `Permission denied` uvnitř `.venv`, opravte
vlastníka prostředí jednorázově:

```bash
sudo chown -R pi:pi /home/pi/meshcom-gateway/.venv
```

Potom jako `pi` znovu aktivujte prostředí a nainstalujte závislosti:

```bash
cd /home/pi/meshcom-gateway
.venv/bin/python -m pip install -r requirements.txt
```

Pokud se při spuštění objeví `ModuleNotFoundError: No module named 'flask'`,
znovu nainstalujte závislosti do projektového prostředí a ověřte import:

```bash
cd /home/pi/meshcom-gateway
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python -c "import flask; print(flask.__version__)"
```

## 5. Konfigurace

Nejbezpečnější způsob je používat `.env` soubor s proměnnými `MESHCOM_*` a případně ponechat `config.json` pouze jako fallback. Příklad najdete v `.env.example`.

Upravte soubor `config.json`:

```json
{
  "mesh_node_ip": "192.168.1.50",
  "mesh_udp_port": 1799,
  "listen_ip": "0.0.0.0",
  "mesh_group": "12345",
  "smtp_host": "smtp.example.com",
  "smtp_port": 587,
  "smtp_user": "gateway@example.com",
  "smtp_password": "secret",
  "smtp_use_tls": true,
  "mail_from": "meshcom-gateway@example.com",
  "mail_to": "you@example.com",
  "maildir_new": "/home/pi/Maildir/new",
  "maildir_cur": "/home/pi/Maildir/cur",
  "imap_host": "imap.example.com",
  "imap_port": 993,
  "imap_user": "gateway@example.com",
  "imap_password": "secret",
  "imap_use_ssl": true,
  "imap_folder": "INBOX",
  "poll_interval_seconds": 5,
  "max_mesh_payload": 150,
  "callsign": "OK2ZAW-10",
  "web_username": "admin",
  "web_password": "changeme"
}
```

Důležité:

- `mesh_node_ip` je IP adresa MeshCom uzlu, ke kterému posíláte UDP zprávy
- `mesh_group` musí odpovídat skupině, ze které chcete přijímat a do které posílat
- `maildir_new` a `maildir_cur` musí být přístupné pro uživatele, který spouští gateway
- `imap_*` nastavení je pro externí IMAP frontu; pokud je prázdné, gateway pracuje jen s lokálním Maildir

## 6. Spuštění gateway

```bash
.venv/bin/python gateway.py
```

Webové rozhraní:

```bash
.venv/bin/python webapp.py
```

Web běží na portu 8899. Přístup je chráněn podle nastavení `web_username` a `web_password` v `config.json`.

## 7. Systémová služba (systemd)

Soubory v `systemd/` předpokládají projekt v `/home/pi/meshcom-gateway` a
virtuální prostředí vytvořené v předchozím kroku. Gateway běží jako samostatná
služba; web používá produkční WSGI server Waitress, nikoli vývojový server Flask.
Obě jednotky načítají volitelný `.env` soubor. Nainstalujte a aktivujte je:

```bash
sudo cp systemd/meshcom-gateway.service systemd/meshcom-web.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now meshcom-gateway meshcom-web
sudo systemctl status meshcom-gateway meshcom-web
```

Pro zobrazení průběžných logů použijte `sudo journalctl -u meshcom-gateway -u meshcom-web -f`.
Webové rozhraní je dostupné na `http://<IP-Raspberry-Pi>:8899/`; ve výchozím
stavu poslouchá na všech síťových rozhraních, proto nastavte silné heslo a
přístup omezte na důvěryhodnou síť.

### Aktualizace aplikace z webového rozhraní

Volitelná stránka **Aktualizace** kontroluje pevný remote `origin/main` a nabízí
jen fast-forward update. Kontrola i samotný update vyžadují internet. Web update
zablokuje, pokud se větev rozchází, místní změny kolidují s aktualizací nebo
upstream mění `requirements.txt`, `systemd/` či `setup_rpi.sh`; tyto situace se
řeší ručně. Změny `config.json` a `.env` se automaticky nepřepisují. Aktualizace
nemění Postfix, Dovecot, OS ani systémové Python balíčky.

Webová služba běží bez práv roota a má `NoNewPrivileges=true`; proto restart
zajišťuje systemd path unit. Web pouze vytvoří prázdný marker a rootem běžící
oneshot služba podle něj restartuje pevně dané dvě služby. Neudělujte webovému
uživateli obecné `sudo`.

Pokud projekt není v `/home/pi/meshcom-gateway`, nejprve upravte konstantu
`REQUEST_FILE` v `scripts/meshcom-restart` a `PathExists` v
`systemd/meshcom-restart.path` na skutečnou cestu k souboru
`.meshcom-restart-request` v repozitáři. Pak nainstalujte helper a jednotky:

```bash
sudo install -o root -g root -m 0755 scripts/meshcom-restart /usr/local/sbin/meshcom-restart
sudo cp systemd/meshcom-restart.path systemd/meshcom-restart.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now meshcom-restart.path
sudo systemctl is-active meshcom-restart.path
```

Uživatel webové služby musí mít právo zápisu do adresáře repozitáře, což zároveň
potřebuje Git pro fast-forward update. Helper má vlastníka `root` a práva `0755`;
jeho jedinou akcí je restart `meshcom-gateway` a `meshcom-web`. Služby Postfix a
Dovecot se nikdy nerestartují ani neaktualizují z tohoto UI.

V souboru `VERSION` je číslo vydání aplikace (aktuálně `0.1`). Nové vydání zvýší
správce při publikaci změny; pro přesné porovnání instalace UI zároveň zobrazuje
commit hash. Pokud UI oznámí ruční zásah, zkontrolujte výpis `git status` a
postupujte podle tohoto návodu, nepoužívejte `git reset --hard`.

## 8. Volitelná varianta: lokální poštovní uzel pro LAN/VPN

Tato varianta odpovídá provozu, kdy Raspberry Pi není veřejný server, ale
zprostředkovává poštu pro místní programy a mailové klienty. Veřejnou adresu
nadále hostuje tvůj server:

- **Postfix** přijímá poštu od programů na Pi a klientů v důvěryhodné síti a
  předává odchozí zprávy přes SMTP submission na vlastní veřejný server.
- **Dovecot** zpřístupňuje jednu společnou lokální schránku přes IMAPS klientům
  v LAN nebo přes VPN na routeru.
- **getmail6** může při dostupném internetu stahovat příchozí zprávy z veřejného
  IMAP účtu do lokální schránky. Veřejný originál lze ponechat na serveru.
- Raspberry nepřijímá poštu přímo z internetu. Na routeru neforwardujte porty
  25, 143, 465, 587 ani 993 na Pi; vzdálený klient se připojuje přes VPN.

Při výpadku internetu zůstává místní doručování dostupné. Postfix podrží
odchozí zprávy ve frontě a getmail6 stáhne nové veřejné zprávy po obnovení
připojení. Mailový klient se připojuje k Raspberry přes IMAPS (993); SMTP
submission (587) povolte pouze z LAN/VPN. Programy běžící přímo na Pi mohou
odesílat přes místní Postfix na `127.0.0.1:25`. Mailový klient na PC může číst
místní schránku přes Dovecot IMAPS; pro odesílání použije SMTP server vlastní
domény. Postfix v tomto základním nastavení naslouchá jen na loopbacku.

Nainstalujte služby:

```bash
sudo apt install -y postfix dovecot-core dovecot-imapd getmail6 libsasl2-modules
```

V následujících příkazech nahraďte `/home/pi` cestou skutečného účtu a dosaďte
hostname SMTP/IMAP serveru vlastní domény. V příkladu se používá SMTP submission
na portu 587 se STARTTLS a přihlášením.

### Postfix: místní příjem a odchozí SMTP relay

Omezte příjem Postfixu na loopback a nastavte veřejný SMTP relay:

```bash
sudo postconf -e 'inet_interfaces = loopback-only'
sudo postconf -e 'relayhost = [smtp.example.net]:587'
sudo postconf -e 'smtp_tls_security_level = encrypt'
sudo postconf -e 'smtp_sasl_auth_enable = yes'
sudo postconf -e 'smtp_sasl_password_maps = hash:/etc/postfix/sasl_passwd'
sudo postconf -e 'smtp_sasl_security_options = noanonymous'
sudoedit /etc/postfix/sasl_passwd
```

Do otevřeného editoru vložte jeden řádek a nahraďte hodnoty údaji od správce
veřejného serveru:

```text
[smtp.example.net]:587 uzivatel@example.net:HESLO
```

Soubor uložte, nastavte oprávnění a vytvořte databázi SASL mapy:

```bash
sudo chmod 600 /etc/postfix/sasl_passwd
sudo postmap /etc/postfix/sasl_passwd
sudo chmod 600 /etc/postfix/sasl_passwd.db
sudo systemctl restart postfix
```

Heslo je pouze v chráněném souboru Postfixu, ne v `config.json`, `.env` ani
Gitu. Tato varianta dovoluje odesílat přes relay programům na Raspberry; klient
na PC zatím používá SMTP vlastní domény přímo. SMTP submission z VPN přes
Raspberry by vyžadovalo samostatně nakonfigurované přihlášení a firewall.

### Dovecot: lokální schránka přes IMAPS

Vytvořte Maildir pod účtem, pod kterým poběží lokální schránka a getmail. Pro
výchozí účet z návodu:

```bash
MAILUSER=pi
sudo install -d -o "$MAILUSER" -g "$MAILUSER" -m 700 "/home/$MAILUSER/Maildir/cur" \
  "/home/$MAILUSER/Maildir/new" "/home/$MAILUSER/Maildir/tmp"
```

V `/etc/dovecot/conf.d/10-mail.conf` nastavte:

```text
mail_location = maildir:~/Maildir
```

V `/etc/dovecot/conf.d/10-ssl.conf` ponechte TLS zapnuté a nastavte certifikát,
který klient důvěřuje a jehož jméno odpovídá adrese Raspberry používané přes
VPN. IMAPS na portu 993 povolte jen z LAN/VPN; nevytvářejte přesměrování z WAN.
Konkrétní Dovecot uživatel a jeho IMAP heslo musí odpovídat vlastníkovi Maildiru.

### getmail6: stažení veřejné pošty do lokální schránky

Vytvořte soukromý konfigurační adresář a soubor:

```bash
sudo install -d -o "$MAILUSER" -g "$MAILUSER" -m 700 "/home/$MAILUSER/.getmail"
sudo -u "$MAILUSER" nano "/home/$MAILUSER/.getmail/getmailrc"
```

Vložte šablonu a doplňte server, uživatelské jméno a heslo. Tato konfigurace
čte pouze `INBOX`, nechává originál na veřejném serveru a doručuje do místního
Maildiru:

```ini
[retriever]
type = SimpleIMAPSSLRetriever
server = imap.example.net
port = 993
username = uzivatel@example.net
password = DOPLNIT_HESLO
mailboxes = ("INBOX",)
use_peek = True

[destination]
type = Maildir
path = /home/pi/Maildir/

[options]
read_all = false
delete = false
```

Chraňte soubor s heslem a spusťte jednorázový test pod stejným účtem:

```bash
sudo chmod 600 "/home/$MAILUSER/.getmail/getmailrc"
sudo -u "$MAILUSER" getmail --getmaildir "/home/$MAILUSER/.getmail" \
  --rcfile getmailrc --verbose
```

Po úspěšném testu přidejte do crontabu tohoto uživatele periodické stahování:

```bash
sudo crontab -u "$MAILUSER" -e
```

Přidejte řádek, který spustí getmail každých pět minut:

```cron
*/5 * * * * /usr/bin/getmail --getmaildir /home/pi/.getmail --rcfile getmailrc
```

### Nastavení MeshCom gateway ve webovém prostředí

V Nastavení nastavte SMTP aplikace na místní Postfix:

- server: `127.0.0.1`
- port: `25`
- uživatel a heslo: prázdné
- STARTTLS: vypnuto

Tato neautentizovaná varianta je povolena pouze pro loopback. Postfix následně
předá zprávu veřejnému serveru pomocí chráněného SASL souboru z předchozího kroku.

Pro předávání vybraných veřejných zpráv do MeshCom vytvořte na veřejném IMAP
serveru kopírovací pravidlo do samostatné složky `MeshCom`. V nastavení gateway
pak zadejte veřejný IMAP server, port `993`, účet, heslo, zapněte SSL/TLS a jako
složku nastavte `MeshCom`. Cesty `maildir_new` a `maildir_cur` ponechte ve
vyhrazeném Maildiru gateway, například:

```text
/home/pi/meshcom-gateway/Maildir/new
/home/pi/meshcom-gateway/Maildir/cur
```

Veřejný účet tak používají odděleně: getmail6 čte `INBOX` pro lokální schránku a
gateway čte jen kopírovanou složku `MeshCom`. Pokud aplikaci konfigurujete přes
`.env`, jeho hodnoty `MESHCOM_*` mají před webovým `config.json` přednost; změny
proto proveďte v `.env` a restartujte gateway. Hesla do webového formuláře
neukládejte, jsou-li už nastavená v `.env`.

Po úpravě konfigurace restartujte Postfix, Dovecot a gateway. Dovecot lze ověřit
přihlášením mailového klienta z LAN/VPN na port 993; gateway ověřte v jejím logu.

Pro getmail6 vytvořte konfigurační soubor pod účtem, který bude poštu stahovat,
omez jeho oprávnění na vlastníka (`chmod 600`) a nastav připojení k veřejnému
IMAP serveru přes TLS. Přihlašovací údaje nevkládejte do tohoto návodu. Cílem
doručení je společná lokální schránka Dovecotu; pokud má být zpráva zároveň
předána do MeshCom, použijte samostatnou kopii nebo vyhrazenou složku/pravidlo.

> **Pozor na souběžné stahování:** gateway má vlastní volitelný IMAP poller
> (`MESHCOM_IMAP_*`), který zprávy označuje jako přečtené a předává je do
> MeshCom fronty. Nesmí číst stejnou veřejnou složku současně s getmail6.
> Použijte oddělenou složku nebo vypněte jeden z pollerů; běžnou schránku
> klientů nikdy nenastavujte jako `maildir_new` gateway.

K dokončení konkrétní konfigurace je potřeba hostname veřejného IMAP/SMTP serveru,
porty, požadované TLS režimy a zvolený místní účet. Hesla patří pouze do
chráněných konfiguračních souborů na Raspberry. Roundcube není pro tento provoz
potřeba.

## 9. Tipy pro provoz

- použijte měkký restart po změně `config.json`
- nastavte přístup k webu přes lokální síť, ne z internetu bez zabezpečení
- neukládejte hesla do Gitu
- v produkci používejte `systemd` a logování do journald
- pravidelně ověřujte přístup k SMTP/IMAP a stav heartbeat na webu

## 10. Rozdíl mezi externím a lokálním mailserverem

### Externí SMTP/IMAP (doporučeno)

Výhody:

- menší riziko zneužití Raspberry Pi jako veřejného mailserveru
- jednodušší správa certifikátů, relayingu a zabezpečení
- nižší provozní náročnost

### Lokální Postfix / Dovecot s relayem a IMAP stažením

Výhody:

- místní pošta a dříve stažené zprávy jsou dostupné i bez internetu
- mailové klienty se připojují z LAN/VPN bez veřejné IP Raspberry

Nevýhody:

- větší provozní náročnost
- složitější zabezpečení a aktualizace
- nutnost řešit certifikáty, relaying a správu účtů

Tato varianta není veřejný mailserver: veřejnou adresu a příjem z internetu
nadále zajišťuje vlastní server. Raspberry používá jeho SMTP/IMAP rozhraní.

## 11. VARA, Mercury a VarAC na Raspberry Pi 5 (experimentální)

VARA (EA5HVK) i VarAC jsou programy pro Windows na platformě x86. Raspberry Pi 5
má procesor ARM64, proto se spouštějí přes **Wine** (vrstva Windows API) a
**Box64** (překlad instrukcí x86/x64 na ARM64). Provoz je funkční, ale
experimentální: počítejte s vyšší zátěží CPU, občasnými pády GUI a nutností
ladění zvuku a PTT. Gateway samotná (kapitoly 2–7) na Wine nezávisí.

### 11.1 Požadavky

- Raspberry Pi 5, doporučeno **8 GB RAM**, aktivní chladič (VARA HF trvale
  zatěžuje CPU, bez chlazení dochází k podtaktování)
- kvalitní SSD/NVMe nebo rychlá microSD karta (A2), napájecí zdroj 27 W
- **64bitový Raspberry Pi OS s desktopem** (Bookworm nebo novější); VARA i VarAC
  mají grafické rozhraní, varianta Lite bez desktopu nestačí
- USB zvuková karta / rozhraní k rádiu (SignaLink, Digirig, vestavěná karta
  transceiveru apod.) a případně USB‑serial pro CAT/PTT
- přístup k desktopu – monitor, nebo vzdáleně přes VNC (`sudo raspi-config` →
  *Interface Options* → *VNC*)
- registrační klíč VARA (volitelně; bez něj běží VARA v omezené rychlosti)

### 11.2 Přepnutí jádra na 4K stránky paměti

Výchozí jádro Pi 5 používá stránky o velikosti 16 KB, se kterými Wine a Box64
nefungují. Raspberry Pi OS obsahuje i jádro se 4 KB stránkami, stačí ho zapnout:

```bash
sudo nano /boot/firmware/config.txt
```

Na konec souboru (do sekce `[all]`) přidejte:

```text
kernel=kernel8.img
```

Restartujte a ověřte velikost stránky – výsledek musí být `4096`:

```bash
sudo reboot
getconf PAGESIZE
```

### 11.3 Aktualizace systému a doporučené nastavení desktopu

```bash
sudo apt update
sudo apt full-upgrade -y
sudo apt install -y git wget curl cabextract unzip p7zip-full alsa-utils
```

Wine běží nejspolehlivěji pod X11. Pokud se okna VARA/VarAC chovají divně
(nepřekreslují se, nedají se přesunout), přepněte desktop z Waylandu na X11:
`sudo raspi-config` → *Advanced Options* → *Wayland* → *X11* a restartujte.

### 11.4 Instalace Box64 a Wine přes Pi-Apps

Nejjednodušší a udržovaná cesta je balík **Wine (x64)** z Pi-Apps, který
nainstaluje Box64 i Wine v režimu WoW64 (umí spouštět 64bitové i 32bitové
Windows programy – VARA je 32bitová). Spusťte jako uživatel `pi`, bez `sudo`:

```bash
wget -qO- https://raw.githubusercontent.com/Botspot/pi-apps/master/install | bash
~/pi-apps/manage install "Wine (x64)"
```

Instalace na Pi 5 trvá přibližně 5–15 minut. Alternativně lze totéž provést v
grafickém Pi-Apps: *Tools* → *Emulation* → *Wine (x64)* → *Install*.

Ověřte funkčnost:

```bash
box64 --version
wine --version
```

Do `~/.bashrc` přidejte proměnné, které na Pi 4/5 předcházejí zamrzání:

```bash
cat >> ~/.bashrc <<'EOF'
export BOX64_DYNAREC_STRONGMEM=1
export MESA_GL_VERSION_OVERRIDE=3.2
EOF
source ~/.bashrc
```

### 11.5 Příprava Wine prostředí (prefixu)

Vytvořte výchozí prefix `~/.wine` a nainstalujte aktuální winetricks:

```bash
wineboot -u
mkdir -p ~/.local/bin
wget -O ~/.local/bin/winetricks \
  https://raw.githubusercontent.com/Winetricks/winetricks/master/src/winetricks
chmod +x ~/.local/bin/winetricks
export PATH="$HOME/.local/bin:$PATH"
```

Nainstalujte knihovny, které VARA a VarAC potřebují (VARA je psaná ve Visual
Basic 6, VarAC v .NET Frameworku). Instalace .NET pod emulací trvá dlouho
(desítky minut), nepřerušujte ji ani při dlouhé nečinnosti:

```bash
winetricks -q vb6run pdh_nt4 corefonts
winetricks -q dotnet48
```

Nastavte verzi Windows na Windows 10:

```bash
winecfg -v win10
```

V `winecfg` na kartě *Audio* zkontrolujte, že je vybrán ovladač ALSA nebo
PulseAudio/PipeWire a že je vidět USB zvuková karta.

### 11.6 Instalace VARA (HF a/nebo FM)

1. Stáhněte instalační ZIP VARA HF (případně VARA FM) ze stránky autora
   <https://rosmodem.wordpress.com/> do `~/Downloads`.
2. Rozbalte a spusťte instalátor:

   ```bash
   cd ~/Downloads
   unzip -o "VARA HF*.zip" -d vara-hf
   wine vara-hf/*setup*.exe
   ```

3. Instalaci proveďte s výchozí cestou (`C:\VARA`, resp. `C:\VARA FM`).
4. První spuštění:

   ```bash
   wine "$HOME/.wine/drive_c/VARA/VARA.exe"
   ```

5. Ve VARA nastavte v *Settings* → *SoundCard* vstup a výstup USB zvukové
   karty. Pokud ji nevidíte, ukončete Wine (`wineserver -k`), zkontrolujte kartu
   příkazem `arecord -l` a znovu otevřete `winecfg`.
6. V *Settings* → *VARA Setup* zadejte volací znak a případně registrační klíč.
7. **Vypněte vodopád** (Waterfall / Spectrum) – vykreslování pod emulací
   zbytečně zatěžuje CPU a je známou příčinou pádů.
8. TCP porty nechte výchozí (VARA HF `8300`/`8301`); stejné porty pak zadáte ve
   VarAC.

### 11.7 Sériový port pro CAT/PTT

Wine mapuje linuxová sériová zařízení na COM porty v adresáři `dosdevices`.
Přidejte uživatele do skupiny `dialout` a vytvořte odkaz (příklad pro
`/dev/ttyUSB0` jako `COM5`):

```bash
sudo usermod -aG dialout pi
ls -l /dev/serial/by-id/
ln -sf /dev/serial/by-id/<ID-vaseho-zarizeni> ~/.wine/dosdevices/com5
```

Odkaz na `/dev/serial/by-id/...` je stabilní i po přepojení USB, na rozdíl od
`/dev/ttyUSB0`. Po přidání do skupiny `dialout` se odhlaste a znovu přihlaste.
Pro rádia s VOX nebo Digirig s PTT přes zvukovou kartu tento krok není nutný.

### 11.8 Instalace VarAC

1. Stáhněte aktuální instalační balík VarAC z <https://www.varac-hamradio.com/>
   do `~/Downloads`.
2. Rozbalte a spusťte instalátor:

   ```bash
   cd ~/Downloads
   unzip -o "VarAC*.zip" -d varac
   wine varac/*.exe
   ```

3. Po instalaci spusťte VarAC (cestu upravte podle skutečného umístění):

   ```bash
   ls "$HOME/.wine/drive_c/"
   wine "$HOME/.wine/drive_c/VarAC/VarAC.exe"
   ```

4. V nastavení VarAC zadejte volací znak a lokátor, cestu k `VARA.exe`
   (`C:\VARA\VARA.exe`), TCP porty VARA (`8300`/`8301`) a způsob ovládání
   PTT/CAT (COM port z kroku 11.7, případně VOX).
5. Nejprve ověřte příjem (dekódování beaconů na volací frekvenci VarAC) a
   teprve potom vysílání.

### 11.9 Instalace modemu Mercury (volitelně)

[Mercury](https://github.com/Rhizomatica/mercury) je open-source HF modem
projektu HERMES (Rhizomatica). Běží nativně na ARM64 Linuxu bez Wine a bez
grafického prostředí a nabízí TCP rozhraní kompatibilní s VARA (ARQ port
`8300`, data `8301`). Balíčky jsou připravené pro Debian 13 Trixie / aktuální
Raspberry Pi OS arm64:

```bash
wget -qO- https://debian.hermes.radio/hermes/hermes.key | gpg --dearmor \
  | sudo tee /etc/apt/trusted.gpg.d/hermes.gpg > /dev/null
echo 'deb [arch=arm64] http://debian.hermes.radio/hermes trixie main' \
  | sudo tee /etc/apt/sources.list.d/hermes.list
sudo apt update
sudo apt install -y mercury
```

Na starším systému (Bookworm) Mercury přeložte ze zdrojů:

```bash
sudo apt install -y build-essential pkg-config libasound2-dev libpulse-dev libhamlib-dev make git
git clone https://github.com/Rhizomatica/mercury.git ~/mercury
cd ~/mercury && make && sudo make install
```

Vypište zvuková zařízení a vyzkoušejte ruční spuštění (příklad: ALSA karta 1,
PTT přes sériový port, ARQ port 8400):

```bash
mercury -z
mercury -x alsa -i plughw:1,0 -o plughw:1,0 -P serial -A /dev/ttyUSB0 -p 8400
```

**Souběh s VARA:** Mercury i VARA mají výchozí port `8300`. Pokud mají běžet
současně, nastavte Mercury jiný port (`-p 8400`). Obě aplikace navíc nemohou
sdílet stejnou zvukovou kartu přes přímé ALSA zařízení `hw:`/`plughw:` –
použijte dvě rozhraní, nebo PulseAudio/PipeWire (`-x pulse`). Vysílat může
v jednu chvíli jen jeden modem.

### 11.10 Ovládání z webu – záložka VarAC

Záložka **VarAC** ve webovém rozhraní gateway spouští, zastavuje a restartuje
VARA, Mercury a VarAC, zobrazuje jejich stav a poslední výpis a nastavuje
automatický start po přihlášení do desktopu se zpožděním pro každý program
zvlášť (např. VARA 10 s, Mercury 10 s, VarAC 40 s).

Programy běží jako uživatelské systemd jednotky (`meshcom-radio-vara`,
`meshcom-radio-mercury`, `meshcom-radio-varac` a sdílený
`meshcom-radio-wineserver`). Restart nebo aktualizace webu je proto nezastaví.

Jednorázová příprava:

1. Zapněte automatické přihlášení do desktopu, jinak VARA a VarAC nemají
   kam vykreslit okno: `sudo raspi-config` → *System Options* →
   *Boot / Auto Login* → *Desktop Autologin*.
2. Povolte uživatelský systemd i mimo přihlášení, aby web mohl spouštět
   programy kdykoli:

   ```bash
   sudo loginctl enable-linger pi
   ```

3. Pokud jste dříve vytvořili ruční spouštěcí skript `~/start-varac.sh` a
   položku `~/.config/autostart/varac.desktop`, smažte je, aby se programy
   nespouštěly dvakrát:

   ```bash
   rm -f ~/start-varac.sh ~/.config/autostart/varac.desktop
   ```

4. Ve webu na záložce **VarAC** zkontrolujte cesty k `VARA.exe` a
   `VarAC.exe`, parametry Mercury (zvuková karta, PTT, port) a displej
   (obvykle `:0`). Po zaškrtnutí autostartu a uložení web sám vytvoří
   `~/.config/autostart/meshcom-radio.desktop`; po odškrtnutí všech programů
   ji smaže.

Ruční kontrola z příkazové řádky:

```bash
systemctl --user list-units 'meshcom-radio-*'
journalctl --user -u meshcom-radio-varac -n 50
```

> **Bezpečnost:** Záložka spouští programy pod účtem `pi`. Web proto
> zpřístupněte jen v důvěryhodné síti se silným heslem. Spustit lze pouze
> soubory `.exe` přes Wine a program `mercury`.

### 11.11 Řešení problémů


| Problém | Řešení |
| --- | --- |
| Wine nespustí nic, chyby paměti / `mmap` | `getconf PAGESIZE` musí vracet `4096` – viz krok 11.2 |
| Systém zamrzne, okna nereagují | `wineserver -k`; zkontrolujte `BOX64_DYNAREC_STRONGMEM=1`, vypněte vodopád ve VARA |
| VARA nevidí zvukovou kartu | `arecord -l`, `aplay -l`; ve `winecfg` → *Audio* zvolte jiný ovladač; restartujte Wine |
| VarAC hlásí chybějící .NET | znovu `winetricks -q dotnet48`, případně v čistém prefixu (`mv ~/.wine ~/.wine.bak`) |
| PTT nefunguje | ověřte skupinu `dialout`, odkaz v `~/.wine/dosdevices/` a číslo COM portu ve VarAC |
| Web hlásí „Uživatelský systemd neběží“ | `sudo loginctl enable-linger pi`, případně přihlášení do desktopu |
| Program na záložce VarAC hned skončí (Chyba) | rozbalte jeho výpis na záložce, nebo `journalctl --user -u meshcom-radio-<program>` |
| Okno VARA/VarAC se neobjeví | zkontrolujte přihlášený desktop a hodnotu Displej (`echo $DISPLAY` v terminálu desktopu) |
| VarAC se nepřipojí k Mercury | port ve VarAC musí odpovídat `-p` v parametrech Mercury |
| Vysoká teplota, nízký výkon | `vcgencmd measure_temp`, `vcgencmd get_throttled`; použijte aktivní chladič |

Zátěž lze sledovat příkazem `htop`. Trvale přes 90 % CPU při příjmu znamená,
že je potřeba vypnout vodopád, snížit šířku pásma VARA nebo zavřít jiné
programy na desktopu.

> **Poznámka:** Gateway (kapitoly 2–7) zatím nepředává zprávy mezi MeshCom a
> VarAC; záložka VarAC slouží jen ke spouštění a sledování programů.
