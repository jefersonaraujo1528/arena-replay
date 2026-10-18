# Arquitetura do Arena Replay (MVP)

## 1. Visão geral

```
                         ┌─────────────────────── Notebook ───────────────────────────┐
 ┌─────────┐   quadros   │ ┌──────────┐ último  ┌───────────────┐ bgr24  ┌──────────┐  │
 │ Webcam  │────────────▶│ │ Capture  │ quadro  │ SegmentRecorder│──pipe─▶│  FFmpeg  │  │
 │ / RTSP  │             │ │ (thread) │────────▶│ relógio 30 fps │        │  x264    │  │
 └─────────┘             │ └──────────┘         └───────────────┘        └────┬─────┘  │
                         │                                                    │ .ts 1 s │
                         │                       data/buffer/session_*/seg_*.ts ◀┘       │
 ┌─────────┐  POST       │ ┌──────────┐ replay  ┌──────────────┐  concat  ┌──────────┐  │
 │ ESP32   │────────────▶│ │ Events   │────────▶│ ClipService  │─────────▶│ replays/ │  │
 │ botão   │◀── 201/200 ─│ │ dedupe   │  fila   │ (thread)     │ verifica │  *.mp4   │  │
 └─────────┘             │ └────┬─────┘         └──────┬───────┘          └────┬─────┘  │
                         │      └──────── SQLite (eventos, replays) ◀──────────┘        │
 ┌─────────┐  HTTP       │ ┌──────────────────────────────────────────┐                 │
 │ Celular │◀───────────▶│ │ FastAPI: /api/*  +  site estático web/   │                 │
 └─────────┘             │ └──────────────────────────────────────────┘                 │
                         └─────────────────────────────────────────────────────────────┘
```

Threads independentes: **capture** (lê a câmera), **recorder** (envia quadros ao FFmpeg),
**rec-watchdog**, **clip-N** (gera clipes) e o servidor HTTP. Nenhuma espera pela outra
em operação normal: um clipe demorado não afeta a gravação.

## 2. Decisões principais

### 2.1 Buffer em disco com segmentos de 1 s (e não em memória)

| Opção | Prós | Contras |
|---|---|---|
| Quadros brutos em RAM (deque) | Simples | 10 s de 720p30 ≈ 830 MB; precisaria codificar o clipe inteiro na hora (CPU) |
| JPEG em RAM | ~60 MB | Codificar JPEG a 30 fps em Python pesa; ainda recodifica tudo no clipe |
| **Segmentos H.264 de 1 s no disco** ✅ | ~20 KB–150 KB por segundo; clipe = copiar segmentos (sem recodificar, ~1 s); sobrevive a picos de CPU | Recorte com precisão de 1 s (ver 2.3) |
| FFmpeg lendo a webcam direto (dshow) | Menos Python | Perdemos o controle de quadros, estado da câmera e a tela "sem sinal"; RTSP/webcam ficam com código diferente |

O buffer guarda `pre + post + buffer_extra_seconds` (padrão 45 s) e apaga o resto.
Segmentos usados por um replay pendente são protegidos por *lease* até o clipe terminar.

### 2.2 Relógio próprio de quadros (CFR)

A thread `recorder` “bate” 30 vezes por segundo e envia ao FFmpeg o quadro mais recente:

- câmera mais lenta que 30 fps → repete o último quadro;
- câmera parada há mais de 2 s → envia a tela **SEM SINAL DA CAMERA**;
- CPU atrasou mais de 0,5 s → ressincroniza e conta `dropped_ticks`.

Resultado: a linha do tempo do vídeo segue o relógio real e sabemos o horário exato do primeiro
quadro de cada segmento. Isso é o que permite cortar “10 s antes do toque” com confiança.
Keyframes são forçados a cada segmento, então cada `.ts` é decodificável sozinho.

### 2.3 Recorte: cópia de segmentos (padrão) x recorte exato

- `precise_trim: false` (padrão): junta os segmentos que cobrem `[toque − 10 s, toque + 5 s]`
  com `-c copy`. Custo de CPU quase zero; duração real **15–17 s** (até +1 s de cada lado).
- `precise_trim: true`: recodifica com `-ss/-t` para exatamente 15 s. Usa CPU por alguns segundos.

### 2.4 Horário do lance = horário do clique

O ESP32 envia `age_ms` (há quanto tempo o botão foi apertado). O servidor calcula
`press_time = recebido − age_ms`. Retentativas por Wi-Fi ruim não deslocam o replay.
`age_ms` é limitado a `max_event_age_ms` (30 s).

### 2.5 Idempotência e acionamentos próximos

- `event_id` é chave primária no SQLite: o mesmo evento retorna `duplicate` com o mesmo `replay_id`.
- Dois toques diferentes a menos de `merge_window_seconds` (3 s) viram **um** replay
  (`merged`, contador `presses`). Acima disso, cada toque gera seu clipe, mesmo que se sobreponham.

### 2.6 “Só é sucesso depois de verificar”

O clipe é escrito em `data/tmp/<id>.part.mp4`, aberto com OpenCV (primeiro e último quadros,
duração dentro da faixa esperada) e só então movido com `os.replace` (atômico) para
`data/replays/`. O estado `done` é gravado depois disso.

### 2.7 Persistência: SQLite

Uma conexão com lock + WAL. Volume do MVP é baixo (eventos por minuto, não por segundo).
Na inicialização: replays pendentes viram `error` (“interrompido pela reinicialização”),
arquivos `.part` são apagados e replays `done` sem arquivo viram `error`.

### 2.8 Sem variáveis globais

Tudo é criado em `Services.build()` e injetado. Os testes criam instâncias isoladas com
câmera sintética e diretório temporário.

## 3. Estados de um replay

```
waiting_recording ──(gravação passou de toque+5s)──▶ processing ──(verificado)──▶ done
        │                                                │
        └──── timeout (câmera/encoder parados) ──────────┴──── falha / disco ─────▶ error
```

## 4. Tratamento de falhas

| Falha | Comportamento |
|---|---|
| Câmera indisponível na partida | Tenta reabrir (1 s → 10 s); estado `unavailable`; gravação continua com “SEM SINAL” |
| Falha de leitura de quadros | 15 falhas seguidas → fecha e reabre a câmera (`reconnects`) |
| FFmpeg encerra/trava | Descarta o segmento parcial e reinicia o encoder; watchdog mata o processo se travar 5 s |
| ESP32 / Wi-Fi caiu | Firmware guarda até 8 toques e reenvia até 25 s; servidor deduplica |
| Evento recebido 2 vezes | `duplicate`, mesmo replay |
| Toques muito próximos | `merged` (< 3 s) |
| Pouco espaço | Replay falha com mensagem “Pouco espaço em disco”; retenção apaga os mais antigos (`max_replays`) |
| Vídeo corrompido / duração errada | Replay `error`, arquivo não é publicado |
| Buffer insuficiente (toque logo após ligar) | Gera o que existe e avisa “Só havia X s gravados antes do lance” |
| Reinicialização do notebook | Pendentes viram `error` com mensagem clara; buffer da sessão anterior é limpo |

## 5. Preparado para evoluir

- **Câmera IP/RTSP:** `camera_source: "rtsp://usuario:senha@ip/stream"` já funciona via OpenCV/FFmpeg
  (testar latência e reconexão com a câmera real).
- **Múltiplas câmeras/quadras:** hoje há um conjunto Capture→Recorder→Clip por processo. A evolução
  natural é um `Services` por câmera com `camera_id` no banco e no `device_id` do botão
  (ver [ROADMAP.md](ROADMAP.md)).

## 6. Limitações conhecidas do MVP

- Sem áudio (o microfone do notebook capta mal a quadra; fica para câmeras IP).
- Precisão de recorte de 1 s no modo padrão.
- Mudança brusca do relógio do Windows durante a gravação pode deslocar o recorte de replays daquele momento.
- Um processo = uma câmera.
