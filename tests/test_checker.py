
import io
import json
import unittest
import zipfile

from checker import CheckError, check_archive


CONTROL = {
    "archive": "backup_2026.zip",
    "hash_algorithm": "sha256",
    "files": [
        {
            "path": "docs/report.txt",
            "size": 5,
            "sha256": "2cf24dba5fb0a30e26e83b2ac5b9e29e1b161e5c1fa7425e73043362938b9824",
        },
        {
            "path": "config/settings.txt",
            "size": 5,
            "sha256": "486ea46224d1bb4fb680f34f7c9ad96a8f24ec88be73ea8e5a6c65260e9cb8a7",
        },
    ],
}


def make_zip(files: dict[str, bytes]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for path, content in files.items():
            archive.writestr(path, content)
    return buffer.getvalue()


def run_check(files: dict[str, bytes]) -> dict[str, str]:
    result = check_archive(
        make_zip(files),
        json.dumps(CONTROL).encode("utf-8"),
    )
    return {row["path"]: row["status"] for row in result}


class CheckerTests(unittest.TestCase):
    def test_all_files_match(self):
        statuses = run_check({
            "docs/report.txt": b"hello",
            "config/settings.txt": b"world",
        })
        self.assertEqual(statuses["docs/report.txt"], "Совпадает")
        self.assertEqual(statuses["config/settings.txt"], "Совпадает")

    def test_missing_file(self):
        statuses = run_check({"config/settings.txt": b"world"})
        self.assertEqual(statuses["docs/report.txt"], "Отсутствует")
        self.assertEqual(statuses["config/settings.txt"], "Совпадает")

    def test_changed_file_same_size(self):
        statuses = run_check({
            "docs/report.txt": b"world",
            "config/settings.txt": b"world",
        })
        self.assertEqual(statuses["docs/report.txt"], "Изменён")
        self.assertEqual(statuses["config/settings.txt"], "Совпадает")

    def test_extra_file(self):
        statuses = run_check({
            "docs/report.txt": b"hello",
            "config/settings.txt": b"world",
            "notes/extra.txt": b"x",
        })
        self.assertEqual(statuses["notes/extra.txt"], "Лишний")

    def test_invalid_json(self):
        with self.assertRaises(CheckError):
            check_archive(make_zip({}), b"{bad json")

    def test_not_a_zip(self):
        with self.assertRaises(CheckError):
            check_archive(b"not a zip", json.dumps(CONTROL).encode("utf-8"))


if __name__ == "__main__":
    unittest.main(verbosity=2)
