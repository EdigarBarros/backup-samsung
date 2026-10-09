# Backup Android

Faz backup de **qualquer celular Android** pelo cabo USB e depois:

- envia tudo para o **Google Drive**, ou
- organiza fotos, contatos e agenda para importar num **iPhone**.

Funciona no Windows, no macOS e no Linux. Só precisa do Python e do ADB, e o
próprio programa baixa o ADB pra você.

## O que entra no backup

| Item | Como fica salvo | Serve para |
|---|---|---|
| Fotos, vídeos, prints, Downloads, Documentos, áudios e gravações | `arquivos/` (mesma estrutura do celular) | qualquer lugar |
| Mídias do WhatsApp/Telegram (fotos, vídeos, áudios, documentos) | `arquivos/Android/media/...` | qualquer lugar |
| Cartão SD | `cartao_sd/` | qualquer lugar |
| Contatos | `contatos/contatos.vcf` + `.csv` | iPhone, Google, Excel |
| SMS | `mensagens/` (`.xml` do *SMS Backup & Restore*, `.json` e conversas em `.txt`) | restaurar em Android / consultar |
| Registro de chamadas | `chamadas/chamadas.csv` | consulta / Excel |
| Agenda | `calendario/calendario.ics` | iPhone, Google Agenda |
| Lista de apps | `apps/apps_instalados.txt` (+ APKs com `--apks`) | reinstalar |

O backup é **retomável**: se o cabo soltar ou você fechar o programa, é só rodar
de novo. Ele pula o que já foi copiado e baixa só o que falta. Os arquivos mantêm a
data original, então as fotos continuam na ordem certa na galeria nova.

### O que NÃO dá para copiar pelo cabo (sem root)

- Dados internos dos apps (logins, jogos, configurações).
- O **histórico do WhatsApp** direto pro iPhone. O backup (`msgstore.db.crypt`) é
  criptografado e só restaura em Android. Veja a seção do WhatsApp abaixo para o caminho
  que funciona.
- MMS e mensagens RCS ("Chat").
- Senhas, apps de banco e autenticadores 2FA.
- Samsung Notes, Samsung Pass e Samsung Saúde.

Isso fica registrado no `RELATORIO.txt` de cada backup, e o comando `checklist`
explica como resolver cada item.

## Passo a passo

### 1. Baixe o app (Windows)

1. Abra a página **Releases** do repositório (coluna da direita no GitHub) e baixe o
   **BackupAndroid.exe**.
2. Crie uma pasta num disco com **espaço livre maior que o usado no celular**
   (ex.: `D:\Backup Celular`) e coloque o `.exe` lá dentro. Os backups ficam ao lado dele.
3. Abra com dois cliques. Se o Windows mostrar "O Windows protegeu o computador", clique
   em **Mais informações > Executar assim mesmo**. O aviso aparece porque o app não tem
   assinatura digital paga, não porque tem vírus.
4. Na primeira vez, o app oferece baixar o ADB (Google) e o rclone. Aceite.

> Mac ou Linux: instale o Python 3 e rode `python3 backup_android.py` (abre a mesma
> janela). Sem janela: `python3 backup_android.py menu`.

### 2. Prepare o celular

1. **Configurações > Sobre o telefone > Informações do software** e toque **7 vezes**
   em **Número de compilação**. Isso ativa as Opções do desenvolvedor.
2. **Configurações > Opções do desenvolvedor > Depuração USB**: ligue.
3. Conecte o cabo USB, desbloqueie a tela e toque em **Permitir** na janela
   "Permitir depuração USB?" (marque "Sempre permitir").

> Windows + Samsung: se o celular não aparecer, instale o *Samsung Android USB Driver*
> (site de desenvolvedores da Samsung).

### 3. Use o app

```
┌────────────────────────────────────────────────────────────┐
│ Backup Android                                             │
│ Pasta do backup: [________________________] [Escolher...]  │
│ [ Verificar celular ]          [ 1. Backup COMPLETO ]       │
│ [ 2. Backup do WhatsApp ]      [ 3. Restaurar WhatsApp ]    │
│ [ 4. Enviar p/ Google Drive ]  [ 5. Preparar p/ iPhone ]    │
│ [ Checklist antes de vender ]  [ Instalar ADB e rclone ]    │
│ Concluído!                        [Abrir pasta] [Parar]    │
│ ┌────────────────────────────────────────────────────────┐ │
│ │ [ 45.2%] 1234/5000 arquivos | 12.3 GB de 40.1 GB ...   │ │
│ └────────────────────────────────────────────────────────┘ │
└────────────────────────────────────────────────────────────┘
```

Ordem na primeira vez: **Verificar celular → 1. Backup COMPLETO**.
O **Parar** pode ser usado a qualquer momento: ao clicar de novo, o backup continua de
onde parou.

### 4. WhatsApp: guardar agora, restaurar depois

O backup do WhatsApp **não abre direto no iPhone**, porque o iPhone só restaura
backups do iCloud. Mas dá para guardar agora e usar depois neste caminho:

**backup (botão 2) → outro Android com o MESMO NÚMERO (botão 3) → restaurar →
"Mover para iOS" desse Android para o iPhone.**

1. No WhatsApp: **Configurações > Conversas > Backup de conversas > Fazer backup**.
2. Botão **2. Backup do WhatsApp**. O programa copia conversas, mídias e configurações, **mostra
   a data do backup e avisa se ele não for de hoje**. Também cria o arquivo
   `WHATSAPP_LEIA.txt` com o passo a passo da restauração.
3. Quando tiver o Android emprestado: instale o WhatsApp **sem abrir**, conecte no PC
   e use o botão **3. Restaurar WhatsApp**. Depois abra o WhatsApp, confirme seu número e toque em
   **Restaurar**.
4. No iPhone novo (ou apagado), use o **Mover para iOS** a partir desse Android e
   marque o WhatsApp.

> Se o **backup criptografado de ponta a ponta** estiver ativo no WhatsApp, guarde a
> senha ou a chave de 64 dígitos. Sem ela, ninguém recupera as conversas.
>
> Garantia extra: deixe também o backup do WhatsApp no Google Drive (passo 1). No
> Android emprestado, logado na mesma conta Google, ele restaura de lá.

### 5a. Mandar para o Google Drive

Botão **4. Enviar para o Google Drive**. Na primeira vez abre o navegador para você entrar na conta
Google. Quem faz o envio é o [rclone](https://rclone.org), que é open source. O
programa confere o espaço livre no Drive, envia, continua de onde parou se cair, e
no fim confere se tudo chegou.

> O Drive gratuito tem 15 GB. Se o backup for maior, o programa avisa. Aí você
> pode assinar o Google One, mandar só uma parte (`drive --subpasta arquivos/DCIM`)
> ou guardar o resto num HD externo.

### 5b. Passar para o iPhone

Botão **5. Preparar para o iPhone**. Ele cria `Para_iPhone/` com:

- `Fotos_e_Videos/`: câmera, prints, downloads e mídias do WhatsApp, juntos e
  prontos pra mandar ao iCloud ou ao Google Fotos (sem ocupar espaço duplicado no
  disco);
- `contatos.vcf` e `calendario.ics`;
- `conversas/`: os SMS em texto;
- `COMO_PASSAR_PRO_IPHONE.txt`: o passo a passo.

> **Importante:** o jeito mais completo de ir para o iPhone é o app **"Mover para iOS"**
> da Apple, usado na configuração inicial do iPhone. Ele é o **único** que leva o
> histórico do WhatsApp e os SMS para dentro do iPhone, e **precisa do Android
> funcionando**. Faça isso **antes** de resetar e vender o celular. Use este backup
> como cópia de segurança e para tudo o que o Mover para iOS não leva.

## Linha de comando (opcional, com Python)

```bash
python backup_android.py backup                         # tudo
python backup_android.py backup --somente fotos,whatsapp,contatos
python backup_android.py backup --destino "D:\Backup S24" --apks
python backup_android.py drive  --pasta "D:\Backup S24"
python backup_android.py iphone --pasta "D:\Backup S24"
python backup_android.py checklist
python backup_android.py whatsapp
python backup_android.py whatsapp-restaurar --pasta "D:\Backup S24"   # no outro Android
```

Categorias aceitas no `--somente`: `fotos`, `whatsapp`, `telegram`, `downloads`,
`documentos`, `audios`, `arquivos` (memória inteira), `sd`, `contatos`, `sms`,
`chamadas`, `calendario`, `apps`.

## Se contatos ou SMS aparecerem como "bloqueado pelo aparelho"

Alguns fabricantes bloqueiam a leitura de contatos e SMS pelo cabo. Nesse caso,
exporte pelo próprio celular:

- **Contatos:** app Contatos > menu > Gerenciar contatos > Importar/exportar >
  Exportar > Memória interna. Depois rode o backup de novo para o `.vcf` entrar junto.
  Se seus contatos estão na conta Google, eles já estão salvos em contacts.google.com.
- **SMS:** app *SMS Backup & Restore* (Play Store), que salva direto no Google Drive.

## Antes de vender

```bash
python backup_android.py checklist
```

Os itens que mais dão dor de cabeça quando esquecidos:

- transferir os **apps autenticadores** (2FA);
- **remover a conta Google e a Samsung** antes de resetar, senão o comprador fica
  travado no bloqueio de fábrica (FRP);
- cadastrar o celular novo nos **bancos**.

## Como o .exe é gerado

O GitHub Actions (`.github/workflows/build.yml`) roda os testes, gera o
`BackupAndroid.exe` com o PyInstaller num Windows, confere se ele abre e publica na
página Releases. Isso acontece a cada alteração enviada ao repositório.

## Testes

```bash
python -m unittest discover -s tests -v
```

Os testes rodam um backup completo contra um celular simulado (`tests/fake_adb.py`).
