// Copie este arquivo para "secrets.h" (na mesma pasta) e preencha.
// secrets.h está no .gitignore: NUNCA envie suas senhas para o repositório.
#pragma once

#define WIFI_SSID     "NOME_DA_REDE"
#define WIFI_PASS     "SENHA_DA_REDE"

// IP do notebook na rede local (veja o terminal ao iniciar o Arena Replay).
// Dica: reserve esse IP no roteador (DHCP reservation) para ele não mudar.
#define SERVER_HOST   "192.168.0.10"
#define SERVER_PORT   8000

// Mesmo valor de "device_token" no config.json do notebook.
#define DEVICE_TOKEN  "TROQUE-POR-UM-TOKEN-LONGO"

// Identificador da quadra/botão: só letras, números, "-" ou "_" (máx. 32).
#define DEVICE_ID     "quadra1"
