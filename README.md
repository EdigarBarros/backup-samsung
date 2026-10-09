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
- O **histórico de conversas do WhatsApp** em formato que o iPhone aceite. O arquivo
  `msgstore.db.crypt` é criptografado e só restaura em Android.
- MMS e mensagens RCS ("Chat").
- Senhas, apps de banco e autenticadores 2FA.
- Samsung Notes, Samsung Pass e Samsung Saúde.

Isso fica registrado no `RELATORIO.txt` de cada backup, e o comando `checklist`
explica como resolver cada item.

## Passo a passo

### 1. Prepare o computador

Instale o **Python 3** (https://www.python.org/downloads/). No Windows, marque
**"Add Python to PATH"** na instalação.

Baixe este projeto (botão **Code > Download ZIP** no GitHub) e extraia numa pasta
de um disco com **espaço livre maior que o usado no celular**.

### 2. Prepare o celular

1. **Configurações > Sobre o telefone > Informações do software** e toque **7 vezes**
   em **Número de compilação**. Isso ativa as Opções do desenvolvedor.
2. **Configurações > Opções do desenvolvedor > Depuração USB**: ligue.
3. Conecte o cabo USB, desbloqueie a tela e toque em **Permitir** na janela
   "Permitir depuração USB?" (marque "Sempre permitir").

> Windows + Samsung: se o celular não aparecer, instale o *Samsung Android USB Driver*
> (site de desenvolvedores da Samsung).

### 3. Rode o programa

- **Windows:** dois cliques em `INICIAR-WINDOWS.bat`
- **Mac/Linux:** `./iniciar-mac-linux.sh`

Aparece este menu:

```
  1. Verificar se o celular está conectado
  2. Fazer BACKUP COMPLETO
  3. Enviar o backup para o GOOGLE DRIVE
  4. Preparar arquivos para o IPHONE
  5. Checklist antes de vender
  6. Instalar ferramentas (ADB e rclone)
```

Na primeira vez, use a ordem **6 → 1 → 2**.

O backup fica na pasta `Backup_<fabricante>_<modelo>`, ao lado do programa.

### 4a. Mandar para o Google Drive

Opção **3** do menu. Na primeira vez abre o navegador para você entrar na conta
Google. Quem faz o envio é o [rclone](https://rclone.org), que é open source. O
programa confere o espaço livre no Drive, envia, continua de onde parou se cair, e
no fim confere se tudo chegou.

> O Drive gratuito tem 15 GB. Se o backup for maior, o programa avisa. Aí você
> pode assinar o Google One, mandar só uma parte (`drive --subpasta arquivos/DCIM`)
> ou guardar o resto num HD externo.

### 4b. Passar para o iPhone

Opção **4** do menu. Ela cria `Para_iPhone/` com:

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

## Linha de comando

```bash
python backup_android.py backup                         # tudo
python backup_android.py backup --somente fotos,whatsapp,contatos
python backup_android.py backup --destino "D:\Backup S24" --apks
python backup_android.py drive  --pasta "D:\Backup S24"
python backup_android.py iphone --pasta "D:\Backup S24"
python backup_android.py checklist
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

## Testes

```bash
python -m unittest discover -s tests -v
```

Os testes rodam um backup completo contra um celular simulado (`tests/fake_adb.py`).
