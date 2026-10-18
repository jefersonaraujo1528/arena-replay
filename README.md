# Arena Replay — MVP

Replay instantâneo para quadras esportivas. Um botão físico (ESP32) marca o lance; o
notebook guarda os **10 s anteriores + 5 s posteriores** e publica o vídeo em um site
local que abre no notebook ou no celular conectado à mesma rede. Sem internet.

```
[Botão ESP32] --Wi-Fi/HTTP--> [Notebook: captura + buffer + clipes + site] <--Wi-Fi-- [Celulares]
```

## Início rápido (Windows 10/11)

Pré-requisito: **Python 3.10 ou superior** ([python.org](https://www.python.org/downloads/) — marque *Add python.exe to PATH*).
O FFmpeg vem automaticamente pelo pacote `imageio-ffmpeg`; não é preciso instalar à parte.

```bat
cd "C:\caminho\para\arena-replay"
setup.bat                 :: cria .venv, instala dependências, cria config.json
notepad config.json       :: troque o device_token
run.bat                   :: inicia com a webcam (camera_source "0")
run.bat --synthetic       :: inicia sem webcam (padrão de teste)
test.bat -m "not slow"    :: testes rápidos (~1 min)
```

Manual (equivalente ao setup.bat):

```bat
py -3 -m venv .venv
.venv\Scripts\activate
pip install -r requirements-dev.txt
copy config.example.json config.json
python -m app.main
```

Ao iniciar, o terminal mostra os endereços:

```
Neste computador:  http://localhost:8000
Celular (mesma rede): http://192.168.0.10:8000
```

Na primeira execução o Windows pergunta sobre o Firewall: **permita apenas em "Redes privadas"**
(veja [docs/SEGURANCA.md](docs/SEGURANCA.md)).

## Usando o painel (celular, tablet ou computador)

1. Toque em **Celular** no topo e leia o QR Code com o celular (mesmo Wi-Fi).
2. Quando alguém aperta o botão, aparece a faixa amarela **“Lance marcado!”** com a contagem.
   Em ~6 s o replay abre sozinho e o botão **Baixar** pisca.
3. Para baixar só uma parte: arraste as **alças amarelas** na barra (o traço vermelho é o
   momento do botão) ou use **Início aqui / Fim aqui** com o vídeo parado no ponto certo.
   Atalhos: **Só o lance** (6 s antes → 2 s depois) e **5 s antes do botão**.
4. Volte e avance com **« 1 s**, **‹ quadro ›**, **1 s »**; **1×** alterna para câmera lenta (0,5× e 0,25×).
5. **Baixar trecho** gera o recorte exato no notebook (~1–3 s) e baixa o MP4.
   No iPhone o arquivo vai para o app *Arquivos* → Compartilhar → *Salvar vídeo* para ir à galeria.
6. No celular, “Adicionar à tela inicial” instala o painel como um app.

## Testar sem o ESP32

- No site, botão **Simular lance**; ou
- `python tools\simulate_button.py --token SEU_TOKEN --duplicate` (mesmo protocolo do firmware).

## Estrutura

```
arena-replay/
├── app/                    # aplicação Python
│   ├── config.py           # configurações (config.json + variáveis ARENA_*)
│   ├── sources.py          # fontes de vídeo: webcam, RTSP, sintética
│   ├── capture.py          # thread de leitura da câmera + reconexão
│   ├── recorder.py         # encoder FFmpeg + buffer circular em segmentos de 1 s
│   ├── clipper.py          # monta, verifica e publica os clipes MP4
│   ├── events.py           # acionamentos: idempotência, agrupamento, horário do toque
│   ├── storage.py          # SQLite (replays e eventos)
│   ├── exporter.py         # recortes "do segundo X ao Y" para download
│   ├── network.py          # IPs da rede local e endereço do QR Code
│   ├── services.py         # liga tudo e expõe o estado do sistema
│   ├── api.py              # rotas HTTP + segurança
│   ├── ffmpeg_tools.py     # localizar FFmpeg, verificar vídeo, miniatura
│   ├── logging_setup.py    # logs em data/logs
│   └── main.py             # python -m app.main
├── web/                    # site local (HTML/CSS/JS puro, funciona offline)
├── firmware/arena_button/  # Arduino IDE (ESP32)
├── tools/simulate_button.py
├── tests/                  # pytest (unitários + ponta a ponta com FFmpeg real)
├── docs/                   # arquitetura, plano, testes, segurança, roadmap, firmware
├── data/                   # gerado em execução (buffer, replays, banco, logs) — não versionar
├── config.example.json
├── requirements.txt / requirements-dev.txt
└── setup.bat / run.bat / test.bat
```

## Documentação

| Documento | Conteúdo |
|---|---|
| [docs/ARQUITETURA.md](docs/ARQUITETURA.md) | Decisões, fluxo de dados, concorrência, alternativas |
| [docs/PLANO_MVP.md](docs/PLANO_MVP.md) | Etapas 1–8 com critérios de conclusão e como validar |
| [docs/TESTES.md](docs/TESTES.md) | Testes automatizados, metas de desempenho, medido vs. esperado |
| [docs/FIRMWARE.md](docs/FIRMWARE.md) | Montagem do botão, Arduino IDE, diagnóstico |
| [docs/SEGURANCA.md](docs/SEGURANCA.md) | Proteções implementadas e limitações do protótipo |
| [docs/ROADMAP.md](docs/ROADMAP.md) | Evolução: RTSP, múltiplas quadras, mini PC, SaaS |

## API

| Método | Rota | Uso |
|---|---|---|
| GET | `/api/health` | vivo? |
| GET | `/api/status` | câmera, gravação, buffer, fila, disco |
| GET | `/api/replays?limit=30` | lista recente |
| GET | `/api/replays/{id}` | um replay |
| GET | `/api/replays/{id}/video[?download=1]` | MP4 (suporta Range) |
| GET | `/api/replays/{id}/thumb.jpg` | miniatura do momento do lance |
| GET | `/api/replays/{id}/filmstrip.jpg` | tira de miniaturas da barra de recorte |
| POST | `/api/replays/{id}/exports` | gera recorte `{"start": 4.1, "end": 12.1}` (segundos) |
| GET | `/api/exports/{nome}` | baixa o recorte (MP4) |
| GET | `/api/access`, `/api/access/qr.svg` | endereço na rede e QR Code para o celular |
| GET | `/api/camera/snapshot.jpg` | imagem ao vivo (baixa resolução) |
| POST | `/api/events` | botão ESP32 (`X-Arena-Token`) |
| POST | `/api/events/test` | botão "Simular lance" (desligável) |
