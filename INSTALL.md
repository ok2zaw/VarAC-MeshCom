# Instalace a provoz MeshCom Gateway na Raspberry Pi / Debian

> **Uživatelský účet:** Návod i `setup_rpi.sh` předpokládají účet `ok2zaw`. Pokud
> používáte jiný účet, po instalaci a před spuštěním služeb nahraďte `ok2zaw` jeho
> skutečným jménem v cestách `/home/ok2zaw`, ve vlastnictví `ok2zaw:ok2zaw`, v cestách Maildir
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
mkdir -p /home/ok2zaw/Maildir/{new,cur,tmp}
chown -R ok2zaw:ok2zaw /home/ok2zaw/Maildir
```

## 3. Stažení projektu

```bash
cd /home/ok2zaw
git clone https://github.com/ok2zaw/VarAC-MeshCom.git meshcom-gateway
cd /home/ok2zaw/meshcom-gateway
```

Projekt instalujte pod uživatelem `ok2zaw`, protože obě systemd služby pod tímto
uživatelem zapisují konfiguraci, SQLite databázi a Maildir. Pokud používáte
jméno jiného účtu, upravte `User`, `Group` a cesty v obou souborech v `systemd/`.

## 4. Virtuální prostředí

```bash
python3 -m venv .venv
.venv/bin/python -m pip install --upgrade pip
.venv/bin/python -m pip install -r requirements.txt
```

Spusťte tyto příkazy jako uživatel `ok2zaw`, bez `sudo`. `sudo` patří k instalaci
systémových balíčků (`apt`) a ke správě služeb, ne k instalaci balíčků do `.venv`.
Skript `setup_rpi.sh` spuštěný jako root skončí s vysvětlením, aby nevytvořil
virtuální prostředí vlastněné rootem.

Pokud už instalace skončila chybou `Permission denied` uvnitř `.venv`, opravte
vlastníka prostředí jednorázově:

```bash
sudo chown -R ok2zaw:ok2zaw /home/ok2zaw/meshcom-gateway/.venv
```

Potom jako `ok2zaw` znovu aktivujte prostředí a nainstalujte závislosti:

```bash
cd /home/ok2zaw/meshcom-gateway
.venv/bin/python -m pip install -r requirements.txt
```

Pokud se při spuštění objeví `ModuleNotFoundError: No module named 'flask'`,
znovu nainstalujte závislosti do projektového prostředí a ověřte import:

```bash
cd /home/ok2zaw/meshcom-gateway
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
  "maildir_new": "/home/ok2zaw/Maildir/new",
  "maildir_cur": "/home/ok2zaw/Maildir/cur",
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

### Cílová skupina e-mailu a potvrzení doručení

E-mail se do MeshCom posílá do skupiny `mesh_group` z nastavení. Jinou skupinu
určíte značkou `GROUP: <číslo>` kdekoli v předmětu; značka se z textu zprávy
odstraní:

| Předmět e-mailu | Cíl | Text v MeshCom |
|---|---|---|
| `Schůzka` | skupina z nastavení | `Jan: Schůzka - …` |
| `GROUP: 262` | skupina 262 | `Jan: …` |
| `GROUP: 2620 - Porucha` | skupina 2620 | `Jan: Porucha - …` |
| `Re: GROUP: 9 Cvičení` | skupina 9 | `Jan: Re: Cvičení - …` |

Stav doručení každé zprávy odeslané z webu nebo e-mailu je na stránce
**Zprávy** (a u vlastních zpráv na stránce **Mesh zprávy**): *předáno uzlu* →
*uzel vysílá* (echo uzlu) → *slyšel soused* → *doručeno přes bránu* nebo
*potvrzeno příjemcem*; případně *drží schránka* nebo *nedoručeno*. Úplná
potvrzení posílá firmware 4.40a a novější; u starších verzí potvrzují jen
přímé zprávy (`ackNNN`). Zprávy do skupin příjemci nepotvrzují. Pro párování
musí být `callsign` v nastavení stejná značka, jakou má uzel.

## 7. Systémová služba (systemd)

Soubory v `systemd/` předpokládají projekt v `/home/ok2zaw/meshcom-gateway` a
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

Pokud projekt není v `/home/ok2zaw/meshcom-gateway`, nejprve upravte konstantu
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

V následujících příkazech nahraďte `/home/ok2zaw` cestou skutečného účtu a dosaďte
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
MAILUSER=ok2zaw
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
path = /home/ok2zaw/Maildir/

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
*/5 * * * * /usr/bin/getmail --getmaildir /home/ok2zaw/.getmail --rcfile getmailrc
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
/home/ok2zaw/meshcom-gateway/Maildir/new
/home/ok2zaw/meshcom-gateway/Maildir/cur
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

### 9.1 Napájení Raspberry Pi 5 a start bez tlačítka

Raspberry Pi 5 se při startu ptá zdroje přes USB-C PD, zda dodá 5 A. Pokud
zdroj 5 A neohlásí (běžné u jiných než oficiálních 27W zdrojů), omezí proud do
USB na 600 mA a při startu z USB disku bootloader **zastaví a čeká na stisk
tlačítka** – bez monitoru to vypadá, že Pi „nenaběhlo“.

Zjištění příčiny (přes SSH, po ručním startu):

```bash
for f in max_current usb_max_current_enable; do
  printf '%-24s %s\n' "$f" "$(od -An -tu4 --endian=big /proc/device-tree/chosen/power/$f | tr -d ' ')"
done
vcgencmd get_throttled
sudo rpi-eeprom-config
```

- `max_current` = `3000` (nebo méně) znamená, že zdroj 5 A neohlásil.
- `throttled=0x0` znamená, že nedošlo k podpětí – zdroj při dané zátěži stačí
  a kontrolu lze bezpečně obejít. Jiná hodnota (např. `0x50005`) znamená
  skutečné podpětí; pak je potřeba silnější zdroj (oficiální 27 W, 5,1 V/5 A).

Obejití kontroly – oba kroky udělejte **před** dalším restartem:

1. V `/boot/firmware/config.txt` v sekci `[all]`:

   ```text
   usb_max_current_enable=1
   ```

2. V konfiguraci bootloaderu (EEPROM):

   ```bash
   sudo rpi-eeprom-config > /tmp/boot.conf
   sed -i '/^PSU_MAX_CURRENT=/d' /tmp/boot.conf
   echo 'PSU_MAX_CURRENT=5000' >> /tmp/boot.conf
   cat /tmp/boot.conf
   sudo rpi-eeprom-config --apply /tmp/boot.conf
   sudo reboot
   ```

   Zápis EEPROM proběhne při restartu (může se restartovat dvakrát; během něj
   neodpojujte napájení). Hláška `SPI device /dev/spidev10.0 not found` je
   běžná. `--apply` zároveň aktualizuje bootloader na verzi ze systému.

Ověření po restartu:

```bash
sudo rpi-eeprom-config | grep PSU
od -An -tu4 --endian=big /proc/device-tree/chosen/power/max_current
vcgencmd get_throttled
```

Při vzdálené správě vždy používejte `sudo reboot`. Po `shutdown`/`poweroff`
zůstává Pi 5 vypnuté, dokud někdo nestiskne tlačítko nebo neodpojí napájení.
Pod zátěží (VARA HF, SSD, zvuková karta) občas zkontrolujte
`vcgencmd get_throttled`.

### 9.2 Trvalé ukládání logů

Výchozí journal na Raspberry Pi OS může být jen v RAM a po restartu se ztratí
(`journalctl -b -1` pak hlásí `no persistent journal was found`). Trvalé
ukládání zapnete:

```bash
sudo mkdir -p /var/log/journal
sudo systemctl restart systemd-journald
journalctl --list-boots
```

Logy předchozího běhu pak zobrazí `journalctl -b -1 -n 50`.

### 9.3 Diagnostika příjmu RF stanic

Stránka **RF stanice** ukládá jen rámce přijaté přes LoRa (`src_type` =
`lora`) typu `pos`, `msg` a `tele`, které neodeslal vlastní uzel. Pokud zůstává
prázdná:

1. Na stránce **Stav** zkontrolujte kartu *UDP příjem*:
   - `RF pozorování` – ukládání funguje,
   - `ignorován jiný typ` – chodí jen pakety z internetu (`udp`) nebo
     vlastního uzlu (`node`),
   - `neplatný JSON` – datagram nejde přečíst,
   - 0 datagramů – uzel nic neposílá; zkontrolujte na uzlu `--extudp on` a
     `--extudpip <IP-Raspberry-Pi>`.
2. Stránka **UDP monitor** ukazuje posledních 300 surových datagramů v obou
   směrech (uzel → Pi i Pi → uzel) s výsledkem zpracování, zda gateway na
   portu naslouchá, a IP adresu Pi, kterou je třeba zadat jako `--extudpip`.
   Pokud se gateway nepodaří port otevřít (špatné `listen_ip`, port obsazený
   jiným programem), zobrazí zde chybu a pokus každých 10 s opakuje.
   Podívejte se také přímo na síť:

   ```bash
   sudo tcpdump -A -n -c 20 udp port 1799
   ```

3. Ověřte obsah databáze:

   ```bash
   sqlite3 /home/ok2zaw/meshcom-gateway/gateway.db \
     "SELECT COUNT(*), datetime(MAX(observed_at),'unixepoch','localtime') FROM station_observations;"
   ```

### 9.4 Síťové porty a souběh s dalšími službami

| Port | Služba | Poznámka |
| --- | --- | --- |
| 1799/UDP | MeshCom ExtUDP | gateway ⇄ uzel MeshCom |
| 8899/TCP | web gateway | jen LAN/VPN |
| 25, 587, 993/TCP | Postfix, Dovecot | kapitola 8, jen LAN/VPN |
| 8300–8301/TCP | VARA (ARQ, data) | výchozí, kapitola 11 |
| 8400–8401/TCP | Mercury (ARQ, data) | `-p 8400`, aby nekolidoval s VARA |
| 10080/TCP | webové GUI Mercury | `-G -U`; bez přihlášení, jen LAN/VPN |
| 10001, 10002/TCP | LoRa TNC1, TNC2 (KISS) | jen `127.0.0.1` |
| 8008, 8010/TCP | LinBPQ web, telnet | jen LAN/VPN |
| 8025, 8110/TCP | BPQMail SMTP, POP3 | kapitola 12.6 |

Obsazené porty zjistíte příkazem `sudo ss -ltnup`. Pokud na Pi běží **Webmin**,
používá port `10000` – proto má webové GUI Mercury výchozí port `10080`.
Webmin lze případně přesunout jinam (*Webmin* → *Webmin Configuration* →
*Ports and Addresses*, nebo):

```bash
sudo sed -i 's/^port=10000$/port=10010/; s/^listen=10000$/listen=10010/' /etc/webmin/miniserv.conf
sudo systemctl restart webmin
```

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

## 11. VARA, Mercury a VarAC na Raspberry Pi (experimentální)

VARA (EA5HVK) i VarAC jsou programy pro Windows. Na Raspberry Pi se spouštějí
přes **Wine** a emulaci x86; tento postup používá instalátor Winelink s Box86.
Raspberry Pi 4 je doporučené, na Pi 3 počítejte s pomalým během. Na Pi 5 je
nutné přepnout jádro na 4KB stránky. Provoz je experimentální: počítejte s
vyšší zátěží CPU, občasnými pády GUI a nutností ladění zvuku a PTT. Mercury
běží samostatně nativně na ARM64 Linuxu. Gateway samotná (kapitoly 2–7) na
Wine nezávisí.

### 11.1 Instalace VARA a VarAC od nuly

#### 0. Příprava systému

- Raspberry Pi 4 (Pi 3 funguje, ale pomalu) s Raspberry Pi OS, ideálně 64bit.
- **Pi 5:** do `/boot/firmware/config.txt` přidejte `kernel=kernel8.img` a
  restartujte. Ověřte `getconf PAGESIZE`; výsledek musí být `4096`.
- Pracujte z grafické plochy nebo přes VNC, ne z čistého SSH. Příkazy spouštějte
  jako běžný uživatel, ne jako root.
- VARA a VarAC potřebují desktopové prostředí a zvukové rozhraní k rádiu.

#### 1. Úklid po neúspěšném pokusu s dotnet48

Proveďte pouze v případě, že instalace `dotnet48` už dříve selhala. Pokud
`~/.wine-old` existuje, nejdřív zvolte jiný název zálohy.

```bash
wineserver -k
mv ~/.wine ~/.wine-old
rm -rf ~/.cache/winetricks/dotnet48
```

#### 2. Winelink (Box86, Wine, wine-mono a VARA)

```bash
curl -O https://raw.githubusercontent.com/WheezyE/Winelink/main/install_winelink.sh
bash install_winelink.sh vara_only
```

Na konci instalace skript provede nastavení zvukové karty pro VARA.

#### 3. Fonty pro VarAC

```bash
sudo apt install ttf-mscorefonts-installer
```

Nainstalujte také font **Segoe UI Emoji** podle návodu
[segoe-ui-linux](https://github.com/SpudGunMan/segoe-ui-linux).

#### 4. VarAC

Stáhněte ZIP z <https://www.varac-hamradio.com/download> do `~/Downloads` a
rozbalte jej:

```bash
mkdir -p ~/.wine/drive_c/VarAC
unzip ~/Downloads/VarAC_*.zip -d ~/.wine/drive_c/VarAC
```

#### 5. První spuštění a Linux mód

Spusťte VarAC:

```bash
env BOX86_DYNAREC_BIGBLOCK=0 WINEDEBUG=-all wine ~/.wine/drive_c/VarAC/VarAC.exe
```

Vyplňte volací znak a lokátor a aplikaci zavřete. Potom zapněte kompatibilní
režim pro Linux:

```bash
sed -i 's/LinuxCompatibleMode=OFF/LinuxCompatibleMode=ON/' ~/.wine/drive_c/VarAC/VarAC.ini
```

Nastavení lze změnit také v GUI: *Settings* → *Rig control & Vara
configuration* → *Linux compatible mode*. Pokud `VarAC.ini` v této složce
nevznikne, hledejte jej v `~/VarAC.ini`. Někdy je potřeba aplikaci několikrát
restartovat, než nastavení uloží.

#### 6. Červené křížky v GUI

Pokud GUI zobrazuje červené křížky místo ikon, spusťte:

```bash
BOX86_DYNAREC=0 winetricks -q gdiplus corefonts
```

#### 7. Zástupce v menu

```bash
cat << EOF | sudo tee /usr/share/applications/VarAC.desktop
[Desktop Entry]
Name=VarAC Chat
Exec=env BOX86_DYNAREC_BIGBLOCK=0 WINEDEBUG=-all wine $HOME/.wine/drive_c/VarAC/VarAC.exe
Type=Application
Categories=HamRadio;
EOF
```

### 11.2 Volitelné nastavení sériového CAT/PTT

Wine mapuje linuxová sériová zařízení na COM porty v adresáři `dosdevices`.
Přidejte uživatele do skupiny `dialout` a vytvořte odkaz (příklad pro
`/dev/ttyUSB0` jako `COM5`):

```bash
sudo usermod -aG dialout ok2zaw
ls -l /dev/serial/by-id/
ln -sf /dev/serial/by-id/<ID-vaseho-zarizeni> ~/.wine/dosdevices/com5
```

Odkaz na `/dev/serial/by-id/...` je stabilní i po přepojení USB, na rozdíl od
`/dev/ttyUSB0`. Po přidání do skupiny `dialout` se odhlaste a znovu přihlaste.
Pro rádia s VOX nebo Digirig s PTT přes zvukovou kartu tento krok není nutný.

### 11.3 Když instalace nefunguje

- **Winelink skončí chybou** (často na systému novějším než Bullseye nebo na
  Pi 5): nainstalujte Pi-Apps příkazem
  `wget -qO- https://raw.githubusercontent.com/Botspot/pi-apps/master/install | bash`.
  V Pi-Apps zvolte *Tools* → *Emulation* → *Box86*; instalace Box86 nainstaluje
  i Wine. Potom ve staženém instalačním adresáři VARA spusťte:

  ```bash
  cd ~/Downloads
  winetricks -q comdlg32ocx pdh_nt4 vb6run
  wine "VARA setup (Run as Administrator).exe"
  cp ~/.wine/drive_c/VARA/OCX/* ~/.wine/drive_c/VARA/
  ```

  Po instalaci VARA pokračujte krokem 3 výše. Pokud se název instalačního EXE
  liší, použijte skutečný název staženého souboru.
- **VarAC se vůbec neotevře:** příčinou bývá příliš stará verze Wine (Winelink
  používá Wine 7.1); cesta přes Pi-Apps instaluje novější verzi.
- **Systém zamrzne:** z jiného počítače se připojte přes SSH a spusťte
  `wineserver -k`.
- **Červené křížky v GUI:** použijte příkaz z kroku 6.
- **Nedoinstalovávejte `dotnet48`:** VarAC funguje s wine-mono.

### 11.4 Instalace modemu Mercury (volitelně)

[Mercury](https://github.com/Rhizomatica/mercury) je open-source HF modem
projektu HERMES (Rhizomatica). Běží nativně na ARM64 Linuxu bez Wine a bez
grafického prostředí a nabízí TCP rozhraní kompatibilní s VARA (ARQ port
`8300`, data `8301`). Balíčky jsou připravené pro Debian 13 Trixie / aktuální
Raspberry Pi OS arm64:

Server s klíčem nemá platný HTTPS certifikát, proto klíč stáhněte bez kontroly
certifikátu a **před instalací ověřte jeho otisk**:

```bash
wget --no-check-certificate -O /tmp/hermes.key https://debian.hermes.radio/hermes/hermes.key
gpg --show-keys --with-fingerprint /tmp/hermes.key
```

Otisk musí být `EEE8 F00A D242 EC55 9266  7F75 EA13 67BE 5DD1 91BB`. Pokud
nesouhlasí, nepokračujte a použijte překlad ze zdrojů níže. Při shodě klíč
nainstalujte jen pro tento repozitář (`signed-by`):

```bash
sudo install -d -m 755 /etc/apt/keyrings
gpg --dearmor < /tmp/hermes.key | sudo tee /etc/apt/keyrings/hermes.gpg > /dev/null
echo 'deb [arch=arm64 signed-by=/etc/apt/keyrings/hermes.gpg] http://debian.hermes.radio/hermes trixie main' \
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

**Webové GUI Mercury:** s přepínačem `-G` Mercury sám servíruje webové
rozhraní se spektrem, vodopádem, stavem spojení a nastavením rádia a zvuku –
na Pi není potřeba nic doinstalovat ani mít desktop. Na záložce **VarAC**
zaškrtněte *Webové GUI Mercury* (výchozí port `10080`), uložte a Mercury
restartujte; na kartě Mercury se objeví odkazy **Otevřít GUI**
(`http://<IP-Pi>:10080/mercury.html`) a **Vodopád** (`waterfall.html`).
Samotný Mercury má výchozí port GUI `10000`; aplikace ho nastavuje na `10080`,
protože port 10000 používá Webmin (viz kapitola 9.4).
Vodopád a spektrum zatěžují CPU; volba *Bez vodopádu* (`-W`) je vypne.

> **Bezpečnost:** GUI Mercury nemá přihlášení – kdokoli, kdo se dostane na
> jeho port, může modem ovládat včetně vysílání. Port 10080 zpřístupněte jen
> v LAN/VPN, nikdy ho nepřesměrovávejte z internetu.

Grafická aplikace `mercury-ui` (Fyne) obsahuje vlastní kopii modemu, proto ji
nespouštějte současně s Mercury ze záložky VarAC. Pro vzdálený provoz na Pi je
webové GUI vhodnější.

### 11.5 Ovládání z webu – záložka VarAC

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
   sudo loginctl enable-linger ok2zaw
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

> **Bezpečnost:** Záložka spouští programy pod účtem `ok2zaw`. Web proto
> zpřístupněte jen v důvěryhodné síti se silným heslem. Spustit lze pouze
> soubory `.exe` přes Wine a program `mercury`.

### 11.6 Řešení problémů

Zdroje:

- [Winelink (WheezyE/KI7POL)](https://github.com/WheezyE/Winelink/)
- [Installing VarAC on Linux/raspberry64/Mac using WINE](https://www.varac-hamradio.com/post/installing-varac-on-linux-raspberry64-using-wine)


| Problém | Řešení |
| --- | --- |
| Wine nespustí nic, chyby paměti / `mmap` | Na Pi 5 ověřte `getconf PAGESIZE` = `4096` (krok 0); případně použijte cestu přes Pi-Apps |
| Systém zamrzne, okna nereagují | Z jiného počítače spusťte `wineserver -k`; vypněte vodopád ve VARA |
| VARA nevidí zvukovou kartu | Znovu spusťte nastavení zvuku Winelink; zkontrolujte `arecord -l` a `aplay -l` |
| VarAC hlásí chybějící .NET | Neinstalujte `dotnet48`; ověřte instalaci wine-mono nebo použijte čistý prefix |
| PTT nefunguje | Ověřte skupinu `dialout`, odkaz v `~/.wine/dosdevices/` a číslo COM portu ve VarAC |
| Web hlásí „Uživatelský systemd neběží“ | `sudo loginctl enable-linger ok2zaw`, případně přihlášení do desktopu |
| Program na záložce VarAC hned skončí (Chyba) | rozbalte jeho výpis na záložce, nebo `journalctl --user -u meshcom-radio-<program>` |
| Okno VARA/VarAC se neobjeví | zkontrolujte přihlášený desktop a hodnotu Displej (`echo $DISPLAY` v terminálu desktopu) |
| VarAC se nepřipojí k Mercury | port ve VarAC musí odpovídat `-p` v parametrech Mercury |
| GUI Mercury se nenačte | Mercury musí běžet se zapnutým webovým GUI (po změně restart); `ss -ltn \| grep 10080`; port nesmí používat jiná služba (Webmin) |
| Vysoká teplota, nízký výkon | `vcgencmd measure_temp`, `vcgencmd get_throttled`; použijte aktivní chladič |

Zátěž lze sledovat příkazem `htop`. Trvale přes 90 % CPU při příjmu znamená,
že je potřeba vypnout vodopád, snížit šířku pásma VARA nebo zavřít jiné
programy na desktopu.

> **Poznámka:** Gateway (kapitoly 2–7) zatím nepředává zprávy mezi MeshCom a
> VarAC; záložka VarAC slouží jen ke spouštění a sledování programů.

## 12. LoRa TNC a LinBPQ s BBS a e-mailovou bránou (experimentální)

Tato kapitola přidává na Raspberry Pi packet radio uzel **LinBPQ** (G8BPQ) s
BBS a jako rádiové porty **dva LoRa KISS TNC (TNC1 a TNC2)** s moduly SX127x
(RFM96/RFM98) na SPI, například na desce OK2ZAW se dvěma moduly. TNC je
upravená kopie projektu
[IZ7BOJ/RPi-LoRa-KISS-TNC-2ndgen, větev Lora-Packet](https://github.com/IZ7BOJ/RPi-LoRa-KISS-TNC-2ndgen/tree/Lora-Packet)
ve složce [`lora_tnc/`](lora_tnc/README.md) tohoto projektu (seznam úprav je
v jejím README). Instaluje se tedy spolu s gateway a nic dalšího se pro TNC
nestahuje.

BBS je napojena na místní Postfix z kapitoly 8:

- uživatelé BBS posílají e-maily na internetové adresy – BPQMail je předá
  Postfixu, ten přes relay vlastního serveru,
- pošta pro uživatele BBS se z Postfixu doručí do SMTP serveru BPQMail,
- volitelně se kopie pošty z místní schránky (Maildir) doručuje i do BBS
  (krok 12.7).

LinBPQ neumí číst Maildir přímo – má vlastní úložiště zpráv. Propojení proto
vede přes SMTP. Postup předpokládá hotovou kapitolu 8 (Postfix s relayem).

```text
LoRa modul 1 ⇄ TNC1 (KISS TCP 127.0.0.1:10001) ⇄ ┐
LoRa modul 2 ⇄ TNC2 (KISS TCP 127.0.0.1:10002) ⇄ ┴ LinBPQ + BBS
                                                      ⇅ SMTP
            internet ⇄ relay vlastního serveru ⇄ Postfix ⇄ Maildir
```

### 12.1 Zapnutí SPI a závislosti TNC

```bash
sudo raspi-config nonint do_spi 0
sudo apt install -y python3 python3-spidev python3-rpi-lgpio
sudo usermod -aG spi,gpio ok2zaw
sudo reboot
```

Po restartu ověřte SPI zařízení (dvě hodnoty chip select = dva moduly na
sběrnici 0):

```bash
ls /dev/spidev0.*
```

> **Raspberry Pi 5:** knihovna `RPi.GPIO` (balík `python3-rpi.gpio`) na Pi 5
> nefunguje. `python3-rpi-lgpio` je náhrada se stejným rozhraním; oba balíčky
> nelze mít nainstalované současně. Pokud je starý balík nainstalovaný, apt
> ho při instalaci `python3-rpi-lgpio` odebere.

Knihovny pro OLED displej (`adafruit-circuitpython-ssd1306`) nejsou potřeba;
displej je v kopii TNC vypnutý.

### 12.2 Nastavení TNC1 a TNC2 ve webu

Na záložce **BPQ** webu gateway nastavte každý TNC zvlášť:

| Položka | TNC1 | TNC2 | Poznámka |
| --- | --- | --- | --- |
| KISS TCP adresa | `127.0.0.1` | `127.0.0.1` | jen pro LinBPQ na tomto Pi |
| KISS TCP port | `10001` | `10002` | musí se lišit |
| IRQ pin (DIO0, BCM) | `5` | `19` | deska OK2ZAW |
| SPI sběrnice / chip select | `0` / `0` | `0` / `1` | podle zapojení desky |
| Kmitočet, SF, šířka pásma, CR, sync word | podle sítě | podle sítě | shodné s protistanicemi |

1. Zaškrtněte **TNC je osazen** u každého použitého modulu a vyplňte
   **kmitočet** – bez něj web službu nevytvoří (ochrana před vysíláním na
   nechtěném kmitočtu).
2. Volitelně zaškrtněte start po spuštění Pi a zpoždění.
3. Uložte. Web zapíše nastavení do `~/.config/meshcom/tnc1.json` a
   `tnc2.json` a vytvoří služby `meshcom-tnc1` a `meshcom-tnc2`.
4. Tlačítkem **Spustit** spusťte TNC a ve výpisu ověřte, že poslouchá:

   ```bash
   ss -ltn | grep -E '1000[12]'
   journalctl --user -u meshcom-tnc1 -n 30
   ```

Důležité:

- Kopie TNC vždy používá standardní AX.25 rámce a nepřidává k nim signálový
  report (`appendSignalReport = False`) – jinak by se rozbily spojené relace.
- Kmitočet volte podle povolení a band plánu. Nepoužívejte kmitočet LoRa APRS
  (433,775 MHz) ani MeshCom (433,175 MHz). Pokud oba TNC pracují v jednom
  pásmu, volte dostatečný odstup kmitočtů, aby se moduly navzájem nerušily.
- Při SF12/BW125 je přenosová rychlost jen kolem 300 bit/s – BBS je vhodná pro
  krátké textové zprávy.
- Výkon `0–15` odpovídá 2–17 dBm (modul SX127x s PA_BOOST).

### 12.3 Instalace LinBPQ

Použijte oficiální instalační skript autora (G8BPQ), který stáhne verzi pro
Raspberry Pi do `/home/ok2zaw/linbpq`. Spusťte jako `ok2zaw`:

```bash
cd /home/ok2zaw
wget https://www.cantab.net/users/john.wiseman/Downloads/installLinBPQ
less installLinBPQ        # projděte, co skript dělá
chmod +x installLinBPQ
./installLinBPQ
ls -l /home/ok2zaw/linbpq/
```

Pokud chybí knihovna `libconfig`, doinstalujte ji (`sudo apt install libconfig9`
nebo `libconfig11` podle verze systému). Pokud skript nebo nástroj
`bpq-config` vytvořil systémovou službu `bpq.service`, vypněte ji – LinBPQ
bude spouštět záložka BPQ a dvě instance by se praly o porty:

```bash
sudo systemctl disable --now bpq.service 2>/dev/null || true
```

### 12.4 Základní konfigurace `bpq32.cfg`

Vytvořte `/home/ok2zaw/linbpq/bpq32.cfg` (volací znaky, lokátor a hesla upravte).
Každý TNC je v LinBPQ samostatný port s vlastním TCP portem:

```text
SIMPLE
NODECALL=OK2ZAW-7
NODEALIAS=ZAWNOD
LOCATOR=JN89AA
INFOMSG:
OK2ZAW-7 LinBPQ uzel s BBS pres LoRa
***

PORT
 PORTNUM=1
 ID=Telnet a web
 DRIVER=TELNET
 CONFIG
  LOGGINGLEVEL=1
  HTTPPORT=8008
  TCPPORT=8010
  MAXSESSIONS=10
  USER=sysop,SILNE_HESLO,OK2ZAW,,SYSOP
ENDPORT

PORT
 PORTNUM=2
 ID=LoRa TNC1
 TYPE=ASYNC
 PROTOCOL=KISS
 IPADDR=127.0.0.1
 TCPPORT=10001
 CHANNEL=A
 PACLEN=128
 MAXFRAME=1
 FRACK=20000
 RESPTIME=1000
 RETRIES=10
ENDPORT

PORT
 PORTNUM=3
 ID=LoRa TNC2
 TYPE=ASYNC
 PROTOCOL=KISS
 IPADDR=127.0.0.1
 TCPPORT=10002
 CHANNEL=A
 PACLEN=128
 MAXFRAME=1
 FRACK=20000
 RESPTIME=1000
 RETRIES=10
ENDPORT

APPLICATION 1,BBS,,OK2ZAW-1,ZAWBBS,255
LINMAIL
```

- `IPADDR` a `TCPPORT` místo `COMPORT` připojí KISS port přes TCP k TNC;
  `TCPPORT` musí odpovídat portu TNC1/TNC2 na záložce BPQ.
- TNC přijímá vždy jen jedno TCP spojení – k jednomu TNC se smí připojit jen
  LinBPQ, ne současně další program (aprx, Direwolf).
- `FRACK=20000` (ms) a `MAXFRAME=1` odpovídají pomalému LoRa kanálu: krátký
  rámec trvá při SF12/BW125 asi 1,3 s, rámec 128 bajtů přes 4 s. `FRACK` je
  čas do opakování nepotvrzeného rámce – každá ztráta proto zdrží relaci o
  tuto dobu.
- `RESPTIME=1000` (ms) je čekání před odesláním potvrzení. Vyšší hodnota
  (např. 3000) přidává zpoždění ke každé výměně rámců.
- `LINMAIL` zapne poštovní server (BBS); `APPLICATION` zpřístupní BBS pod
  volacím znakem `OK2ZAW-1`.
- Web LinBPQ (8008) a telnet (8010) zpřístupněte jen v LAN/VPN.

Vyzkoušejte ruční spuštění (nejdřív musí běžet TNC z kroku 12.2), otevřete
`http://<IP-Raspberry-Pi>:8008/` a pak LinBPQ ukončete Ctrl+C:

```bash
cd /home/ok2zaw/linbpq && ./linbpq
```

### 12.5 Spouštění ze záložky BPQ

Záložka **BPQ** ve webu gateway spouští, zastavuje a restartuje TNC1, TNC2 a
LinBPQ, zobrazuje jejich stav a výpis a nastavuje start po spuštění Raspberry
Pi se zpožděním. LinBPQ se vždy spouští až po obou TNC. Na rozdíl od VARA a
VarAC nepotřebují přihlášený desktop.

1. Povolte uživatelský systemd bez přihlášení (stačí jednou, viz i 11.5):

   ```bash
   sudo loginctl enable-linger ok2zaw
   ```

2. Na záložce **BPQ** zkontrolujte cestu k LinBPQ (`/home/ok2zaw/linbpq/linbpq`),
   zaškrtněte automatický start, nastavte zpoždění (např. TNC 0 s,
   LinBPQ 15 s) a uložte.
3. Tlačítkem **Spustit** spusťte TNC a potom LinBPQ. Po změně nastavení
   běžící služby restartujte tlačítkem **Restart**.

Ruční kontrola:

```bash
systemctl --user status meshcom-tnc1 meshcom-tnc2 meshcom-linbpq
journalctl --user -u meshcom-linbpq -n 50
```

Pokud jste dříve podle starší verze návodu vytvořili službu
`meshcom-lora-tnc` nebo složku `~/RPi-LoRa-KISS-TNC`, odstraňte je:

```bash
systemctl --user disable --now meshcom-lora-tnc 2>/dev/null || true
rm -f ~/.config/systemd/user/meshcom-lora-tnc.service
systemctl --user daemon-reload
```

### 12.6 BBS a e-mailová brána přes Postfix

Nastavení pošty se dělá ve webu LinBPQ (`http://<IP-Pi>:8008/` → *Mail Mgmt*
→ *Configuration*). Názvy položek se mohou mezi verzemi LinBPQ mírně lišit.

1. **SMTP a POP3 server BPQMail:** nastavte SMTP port `8025` a POP3 port `8110`
   (port 25 už používá Postfix). Vzdálený přístup (*Enable Remote Access*)
   nechte vypnutý – Postfix se připojuje z `127.0.0.1`.
2. **Doména BBS:** jako vlastní doménu BBS (*My Domain*) nastavte např.
   `bbs.lan`. Uživatelé BBS pak mají e-mailové adresy `<znacka>@bbs.lan`.
3. **Odchozí pošta na internet (ISP Gateway):** zapněte bránu, jako SMTP
   server zadejte `127.0.0.1`, port `25`, bez přihlášení. Postfix ji předá
   dál přes relay z kapitoly 8. Pokud verze vyžaduje POP3 účet poskytovatele,
   ponechte příjem přes POP3 vypnutý – příchozí poštu doručuje Postfix.
4. **Uživatelé:** v *Mail Mgmt* → *Users* povolte uživatelům odesílání na
   internet, pokud to vaše verze vyžaduje, a nastavte jim heslo (slouží i pro
   přístup mailovým klientem přes POP3/SMTP BPQMail).

Postfixu řekněte, že doména `bbs.lan` se doručuje do BPQMail:

```bash
echo 'bbs.lan smtp:[127.0.0.1]:8025' | sudo tee /etc/postfix/transport
sudo postmap /etc/postfix/transport
sudo postconf -e 'transport_maps = hash:/etc/postfix/transport'
sudo postconf -e 'relay_domains = bbs.lan'
```

Kapitola 8 vyžaduje TLS pro všechna odchozí SMTP spojení
(`smtp_tls_security_level = encrypt`), BPQMail ale TLS nepodporuje. Výjimku
nastavte jen pro místní spojení do BPQ; relay na veřejný server zůstává
šifrovaný:

```bash
echo '[127.0.0.1]:8025 none' | sudo tee /etc/postfix/tls_policy
sudo postmap /etc/postfix/tls_policy
sudo postconf -e 'smtp_tls_policy_maps = hash:/etc/postfix/tls_policy'
sudo systemctl reload postfix
```

Otestujte doručení z Pi do BBS a odeslání z BBS ven:

```bash
printf 'Subject: Test do BBS\n\nZprava z Postfixu.\n' | sendmail ok2zaw@bbs.lan
journalctl -u postfix -n 20
```

Zpráva se má objevit v BBS (`L` / `R <číslo>` po připojení k BBS). Pak
z BBS pošlete zprávu `SP vas.ucet@example.com` a ověřte, že ji Postfix
předal relayi. Odpovědi z internetu na adresu `@bbs.lan` přímo nedojdou –
Pi není veřejný mailserver; příchozí poštu do BBS řeší krok 12.7.

### 12.7 Volitelně: kopie místní schránky do BBS

Pokud mají e-maily stažené getmail6 do místní schránky (kapitola 8) být
dostupné i na BBS, nechte getmail doručovat přes Postfix a ten je zkopíruje
uživateli BBS:

1. Postfix bude doručovat místní poštu do `~/Maildir/` (stejný Maildir, který
   používá Dovecot):

   ```bash
   sudo postconf -e 'home_mailbox = Maildir/'
   ```

2. Do `/etc/aliases` přidejte řádek (zpětné lomítko zabrání smyčce):

   ```text
   ok2zaw: \ok2zaw, ok2zaw@bbs.lan
   ```

   ```bash
   sudo newaliases
   sudo systemctl reload postfix
   ```

3. V `~/.getmail/getmailrc` nahraďte sekci `[destination]`:

   ```ini
   [destination]
   type = MDA_external
   path = /usr/sbin/sendmail
   arguments = ("-i", "-f", "%(sender)", "ok2zaw")
   ```

Každá stažená zpráva pak přistane v místní schránce i v BBS u uživatele
`OK2ZAW`. Dlouhé zprávy a přílohy přes LoRa nečtěte – kanál je velmi pomalý.

### 12.8 Řešení problémů

| Problém | Řešení |
| --- | --- |
| TNC hlásí chybu GPIO / `RuntimeError` | na Pi 5 musí být `python3-rpi-lgpio`, ne `python3-rpi.gpio`; zkontrolujte IRQ pin (TNC1 = 5, TNC2 = 19) a členství ve skupinách `spi`, `gpio` |
| `/dev/spidev0.0` neexistuje | `sudo raspi-config nonint do_spi 0` a restart |
| LinBPQ se nepřipojí k TNC | `ss -ltn \| grep -E '1000[12]'`; TNC musí běžet dřív než LinBPQ a `TCPPORT` v `bpq32.cfg` musí odpovídat portu TNC |
| TNC nepřijímá nic, druhý funguje | zkontrolujte chip select (TNC1 = 0, TNC2 = 1) a IRQ pin; oba TNC nesmí mít stejné hodnoty |
| Web nevytvoří službu TNC | vyplňte kmitočet a zkontrolujte hlášení po uložení |
| Spojení přes LoRa se rozpadá | zvyšte `FRACK`, snižte `PACLEN`, `MAXFRAME=1`; ověřte stejné LoRa parametry protistanice |
| Pošta do BBS nedojde | `journalctl -u postfix`; SMTP port BPQMail (`ss -ltn \| grep 8025`), doména v `transport` |
| Záložka BPQ hlásí, že služba není vytvořená | povolte TNC (TNC je osazen) a uložte nastavení na záložce BPQ |
