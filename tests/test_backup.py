import os
import stat
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import backup_android as ba  # noqa: E402

CONTACTS = (
    "Row: 0 contact_id=1, mimetype=vnd.android.cursor.item/name, data1=João Silva, data2=João, data3=Silva, data4=NULL\n"
    "Row: 1 contact_id=1, mimetype=vnd.android.cursor.item/phone_v2, data1=+55 11 91234-5678, data2=2, data3=NULL, data4=NULL\n"
    "Row: 2 contact_id=1, mimetype=vnd.android.cursor.item/phone_v2, data1=11912345678, data2=2, data3=NULL, data4=NULL\n"
    "Row: 3 contact_id=1, mimetype=vnd.android.cursor.item/note, data1=linha 1\nlinha 2, com vírgula; e ponto, data2=NULL, data3=NULL, data4=NULL\n"
    "Row: 4 contact_id=2, mimetype=vnd.android.cursor.item/email_v2, data1=ana@ex.com, data2=1, data3=NULL, data4=NULL\n"
)
SMS = (
    "Row: 0 _id=1, thread_id=1, address=+5511999990000, date=1700000000000, date_sent=1700000000000, type=1, read=1, body=Oi <tudo> bem?\nNova linha & \"aspas\"\n"
    "Row: 1 _id=2, thread_id=1, address=+5511999990000, date=1700000060000, date_sent=0, type=2, read=1, body=Tudo, e você?\n"
)
CALLS = "Row: 0 number=+5511999990000, name=João, date=1700000000000, duration=65, type=3\n"
EVENTS = (
    "Row: 0 _id=7, title=Aniversário, description=NULL, eventLocation=Casa, dtstart=1700006400000, dtend=1700092800000, allDay=1, rrule=FREQ=YEARLY, duration=NULL, deleted=0\n"
    "Row: 1 _id=8, title=Reunião, description=NULL, eventLocation=NULL, dtstart=1700006400000, dtend=NULL, allDay=0, rrule=FREQ=WEEKLY, duration=P3600S, deleted=0\n"
    "Row: 2 _id=9, title=Apagado, description=NULL, eventLocation=NULL, dtstart=1700006400000, dtend=NULL, allDay=0, rrule=NULL, duration=NULL, deleted=1\n"
)


class ParserTests(unittest.TestCase):
    def test_parse_rows_with_multiline_values(self):
        rows = ba.parse_content_rows(CONTACTS, ba.CONTACT_COLUMNS)
        self.assertEqual(len(rows), 5)
        self.assertEqual(rows[3]["data1"], "linha 1\nlinha 2, com vírgula; e ponto")
        self.assertIsNone(rows[0]["data4"])

    def test_contacts_dedupe_and_vcard(self):
        contacts = ba.build_contacts(ba.parse_content_rows(CONTACTS, ba.CONTACT_COLUMNS))
        self.assertEqual([c["nome"] for c in contacts], ["ana@ex.com", "João Silva"])
        joao = contacts[1]
        self.assertEqual(len(joao["telefones"]), 1)
        card = ba.to_vcard(joao)
        self.assertIn("N:Silva;João;;;", card)
        self.assertIn("TEL;TYPE=CELL:+55 11 91234-5678", card)
        self.assertIn("NOTE:linha 1\\nlinha 2\\, com vírgula\\; e ponto", card)

    def test_sms_xml_is_valid(self):
        import xml.etree.ElementTree as ET
        msgs = ba.parse_content_rows(SMS, ba.SMS_COLUMNS)
        root = ET.fromstring(ba.sms_to_xml(msgs).encode("utf-8"))
        self.assertEqual(root.get("count"), "2")
        self.assertEqual(root[0].get("body"), 'Oi <tudo> bem?\nNova linha & "aspas"')

    def test_ics(self):
        ics, count = ba.events_to_ics(ba.parse_content_rows(EVENTS, ba.EVENT_COLUMNS))
        self.assertEqual(count, 2)
        self.assertIn("DTSTART;VALUE=DATE:", ics)
        self.assertIn("DURATION:PT3600S", ics)
        self.assertNotIn("Apagado", ics)

    def test_stat_parsing_and_sanitize(self):
        files = ba.parse_stat_output("12 1700000000 /storage/emulated/0/DCIM/a b.jpg\nlixo\n")
        self.assertEqual(files[0].path, "/storage/emulated/0/DCIM/a b.jpg")
        self.assertEqual(ba.sanitize_name('foto: "1"?.jpg'), "foto_ _1__.jpg")
        self.assertEqual(ba.sanitize_name("CON.txt"), "_CON.txt")

    def test_selection(self):
        parts, folders = ba.parse_selection("fotos,contatos")
        self.assertEqual(parts, {"arquivos", "contatos"})
        self.assertIn("DCIM", folders)
        with self.assertRaises(SystemExit):
            ba.parse_selection("bobagem")


class EndToEndTests(unittest.TestCase):
    """Roda o backup inteiro contra um celular simulado."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        phone = self.tmp / "phone"
        internal = phone / "emulated" / "0"
        files = {
            "DCIM/Camera/IMG_1.jpg": b"x" * 1000,
            "DCIM/Camera/VID_1.mp4": b"v" * 5000,
            "DCIM/.thumbnails/t.jpg": b"t",
            "Download/doc: nome?.pdf": b"pdf",
            "Android/data/com.app/secret.bin": b"s",
            "Android/media/com.whatsapp/WhatsApp/Media/WhatsApp Images/IMG-WA.jpg": b"w" * 10,
            "Pictures/.trashed-123-old.jpg": b"o",
        }
        for rel, data in files.items():
            p = internal / rel
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_bytes(data)
        (phone / "self").mkdir()
        sd = phone / "1A2B-3C4D" / "Musicas"
        sd.mkdir(parents=True)
        (sd / "song.mp3").write_bytes(b"m" * 20)
        data = self.tmp / "data"
        data.mkdir()
        for name, text in {"contacts.txt": CONTACTS, "sms.txt": SMS, "calls.txt": CALLS,
                           "events.txt": EVENTS}.items():
            (data / name).write_text(text, encoding="utf-8")

        fake = self.tmp / "adb"
        fake.write_text(f"#!/bin/sh\nexec {sys.executable} {ROOT / 'tests' / 'fake_adb.py'} \"$@\"\n")
        fake.chmod(fake.stat().st_mode | stat.S_IEXEC)
        os.environ.update(ADB=str(fake), FAKE_PHONE=str(phone), FAKE_DATA=str(data))
        self.out = self.tmp / "backup"

    @unittest.skipIf(os.name == "nt", "adb falso usa shell script")
    def test_full_backup_resume_and_iphone(self):
        self.assertEqual(ba.main(["backup", "--destino", str(self.out), "--sim"]), 0)
        a = self.out / "arquivos"
        self.assertEqual((a / "DCIM/Camera/VID_1.mp4").stat().st_size, 5000)
        self.assertTrue((a / "Download/doc_ nome_.pdf").exists())
        self.assertFalse((a / "DCIM/.thumbnails").exists())
        self.assertFalse((a / "Android/data").exists())
        self.assertFalse((a / "Pictures/.trashed-123-old.jpg").exists())
        self.assertTrue((self.out / "cartao_sd/1A2B-3C4D/Musicas/song.mp3").exists())
        self.assertIn("BEGIN:VCARD", (self.out / "contatos/contatos.vcf").read_text("utf-8"))
        self.assertTrue((self.out / "mensagens/sms_backup_restore.xml").exists())
        self.assertTrue((self.out / "chamadas/chamadas.csv").exists())
        self.assertTrue((self.out / "calendario/calendario.ics").exists())
        self.assertTrue((self.out / "RELATORIO.txt").exists())

        # Retomada: arquivo truncado é copiado de novo, os outros são pulados.
        (a / "DCIM/Camera/VID_1.mp4").write_bytes(b"v" * 10)
        self.assertEqual(ba.main(["backup", "--destino", str(self.out), "--sim"]), 0)
        self.assertEqual((a / "DCIM/Camera/VID_1.mp4").stat().st_size, 5000)

        self.assertEqual(ba.main(["iphone", "--pasta", str(self.out)]), 0)
        media = list((self.out / "Para_iPhone/Fotos_e_Videos").rglob("*.*"))
        self.assertEqual(sorted(p.name for p in media), ["IMG-WA.jpg", "IMG_1.jpg", "VID_1.mp4"])
        self.assertTrue((self.out / "Para_iPhone/contatos.vcf").exists())


if __name__ == "__main__":
    unittest.main()
