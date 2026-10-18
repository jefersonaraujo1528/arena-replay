# Segurança

## Implementado

| Ameaça | Proteção |
|---|---|
| Qualquer aparelho da rede disparar replays | `X-Arena-Token` obrigatório em `/api/events` quando `device_token` está definido (comparação em tempo constante) |
| Dados malformados | Pydantic: `event_id`/`device_id` só `[A-Za-z0-9_-]`, até 64/32 caracteres; `age_ms` 0–600000; campos extras rejeitados (422) |
| Corpos enormes | POST com mais de 2 KB → 413 |
| Nomes de arquivo maliciosos / path traversal | IDs de replay seguem `AAAAMMDD-HHMMSS-hex6`; o arquivo é resolvido e precisa estar **dentro** de `data/replays` |
| Listagem de diretórios de vídeo | Não há `StaticFiles` sobre `data/`; vídeos só pela rota que valida o ID |
| Injeção de script no site | Conteúdo inserido com `textContent`; CSP `default-src 'self'`; `X-Frame-Options: DENY`; `nosniff` |
| Exposição de documentação da API | `/docs` e `/openapi.json` desativados |
| Senhas no repositório | `config.json` e `firmware/.../secrets.h` no `.gitignore`; só arquivos `*.example` são versionados |
| Token aparecendo na interface | `/api/status` não inclui o token |

## Rede (faça na instalação)

1. Wi-Fi da arena com **WPA2/WPA3**; o ideal é uma rede só para o sistema (ou rede de convidados
   isolada para os clientes, se o roteador permitir que acessem o notebook).
2. Firewall do Windows: na primeira execução, permita **somente “Redes privadas”**. Para criar a regra manualmente
   (PowerShell como administrador):
   ```powershell
   New-NetFirewallRule -DisplayName "Arena Replay" -Direction Inbound -Protocol TCP -LocalPort 8000 -Action Allow -Profile Private
   ```
   E marque a rede Wi-Fi da arena como **Privada** (*Configurações → Rede → Propriedades*).
3. **Não** faça redirecionamento de porta (port forwarding) no roteador. O sistema não deve ser acessível pela internet.

## Limitações do protótipo (aceitas conscientemente)

- **HTTP sem TLS:** na rede local o token trafega em texto. Quem estiver na mesma rede e capturar o tráfego
  pode disparar replays. Impacto baixo (só gera replays), mas por isso a rede deve ser protegida.
  Evolução: HTTPS com certificado local ou HMAC por evento (token + timestamp + assinatura).
- **Visualização aberta na rede local:** qualquer pessoa conectada vê e baixa os replays. Evolução:
  link por replay com código curto/QR e expiração.
- **Botão de teste no site:** qualquer pessoa na rede pode simular lances. Desligue em produção:
  `"allow_test_trigger": false`.
- **Token fixo no firmware:** quem tiver acesso físico ao ESP32 pode extraí-lo. Trocar o token
  se um botão for perdido.
- **LGPD:** replays contêm imagem de pessoas. Antes de comercializar: aviso visível na quadra, política de
  retenção (`max_replays`) e termo com a arena definindo quem é o controlador dos dados.
