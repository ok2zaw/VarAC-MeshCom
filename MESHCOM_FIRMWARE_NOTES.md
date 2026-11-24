# Úpravy MeshCom firmwaru pro RAK4630

Poznámky pro vlastní výchozí konfiguraci a rozšíření UDP monitoru ve firmwaru
[icssw-org/MeshCom-Firmware](https://github.com/icssw-org/MeshCom-Firmware).
RAK je napájený přes USB. Cílem je obnovit provoz po ztrátě nastavení a získat
z uzlu podrobnější přehled RF provozu pro tuto Python gateway.

Stav k 24. 11. 2026: první etapa je implementovaná a sestavená pro RAK.
Obsahuje vlastní profil `OK2ZAW-30-v2`, surový UDP RX monitor,
periodický stav uzlu a trvalý RF archiv v gateway. Profil obnovuje značku,
zapíná webserver a volí Ethernet. Instalace používá Ethernet a DHCP;
po smazání nastavení si uzel vyžádá adresu z DHCP a spustí web pro konfiguraci.
Skupiny, poloha a cílová IP Pi pro ExtUDP v profilu zatím nejsou.
Na zařízení nebyl firmware nahrán ani ověřen výpadky napájení.

Zdroje a přenositelné integrační změny jsou ve [firmware/](firmware/README.md).
Výsledky sestavení a testů jsou v [záznamu ověření](firmware/VALIDATION.md).
`firmware/prepare.py` je umí aplikovat na nový nebo čistý checkout přesně
určené verze. Výchozí profil se používá i při výslovném továrním resetu.

## Výchozí verze a sestavení

- Poslední vydání ověřené při rozboru: `v4.40a.11.22` z 22. 11. 2026.
- Pro rozbor UDP je připnutý upstream `dev` commit
  `68286e071951bed184ed68ca4e8363e19567aea5` z 23. 11. 2026.
- PlatformIO environment: `wiscore_rak4631`; board: `rak4630`.
- MCU: nRF52840, rádio: SX126x. Parametry sestavení jsou v
  `variants/wiscore_rak4631/platformio.ini` a `boards/rak4630.json`.
- Základ nRF52 používá `nordicnrf52@10.12.0`, který deklaruje Adafruit framework
  `~1.10700.0`. Při sestavení zaznamenat skutečně nainstalovanou verzi.
- USB napájení neurčuje síťové rozhraní. Před testem UDP zaznamenat také síťový
  modul a jeho zapojení; nRF52 kód zde obsahuje podporu Ethernetu W5100S.

Odkazy níže vedou na připnutý commit, aby změny upstreamu neměnily jejich význam.

## Vlastní výchozí konfigurace

Zvolený směr je profil zabudovaný při kompilaci do programu. Při chybě nebo
nepřítomnosti uložené konfigurace se obnoví tento profil. Novější změny uložené
jen za běhu se při takové obnově vrátí k hodnotám profilu. Nový firmware musí
profil znovu obsahovat; změna profilu vyžaduje nové sestavení.

Hodnoty zadané přes BT nebo web se ukládají do běžné konfigurace, nikoli do
profilu zabudovaného ve firmwaru. Přežijí restart, ale při úplné ztrátě úložiště
se vrátí hodnoty profilu. Webový přístup po obnově používá DHCP; přidělenou IP
lze zjistit v routeru nebo ve výpisu uzlu a otevřít `http://<IP-uzlu>/`.

### Místa pro úpravu

| Místo | Současná úloha | Potřebná úprava |
| --- | --- | --- |
| `src/meshcom_settings.h` | Inicializátory běžné struktury nastavení. | Zachovat upstream výchozí hodnoty a přidat vlastní profil jako samostatný krok. |
| `src/nrf52/nrf52_flash.cpp`, `init_flash()` | Načítání klíčového úložiště a převod starého binárního formátu. | Použít profil při obnově; odlišit obnovu od úspěšného načtení uživatelských nastavení. |
| `src/nrf52/nrf52_flash.cpp`, `flash_reset()` | Vytváří výchozí strukturu, převádí ji přes `bleSettingsToV1()` a zapisuje ji. | Použít stejnou funkci profilu v obou větvích tvorby výchozí struktury, včetně větve po formátování. |
| `src/nrf52/nrf52_main.cpp`, `nrf52setup()` | Kontrola kompatibility, načtení příznaků, následné `save_settings()`. | Ověřit, že skutečně načte obnovený profil a nepřepíše jej starou RAM kopií. |

Vhodný tvar je samostatná funkce `applyLocalDefaults(settings)` a lokální
soubor s profilem. Profil má obsahovat nastavení, nikoli runtime hodnoty,
čas, stav spojení nebo čítače zpráv. Neměnit rozložení zmrazeného
`s_ble_settings_v1` ani kvůli profilu zvyšovat `FLASH_STRUCT_VERSION`.

Současná implementace používá vlastní profil i při výslovném továrním resetu.
Stav uzlu hlásí zdroj nastavení a identifikátor profilu. Po resetu kvůli změně
layoutu se nastavení znovu načte, aby stará RAM kopie nepřepsala obnovený profil.

### Co profil řeší a co zůstává k ověření

`InternalFS.begin()` v Adafruit frameworku 1.7.0 při neúspěšném mountu maže
celou oblast souborového systému a formátuje ji. Profil zabudovaný v programu
je mimo tuto oblast. Druhý běžný soubor na stejném filesystemu tuto ochranu
nemá. Uložení aktuální konfigurace příkazem „uložit jako výchozí“ za běhu je
jiná, rozsáhlejší úprava s vyhrazenou oblastí flash a kontrolou integrity.

MeshCom používá dočasný soubor a přejmenování, ale flash cache pod ním maže
a přepisuje celou fyzickou stránku. Samotné přejmenování proto není důkazem
odolnosti vůči přerušení napájení na tomto konkrétním ovladači.

Současné načítání navíc neodmítá každý částečně poškozený soubor: podmínka
`fields_set > 0` a neprázdná značka neprokazuje úplnost konfigurace. Pro obnovu
celého profilu po poškození je nutné navrhnout i kontrolu integrity uloženého
záznamu a migraci starých záznamů. Jinak mohou některé chybějící položky zůstat
na obecných výchozích hodnotách bez vyvolání obnovy.

Zdroje: [načítání a reset nastavení](https://github.com/icssw-org/MeshCom-Firmware/blob/68286e071951bed184ed68ca4e8363e19567aea5/src/nrf52/nrf52_flash.cpp),
[výchozí struktura](https://github.com/icssw-org/MeshCom-Firmware/blob/68286e071951bed184ed68ca4e8363e19567aea5/src/meshcom_settings.h),
[mount a automatický formát](https://github.com/adafruit/Adafruit_nRF52_Arduino/blob/1.7.0/libraries/InternalFileSytem/src/InternalFileSystem.cpp),
[zápis flash cache](https://github.com/adafruit/Adafruit_nRF52_Arduino/blob/1.7.0/libraries/InternalFileSytem/src/flash/flash_cache.c).

## Co dnes odchází přes ExtUDP

`sendExtern()` vytváří `pos` a `msg`; k pozici může přidat samostatný `tele`.
Potvrzení `ack` mají samostatnou cestu. `src_type` rozlišuje `lora`, `node`
a `udp`. Export obsahuje mimo jiné RSSI/SNR, ID zprávy, zdrojovou cestu,
typ hardwaru původce, modulaci, zbývající hops a firmware původce.
Telemetrie zahrnuje teploty, vlhkost, tlak, plyn, CO2 a případně digitální vstupy.

To je export zpracovaných zpráv. Pro úplný RF monitor má tyto mezery:

- `queueExtern()` se volá uvnitř větve po potlačení duplicit; přes tuto cestu
  se neexportují všechny opakované příjmy, vlastní ozvěny ani opakování PN.
- RF ACK zpracovává `handleACK()` před touto větví. Stávající JSON `ack`
  nenahrazuje surový záznam každého RF ACK.
- HEY se může dostat do fronty, ale `sendExtern()` pro jeho typ nemá větev.
  HN sousedské zprávy končí vlastní větví ještě před běžným exportem.
- Textová telemetrie na cíl `100001` se v `sendExtern()` přeskočí.
- Vadné, neznámé a neúspěšně dekódované rámce v běžném exportu chybí.
- `--nopmother on` potlačuje export cizích přímých textových zpráv.
- Exportní fronta má dvě položky. `queueExtern()` před přepsáním položky
  nekontroluje její obsazenost; při zpoždění odesílání může přepsat neodeslaný
  záznam. Současný kód neposkytuje důkaz bezeztrátového exportu.
- `sendExternHeartbeat()` je prázdná funkce. JSON heartbeat `hb` v
  `getExternUDP()` je pod `MC_TEST_HOOKS`, tedy nikoli obecně dostupný provozní stav.

Zdroje: [RF příjem a odesílání](https://github.com/icssw-org/MeshCom-Firmware/blob/68286e071951bed184ed68ca4e8363e19567aea5/src/lora_functions.cpp),
[ExtUDP a fronta](https://github.com/icssw-org/MeshCom-Firmware/blob/68286e071951bed184ed68ca4e8363e19567aea5/src/extudp_functions.cpp),
[JSON textů](https://github.com/icssw-org/MeshCom-Firmware/blob/68286e071951bed184ed68ca4e8363e19567aea5/src/extern_msg_json.h),
[JSON telemetrie](https://github.com/icssw-org/MeshCom-Firmware/blob/68286e071951bed184ed68ca4e8363e19567aea5/src/extern_tele_json.h).

## Další dostupné informace

| Oblast | Co lze doplnit do UDP | Význam a podmínky |
| --- | --- | --- |
| Surový RX | Přesné bajty rámce, délka, RSSI, SNR a čas zachycení. | Kopírovat před filtry, ACK větví a dekódováním; zachovat i duplicity a neznámé typy. |
| Cesta zprávy | Původce, poslední stanice v cestě, celý zdrojový i cílový řetězec, zbývající hops, hardware posledního hopu. | RSSI/SNR měří poslední RF spojení k tomuto přijímači, nikoli vzdáleného původce. Poslední značka v cestě je údaj rámce. |
| Průchod serverem | `msg_server`, `msg_mesh`, `msg_track`, `msg_app_offline`. | Oddělit médium příjmu `lora` od příznaku dřívějšího průchodu serverem. `lora` samo neprokazuje cestu bez internetu. |
| Popis stanice | `pos_name`, `pos_atxt`, `ncnt`, seznam skupin `grc[]`. | Decoder je má, současný `pos` JSON je neobsahuje. Přidat pouze hodnoty skutečně přítomné v rámci. |
| Napájení a senzory | `vbus`, `vcurrent`, napětí baterie vlastního uzlu, další dostupné hodnoty. | `/U=` a `/I=` vycházejí z INA226; nelze je automaticky vydávat za měření USB napájení každé desky. |
| RF ACK | ID ACK, ID potvrzované zprávy, hops, RF kvalita. | Identifikovat pouze údaje, které ACK skutečně přenáší; značku potvrzující stanice nedoplňovat odhadem. |
| TX | Zařazení, pokus o vysílání, `OnTxDone`, `OnTxTimeout`, relay, opakování a čekání kvůli obsazenému kanálu. | Stávající `src_type=node` ani TX capture před startem vysílání nejsou potvrzení skutečně dokončeného vysílání. |
| RF chyby | Události RX error a timeout, jejich počty. | Na RAKu nemá současný `OnRxError()` payload, délku ani frekvenční chybu; RSSI/SNR mohou být při chybě hlavičky staré. |
| Sousedé a topologie | Přímí sousedé, stáří, RSSI/SNR, kdo slyší nás, role uzlu, gateway flag, známé cesty. | Použít `NbrMhView` a `NbrRouteView`; jde o odvozený stav s časovou platností. |
| Stav uzlu | Uptime, reset reason, zdroj konfigurace, profil, firmware, RAM, RX/TX airtime, duplicity, chyby a zaplnění front. | Statistika využití kanálu je odhad založený na RX/TX době, nikoli měření veškerého rušení v pásmu. Reset reason sám neprokazuje brownout. |
| Ethernet | Výpadky linky, získání IP, DHCP chyby, restarty a chyby UDP odesílání. | Čítače už existují v `nrf_eth.cpp`; před exportem vyjasnit jejich současný rozsah a oddělit chyby monitoru. |

Zdroje: [datové struktury](https://github.com/icssw-org/MeshCom-Firmware/blob/68286e071951bed184ed68ca4e8363e19567aea5/src/aprs_structures.h),
[decoder](https://github.com/icssw-org/MeshCom-Firmware/blob/68286e071951bed184ed68ca4e8363e19567aea5/src/aprs_functions.cpp),
[sousedé a cesty](https://github.com/icssw-org/MeshCom-Firmware/blob/68286e071951bed184ed68ca4e8363e19567aea5/src/nbr_views.h),
[statistická pole](https://github.com/icssw-org/MeshCom-Firmware/blob/68286e071951bed184ed68ca4e8363e19567aea5/src/setlog_lines.h),
[stav RAKu a příčina restartu](https://github.com/icssw-org/MeshCom-Firmware/blob/68286e071951bed184ed68ca4e8363e19567aea5/src/nrf52/nrf52_main.cpp),
[Ethernet](https://github.com/icssw-org/MeshCom-Firmware/blob/68286e071951bed184ed68ca4e8363e19567aea5/src/nrf52/nrf_eth.cpp).

## Implementovaná první etapa UDP monitoru

- Nové typy `rf_rx` a `node_status`, protokol `monitor_version=1`.
- RAK zachytává RX v `rxsOnRxDone()` ještě před voláním sdíleného `OnRxDone()`.
  Sdílená obsluha LoRa ostatních platforem se nemění.
- Samostatná osmipoložková fronta, maximálně 255 bajtů na rámec, kódování HEX,
  čas příjmu v uzlu, pořadí události a náhodné 64bitové ID startu.
- Stav uzlu po zpřístupnění transportu a dále každých 30 sekund. Obsahuje
  počty ztrát, obsazenost fronty, chyby UDP, RAM a zdroj nastavení.
- Monitor je volitelný při sestavení přes `MC_RF_MONITOR`. Za běhu vyžaduje
  zapnuté ExtUDP. Při `--nopmother on` je surový RX zcela potlačený, včetně
  záznamů čekajících ve frontě; stav uzlu pokračuje.
- `MC_LOCAL_DEFAULTS` zapíná profil. Oba přepínače jsou zapnuté pouze pro
  sestavení `wiscore_rak4631`, bez nových uložených bitů a bez změny RF protokolu.
- `rf_tx`, `rf_error`, rozšířená dekódovaná metadata a export sousedů jsou
  další etapa; nejsou součástí současného UDP formátu.

## Návrh dalších rozšíření UDP monitoru

Zachovat současné `msg`, `pos`, `tele` a `ack` pro kompatibilitu gateway.
Přidat volitelný proud s verzí schématu a typy `rf_rx`, `rf_tx`, `rf_error`,
`node_status` a později `neighbors`. Názvy jsou návrh, nikoli existující API.

Pro první etapu má největší přínos surový RX a stav uzlu:

- Každý záznam má ID uzlu, nové ID relace po startu, pořadové číslo a čas
  události `uptime_ms`. UTC přidávat jen při platném čase; Pi ukládá i vlastní
  čas přijetí. Čas zachytit při události, nikoli až při odesílání UDP.
- RX obsahuje délku a `raw_hex` nebo `raw_b64`, RSSI a SNR. Zvolit jedno
  kódování a dokumentovat ho. U nepřítomných měření použít `null` nebo pole
  vynechat; nula nesmí předstírat měření.
- Každému fyzickému příjmu přidělit vlastní `event_seq`; `msg_id` slouží ke
  korelaci opakování, nikoli jako unikátní klíč RF události.
- Volitelně přidat dekódovaná pole a `decode_ok`; odlišit rádiové CRC od
  MeshCom FCS. U nepodporovaného formátu výsledek FCS neodhadovat.
- Kopii rámce vkládat do omezené fronty z RX callbacku. JSON a UDP obsluhovat
  v hlavní smyčce. Síť ani zápis do flash nesmějí blokovat RF callback.
- Použít frontu se správným předáváním vlastnictví mezi tasky. Počítat
  přeplnění, maximum obsazenosti, chyby serializace a chyby UDP zápisu i
  `endPacket()`. Pořadí číslovat před pokusem o vložení, aby byly vidět mezery.
- UDP neposkytuje potvrzené doručení. Pravidelný `node_status` umožní poznat
  restart a část ztrát; pro garantovaný archiv by byl potřeba další protokol.
- Odesílání vypnuté/zapnuté nezávisle na `--loradebug`, aby provozní monitor
  nevyžadoval rozsáhlý sériový debug. Ověřit také chování sestavení s `MC_DIAG=0`.
- Chování `--nopmother` musí být pro nový monitor výslovně určené; surová
  kopie před filtrem by jinak umožnila obejít potlačení cizích přímých zpráv.

V kódu již existuje `captureFrame()` na začátku `OnRxDone()` a před TX.
Současný capture používá 768B kruhový buffer a výstup na sériovou konzoli,
neobsahuje čas jednotlivých rámců a počítá ztráty. Je to vzor pro bezpečné
kopírování, ne hotový UDP monitor. Nový konzument nesmí soupeřit se sériovým
konzumentem o stejné položky; navrhnout samostatnou frontu nebo společné
vyzvednutí strukturovaného záznamu s následným rozesláním.
[Capture implementace](https://github.com/icssw-org/MeshCom-Firmware/blob/68286e071951bed184ed68ca4e8363e19567aea5/src/capture_functions.cpp).

„Plný RF RX“ zde znamená všechny rámce dodané callbacku přijímače v aktuálně
nastavené modulaci a na aktuálním kanálu. Nezahrnuje pakety, které rádio
nedokázalo přijmout, provoz během vlastního TX, jiné kanály ani surové IQ.
Pro surový obsah rámců s rádiovou CRC chybou by bylo nutné ověřit a upravit
také ovladač SX126x.

## Dopady na tuto Python gateway

`gateway.py` rozpoznává nové monitorovací typy a předává je do `rf_monitor.py`.
Samostatná tabulka `rf_events` zachovává binární obsah, původní JSON, ID startu,
pořadí, adresu odesílatele a čas přijetí na Pi. Nepotlačuje opakování a nemá
automatické mazání. Stránka `/rf` poskytuje filtrování, stránkování a export
NDJSON; platí pro ni stávající přihlášení do webu. Objem databáze je potřeba
zohlednit při dlouhodobém provozu.

Navazující rozšíření gateway rozebírá surové rámce pomocí `mesh_frame.py`
do oddělené tabulky `rf_frame_details`. Zachovává původní bajty i JSON,
kontroluje MeshCom součet a čitelně ukazuje text, cesty, cíle, příznaky a RF ACK.
Starší záznamy se při startu doplní o tentýž rozbor po dávkách. Filtry podle
původce, cíle, startu, typu a výsledku rozboru platí i pro export. Stav uzlu
má označení čerstvosti a přehled startů rozlišuje opakované doručení UDP
od samostatného opakovaného RF příjmu. Dekódování zůstává diagnostické a
nemění pravidla pro přeposílání do e-mailu. Nový firmware k tomu není potřeba.

Krátký UDP monitor zůstává omezený na posledních 300 datagramů a 2000 znaků.
Konverzace, pozorování stanic a přeposílání do e-mailu mají nadále vlastní
pravidla. Monitorovací události se do RF ani e-mailu nepřeposílají.
Před nasazením sladit značku v konfiguraci gateway na `OK2ZAW-30`.

## Postup přenosu na novější firmware

1. Zapsat tag a SHA nové základní verze, aktuální verze PlatformIO a knihoven,
   bootloader, typ desky a síťový modul. Uložit export konfigurace před změnou.
2. Aktualizovat pracovní větev vůči upstream `dev`. Vlastní profil a UDP
   monitor držet v oddělených malých commitech; zachovat jejich patch soubory.
3. Zkontrolovat aktuální pravidla upstreamu v `CLAUDE.md` a `REVIEW.md`.
   Jejich dnešní postup vyžaduje malé cílené změny a PR proti `dev`.
4. Hledat funkce podle názvu a významu, nikoli podle čísel řádků: `init_flash`,
   `flash_reset`, `settingsStoreLoad`, `writeFileAtomic`, `OnRxDone`,
   `handleACK`, `OnRxError`, `OnTxDone`, `OnTxTimeout`, `queueExtern`,
   `flushExternQueue`, `captureFrame` a `captureDrain`.
5. Znovu zkontrolovat změny schématu nastavení, inicializátory, migraci starého
   formátu, automatický formát v knihovně a všechny větve obnovy. Profil
   nesmí přepisovat úspěšně načtená uživatelská nastavení.
6. Zkontrolovat pořadí RX filtrů a ACK větví. Surovou kopii pořizovat před
   změnou délky nebo obsahu rámce. Prověřit kontext každého callbacku a všech
   producentů fronty; pozdější síťové zpracování může běžet v jiném tasku.
7. Přenést změny přes `cherry-pick` nebo `git apply --check` a ručně prověřit
   i změny, které projdou bez konfliktu. Sestavit `pio run -e wiscore_rak4631`.
   U sdílených změn sestavit i další prostředí podle pravidel upstreamu.
8. Porovnat flash/RAM a maximální zásobník s původní verzí. Uložit výsledný
   artefakt, jeho kontrolní součet, SHA upstreamu a revizi profilu.
9. Provést následující testy a zapsat skutečný výsledek pro tuto verzi.

## Ověření před nasazením

- Platná uložená konfigurace má přednost před profilem, změna přežije restart.
- Prázdné, chybějící, zkrácené a poškozené nastavení vyvolá očekávanou obnovu;
  ověřit i částečně čitelný soubor, migraci a obě větve `flash_reset()`.
- Po opakovaných výpadcích USB, včetně přerušení zápisu, uzel obnoví profil.
  Ověřit získání DHCP adresy a dostupnost konfiguračního webu po smazání nastavení.
  Ověřit start se skutečně stabilním napájením; simulace nenahradí HW test.
- RF capture zahrne opakované příjmy, ACK, HEY, HN, telemetrii a odmítnuté
  formáty, pokud je dodal rádiový callback. Bajty porovnat s nezávislým záznamem.
- Ověřit samostatně chybu dekódování MeshCom a rádiovou RX chybu. U druhé
  nepředstírat dostupnost payloadu nebo platnost starých RSSI/SNR.
- Výpadek Ethernetu, restart Pi a plná fronta nezablokují rádio; po obnově
  status hlásí počty ztrát a správné ID relace. UDP chyby se nepočítají jako RF chyby.
- TX pokus, dokončení a timeout jsou rozlišitelné; zařazení do fronty
  neoznačovat za odvysílání ani doručení protistraně.
- Ověřit validní JSON, maximální rámec, nulové a netextové bajty, escapování,
  přetečení časových/sekvenčních čítačů a kompatibilitu původní gateway.
- RF testy provádět na vlastních uzlech a testovacích cílech dle upstream
  pravidel; výsledky zapsat s verzí, deskou a zapojením.
