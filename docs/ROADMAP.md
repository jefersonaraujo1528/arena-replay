# Evolução técnica e comercial

Nada abaixo está implementado. Ordem pensada para maximizar valor com o menor risco.

## Fase 1 — Piloto em 1 arena (pós-MVP)
- Inicialização automática com o Windows (serviço via NSSM ou Agendador de Tarefas).
- **Câmera IP RTSP** fixa no alto da quadra (já suportada por `camera_source: "rtsp://..."`; validar
  latência e reconexão). Faixa: R$ 250–600 por câmera Full HD com RTSP (pesquisar modelo antes de comprar).
- QR Code impresso na quadra → site local.
- Marca d'água com o logo da arena (filtro `overlay` do FFmpeg no modo `precise_trim`).
- `allow_test_trigger: false` e token próprio por botão.

## Fase 2 — Produto instalável
- **Mini PC dedicado** (Intel N100, ~R$ 900–1.400) com Linux ou Windows, no lugar do notebook.
  Raspberry Pi 5 funciona para 1 câmera 1080p com encoder em hardware limitado; mini PC x86 é mais simples.
- **Múltiplas câmeras/quadras:** um `Services` por câmera; tabela `cameras`; `device_id` do botão → câmera.
  Encoder por hardware (Intel QuickSync `h264_qsv`) para várias câmeras no mesmo equipamento.
- Painel administrativo: quadras, botões, retenção, status de saúde.
- Retenção por idade e por GB, além de `max_replays`.

## Fase 3 — Serviço comercial (SaaS)
- **Sincronização com a nuvem** quando houver internet: o equipamento local continua funcionando
  offline e envia replays para um storage (ex.: S3/R2) em segundo plano, com fila persistente.
- Página pública por replay com link curto, expiração e compartilhamento (WhatsApp/Instagram).
- Personalização da marca da arena (logo, cores, vinheta) e patrocinadores no vídeo.
- Multi-arena: cada equipamento autentica na nuvem com credencial própria; painel por arena.
- Cobrança: assinatura mensal por quadra (ver material comercial em `../comercial`).
- Monitoramento remoto: heartbeat do equipamento, alertas de câmera/disco.

## Riscos a acompanhar
- Qualidade de imagem da webcam do notebook é ruim para vender — o piloto comercial deve usar câmera IP.
- Wi-Fi da arena instável → considerar botão com cabo (ESP32 com Ethernet/PoE) em quadras grandes.
- LGPD: consentimento/aviso, retenção e contrato com a arena.
