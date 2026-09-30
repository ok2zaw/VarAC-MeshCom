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

## Poznámka

Pro produkční nasazení je vhodné:
- používat zabezpečené SMTP a HTTPS
- uložit hesla do prostředí nebo zabezpečeného úložiště
- nastavit Maildir/cur pro archivaci zpracovaných e-mailů

