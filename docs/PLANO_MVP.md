# Plano de implementação do MVP

Cada etapa tem **como validar** e **critério de conclusão**. Só avance quando o critério for atendido
no notebook real. As etapas 1–6 já estão implementadas no código; a validação no hardware é sua.

Legenda: ✅ implementado e testado automaticamente aqui · 🔧 depende de validação no seu notebook/ESP32

---

## Etapa 0 — Ambiente (Windows)

```bat
python --version            :: precisa ser 3.10+
cd "C:\...\arena-replay"
setup.bat
test.bat -m "not slow"
```

**Conclusão:** todos os testes passam (`passed`, nenhum `failed`).

## Etapa 1 — Abrir a câmera do notebook ✅🔧

```bat
run.bat
```
Abra `http://localhost:8000`. O card **Sistema** mostra a imagem ao vivo e “Câmera: Ativa · ~30 fps”.

Problemas comuns:
- “Câmera indisponível”: feche Teams/Zoom/app Câmera (a webcam só aceita um programa por vez).
  Confira em *Configurações → Privacidade → Câmera* se apps da área de trabalho podem usar a câmera.
- Imagem lenta para abrir: troque `camera_backend` para `"msmf"` ou `"dshow"`.
- Notebook com duas câmeras (ex.: IR do Windows Hello): use `camera_source: "1"`.

**Conclusão:** imagem ao vivo no site e fps medido ≥ 20.

## Etapa 2 — Captura contínua ✅🔧

Deixe rodando 30 minutos. No `/api/status` acompanhe `camera.frames_total` subindo,
`recorder.restarts = 0` e `recorder.dropped_ticks` baixo (< 1% dos quadros).

**Conclusão:** 30 min sem reinício do encoder e sem crescimento de memória (Gerenciador de Tarefas).

## Etapa 3 — Manter os últimos 10 s ✅🔧

`/api/status → recorder.buffer_seconds` sobe até ~45 s e fica estável. A pasta
`data\buffer\session_*` mantém ~45 arquivos `.ts` (não cresce).

**Conclusão:** buffer estável por 30 min; pasta de buffer < 20 MB.

## Etapa 4 — Acionamento de teste ✅🔧

Botão **Simular lance** no site ou:
```bat
python tools\simulate_button.py --token SEU_TOKEN --duplicate
```
**Conclusão:** primeira resposta `201 accepted`, reenvio `200 duplicate` com o mesmo `replay_id`.

## Etapa 5 — Clipe de 10 s antes + 5 s depois ✅🔧

Com `overlay_clock: true`, cada quadro mostra o relógio. Aperte o botão olhando o relógio do
Windows e confira no vídeo: começa ~10 s antes e termina ~5 s depois.

**Conclusão:** 10 replays seguidos com duração entre 15 e 17 s, todos com o momento do toque
visível, e pronto em até ~8 s após o toque.

## Etapa 6 — Exibir no site local (notebook e celular) ✅🔧

1. Configure o Firewall (ver SEGURANCA.md) e abra no celular o endereço “Celular (mesma rede)”.
2. Toque no replay → deve tocar no próprio navegador. Teste Android (Chrome) e iPhone (Safari).
3. “Baixar MP4” salva o arquivo.

**Conclusão:** reprodução e download funcionando em pelo menos 1 Android e 1 iPhone.

## Etapa 7 — Botão físico com ESP32 🔧

Siga [FIRMWARE.md](FIRMWARE.md). Monitor Serial a 115200 baud.

**Conclusão:**
- 20 toques → 20 replays (ou agrupados, se < 3 s entre eles), nenhum duplicado;
- desligar o roteador por 10 s, apertar, religar → replay gerado com o horário do toque;
- LED indica sem Wi-Fi / enviando / confirmado.

## Etapa 8 — Falhas e confiabilidade ✅🔧

Roteiro manual (os automáticos estão em TESTES.md):

| Teste | Esperado |
|---|---|
| Tampar/desconectar a câmera durante 10 s | Site mostra “Câmera sem sinal”; replay tem aviso; volta sozinho |
| Fechar o FFmpeg no Gerenciador de Tarefas | Encoder reinicia sozinho em ~1 s; replays seguintes OK |
| Apertar 5 vezes em 2 s | Um único replay com “botão apertado 5x” |
| Reiniciar o notebook com replay processando | Replay vira “Interrompido…”; sistema volta a gravar |
| Encher o disco (ou `min_free_disk_mb` alto) | Mensagem “Pouco espaço em disco” |
| Celular em outra rede (4G) | Não acessa (esperado: sistema só local) |
| 2 horas contínuas | Sem queda; memória estável |

**Conclusão:** todos os itens com o comportamento esperado. Registre os resultados em TESTES.md.

---

## Depois do MVP (ordem sugerida)

1. Iniciar automaticamente com o Windows (Agendador de Tarefas ou NSSM como serviço).
2. QR Code fixo na quadra apontando para o site local.
3. Câmera IP RTSP posicionada no alto da quadra (maior impacto na qualidade do produto).
4. Mini PC dedicado por arena. Ver [ROADMAP.md](ROADMAP.md).
