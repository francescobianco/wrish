# wrish

CLI Python per controllare il bracciale `C60-A82C` via BlueZ D-Bus.

## Requisiti

Su Ubuntu servono i binding di sistema per D-Bus e GI:

```shell
sudo apt install python3-dbus python3-gi
```

## Installazione utente

```shell
make install
```

Questo installa il comando `wrish` nel profilo utente con `pip install --user .`.

## Configurazione

`wrish` legge `WRISH_*` da `.wrishrc` nella directory corrente oppure da `~/.wrishrc`.

Esempio:

```shell
WRISH_DEVICE=C60-A82C
WRISH_MAC=A4:C1:38:9A:A8:2C
WRISH_HCI=hci0
```

## Esempi

```shell
wrish info
wrish battery
wrish find
wrish vibrate --seconds 300
wrish notify --app whatsapp --title "Mario" --body "Ciao"
wrish sms --from "+39123456789" --body "ciao come stai?"
wrish call --from "Mario" --number "+39123456789"
wrish button
wrish sentinel
wrish relay https://www.hookpool.com/xxxx/xxxx.relay --sentinel
wrish systemd
wrish relay https://hookpool.com/xxxx/xxxx.relay
wrish raw 27 00 00 74
```

## Comandi disponibili

- `info`
- `battery`
- `find`
- `vibrate`
- `notify`
- `sms`
- `call`
- `button`
- `sentinel`
- `relay`
- `systemd`
- `raw`

`wrish button` enters the bracelet camera/shutter mode, listens for button press events, and sends the validated stop command when it exits.

`wrish vibrate --seconds 300` repeats the bracelet vibration for coverage/range testing. You can also tune the interval:

```shell
wrish vibrate --seconds 300 --interval 1
```

## Sentinel

`wrish sentinel` e` un loop infinito che controlla il braccialetto, prova a riconnettersi quando cade e invia una notifica appena la connessione torna disponibile.

Puoi anche abbinarlo al relay nello stesso processo:

```shell
wrish relay https://www.hookpool.com/braccialetto/7bgs3p.relay --sentinel
```

Questo e` il caso tipico da mettere sotto `systemd --user`, cosi` il processo resta in background e:

- tiene attivo il relay HTTP verso Hookpool
- tenta continuamente di riallacciare il BLE
- manda il messaggio di avvenuta connessione al braccialetto

## Systemd Wizard

`wrish systemd` apre un piccolo wizard interattivo e crea un servizio user-level in `~/.config/systemd/user/`.

Poi basta eseguire:

```shell
systemctl --user daemon-reload
systemctl --user enable --now wrish.service
journalctl --user -u wrish.service -f
```

## Relay HTTP

`wrish relay <url>.relay` avvia un piccolo server HTTP locale e apre il long-poll verso Hookpool.

Esempio:

```shell
wrish relay https://hookpool.com/xxxx/xxxx.relay
```

Endpoint locali esposti:

```shell
POST /sms?from=+39123456789     body testuale del messaggio
POST /call?from=Mario&number=+39123456789
POST /notify?app=whatsapp&title=Ciao   body testuale della notifica
POST /find
POST /vibrate
GET  /battery
GET  /health
```

Esempio locale:

```shell
curl -X POST 'http://127.0.0.1:8787/sms?from=asasasd' -d 'ciao'
```
