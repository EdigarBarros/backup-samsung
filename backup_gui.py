"""Janela do Backup Android (tkinter, já vem com o Python do Windows e do Mac).

Cada botão roda um comando do backup_android.py numa thread, com a saída
desviada para a caixa de texto. Assim a janela não trava, o botão "Parar"
funciona e tudo continua dentro de um único .exe.
"""
from __future__ import annotations

import contextlib
import os
import queue
import subprocess
import sys
import threading
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

import backup_android as ba

HERE = ba.HERE
BACKUP_MARKERS = ("arquivos", "backup_info.json", "WHATSAPP_LEIA.txt")


class App:
    def __init__(self, root: tk.Tk):
        self.root = root
        self.busy = False
        self.output: "queue.Queue[object]" = queue.Queue()
        ba.ASK_HOOK = self.ask
        root.title("Backup Android")
        root.geometry("820x640")
        root.minsize(640, 520)

        main = ttk.Frame(root, padding=14)
        main.pack(fill="both", expand=True)

        ttk.Label(main, text="Backup Android", font=("Segoe UI", 18, "bold")).pack(anchor="w")
        ttk.Label(main, text="Conecte o celular no cabo USB, com a Depuração USB ligada e a tela "
                             "desbloqueada.", foreground="#555").pack(anchor="w", pady=(0, 10))

        folder = ttk.LabelFrame(main, text="Pasta do backup", padding=8)
        folder.pack(fill="x")
        self.folder = tk.StringVar()
        ttk.Entry(folder, textvariable=self.folder).pack(side="left", fill="x", expand=True)
        ttk.Button(folder, text="Escolher...", command=self.choose_folder).pack(side="left", padx=(6, 0))
        ttk.Label(main, text="Vazio = automático (pasta Backup_<modelo> ao lado do programa). "
                             "Use um disco com espaço sobrando.",
                  foreground="#777").pack(anchor="w", pady=(2, 10))

        grid = ttk.Frame(main)
        grid.pack(fill="x")
        buttons = [
            ("Verificar celular", self.check),
            ("1. Backup COMPLETO", self.backup),
            ("2. Backup do WhatsApp", self.whatsapp),
            ("3. Restaurar WhatsApp em outro Android", self.whatsapp_restore),
            ("4. Enviar para o Google Drive", self.drive),
            ("5. Preparar para o iPhone", self.iphone),
            ("Checklist antes de vender", self.checklist),
            ("Instalar ADB e rclone", self.install),
        ]
        self.buttons = []
        for i, (label, cmd) in enumerate(buttons):
            b = ttk.Button(grid, text=label, command=cmd)
            b.grid(row=i // 2, column=i % 2, sticky="ew", padx=3, pady=3, ipady=6)
            self.buttons.append(b)
        grid.columnconfigure(0, weight=1)
        grid.columnconfigure(1, weight=1)

        bar = ttk.Frame(main)
        bar.pack(fill="x", pady=(10, 4))
        self.status = tk.StringVar(value="Pronto.")
        ttk.Label(bar, textvariable=self.status, font=("Segoe UI", 10, "bold")).pack(side="left")
        self.stop_btn = ttk.Button(bar, text="Parar", command=self.stop, state="disabled")
        self.stop_btn.pack(side="right")
        ttk.Button(bar, text="Abrir pasta", command=self.open_folder).pack(side="right", padx=6)

        log_frame = ttk.Frame(main)
        log_frame.pack(fill="both", expand=True)
        self.log = tk.Text(log_frame, wrap="word", height=14, font=("Consolas", 10),
                           bg="#111", fg="#e6e6e6", insertbackground="#e6e6e6")
        scroll = ttk.Scrollbar(log_frame, command=self.log.yview)
        self.log.configure(yscrollcommand=scroll.set, state="disabled")
        scroll.pack(side="right", fill="y")
        self.log.pack(side="left", fill="both", expand=True)

        root.protocol("WM_DELETE_WINDOW", self.on_close)
        self.write("Bem-vindo! Na primeira vez: 'Instalar ADB e rclone' > 'Verificar celular' > "
                   "'Backup COMPLETO'.\n")
        root.after(100, self.pump)
        root.after(400, self.first_run)

    def first_run(self):
        if ba.find_adb() and ba.find_rclone():
            return
        if messagebox.askyesno(
                "Primeira vez",
                "Para conversar com o celular e com o Google Drive, o programa precisa baixar "
                "duas ferramentas gratuitas (ADB, do Google, e rclone), cerca de 30 MB.\n\n"
                "Baixar agora?"):
            self.install()

    # ---- pasta -----------------------------------------------------------
    def choose_folder(self):
        chosen = filedialog.askdirectory(title="Onde salvar / qual backup usar")
        if not chosen:
            return
        p = Path(chosen)
        is_backup = any((p / m).exists() for m in BACKUP_MARKERS)
        if not is_backup and any(p.iterdir()):
            p = p / "Backup_Celular"  # não espalhar arquivos numa pasta que já tem coisas
        self.folder.set(str(p))

    def folder_arg(self, flag: str) -> list:
        value = self.folder.get().strip()
        return [flag, value] if value else []

    def open_folder(self):
        value = self.folder.get().strip()
        target = Path(value) if value else HERE
        if not target.exists():
            target = HERE
        if sys.platform.startswith("win"):
            os.startfile(target)  # type: ignore[attr-defined]
        elif sys.platform == "darwin":
            subprocess.Popen(["open", str(target)])
        else:
            subprocess.Popen(["xdg-open", str(target)])

    # ---- ações -----------------------------------------------------------
    def check(self):
        self.run("Verificando celular", ["verificar"])

    def backup(self):
        self.run("Fazendo backup completo", ["backup"] + self.folder_arg("--destino"))

    def whatsapp(self):
        # A pergunta "já fez o backup no WhatsApp?" vem do próprio comando, como janela.
        self.run("Copiando WhatsApp", ["whatsapp"] + self.folder_arg("--destino"))

    def whatsapp_restore(self):
        ok = messagebox.askyesno(
            "Restaurar WhatsApp",
            "Conecte o celular de DESTINO (o Android emprestado).\n\n"
            "Nele, o WhatsApp precisa estar INSTALADO e AINDA NÃO ABERTO.\n\nContinuar?")
        if ok:
            self.run("Restaurando WhatsApp", ["whatsapp-restaurar"] + self.folder_arg("--pasta"))

    def drive(self):
        messagebox.showinfo(
            "Google Drive",
            "Na primeira vez vai abrir o navegador para você entrar na conta Google e "
            "clicar em Permitir.\n\nO envio pode demorar horas. Se parar, é só clicar de "
            "novo: ele continua de onde parou.")
        self.run("Enviando para o Google Drive", ["drive"] + self.folder_arg("--pasta"))

    def iphone(self):
        self.run("Preparando para o iPhone", ["iphone"] + self.folder_arg("--pasta"))

    def checklist(self):
        self.run("Checklist", ["checklist"])

    def install(self):
        self.run("Instalando ferramentas", ["instalar"])

    # ---- execução --------------------------------------------------------
    def run(self, title: str, args: list):
        if self.busy:
            return
        self.set_busy(True, title + "...")
        self.write(f"\n=== {title} ===\n")
        ba.CANCEL.clear()
        threading.Thread(target=self.worker, args=(args,), daemon=True).start()

    def worker(self, args: list):
        out = QueueWriter(self.output)
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(out):
            try:
                rc = ba.main(args)
            except SystemExit as exc:  # mensagens de erro do argparse/pastas
                if isinstance(exc.code, str):
                    print(exc.code)
                rc = exc.code if isinstance(exc.code, int) else 1
            except Exception as exc:  # noqa: BLE001 - mostrar qualquer erro na janela
                print(f"\nErro inesperado: {exc}")
                rc = 1
        self.output.put(("fim", rc))

    def ask(self, question: str, default: bool) -> bool:
        """Chamado pela thread do backup: mostra a pergunta na janela e espera."""
        done = threading.Event()
        answer = {"value": default}

        def show():
            answer["value"] = messagebox.askyesno("Confirmação", question, parent=self.root)
            done.set()

        self.root.after(0, show)
        done.wait()
        return answer["value"]

    def pump(self):
        try:
            while True:
                item = self.output.get_nowait()
                if isinstance(item, tuple):
                    rc = item[1]
                    msg = {0: "Concluído!", 2: "Concluído com avisos (leia acima).",
                           130: "Parado. Clique de novo para continuar."}.get(
                        rc, "Terminou com erro (leia acima).")
                    self.write(f"\n>>> {msg}\n")
                    self.set_busy(False, msg)
                else:
                    self.write(item)
        except queue.Empty:
            pass
        self.root.after(100, self.pump)

    def write(self, text: str):
        self.log.configure(state="normal")
        text = text.replace("\r\n", "\n")
        parts = text.split("\r")
        for i, part in enumerate(parts):
            if i > 0:  # \r = linha de progresso: reescreve a linha atual
                self.log.delete("end-1c linestart", "end-1c")
            self.log.insert("end", part)
        self.log.see("end")
        self.log.configure(state="disabled")

    def set_busy(self, busy: bool, status: str):
        self.busy = busy
        self.status.set(status)
        for b in self.buttons:
            b.configure(state="disabled" if busy else "normal")
        self.stop_btn.configure(state="normal" if busy else "disabled")

    def stop(self):
        if self.busy and messagebox.askyesno(
                "Parar", "Parar agora? Depois é só clicar de novo que continua de onde parou."):
            ba.CANCEL.set()
            self.status.set("Parando...")

    def on_close(self):
        if self.busy:
            if not messagebox.askyesno("Sair", "Tem uma tarefa rodando. Parar e sair?"):
                return
            ba.CANCEL.set()
        self.root.destroy()


class QueueWriter:
    """Arquivo falso: tudo que o backup imprime vai para a fila da janela."""

    def __init__(self, q: "queue.Queue[object]"):
        self.q = q

    def write(self, text: str) -> int:
        if text:
            self.q.put(text)
        return len(text)

    def flush(self) -> None:
        pass

    def isatty(self) -> bool:
        return False


def main() -> int:
    os.chdir(HERE)  # backups e ferramentas ficam ao lado do programa
    root = tk.Tk()
    try:
        ttk.Style().theme_use("vista" if sys.platform.startswith("win") else "clam")
    except tk.TclError:
        pass
    App(root)
    root.mainloop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
