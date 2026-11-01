# LoRa KISS TNC (upravená kopie pro MeshCom gateway)

Upravená kopie projektu
[IZ7BOJ/RPi-LoRa-KISS-TNC-2ndgen, větev `Lora-Packet`](https://github.com/IZ7BOJ/RPi-LoRa-KISS-TNC-2ndgen/tree/Lora-Packet)
(commit `39581880a3e08b77441617ea76efe6cda59c1fda`, 2022-09-14) pro packet radio
přes LoRa s LinBPQ. Licence GPL-3.0 (`LICENSE`).

Složka `pySX127x/` obsahuje potřebnou část knihovny
[mayeranalytics/pySX127x](https://github.com/mayeranalytics/pySX127x)
(commit `a839d6ad38971c4ee27f3607009149a254bf7357`, licence AGPL-3.0,
`pySX127x/LICENSE`); `LoRa.py` a `board_config.py` jsou upravené verze od
IZ7BOJ, jak předepisuje jeho návod.

## Změny oproti originálu

- `config.py` načte nastavení instance ze souboru JSON v proměnné prostředí
  `LORA_TNC_CONFIG`. Díky tomu běží z jedné složky dvě instance (TNC1, TNC2)
  s různým TCP portem, IRQ pinem, SPI chip selectem a LoRa parametry.
  Soubory zapisuje záložka **BPQ** webu gateway do `~/.config/meshcom/`.
- Logování do souboru je ve výchozím stavu vypnuté; výstup jde do journalu.
- SX127x: hodnota `sync_word` z konfigurace se skutečně předává modulu
  (originál ji ignoroval a používal vždy `0x12`).
- `TCPServer.py`: doplněný `import traceback`; přerušené spojení s klientem
  (LinBPQ) neukončí vlákno serveru a TNC přijme nové spojení; chyba odeslání
  neshodí příjem.
- Knihovny displeje (`board`, `adafruit_ssd1306`, `PIL`) se importují jen při
  `disp_en = True`.
- Cesta k `pySX127x` je určena podle umístění skriptu, ne aktuálního adresáře;
  `setDaemon()` je nahrazené atributem `daemon`.

## Spuštění

Instance spouští web gateway jako uživatelské systemd služby
`meshcom-tnc1` a `meshcom-tnc2`. Ruční spuštění pro test:

```bash
LORA_TNC_CONFIG=~/.config/meshcom/tnc1.json python3 Start_lora-tnc.py
```

Závislosti (Raspberry Pi OS): `python3-spidev` a `python3-rpi-lgpio` (na Pi 5
místo `python3-rpi.gpio`), zapnuté SPI. Postup je v kapitole 12 `INSTALL.md`.
