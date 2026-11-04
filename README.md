# MeshCom Gateway

Pythonová obousměrná gateway mezi MeshCom a e-mailem. Aktuální implementace
komunikuje s MeshCom uzlem přes UDP/JSON; VARA, Mercury a VarAC umí spouštět,
ale zprávy přes ně zatím nepředává.
Přehled hotových funkcí a další práce je v [ROADMAP.md](ROADMAP.md), instalace
na Raspberry Pi/Debian v [INSTALL.md](INSTALL.md).

## Co projekt umí

- Přijímat zprávy z nakonfigurované MeshCom skupiny a odesílat je přes SMTP.
- Načítat e-maily z `Maildir/new`; volitelně stahovat nepřečtené zprávy přes IMAP.
- Převádět e-maily na MeshCom zprávy, dlouhé texty rozdělit podle limitu a
	uvést názvy příloh bez odesílání jejich obsahu. Značka `GROUP: 262`
	v předmětu pošle e-mail do zvolené skupiny místo skupiny z nastavení.
- Sledovat stav doručení odeslaných zpráv (echo uzlu, potvrzení `ack`
	z firmwaru 4.40a i textové `ackNNN` u přímých zpráv).
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
	Mapa stanic ukazuje vzdálenosti od vlastního uzlu, stav 🟢/🟡/⚪ a po
	zapnutí i cesty přes převaděče; polohu uzlu lze nastavit kliknutím do mapy.
- Na stránce **Mesh zprávy** zobrazit všechny textové zprávy z uzlu (LoRa,
	internetová brána i echo vlastních zpráv) roztříděné podle cíle: `*` pro
	všechny, číselné skupiny a přímé zprávy na značku. Duplicitní opakování,
	potvrzení (`ack`) a časové značky `{CET}` se vynechávají. Text se ukládá
	lokálně do SQLite bez automatického mazání.
- Na stránce **UDP monitor** sledovat surové datagramy mezi uzlem a Pi a stav
	příjmu (naslouchání na portu, chyby, IP adresa pro `--extudpip`).
- Odeslat ze stránky **Zprávy** zprávu do MeshCom skupiny. Předvyplněná je
	skupina z nastavení, lze ji ale přepsat jiným číslem (nabízí se skupiny
	slyšené za posledních 30 dní). Délka se kontroluje v bajtech a pokus se
	zaznamená do historie.
- Zkontrolovat novou verzi aplikace a bezpečně aktualizovat `origin/main` z UI;
  update je fast-forward-only, chrání místní změny a vyžaduje síťové připojení.
- Spouštět, zastavovat a sledovat VARA, modem Mercury a VarAC ze záložky
	VarAC, včetně automatického startu po přihlášení do desktopu se zpožděním
	pro každý program; instalace je v kapitole 11 [INSTALL.md](INSTALL.md).
- Otevřít webové GUI Mercury (spektrum, vodopád, stav spojení) tlačítkem
	**Otevřít GUI** na kartě Mercury. Postup:
	1. Na záložce VarAC zaškrtněte *Webové GUI Mercury* (port 10088) a uložte.
	2. Mercury restartujte.
	3. Tlačítko se ukáže, jakmile GUI na portu odpovídá. Jinak karta vysvětlí
	   proč: GUI je vypnuté, nebo Mercury běží bez něj a je třeba ho restartovat.

	GUI nemá přihlášení, port zpřístupněte jen v LAN/VPN.
- Spouštět, nastavovat a sledovat dva LoRa KISS TNC (TNC1, TNC2; upravená
	kopie IZ7BOJ ve složce `lora_tnc/`) a LinBPQ (uzel a BBS) ze záložky BPQ,
	včetně startu po zapnutí Pi se zpožděním; BBS lze napojit na místní
	Postfix jako e-mailovou bránu (kapitola 12 [INSTALL.md](INSTALL.md)).
- Ukládat historii, chyby a heartbeat do SQLite.

## Formát zpráv

### E-mail → MeshCom

Gateway čte nové e-maily z `maildir_new` (a volitelně nepřečtené zprávy ze
složky `imap_folder` přes IMAP). Z každého e-mailu sestaví text:

```
<odesílatel>: <předmět> | Příloha: <názvy> - <text e-mailu>
```

- **Odesílatel** je jméno z hlavičky `From` bez adresy
  (`Jan Novák <jan@example.net>` → `Jan Novák`). Bez jména se použije celá
  adresa, která zabere část limitu. Nastavte si proto v klientovi krátké
  jméno, třeba volací značku.
- **Text** se bere z části `text/plain` a zalomení řádků i vícenásobné
  mezery se slijí do jedné mezery. E-mail jen v HTML bez textové části dorazí
  jako `(bez textu)`, proto posílejte prostý text.
- **Přílohy** se neposílají, uvedou se jen první tři názvy.
- **Délka**: jedna MeshCom zpráva má nejvýše `max_mesh_payload` **bajtů**
  UTF-8 (výchozí a zároveň nejvyšší hodnota je 150). Delší text firmware 4.40a
  zahodí a do logu uzlu zapíše `[EXT] invalid lengths`. Písmena s diakritikou
  zabírají 2 bajty, takže se česká zpráva vejde zhruba do 100–140 znaků.
- **Dělení na části**: delší e-mail se rozdělí na hranici slov a každá část
  nese v předmětu své pořadí. Přílohy jsou uvedené jen v první části:

  ```
  Jan Novák: Hlášení (1/3) | Příloha: foto.jpg - Ahoj, posílám …
  Jan Novák: Hlášení (2/3) - … pokračování textu …
  Jan Novák: Hlášení (3/3) - … konec.
  ```

  Bez předmětu je pořadí za jménem: `Jan Novák (2/3): …`. Příliš dlouhý
  předmět se zkrátí se třemi tečkami tak, aby na text zbyla aspoň polovina
  zprávy.
- **Maximální délka e-mailu**: `max_email_parts` (výchozí 4) omezuje počet
  částí. Text za touto hranicí se zahodí a poslední část končí
  ` [zkráceno]`. Při výchozích hodnotách se tak přenese zhruba 400–500 znaků
  textu.
- **Pauza** `email_part_delay_seconds` (výchozí 5 s) mezi částmi brání tomu,
  aby uzel vysílal celý e-mail jednou dávkou.
- Po úspěšném odeslání všech částí se e-mail přesune do `Maildir/cur`.

### Cílová skupina v předmětu

Bez značky jde zpráva do skupiny `mesh_group` z nastavení. Značka
`GROUP: <číslo>` kdekoli v předmětu pošle zprávu do jiné skupiny. Velikost
písmen nehraje roli, `#` před číslem je volitelný a číslo může mít 1–5 číslic.
Značka i oddělovač `-` nebo `:` za ní se z předmětu odstraní. Samotné
`Re:`/`Fwd:` bez dalšího textu se zahodí.

| Předmět e-mailu | Cíl | Zpráva v MeshCom |
|---|---|---|
| `Schůzka` | `mesh_group` | `Jan Novák: Schůzka - Ahoj, zítra v 18:00.` |
| `GROUP: 262` | 262 | `Jan Novák: Ahoj, zítra v 18:00.` |
| `group:#2620 - Porucha` | 2620 | `Jan Novák: Porucha - Ahoj, zítra v 18:00.` |
| `Re: GROUP: 9 Cvičení` | 9 | `Jan Novák: Re: Cvičení - Ahoj, zítra v 18:00.` |
| `Re: GROUP: 9` | 9 | `Jan Novák: Ahoj, zítra v 18:00.` |

Přímé zprávy na volací značku ani zprávy pro všechny (`*`) se z e-mailu zatím
posílat nedají, cílem je vždy číselná skupina. Stav doručení každé zprávy je
na stránce **Zprávy**. Skupiny příjemci nepotvrzují, takže tam nejvýš uvidíte,
že zprávu uzel vysílá nebo ji slyšel soused.

### MeshCom → e-mail

Na `mail_to` se přeposílají jen nové textové zprávy do skupiny `mesh_group`
od cizích stanic. Neposílají se zprávy do jiných skupin, přímé zprávy, vlastní
zprávy (echo uzlu), duplicitní opakování, potvrzení `ack` ani časové značky
`{CET}`. Všechny zprávy jsou ale vidět na stránce **Mesh zprávy**.

```
Předmět: [MeshCom 262] OK1AAA-1,OK2RPT-9
From: OK1AAA-1,OK2RPT-9
Group: 262

<text zprávy bez pořadového čísla {NNN}>
```

Odesílatel obsahuje i cestu přes převaděče, jak ji ohlásil uzel.

#### Přeposlání na zvolenou adresu (`EMAIL:`)

Zpráva do skupiny `mesh_group`, která začíná `EMAIL:` a aliasem nebo
e-mailovou adresou, se pošle na tuto adresu místo na `mail_to`:

```
MeshCom zpráva do skupiny 262:  EMAIL: OK2ABU Ahoj, zítra v 18:00.

Komu:    ok2abu@example.net        (adresa aliasu OK2ABU)
Předmět: [MeshCom 262] OK1AAA-1
From: OK1AAA-1
Group: 262

Ahoj, zítra v 18:00.
```

Nastavuje se v **Nastavení** → *Přeposílání EMAIL:*:

| Volba | Význam |
| --- | --- |
| *Přeposílat zprávy EMAIL:* | zapíná celou funkci (výchozí vypnuto) |
| *Povolit i přímé e-mailové adresy* | vypnuto: jen aliasy; zapnuto: i `EMAIL: jan@example.net text` |
| *Přímé adresy do všech domén* | přímé adresy do jakékoli domény |
| *Povolené domény přímých adres* | jinak jen do těchto domén (např. `example.net, example.com`) |

**Aliasy** se přidávají v tabulce pod formulářem nastavení: alias (např.
volací značka `OK2ABU`) a e-mail. Zpráva `EMAIL: OK2ABU text` pak odejde na
zadanou adresu. Uložením stejného aliasu se adresa změní. Aliasy platí vždy,
i když jsou přímé adresy vypnuté nebo doména není mezi povolenými.

- Funguje jen ve skupině nastavené jako `mesh_group`; v jiných skupinách se
  zpráva nepřeposílá vůbec.
- Na velikosti písmen nezáleží; přijímá se i `E-MAIL:` a mezera za dvojtečkou
  není povinná. Prefix musí být na začátku zprávy.
- Při neznámém aliasu, neplatné nebo nepovolené adrese či vypnuté volbě se
  zpráva v plném znění (i s `EMAIL:`) pošle na `mail_to`. Důvod je v logu
  gateway.
- Na stránce **Zprávy** je u přeposlané zprávy v předmětu `→ adresa`.
- E-mail odchází z adresy `mail_from`. Pokud tuto schránku gateway čte,
  odpověď příjemce se vrátí do MeshCom skupiny jako každý jiný e-mail.

> **Pozor:** Skupiny MeshCom jsou veřejné. Kdokoli ve skupině může přes
> gateway poslat e-mail z vaší adresy `mail_from`. Nejbezpečnější jsou jen
> aliasy; přímé adresy do všech domén otevírají cestu ke zneužití a ke
> zhoršení pověsti domény.

Když
odpověď dorazí do schránky, kterou gateway čte, zpracuje se jako každý jiný
e-mail. Předmět `Re: [MeshCom 262] …` nemá značku `GROUP`, takže odpověď jde do
`mesh_group` a celý předmět se započítá do délky zprávy. Je proto lepší
předmět zkrátit, případně doplnit `GROUP: <číslo>`.

## Dokumentace

[INSTALL.md](INSTALL.md) popisuje instalaci na Raspberry Pi/Debian a provoz:

- kapitoly 2–7: gateway, konfigurace a systemd služby,
- kapitola 8: volitelný lokální poštovní uzel (Postfix, Dovecot, getmail6),
- kapitola 9: tipy pro provoz – napájení Raspberry Pi 5 a start bez tlačítka
	(`PSU_MAX_CURRENT`), trvalé logy, diagnostika RF stanic a přehled síťových
	portů včetně souběhu s Webminem,
- kapitola 11: VARA, Mercury (včetně webového GUI) a VarAC na Raspberry Pi,
- kapitola 12: dva LoRa KISS TNC, LinBPQ s BBS a e-mailová brána přes Postfix.

Upravená kopie LoRa TNC je ve složce [`lora_tnc/`](lora_tnc/README.md) i se
seznamem změn oproti originálu.

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
