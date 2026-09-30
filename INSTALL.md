# Instalace a provoz MeshCom Gateway na Raspberry Pi / Debian

Tento projekt je navržen jako hranolový gateway mezi MeshCom a e-mailem. Důraz je kladen na jednoduchou a bezpečnou architekturu:

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
cd /opt
sudo git clone https://github.com/ok2zaw/VarAC-MeshCom.git meshcom-gateway
cd meshcom-gateway
```

## 4. Virtuální prostředí

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt
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
source .venv/bin/activate
python3 gateway.py
```

Webové rozhraní:

```bash
source .venv/bin/activate
python3 webapp.py
```

Web běží na portu 8899. Přístup je chráněn podle nastavení `web_username` a `web_password` v `config.json`.

## 7. Systémová služba (systemd)

V adresáři `systemd/` jsou připravené service soubory. Jsou nastavené tak, aby načítaly `.env` z adresáře projektu přes `EnvironmentFile`. Upravte je podle vaší cesty a potom je aktivujte:

```bash
sudo cp systemd/meshcom-gateway.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable meshcom-gateway
sudo systemctl start meshcom-gateway
```

Pro webové rozhraní lze použít podobný service soubor nebo spouštět `webapp.py` přímo.

## 8. Varianta s externím SMTP a IMAP

Toto je doporučená produkční varianta:

- **SMTP**: přístup ke službě typu Mailgun, Postfix na VPS, nebo firmovým mailovými serverem
- **IMAP**: příchozí pošta se čte z externího účtu a ukládá do Maildir fronty
- **Raspberry Pi**: pouze hub / gateway, ne veřejný mailserver

Tímto se zabrání tomu, aby Raspberry Pi bylo vystaveno veřejnému SMTP a IMAP provozu.

## 9. Volitelně: lokální mailserver (Postfix + Dovecot + Roundcube)

Pokud chcete mít na Raspberry Pi i celý lokální mail stack, je to možné, ale není to doporučený základ pro tento projekt.

### Postfix

```bash
sudo apt install -y postfix dovecot-core dovecot-imapd dovecot-pop3d
```

Nastavte:

- hostname a FQDN pro Raspberry Pi
- přímý SMTP relay nebo lokální přeposlání do externího SMTP
- Maildir pro uživatele

### Roundcube

```bash
sudo apt install -y apache2 php php-intl php-mbstring php-xml php-json
sudo apt install -y roundcube roundcube-core
```

Roundcube je vhodné pro webový přístup ke schránkám, ale pro MeshCom gateway není nutné. Pro tuto síťovou aplikaci stačí jednoduché IMAP připojení a Maildir fronta.

## 10. Tipy pro provoz

- použijte měkký restart po změně `config.json`
- nastavte přístup k webu přes lokální síť, ne z internetu bez zabezpečení
- neukládejte hesla do Gitu
- v produkci používejte `systemd` a logování do journald
- pravidelně ověřujte přístup k SMTP/IMAP a stav heartbeat na webu

## 11. Rozdíl mezi externím a lokálním mailserverem

### Externí SMTP/IMAP (doporučeno)

Výhody:

- menší riziko zneužití Raspberry Pi jako veřejného mailserveru
- jednodušší správa certifikátů, relayingu a zabezpečení
- nižší provozní náročnost

### Lokální Postfix / Dovecot / Roundcube

Výhody:

- plná správa pošty lokálně
- možnost plného webového klienta do mailové schránky

Nevýhody:

- větší provozní náročnost
- složitější zabezpečení a aktualizace
- nutnost řešit certifikáty, relaying a správu účtů

Pro tento projekt je výhodnější mít gateway jako jednoduchý bridge a ne plný MTA.
