# Firmware do botão (ESP32)

## Montagem

```
ESP32 DevKit                 Botão (push button)
  GPIO 27  ────────────────── terminal 1
  GND      ────────────────── terminal 2
  GPIO 2   = LED azul da placa (já existe)
```
Sem resistor: o firmware usa o pull-up interno. Para cabos longos (> 2 m) até o botão, use cabo
blindado ou um capacitor de 100 nF entre GPIO 27 e GND para reduzir ruído.

## Arduino IDE

1. *Arquivo → Preferências → URLs adicionais*:
   `https://espressif.github.io/arduino-esp32/package_esp32_index.json`
2. *Ferramentas → Placa → Gerenciador de placas* → instale **esp32 (Espressif)**.
3. Placa: **ESP32 Dev Module**. Porta: a COM que aparece ao plugar o USB
   (se não aparecer, instale o driver CP210x ou CH340, conforme o chip da placa).
4. Na pasta `firmware/arena_button/`, copie `secrets.example.h` para **`secrets.h`** e preencha
   Wi-Fi, IP do notebook, `DEVICE_TOKEN` (igual ao `config.json`) e `DEVICE_ID`.
5. Abra `arena_button.ino` e clique em **Carregar**.
6. *Monitor Serial* a **115200**:
   ```
   Arena Replay botão | dispositivo=quadra1 boot=3fa91c22 servidor=192.168.0.10:8000
   [wifi] conectado, IP 192.168.0.42
   [botao] lance quadra1-3fa91c22-1
   [envio] quadra1-3fa91c22-1 tentativa 1 -> HTTP 201
   ```

Nenhuma biblioteca extra é necessária (`WiFi.h` e `HTTPClient.h` vêm com o pacote ESP32).

## Diagnóstico

| Serial / LED | Causa provável | Ação |
|---|---|---|
| LED piscando devagar, `[wifi] conectando...` repetindo | SSID/senha errados ou rede 5 GHz | ESP32 só usa **2,4 GHz** |
| `HTTP -1` | Notebook inacessível | Confira IP, `run.bat` aberto, Firewall liberado em rede privada |
| `HTTP 401` + 3 piscadas | Token diferente | `DEVICE_TOKEN` = `device_token` do config.json |
| `HTTP 422` | `DEVICE_ID` inválido | Só letras, números, `-`, `_` (até 32) |
| `expirou sem confirmação` | Notebook fora por > 25 s | O toque é descartado de propósito (o vídeo já saiu do buffer) |

O IP do notebook muda quando o roteador reinicia. Reserve o IP no roteador (DHCP reservation)
pelo endereço MAC do notebook.
