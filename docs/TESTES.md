# Testes e metas de desempenho

## Como rodar

```bat
test.bat                  :: todos (~1,5 min)
test.bat -m "not slow"    :: sem o clipe completo de 15 s
```

Os testes usam **câmera sintética** (não precisam de webcam) e o **FFmpeg real**.

## Cobertura

| Requisito | Teste |
|---|---|
| Captura contínua | `test_capture_is_continuous` |
| Buffer limitado (não guarda a partida) | `test_buffer_is_bounded` |
| Recepção do evento + confirmação | `test_api_event_flow_and_download` |
| Geração do clipe / H.264 / faststart / miniatura | `test_clip_generation_duration_and_playback` |
| Duração 10 s + 5 s | `test_full_length_clip` (marcado `slow`) |
| Recorte exato | `test_precise_trim_duration` |
| Eventos duplicados | `test_duplicate_event_creates_single_replay`, `test_api_event_flow_and_download` |
| Toques próximos / consecutivos | `test_close_presses_are_merged`, `test_consecutive_presses_generate_independent_clips` |
| Horário do toque (`age_ms`) | `test_age_ms_moves_press_time_back_and_is_clamped` |
| Reprodução no navegador (Range/206) | `test_api_event_flow_and_download` |
| Funciona sem internet | `test_web_ui_has_no_external_dependencies` |
| Câmera caiu | `test_camera_loss_is_reported_and_recording_continues` |
| FFmpeg morreu | `test_encoder_crash_is_recovered` |
| Pouco disco | `test_low_disk_space_fails_with_clear_message` |
| Buffer insuficiente | `test_press_right_after_start_warns_about_short_buffer` |
| Reinicialização | `test_restart_recovery_marks_interrupted_jobs` |
| Persistência / migração do banco | `test_database_persists_across_reopen`, `test_database_migrates_old_schema` |
| Recorte com duração exata + cache | `test_export_trim_is_precise_and_cached` |
| Validação do recorte / nomes maliciosos | `test_export_validation`, `test_export_download_blocks_bad_names`, `test_export_not_ready_and_unknown` |
| Momento do botão, miniaturas, QR | `test_replay_json_has_editor_fields`, `test_access_qr` |
| Limpeza de recortes antigos | `test_old_exports_are_cleaned` |
| Segurança (token, validação, traversal, tamanho) | `test_api_rejects_bad_token`, `test_api_validates_event_body`, `test_api_blocks_path_traversal`, `test_api_rejects_large_body` |

## Metas de desempenho

| Métrica | Meta MVP | Medido (ver ambiente abaixo) | Notebook Windows real |
|---|---|---|---|
| Replay pronto após o toque | ≤ 8 s (5 s são o pós-lance) | **6,0–6,4 s** (3 medições) | a medir |
| Duração do clipe (modo cópia) | 15–17 s | **16,0 s** | a medir |
| fps gravado | 30 (≥ 20 aceitável) | **29,9–30,1** (sintético) | a medir com a webcam |
| `dropped_ticks` | < 1% | **0** em ~1 min | a medir em 2 h |
| CPU (720p30) | < 50% de 1 núcleo, total | Python ~29% + FFmpeg ~17% de 1 núcleo | a medir |
| Memória (processo Python) | < 300 MB | ~145 MB | a medir |
| Buffer em disco | < 20 MB | < 1 MB (padrão sintético comprime muito) | webcam real: estimado 5–15 MB |
| Tamanho do replay | < 10 MB | 0,3 MB (sintético) | webcam real: estimado 3–8 MB |

**Ambiente da medição:** Mac Apple Silicon, Python 3.9, OpenCV 5.0, FFmpeg 7.1 (imageio-ffmpeg),
câmera sintética 1280x720@30. Imagem sintética comprime muito mais que vídeo real, então tamanho e
CPU **precisam ser medidos de novo** no notebook Windows com a webcam. Os valores “estimado” não foram testados.

## Resultado da última execução

```
56 passed in 143.67s
```
(macOS, mesmo ambiente acima.) Ainda **não executado** em Windows nem com webcam/ESP32 reais —
siga o roteiro manual da Etapa 8 em [PLANO_MVP.md](PLANO_MVP.md) e registre aqui.

### Interface (verificado por emulação, não em aparelho físico)

Brave headless com emulação de dispositivo: 375 px (celular, sem rolagem horizontal), 820 px (tablet)
e 1440 px (computador). No celular emulado: preset “Só o lance” + “Baixar trecho” gerou recorte de 8,0 s.
**Falta testar em iPhone/Android reais** (toque nas alças, download, Safari).

### Registro manual (preencher)

| Data | Notebook | Teste | Resultado |
|---|---|---|---|
| | | | |
