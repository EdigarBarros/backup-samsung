#!/usr/bin/env python3
"""ADB falso para testes: simula um celular a partir de uma pasta local.

Variáveis de ambiente:
  FAKE_PHONE  pasta que representa /storage (contém emulated/0, XXXX-XXXX...)
  FAKE_DATA   pasta com respostas de `content query` (contacts.txt, sms.txt...)
"""
import os
import shlex
import shutil
import sys

PHONE = os.environ["FAKE_PHONE"]
DATA = os.environ.get("FAKE_DATA", "")


def local(remote):
    assert remote.startswith("/storage/"), remote
    return os.path.join(PHONE, remote[len("/storage/"):])


def do_find(cmd):
    roots = shlex.split(cmd.split("\\(")[0])[1:]
    for root in roots:
        base = local(root)
        for dirpath, dirs, files in os.walk(base):
            rel = os.path.relpath(dirpath, local("/storage/emulated/0")).replace(os.sep, "/")
            dirs[:] = [d for d in dirs if d != ".thumbnails"
                       and f"{rel}/{d}".lstrip("./") not in ("Android/data", "Android/obb")]
            for f in files:
                p = os.path.join(dirpath, f)
                remote = "/storage/" + os.path.relpath(p, PHONE).replace(os.sep, "/")
                st = os.stat(p)
                print(f"{st.st_size} {int(st.st_mtime)} {remote}")


def main():
    args = sys.argv[1:]
    if args[:1] == ["-s"]:
        args = args[2:]
    if args[:2] == ["devices", "-l"]:
        print("List of devices attached")
        print("R5CX123 device usb:1-1 product:e2sxxx model:SM_S926B device:e2s transport_id:1")
        return 0
    if args[0] == "shell":
        cmd = args[1]
        if cmd.startswith("getprop"):
            print({"ro.product.manufacturer": "samsung", "ro.product.model": "SM-S926B",
                   "ro.build.version.release": "14"}.get(cmd.split()[1], "x"))
        elif cmd.startswith("find"):
            do_find(cmd)
        elif cmd.startswith("ls /storage"):
            print("  ".join(sorted(os.listdir(PHONE))))
        elif cmd.startswith("content query"):
            uri = cmd.split("--uri ")[1].split()[0]
            name = {"content://com.android.contacts/data": "contacts.txt", "content://sms": "sms.txt",
                    "content://call_log/calls": "calls.txt",
                    "content://com.android.calendar/events": "events.txt"}[uri]
            path = os.path.join(DATA, name)
            sys.stdout.write(open(path, encoding="utf-8").read() if os.path.exists(path)
                             else "No result found.\n")
        elif cmd.startswith("pm list packages"):
            print("package:com.whatsapp\npackage:com.nubank")
        return 0
    if args[0] == "pull":
        srcs = [a for a in args[1:-1] if a != "-a"]
        dst = args[-1]
        for s in srcs:
            if not os.path.exists(local(s)):
                print(f"adb: error: remote object '{s}' does not exist", file=sys.stderr)
                return 1
            target = os.path.join(dst, s.rsplit("/", 1)[1]) if len(srcs) > 1 or os.path.isdir(dst) else dst
            shutil.copy2(local(s), target)
        return 0
    return 1


sys.exit(main())
