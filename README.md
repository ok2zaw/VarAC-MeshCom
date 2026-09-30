# MeshCom Gateway

Jednoduchý Python gateway mezi MeshCom a e-mailem.

## Funkce

- příjem MeshCom UDP zpráv a odeslání přes SMTP
- kontrola nových e-mailů v Maildir/new
- přeposílání e-mailů do MeshCom skupiny
- rozdělení dlouhých e-mailů na více MeshCom zpráv
- ignorování příloh v obsahu zprávy, pouze název a typ přílohy
- webové rozhraní pro status, historii a konfiguraci
- SQLite databáze pro záznam zpráv a heartbeat

## Struktura projektu

- `gateway.py` – daemon pro MeshCom a Maildir
- `webapp.py` – Flask web UI
- `common.py` – konfigurace a database helper
- `config.json` – nastavení projektu
- `templates/` – HTML šablony
- `static/` – CSS
- `systemd/` – service soubory pro Linux

## Instalace

Podrobný návod pro Raspberry Pi / Debian je v souboru [INSTALL.md](INSTALL.md).

Základní kroky:

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

## Spuštění

```bash
python3 gateway.py
```

Pro web rozhraní:

```bash
python3 webapp.py
```

Web běží na portu `8899`.

## Konfigurace

Upravte soubor `config.json` podle vaší sítě a SMTP/Maildir nastavení.

Pro produkční nasazení je vhodné používat i soubor `.env` nebo proměnné prostředí. Projekt podporuje prefixed proměnné typu `MESHCOM_SMTP_HOST`, `MESHCOM_WEB_PASSWORD`, `MESHCOM_IMAP_HOST`, atd. Příklad najdete v `.env.example`.

## Poznámka

Tento projekt je navržen jako gateway, ne jako veřejný mailserver. Pro produkční nasazení je doporučené:
- používat externí SMTP/IMAP backend
- neprovozovat veřejný MTA na Raspberry Pi
- uložit hesla do zabezpečeného úložiště nebo environment proměnných
- nastavit Maildir/cur pro archivaci zpracovaných e-mailů

Volitelně lze přidat lokální Postfix/Dovecot/Roundcube, ale pro MeshCom bridge je jednodušší a bezpečnější externí mailová infrastruktura.

