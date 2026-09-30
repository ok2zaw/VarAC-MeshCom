# Roadmap a připravované úpravy

## Hotové

- základní MeshCom <-> email gateway
- konfigurace v `config.json`
- webové rozhraní pro stav a historii
- logování do SQLite
- heartbeat pro kontrolu běhu démona
- rozdělování dlouhých e-mailů na více MeshCom zpráv
- ignorování příloh v textu zprávy, pouze název a typ přílohy
- archivace zpracovaných e-mailů z `Maildir/new` do `Maildir/cur`
- základní dokumentace v `README.md`

## Plánované úpravy

### 1) Bezpečnější konfigurace
- přesun citlivých hodnot do proměnných prostředí
- podpora `.env` souboru pro SMTP heslo a webové heslo
- validace vstupů z konfigurace

### 2) Lepší Maildir a zpracování pošty
- deduplikace zpracovaných e-mailů
- lepší kontrola, zda soubor ještě nebyl zpracován
- rozlišování `new` / `cur` / `tmp` podle Maildir standardu
- robustnější parsing HTML a multipart e-mailů

### 3) Stabilita a monitoring
- lepší logování chyb do souboru
- watchdog pro restart při pádu démona
- rozšíření status stránky o další diagnostiku
- jednoduché alarmy při chybách SMTP/UDP

### 4) Web a správa
- přidání odkazu na konfiguraci a události
- zobrazení posledních chyb v web UI
- možnost ručního opětovného zpracování záznamů

### 5) Integrace s mailovou infrastruktorou
- Postfix + Dovecot + Roundcube setup na Debian/Raspberry Pi
- možnost provozu s lokálním mailserverem
- přidání lokální správy mailových účtů a Maildir

## Dílčí poznámka

Tento projekt je v současné době funkční jako jednoduchý gateway pro konkrétní provozní scénář. Dále se plánuje přidat spolehlivost, lepší administraci a snadnější nasazení na Raspberry Pi a Debian.
