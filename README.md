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
- Prohlížet RF stanice slyšené přes LoRa, jejich historii s filtrem data od/do,
	RSSI/SNR, polohu, baterii, relay cestu a dostupnou senzorovou telemetrii.
	Historie se začne plnit až po zapnutí ExtUDP a nasazení této verze. Na uzlu
	musí být ExtUDP zapnuté (`--extudp on`) a cílová IP nastavená na Raspberry Pi
	(`--extudpip <IP-Raspberry-Pi>`). Formát paketů popisuje [MeshCom ExtUDP
	reference](https://docs.aprscaching.org/reference/meshcom-extudp/).
- Zobrazit oddělenou časově a skupinově filtrovatelnou tabulku textových zpráv
	zaslechnutých přes LoRa pro cíle mimo hlavní MeshCom skupinu. Text se ukládá
	lokálně do SQLite bez automatického mazání.
- Odeslat z webové stavové stránky provozní zprávu do nakonfigurované MeshCom
	skupiny; délka se kontroluje a pokus se zaznamená do historie.
- Zkontrolovat novou verzi aplikace a bezpečně aktualizovat `origin/main` z UI;
  update je fast-forward-only, chrání místní změny a vyžaduje síťové připojení.
- Ukládat historii, chyby a heartbeat do SQLite.

## Konfigurace

Konfigurace se skládá z výchozích hodnot, `config.json`, volitelného `.env` a
proměnných prostředí `MESHCOM_*`; při konfliktu mají přednost proměnné prostředí.
Příklad proměnných je v [`.env.example`](.env.example). Formulář webu ukládá do
`config.json`, takže hodnoty přepsané v `.env` nebo prostředí se tím nezmění.

Webové rozhraní používá HTTP Basic Auth, pokud je nastavené webové heslo. V
produkci jej zpřístupněte pouze v důvěryhodné síti nebo za odpovídající HTTPS
proxy a hesla neukládejte do Gitu.

Aplikace má verzi v souboru `VERSION` (začíná na `0.1`). Stránka Aktualizace
zobrazuje verzi i commit a umí aplikovat pouze bezpečný fast-forward z
`origin/main`; systémové balíčky a poštovní služby aktualizuje správce zvlášť.

## Volitelný lokální poštovní uzel

Raspberry Pi lze rozšířit o Postfix a Dovecot jako soukromý poštovní uzel pro
programy na Pi a mailové klienty v LAN/VPN. Postfix přijímá místní poštu a
odesílá ji přes SMTP server vlastní domény; při výpadku internetu ji drží ve
frontě. Dovecot zpřístupňuje společnou místní schránku přes IMAPS. Příchozí
poštu lze při dostupném internetu stahovat z veřejné schránky přes IMAP a
doručovat do lokální schránky.

Raspberry nepotřebuje veřejnou IP a poštovní porty se nemají přesměrovávat z
internetu. Místní IMAPS a SMTP submission zpřístupněte jen v LAN/VPN; router
zajišťuje vzdálenou VPN. Bez internetu zůstává místní pošta dostupná, odchozí
pošta čeká ve frontě a nová veřejná pošta se stáhne po obnovení připojení.

Lokální schránka musí být oddělená od `Maildir/new`, který zpracovává MeshCom
gateway. Její IMAP poller nesmí současně číst stejnou veřejnou složku jako
samostatný stahovač pošty; pro předávání vybraných zpráv do MeshCom použijte
oddělenou složku nebo pravidlo. Pro doporučené zapojení getmail6 stahuje veřejnou
složku `INBOX` do Dovecot Maildiru a pravidlo na veřejném serveru kopíruje
vybrané zprávy do složky `MeshCom`, kterou čte gateway.

Při použití místního Postfixu nastavte SMTP aplikace na `127.0.0.1:25`, bez
přihlášení a bez STARTTLS; Postfix pak bezpečně předává odchozí poštu přes
autentizovaný relay. Ukázkové proměnné jsou v [`.env.example`](.env.example).
Postfix a Dovecot jsou systémové služby; webová konfigurace nastavuje jen
aplikaci MeshCom. Podrobnosti instalace jsou v [INSTALL.md](INSTALL.md).

## Spuštění

Podrobné kroky instalace a nasazení přes systemd jsou v [INSTALL.md](INSTALL.md).
Pro ruční spuštění po instalaci závislostí:

```bash
python3 gateway.py
python3 webapp.py
```

Webové rozhraní poslouchá na portu `8899`. V produkci se používá systemd a
Waitress; obě služby se po chybovém ukončení automaticky spouštějí znovu.
Aktualizace z UI se týká pouze aplikace; operační systém, Python balíčky,
Postfix a Dovecot se aktualizují samostatně podle [INSTALL.md](INSTALL.md).

## Struktura

- `gateway.py` – UDP, SMTP, Maildir a volitelné IMAP zpracování
- `webapp.py` – Flask webové rozhraní a validace webového formuláře
- `common.py` – konfigurace, SQLite, Maildir a heartbeat
- `templates/`, `static/` – šablony a styly webu
- `systemd/` – produkční jednotky pro gateway a web

