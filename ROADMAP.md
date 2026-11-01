# Stav projektu a roadmapa

Tento soubor je hlavním přehledem implementovaného stavu a další práce.
Obecný popis funkcí a spuštění je v [README.md](README.md); instalační postup
pro Raspberry Pi/Debian je v [INSTALL.md](INSTALL.md).

## Aktuální stav

Projekt je funkční základní MeshCom–e-mail gateway. MeshCom část používá UDP
protokol s JSON zprávami; výchozí port je `1799`.

### Implementováno

- Příjem zpráv z nakonfigurované MeshCom skupiny a jejich odesílání přes SMTP.
- Příjem e-mailů z `Maildir/new` a jejich odeslání do MeshCom skupiny.
- Volitelné načítání nepřečtených zpráv přes IMAP do Maildir fronty.
- Rozdělování dlouhých zpráv podle limitu MeshCom payloadu; obsah příloh se
	neposílá, jejich názvy či typy se mohou uvést v textu.
- Archivace úspěšně odeslaných e-mailů do `Maildir/cur`.
- Konfigurace přes výchozí hodnoty, `config.json`, `.env` a `MESHCOM_*`
	proměnné prostředí, v tomto pořadí priority.
- Základní validace číselných hodnot a návazností SMTP/IMAP ve webovém formuláři.
- Webové stránky pro stav, stránkovanou historii a úpravu konfigurace; volitelná
	ochrana HTTP Basic Auth.
- UDP monitor na stavové stránce: počty RX/TX, poslední protistrana a výsledek;
	stránka se obnovuje každých 10 sekund. Obsah paketů se neukládá.
- Ruční odeslání zprávy ze stavové stránky do nakonfigurované MeshCom skupiny,
	validace délky, CSRF ochrana a záznam v historii.
- RF tabulka a časová historie stanic z příchozích ExtUDP rámců `pos`, `msg` a
	`tele` s `src_type=lora`; zahrnuje signál, polohu a dostupná metadata.
- Oddělená časově a cílovou skupinou filtrovatelná historie textových LoRa zpráv
	mimo hlavní MeshCom skupinu.
- SQLite historie zpráv a chyb, heartbeat gateway a systemd služby pro gateway
	i web. Systemd restartuje proces po chybovém ukončení.
- Základní testy UDP monitoru, převodu zpráv, Maildir, konfigurace a validace
	formuláře.

### Zatím mimo rozsah

- Integrace VarAC ani VARA není implementována.
- Projekt nezajišťuje plný poštovní server; Postfix/Dovecot/Roundcube jsou pouze
	volitelné externí komponenty, ne součást gateway.

## Další práce

### Priorita 1: upřesnit zadání VarAC/VARA

Zatím jde o požadavek k definování, ne o naplánovanou implementaci. Před návrhem
řešení je potřeba rozhodnout:

- **Potvrzeno:** VarAC a VARA mají běžet na stejném počítači jako gateway.
- **Otevřeno:** směr toku zpráv; bude upřesněn později.
- Jakou přesnou roli má VARA modem v požadovaném scénáři.
- Rozhraní, které má být použito, a dostupnost/konfigurace VarAC a VARA na cílovém
	počítači; operační systém zatím není určen.
- Pravidla pro adresáty/skupiny, identitu odesílatele, délku a dělení zpráv.
- Chování při odpojení, opakování, duplicitách a chybách.
- Jaké provozní záznamy, diagnostiku a testovací prostředí jsou požadované.

Za implementované bude možné považovat až řešení s dohodnutými akceptačními
kritérii pro oba směry toku, chybové stavy a konfiguraci.

### Priorita 2: spolehlivost pošty

- Zabránit ztrátě nebo dvojímu zpracování e-mailu po restartu procesu.
- Zpřesnit správu stavů Maildir a zpracování HTML/multipart zpráv.
- Přidat testy obnovy po chybě během odesílání a archivace.

### Priorita 3: diagnostika a správa

- Zobrazit poslední chyby SMTP/IMAP a podrobnější diagnostiku ve webovém rozhraní;
	poslední stav UDP už monitor zobrazuje.
- Rozpoznat zamrzlý proces, nejen proces ukončený chybou; doplnit provozní alarmy.
- Zvážit bezpečné ruční opakování neúspěšně zpracované zprávy.

### Nižší priorita: lokální poštovní infrastruktura

- Dokumentace nebo automatizace volitelného Postfix/Dovecot nasazení.
- Správa lokálních poštovních účtů není součástí aktuální gateway.
