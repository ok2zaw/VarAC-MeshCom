# MeshCom Gateway

Pythonová obousměrná gateway mezi MeshCom a e-mailem. Aktuální implementace
komunikuje s MeshCom uzlem přes UDP/JSON; podporu VarAC ani VARA zatím nemá.
Přehled hotových funkcí a další práce je v [ROADMAP.md](ROADMAP.md), instalace
na Raspberry Pi/Debian v [INSTALL.md](INSTALL.md).

## Co projekt umí

- Přijímat zprávy z nakonfigurované MeshCom skupiny a odesílat je přes SMTP.
- Načítat e-maily z `Maildir/new`; volitelně stahovat nepřečtené zprávy přes IMAP.
- Převádět e-maily na MeshCom zprávy, dlouhé texty rozdělit podle limitu a
	uvést názvy příloh bez odesílání jejich obsahu.
- Archivovat úspěšně zpracované e-maily do `Maildir/cur`.
- Zobrazit stav gateway, historii a konfiguraci ve webovém rozhraní.
- Sledovat počet UDP datagramů RX/TX, čas a protistranu poslední události a
	poslední výsledek; stavová stránka se obnovuje každých 10 sekund.
- Odeslat z webové stavové stránky provozní zprávu do nakonfigurované MeshCom
	skupiny; délka se kontroluje a pokus se zaznamená do historie.
- Ukládat historii, chyby a heartbeat do SQLite.

## Konfigurace

Konfigurace se skládá z výchozích hodnot, `config.json`, volitelného `.env` a
proměnných prostředí `MESHCOM_*`; při konfliktu mají přednost proměnné prostředí.
Příklad proměnných je v [`.env.example`](.env.example). Formulář webu ukládá do
`config.json`, takže hodnoty přepsané v `.env` nebo prostředí se tím nezmění.

Webové rozhraní používá HTTP Basic Auth, pokud je nastavené webové heslo. V
produkci jej zpřístupněte pouze v důvěryhodné síti nebo za odpovídající HTTPS
proxy a hesla neukládejte do Gitu.

## Spuštění

Podrobné kroky instalace a nasazení přes systemd jsou v [INSTALL.md](INSTALL.md).
Pro ruční spuštění po instalaci závislostí:

```bash
python3 gateway.py
python3 webapp.py
```

Webové rozhraní poslouchá na portu `8899`. V produkci se používá systemd a
Waitress; obě služby se po chybovém ukončení automaticky spouštějí znovu.

## Struktura

- `gateway.py` – UDP, SMTP, Maildir a volitelné IMAP zpracování
- `webapp.py` – Flask webové rozhraní a validace webového formuláře
- `common.py` – konfigurace, SQLite, Maildir a heartbeat
- `templates/`, `static/` – šablony a styly webu
- `systemd/` – produkční jednotky pro gateway a web

